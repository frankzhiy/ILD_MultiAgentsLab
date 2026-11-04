"""Current clinical views must never mix historical snapshots with new results."""
from copy import deepcopy
from hashlib import sha256
import asyncio

import pytest
from fastapi.testclient import TestClient

from src.agents.common.decision_state import project_active_specialty_outputs
from src.agents.common.team_synthesis import DiagnosticCandidate, MDTTeamReport, TeamSynthesis
from src.agents.mdt_discussion.final_report import report_diagnosis
from src.utils.persistence import write_json
from src.workbench.catalog import RunCatalog
from src.workbench.events import EventStore
from src.workbench.runner import RunOrchestrator
from test_expert_protocol import setup_case, run_expert


@pytest.fixture
def current_run(tmp_path):
    directory = tmp_path / 'outputs/runs/run-1'
    directory.mkdir(parents=True)
    case = '100-IPF'
    state, bundle, payload = setup_case()
    for specialty, output in project_active_specialty_outputs(state).items():
        for item in output['specialty_assessments']['assessments']:
            for key in ('judgment_id', 'version_id', 'previous_version_id', 'origin'):
                item.pop(key, None)
        write_json(directory / f'{case}_{specialty}_initial.json', output)
    write_json(directory / '.workbench_run.json', {'case_id': case})
    for suffix in ('clinical_propositions', 'local_graphs'):
        write_json(directory / f'{case}_{suffix}.json', {'segments': []})
    result = run_expert(bundle, [payload]).model_dump(mode='json')
    write_json(directory / f'{case}_mdt_chair_integration.json', result)
    report = MDTTeamReport(case_id=case, team_synthesis=result['team_synthesis'],
                           stop_reason='合成测试完成', discussion_rounds=1).model_dump(mode='json')
    discussion = dict(schema_version='mdt_discussion.v6', status='completed', report_status='completed',
        baseline_sha256=sha256((directory / f'{case}_mdt_chair_integration.json').read_bytes()).hexdigest(),
        latest_chair_result=result, rounds=[{'round_number': 1, 'chair_result': result}],
        active_round=None, decision_state={'judgments': []}, final_report=report, stop_reason='完成')
    write_json(directory / f'{case}_mdt_discussion_state.json', discussion)
    write_json(directory / f'{case}_mdt_final_report.json', report)
    return RunCatalog(tmp_path), directory, case, result, discussion


def test_get_endpoints_return_exact_saved_results_without_presentation(monkeypatch, current_run):
    import src.workbench.app as api
    catalog, directory, case, result, discussion = current_run
    monkeypatch.setattr(api, 'catalog', catalog)
    client = TestClient(api.app)
    response = client.get('/api/runs/run-1/chair')
    assert response.headers['cache-control'] == 'no-store'
    assert response.json()['result'] == result
    assert client.get('/api/runs/run-1/discussion').json()['rounds'] == discussion['rounds']
    report = client.get('/api/runs/run-1/report').json()
    assert report['final_report'] == discussion['final_report']
    primary = next(p for p in result['team_synthesis']['problems'] if p['relationship'] == 'primary')
    assert report['diagnosis_conclusion'] == primary['statement']
    for item in client.get('/api/runs/run-1/specialties').json()['results']:
        import json
        saved = json.loads((directory / f"{case}_{item['specialty']}_initial.json").read_text())
        assert item['output'] == saved
    for route in ('specialties', 'chair', 'discussion', 'report'):
        assert client.post(f'/api/runs/run-1/{route}/presented').status_code == 404
    assert not list(directory.glob('presentation_*.json'))


@pytest.mark.parametrize(("positions", "expected"), [
    (("preferred", "alternative"), "主要考虑诊断甲，鉴别诊断包括诊断乙。"),
    (("unresolved", "unresolved", "alternative"), "诊断甲与诊断乙尚需鉴别；另需鉴别诊断丙。"),
])
def test_report_conclusion_preserves_primary_candidate_roles(current_run, positions, expected):
    team = TeamSynthesis.model_validate(current_run[3]["team_synthesis"])
    primary = next(p for p in team.problems if p.relationship == "primary")
    candidates = [
        DiagnosticCandidate(diagnosis=name, position=position, confidence="low",
                            rationale="测试依据", source_refs=["S001"])
        for name, position in zip(("诊断甲", "诊断乙", "诊断丙"), positions)
    ]
    associated = primary.model_copy(update={"relationship": "comorbidity", "statement": "并存诊断丁"})
    team = team.model_copy(update={"problems": [primary.model_copy(update={"candidates": candidates}), associated]})
    assert report_diagnosis(team) == expected


def test_changed_baseline_hides_all_previous_discussion_content(current_run):
    catalog, directory, case, result, discussion = current_run
    # Even a valid new baseline changes the exact dependency identity.
    write_json(directory / f'{case}_mdt_chair_integration.json', {**result, 'case_id': 'new-case-version'})
    value = catalog.discussion('run-1')
    assert value['status'] == 'outdated'
    assert value['rounds'] == [] and value['current_round'] == 0
    for key in ('active_round', 'latest_chair_result', 'decision_state', 'final_report', 'stop_reason'):
        assert value[key] is None
    assert catalog.report('run-1')['final_report'] is None
    assert (directory / f'{case}_mdt_discussion_state.json').exists()


def test_old_partial_specialty_contract_cannot_be_completed_or_runnable(current_run):
    catalog, directory, case, result, discussion = current_run
    output = catalog.specialties('run-1')['results'][0]['output']
    del output['specialty_assessments']['assessments'][0]['claims']
    write_json(directory / f'{case}_pulmonology_initial.json', output)
    item = catalog.specialties('run-1')['results'][0]
    assert item['status'] == 'outdated' and item['legacy'] and item['output'] is None
    assert not catalog.chair('run-1')['runnable']
    assert catalog.chair('run-1')['result'] is None
    assert 'pulmonology' not in catalog.run_summary(directory)['completed_specialties']


def test_same_version_tampered_snapshot_is_hidden(current_run):
    catalog, directory, case, result, discussion = current_run
    result['team_synthesis']['diagnostic_resolution']['primary_diagnosis'] = '改写后的诊断'
    write_json(directory / f'{case}_mdt_chair_integration.json', result)
    assert catalog.chair('run-1')['status'] == 'outdated'
    assert catalog.chair('run-1')['result'] is None


def test_previous_input_limited_result_remains_a_historical_artifact(current_run):
    catalog, directory, case, result, discussion = current_run
    result['schema_version'] = 'mdt_chair.v12'
    result['team_synthesis']['diagnostic_resolution']['status'] = 'input_limited'
    path = directory / f'{case}_mdt_chair_integration.json'
    write_json(path, result)
    assert catalog.chair('run-1')['status'] == 'outdated'
    assert catalog.chair('run-1')['result'] is None
    assert catalog.report('run-1')['final_report'] is None
    assert path.exists()


def test_new_specialty_result_invalidates_chair_and_report(current_run):
    catalog, directory, case, result, discussion = current_run
    import os
    baseline = directory / f'{case}_mdt_chair_integration.json'
    specialty = directory / f'{case}_pulmonology_initial.json'
    os.utime(specialty, (baseline.stat().st_mtime + 10, baseline.stat().st_mtime + 10))
    assert catalog.chair('run-1')['status'] == 'outdated'
    assert not catalog.run_summary(directory)['chair_complete']
    assert catalog.report('run-1')['final_report'] is None


def test_report_requires_exact_latest_snapshot_and_saved_file(current_run):
    catalog, directory, case, result, discussion = current_run
    assert catalog.report('run-1')['report_status'] == 'completed'
    altered = deepcopy(discussion['final_report'])
    altered['team_synthesis']['diagnostic_resolution']['primary_diagnosis'] = '旧报告判断'
    write_json(directory / f'{case}_mdt_final_report.json', altered)
    assert catalog.report('run-1')['report_status'] == 'outdated'
    assert catalog.report('run-1')['final_report'] is None
    artifacts = {item['name']: item for item in catalog.artifacts('run-1')}
    assert not artifacts[f'{case}_mdt_final_report.json']['current']
    assert artifacts[f'{case}_mdt_chair_integration.json']['current']


def test_rerun_archives_all_downstream_results_before_worker_starts(monkeypatch, current_run):
    catalog, directory, case, result, discussion = current_run
    runner = RunOrchestrator(catalog.root, catalog, EventStore(catalog.root / 'events.sqlite3'))
    write_json(directory / f'{case}_mdt_decision_state.json', {'judgments': ['old']})
    async def no_work(*args, **kwargs):
        pass
    monkeypatch.setattr(runner, '_execute', no_work)
    async def exercise():
        task = runner._launch('run-1', start_stage='mdt_chair')
        assert catalog.chair('run-1')['result'] is None
        assert catalog.discussion('run-1')['rounds'] == []
        assert catalog.report('run-1')['final_report'] is None
        assert not (directory / f'{case}_mdt_decision_state.json').exists()
        assert len(list((directory / 'stage_history').rglob(f'{case}_mdt_final_report.json'))) == 1
        await task
    asyncio.run(exercise())
