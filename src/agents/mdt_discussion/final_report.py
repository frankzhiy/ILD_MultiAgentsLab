"""Deterministic projection of the sealed team synthesis."""

def project_team_report(case_id, chair_result, rounds, stop_reason, baseline, decision_state):
    """No diagnostic model call: every clinical sentence comes from the sealed chair snapshot."""
    from src.agents.common.team_synthesis import TeamSynthesis
    from src.agents.common.decision_state import active_judgments
    from src.agents.mdt_chair.expert import synthesis_hash
    team = TeamSynthesis.model_validate(chair_result['team_synthesis'])
    if synthesis_hash(team) != team.snapshot_hash:
        raise ValueError('Team synthesis hash mismatch; review again before reporting')
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
