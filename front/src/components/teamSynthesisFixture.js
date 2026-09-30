// Shared example of the current wire format for workspace tests.
export const team = {
  schema_version: 'team_synthesis.v2', revision: 1, acceptance: 'chair_adjudicated', stop_kind: 'pending',
  source_catalog: {
    S1: { source_ref: 'S1', specialty: 'pulmonology', source_type: 'interspecialty_question', quote: '影像支持哪种模式？', target_specialty: 'thoracic_radiology' },
    S2: { source_ref: 'S2', specialty: 'thoracic_radiology', source_type: 'specialty_assessment', quote: '影像文字不足以确认 UIP' },
  },
  evidence_catalog: {},
  problems: [{ problem_id: 'P1', title: '慢性肺部病变', relationship: 'primary', assessability: 'partially_assessable', statement: '当前病因仍未明确', rationale: '多专科意见存在资料限制', confidence: 'low', limitations: ['报告未记载病变分布'], source_refs: ['S2'], evidence_refs: [], candidates: [] }],
  judgment_reviews: [{ source_ref: 'S2', disposition: 'qualify', rationale: '资料限制', evidence_refs: [], required_response: false }],
  judgment_boundaries: [{ boundary_id: 'B1', problem_id: 'P1', established: '肺部病变存在', undetermined: '无法确认 UIP', reason: '报告未记载病变分布及蜂窝征', decision_impact: '不能确定模式', source_refs: ['S2'] }],
  disagreements: [],
  issues: [{ issue_id: 'I1', origin: 'chair', kind: 'clarification', target_specialties: ['pulmonology'], raised_by: [], question: '现有检查能否解释急性变化？', decision_impact: '区分慢性与急性因素', assignments: [{ specialty: 'pulmonology', question: '请说明急性变化的解释' }], closure_criterion: '明确归因或判断边界', source_refs: ['S2'], evidence_refs: [] }],
  issue_dispositions: [{ issue_id: 'S1', outcome: 'covered', rationale: '影像科已说明无法确认模式', source_refs: ['S2'], response_refs: [], linked_issue_ids: [] }],
  evidence_needs: [{ need_id: 'N1', problem_id: 'P1', information: '病变分布及蜂窝征的文字记录', status: 'missing', action: 'retain_boundary', available_information: '影像文字报告', missing_information: '病变分布文字', branches: [], feasibility_and_burden: '本轮无法获取', if_unavailable: '保留模式判断边界', source_refs: ['S2'] }],
  conditional_contributions: [], dissent: [], synthesis_rationale: '按临床问题整合', coverage_review: '已核对资料限制',
}
export const chairResult = { schema_version: 'mdt_chair.v11', case_id: 'case-1', team_synthesis: team }
