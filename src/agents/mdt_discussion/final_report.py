"""Deterministic projection of the sealed team synthesis."""

from src.agents.common.team_synthesis import TeamSynthesis, case_diagnosis_text
from src.agents.common.team_synthesis import primary_diagnosis_text as report_diagnosis


def project_team_report(case_id, chair_result, rounds, stop_reason, baseline, decision_state):
    """No diagnostic model call: every clinical sentence comes from the sealed chair snapshot."""
    from src.agents.common.decision_state import active_judgments
    from src.agents.mdt_chair.expert import synthesis_hash
    team = TeamSynthesis.model_validate(chair_result['team_synthesis'])
    if team.diagnostic_opinion is None:
        raise ValueError('缺少主持人全病例诊断意见，请重新运行会前整合及讨论后生成报告')
    if team.diagnostic_resolution is None or team.diagnostic_exploration is None:
        raise ValueError("缺少诊断目标复核，请重新运行会前整合")
    if team.stop_kind == 'completed' and team.diagnostic_resolution.status == 'needs_discussion':
        raise ValueError('诊断推理未完成，不能标记讨论完成')
    if synthesis_hash(team) != team.snapshot_hash:
        raise ValueError('Team synthesis hash mismatch; review again before reporting')
    if team.diagnostic_opinion.diagnosis != case_diagnosis_text(team):
        raise ValueError("综合诊断与已审定的临床问题不一致，请从主持人整合阶段重新运行。")
    if decision_state is not None:
        current = {v.version_id for v in active_judgments(decision_state)}
        if set(team.dependency_versions.values()) != current:
            raise ValueError('Team synthesis depends on obsolete or unreviewed judgment versions')
    if team.stop_kind not in {'completed', 'budget', 'no_progress'}:
        raise ValueError('Discussion must record its stop kind before reporting')
    from src.agents.common.team_synthesis import MDTTeamReport
    report = MDTTeamReport(case_id=case_id, team_synthesis=team,
                           discussion_rounds=len(rounds), stop_reason=stop_reason)
    return report, {'mode': 'deterministic_projection', 'synthesis_revision': team.revision,
                    'snapshot_hash': team.snapshot_hash, 'model_calls': 0}
