"""The public stage lifecycle, without patient data or network requests."""
import asyncio
from copy import deepcopy
from threading import Event
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from src.agents.common.decision_state import SpecialtyJudgmentUpdate, project_active_specialty_outputs
from src.agents.mdt_chair.agent import MDTChairAgent
from src.agents.mdt_discussion.models import SpecialtyTaskAnswer
from src.agents.mdt_discussion.specialty_agent import SpecialtyDiscussionAgent
from src.utils.persistence import write_json, verify_stage_snapshot
from src.workbench.catalog import RunCatalog, SPECIALTIES
from src.workbench.events import EventStore
from src.workbench.runner import RunOrchestrator
from test_expert_protocol import Responses, setup_case


def test_stop_rerun_through_report_and_independent_report_retry(monkeypatch, tmp_path):
    state, _, payload = setup_case()
    run_dir = tmp_path / 'outputs/runs/run-1'
    run_dir.mkdir(parents=True)
    case = '100-IPF'
    for specialty, output in project_active_specialty_outputs(state).items():
        for assessment in output['specialty_assessments']['assessments']:
            for key in ('judgment_id', 'version_id', 'previous_version_id', 'origin'):
                assessment.pop(key, None)
        write_json(run_dir / f'{case}_{specialty}_initial.json', output)
    for name in ('clinical_propositions', 'local_graphs'):
        write_json(run_dir / f'{case}_{name}.json', {'segments': []})
    for agent in (*SPECIALTIES, 'mdt_chair'):
        path = tmp_path / f'configs/agents/{agent}/agent.yaml'
        path.parent.mkdir(parents=True)
        path.write_text('{}')
    write_json(run_dir / '.workbench_run.json', {'case_id': case, 'run_id': 'run-1', 'status': 'completed'})
    for index, specialty in enumerate(('pulmonology', 'thoracic_radiology'), 1):
        payload['issues'].append(dict(issue_id=f'I{index}', origin='chair', kind='clarification',
            question=f'请{specialty}说明判断边界', target_specialties=[specialty],
            assignments=[dict(specialty=specialty, question='说明判断边界')],
            source_refs=[payload['judgment_reviews'][index-1]['source_ref']],
            decision_impact='限定归因', closure_criterion='说明边界'))
    catalog = RunCatalog(tmp_path)
    events = EventStore(tmp_path / 'events.sqlite3')
    runner = RunOrchestrator(tmp_path, catalog, events)
    monkeypatch.setattr('src.workbench.workflow.build_llm_client', lambda _: None)
    calls = []
    entered, release, completed_answer = Event(), Event(), Event()
    original_append = events.append
    def append(run_id, event, *args, **kwargs):
        result = original_append(run_id, event, *args, **kwargs)
        if event == 'discussion_task_completed':
            completed_answer.set()
        return result
    monkeypatch.setattr(events, 'append', append)

    def chair_factory(_cls, *_args, **kwargs):
        class Chair:
            def integrate(self, bundle, **context):
                calls.append('chair')
                candidate = deepcopy(payload)
                if context:
                    candidate['issues'] = []
                    candidate['issue_dispositions'] = [dict(issue_id=a.issue_id, outcome='bounded',
                        rationale='保留资料边界', response_refs=[a.answer_id])
                        for r in context['discussion_responses'] for a in r.answers]
                agent = MDTChairAgent(Responses([candidate]),
                    prompt_path='src/prompts/mdt_chair/expert_synthesis.md', max_attempts=1, event_callback=kwargs['event_callback'])
                return agent.integrate(bundle, **context)
        return Chair()
    monkeypatch.setattr(MDTChairAgent, 'from_config', classmethod(chair_factory))

    def specialty_factory(_cls, *_args, specialty, event_callback):
        def respond(*, task, **_):
            calls.append(task.task_id)
            if specialty == 'thoracic_radiology' and not release.is_set():
                entered.set()
                assert release.wait(5), 'test did not release the in-flight request'
            event_callback('llm_attempt_completed', {})
            return SpecialtyTaskAnswer(answer_id=task.task_id + '-A', task_id=task.task_id,
                issue_type='question', issue_id=task.issue_id, answerability='not_assessable',
                answer='当前资料不足', confidence='unknown', medical_basis='缺少资料',
                changed_from_previous=False), {}
        def update(**_):
            return SpecialtyJudgmentUpdate(specialty=specialty, transaction_id='review-' + specialty), {}
        def review_synthesis(*, team, **_):
            from src.agents.common.team_synthesis import SynthesisAcceptance
            calls.append('accept-' + specialty)
            return SynthesisAcceptance(specialty=specialty, revision=team.revision,
                snapshot_hash=team.snapshot_hash, decision='accept_with_boundaries',
                rationale='接受当前判断范围', boundaries=['病原未定']), {}
        return SimpleNamespace(respond_to_task=respond, propose_judgment_update=update, review_synthesis=review_synthesis)
    monkeypatch.setattr(SpecialtyDiscussionAgent, 'from_config', classmethod(specialty_factory))

    async def exercise():
        task = runner.start_chair('run-1')
        assert await asyncio.to_thread(entered.wait, 3)
        assert await asyncio.to_thread(completed_answer.wait, 3)
        assert catalog.chair('run-1')['status'] == 'completed'
        assert catalog.run_summary(run_dir)['status'] == 'running'
        runner.stop_run('run-1')
        assert catalog.run_summary(run_dir)['status'] == 'stopping'
        with pytest.raises(ValueError, match='仍在执行'):
            runner.start_discussion('run-1')
        release.set()
        await task
        await asyncio.sleep(0)
        stopped = catalog.discussion('run-1')
        assert stopped['status'] == 'stopped'
        assert stopped['active_round']['task_progress']['R01-I1-pulmonology']['status'] == 'completed'
        assert stopped['active_round']['task_progress']['R01-I2-thoracic_radiology']['status'] == 'stopped'
        assert stopped['final_report'] is None
        with pytest.raises(ValueError):
            runner.start_report('run-1')
        assert calls.count('chair') == 1  # Stop prevented the next integration.
        await runner.start_discussion('run-1')
        await asyncio.sleep(0)
        assert catalog.discussion('run-1')['status'] == 'completed'
        assert calls.count('R01-I1-pulmonology') == 2  # Whole stage restarts, including completed tasks.
        assert catalog.report('run-1')['final_report'] is not None
        assert catalog.run_summary(run_dir)['status'] == 'completed'
        assert list((run_dir / 'stage_history').glob(f'*/{case}_mdt_discussion_state.json'))
        from src.agents.mdt_discussion import final_report
        project = final_report.project_team_report
        def fail(*_):
            raise ValueError('模拟报告阶段失败')
        monkeypatch.setattr(final_report, 'project_team_report', fail)
        await runner.start_report('run-1')
        await asyncio.sleep(0)
        assert catalog.report('run-1')['status'] == 'failed'
        assert catalog.discussion('run-1')['status'] == 'completed'
        calls_before = list(calls)
        monkeypatch.setattr(final_report, 'project_team_report', project)
        await runner.start_report('run-1')
        await asyncio.sleep(0)
        report = catalog.report('run-1')
        assert report['status'] == 'completed'
        assert report['final_report']['schema_version'] == 'mdt_final_report.v8'
        assert report['final_report']['team_synthesis']['schema_version'] == 'team_synthesis.v2'
        assert len(report['final_report']['team_synthesis']['acceptance_records']) == 4
        assert report['final_report']['team_synthesis']['acceptance'] == 'accepted_with_boundaries'
        assert calls == calls_before  # Report retry never reruns upstream agents.
        # A failure before the report worker clears the previous state must still hide it.
        original_report = runner.workflow.run_report
        monkeypatch.setattr(runner.workflow, 'run_report', fail)
        await runner.start_report('run-1')
        assert not catalog.run_summary(run_dir)['discussion_complete']
        assert catalog.report('run-1')['final_report'] is None
        monkeypatch.setattr(runner.workflow, 'run_report', original_report)
        def stop_report(*args):
            runner.workflow.stop_events['run-1'].set()
            return project(*args)
        monkeypatch.setattr(final_report, 'project_team_report', stop_report)
        await runner.start_report('run-1')
        assert catalog.run_summary(run_dir)['status'] == 'stopped'
        assert catalog.report('run-1')['report_status'] == 'stopped'
        assert catalog.report('run-1')['final_report'] is None
        assert not (run_dir / f'{case}_mdt_final_report.json').exists()
        monkeypatch.setattr(final_report, 'project_team_report', fail)
        await runner.start_report('run-1')
        await asyncio.sleep(0)
        assert catalog.report('run-1')['final_report'] is None  # Failed retry cannot display old report.
        payload['synthesis_rationale'] = '会前整合重新复核'
        await runner.start_chair('run-1')
        await asyncio.sleep(0)
        assert catalog.discussion('run-1')['status'] == 'completed'
        assert catalog.report('run-1')['status'] == 'failed'
        assert catalog.run_summary(run_dir)['status'] == 'failed'
        assert catalog.report('run-1')['final_report'] is None
    asyncio.run(exercise())


def test_stop_prevents_retry_and_queued_model_request(tmp_path):
    from src.llm.structured import StructuredLLMGenerator
    from src.workbench.workflow import RunStopped, WorkbenchWorkflow
    from pydantic import BaseModel
    class Result(BaseModel):
        value: str
    workflow = WorkbenchWorkflow(tmp_path, EventStore(tmp_path / 'events.sqlite3'))
    stop = workflow.stop_events['run-1'] = Event()
    class LLM:
        calls = 0
        def complete(self, *_args, **_kwargs):
            self.calls += 1
            stop.set()
            raise RuntimeError('connection interrupted')
    llm = LLM()
    generator = StructuredLLMGenerator(llm, temperature=0, max_tokens=100, max_attempts=4, event_callback=workflow._progress('run-1', 'pulmonology'))
    with pytest.raises(RunStopped):
        generator.generate(schema_model=Result, schema_name='test', system_prompt='test', user_prompt='test')
    assert llm.calls == 1
    with pytest.raises(RunStopped):
        generator.generate(schema_model=Result, schema_name='test', system_prompt='test', user_prompt='test')
    assert llm.calls == 1


def test_stage_uses_current_rules_and_rejects_midflight_changes(tmp_path):
    runner = RunOrchestrator(tmp_path, RunCatalog(tmp_path), EventStore(tmp_path / 'events.sqlite3'))
    run_dir = tmp_path / 'outputs/runs/run-1'
    run_dir.mkdir(parents=True)
    config = tmp_path / 'configs/agents/mdt_chair/agent.yaml'
    config.parent.mkdir(parents=True)
    config.write_text('model: fixed-model\n')
    write_json(run_dir / '.workbench_run.json', {'case_id': 'case-1'})
    runner._refresh_configs(run_dir, ('mdt_chair',))
    runner._record_stage_rules(run_dir, 'mdt_chair')
    verify_stage_snapshot(tmp_path, run_dir, 'mdt_chair')
    (run_dir / 'workbench_config/mdt_chair.yaml').write_text('model: changed-midflight\n')
    with pytest.raises(ValueError, match='运行期间规则发生改变'):
        verify_stage_snapshot(tmp_path, run_dir, 'mdt_chair')
    config.write_text('model: fixed-new-model\n')
    runner._refresh_configs(run_dir, ('mdt_chair',))
    runner._archive_stage(run_dir, 'mdt_chair')
    runner._record_stage_rules(run_dir, 'mdt_chair')
    verify_stage_snapshot(tmp_path, run_dir, 'mdt_chair')


def test_stop_and_report_endpoints(monkeypatch, tmp_path):
    import src.workbench.app as api
    calls = []
    monkeypatch.setattr(api.orchestrator, 'stop_run', lambda run: calls.append(('stop', run)))
    monkeypatch.setattr(api.catalog, 'run_dir', lambda run: tmp_path)
    monkeypatch.setattr(api.catalog, 'run_summary', lambda directory: {'status': 'stopping'})
    monkeypatch.setattr(api.orchestrator, 'start_report', lambda run: calls.append(('report', run)))
    monkeypatch.setattr(api.catalog, 'report', lambda run: {'status': 'waiting', 'runnable': True})
    client = TestClient(api.app)
    assert client.post('/api/runs/example/stop').json()['status'] == 'stopping'
    response = client.post('/api/runs/example/report')
    assert response.status_code == 202
    assert response.json()['report_status'] == 'running'
    assert calls == [('stop', 'example'), ('report', 'example')]
    def invalid(_):
        raise ValueError('阶段尚不可运行')
    monkeypatch.setattr(api.orchestrator, 'start_report', invalid)
    monkeypatch.setattr(api.orchestrator, 'stop_run', invalid)
    assert client.post('/api/runs/example/report').status_code == 409
    assert client.post('/api/runs/example/stop').status_code == 409
    assert client.post('/api/runs/example/discussion/stop').status_code == 404


@pytest.mark.parametrize('stop_stage', ['semantic_graphing', 'pulmonology', 'mdt_chair', 'mdt_discussion', 'mdt_report'])
def test_global_stop_waits_for_active_stage_and_blocks_downstream(monkeypatch, tmp_path, stop_stage):
    run_dir = tmp_path / 'outputs/runs/run-1'
    run_dir.mkdir(parents=True)
    write_json(run_dir / '.workbench_run.json', {
        'case_id': 'case-1', 'status': 'queued',
        'configs': {agent: str(tmp_path / f'{agent}.yaml') for agent in ('semantic_graphing', *SPECIALTIES, 'mdt_chair')},
    })
    runner = RunOrchestrator(tmp_path, RunCatalog(tmp_path), EventStore(tmp_path / 'events.sqlite3'))
    entered, release = Event(), Event()
    calls = []
    def step(name):
        def run(*args):
            stage = args[3] if name == 'specialty' else name
            calls.append(stage)
            if stage == stop_stage:
                entered.set()
                assert release.wait(5)
            (run_dir / f'{stage}_saved.txt').write_text('completed work')
        return run
    for method, stage in [('semantic', 'semantic_graphing'), ('specialty', 'specialty'),
                          ('chair', 'mdt_chair'), ('discussion', 'mdt_discussion'), ('report', 'mdt_report')]:
        monkeypatch.setattr(runner.workflow, f'run_{method}', step(stage))
    monkeypatch.setattr(runner, '_record_stage_rules', lambda *_: None)

    async def exercise():
        task = runner.start('run-1', tmp_path / 'input.txt')
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            runner.stop_run('run-1')
            assert runner.catalog.run_summary(run_dir)['status'] == 'stopping'
            assert not task.done()
            with pytest.raises(ValueError, match='仍在执行'):
                runner.start('run-1', tmp_path / 'input.txt')
        finally:
            release.set()
            await task
        assert runner.catalog.run_summary(run_dir)['status'] == 'stopped'
        assert not list(run_dir.glob('*failure_trace.json'))
        assert (run_dir / f'{stop_stage}_saved.txt').exists()
        assert 'run_completed' not in [e['type'] for e in runner.events.list('run-1')]
        assert 'run-1' not in runner.workflow.stop_events
        order = ['semantic_graphing', 'pulmonology', 'mdt_chair', 'mdt_discussion', 'mdt_report']
        assert not set(order[order.index(stop_stage) + 1:]) & set(calls)
    asyncio.run(exercise())


def test_stop_at_stage_boundary_does_not_start_the_next_stage(monkeypatch, tmp_path):
    run_dir = tmp_path / 'outputs/runs/run-1'
    run_dir.mkdir(parents=True)
    write_json(run_dir / '.workbench_run.json', {'case_id': 'case-1', 'configs': {agent: 'unused' for agent in ('semantic_graphing', *SPECIALTIES, 'mdt_chair')}})
    runner = RunOrchestrator(tmp_path, RunCatalog(tmp_path), EventStore(tmp_path / 'events.sqlite3'))
    monkeypatch.setattr(runner.workflow, 'run_semantic', lambda *_: None)
    original = runner.events.append
    def append(run_id, event, *args, **kwargs):
        result = original(run_id, event, *args, **kwargs)
        if event == 'stage_completed':
            runner.stop_run(run_id)
        return result
    monkeypatch.setattr(runner.events, 'append', append)
    async def exercise():
        await runner.start('run-1', tmp_path / 'input.txt')
    asyncio.run(exercise())
    assert runner.catalog.run_summary(run_dir)['status'] == 'stopped'
    assert [e['agent_id'] for e in runner.events.list('run-1') if e['type'] == 'stage_started'] == ['semantic_graphing']


def test_semantic_generators_receive_the_same_stop_signal(monkeypatch, tmp_path):
    from pydantic import BaseModel
    from src.llm.structured import StructuredLLMGenerator
    from src.workbench.workflow import WorkbenchWorkflow, RunStopped
    workflow = WorkbenchWorkflow(tmp_path, EventStore(tmp_path / 'events.sqlite3'))
    stop = workflow.stop_events['run-1'] = Event()
    class Result(BaseModel):
        value: str
    class LLM:
        calls = 0
        def complete(self, *_args, **_kwargs):
            self.calls += 1
            stop.set()
            raise RuntimeError('request interrupted')
    llm = LLM()
    parts = {name: SimpleNamespace(generator=StructuredLLMGenerator(llm, temperature=0, max_tokens=100))
             for name in ('classifier', 'graph_unit_extractor', 'primary_frame_selector', 'clinical_proposition_extractor')}
    def generate(part):
        return part.generator.generate(schema_model=Result, schema_name='test', system_prompt='test', user_prompt='test')
    agent = SimpleNamespace(**parts, classify=lambda *_args, **_kwargs: generate(parts['classifier']))
    monkeypatch.setattr('src.workbench.workflow.SemanticGraphingAgent.from_config', lambda *_: agent)
    monkeypatch.setattr('src.workbench.workflow.build_llm_client', lambda _: llm)
    config = tmp_path / 'config.yaml'
    config.write_text('{}')
    source = tmp_path / 'input.txt'
    source.write_text('synthetic case')
    with pytest.raises(RunStopped):
        workflow.run_semantic('run-1', tmp_path, source, 'case-1', config)
    for part in parts.values():
        with pytest.raises(RunStopped):
            generate(part)
    assert llm.calls == 1
