import scripts.run.run_mdt_chair_agent as runner
from src.agents.common.initial_output import SpecialtyInitialOutput
from synthetic_case import write_run
from test_expert_protocol import Responses, setup_case


def test_cli_integrates_formal_specialty_files_without_top_level_case_id(monkeypatch, tmp_path):
    state, _, payload = setup_case()
    write_run(tmp_path)
    for specialty, context in state.specialty_context.items():
        formal = SpecialtyInitialOutput.model_validate({
            'specialty_assessments': {
                **{key: value for key, value in context.items() if key != 'questions'},
                'assessments': [history.versions[0].assessment.model_dump(mode='json')
                                for history in state.judgments if history.specialty == specialty],
            },
            'interspecialty_questions': {'questions': context['questions']},
        }).model_dump(mode='json')
        assert set(formal) == {'specialty_assessments', 'interspecialty_questions'}
        runner.write_json(tmp_path / f'synthetic-case_{specialty}_initial.json', formal)
    llm = Responses([payload])
    monkeypatch.setattr(runner.sys, 'argv', ['run_mdt_chair_agent.py', '--run-dir', str(tmp_path)])
    monkeypatch.setattr(runner, 'build_llm_client', lambda config: llm)
    monkeypatch.setattr(runner, 'load_env_file', lambda: None)
    monkeypatch.setattr('src.guidelines.runtime.GuidelineRuntime.from_config', lambda config: None)

    assert runner.main() == 0
    result = runner.read_json(tmp_path / 'synthetic-case_mdt_chair_integration.json')
    saved_state = runner.read_json(tmp_path / 'synthetic-case_mdt_decision_state.json')
    assert result['case_id'] == saved_state['case_id'] == 'synthetic-case'
    assert result['schema_version'] == 'mdt_chair.v11'
    assert set(result['team_synthesis']['dependency_versions'].values()) == {
        history['active_version_id'] for history in saved_state['judgments']
    }
    assert llm.calls == 1
