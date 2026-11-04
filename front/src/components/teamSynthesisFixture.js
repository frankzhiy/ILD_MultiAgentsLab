// Shared example of the current wire format for workspace tests.
export const team = {
  schema_version: 'team_synthesis.v2', revision: 1, acceptance: 'chair_adjudicated', stop_kind: 'pending',
  source_catalog: {
    S1: { source_ref: 'S1', specialty: 'pulmonology', source_type: 'interspecialty_question', quote: '影像支持哪种模式？', target_specialty: 'thoracic_radiology' },
    S2: { source_ref: 'S2', specialty: 'thoracic_radiology', source_type: 'specialty_assessment', quote: '影像文字不足以确认 UIP' },
  },
  evidence_catalog: {},
  diagnostic_opinion: {
    case_formulation: '慢性肺部异常伴近期变化，需要分别解释基础病和本次变化。',
    diagnosis: '当前支持慢性肺实质病变，具体病因未定。',
    reasoning: '慢性病程与肺部所见共同支持基础肺病，现有模式资料尚不足以归属具体疾病。',
    differential_reasoning: '影像模式与疾病病因分别评价，现有依据不足以优先确定特定间质性肺病。',
    limitations: [], problem_ids: ['P1'], source_refs: ['S2'], evidence_refs: [],
  },
  diagnostic_exploration: { problem_representation: '慢性肺部异常，模式资料缺失', combined_phenotype_review: '现有记录不足以识别组合表型', candidates: [], insufficient_candidate_basis: '未提供可支持具体候选的模式信息', unrepresented_findings: '病因尚未确定' },
  diagnostic_resolution: { status: 'provisional_diagnosis', achieved_level: 'category', primary_diagnosis: '慢性肺实质病变', goal_review: '整合慢性病程及肺部所见，形成当前暂定判断', remaining_gaps: [{ kind: 'patient_evidence', question: '肺部异常的分布与形态', why_unresolved: '影响具体亚型的确定程度', scope: 'primary_diagnosis', issue_ids: [] }], candidate_dispositions: [] },
  problems: [{ problem_id: 'P1', title: '慢性肺部病变', relationship: 'primary', assessability: 'partially_assessable', statement: '当前病因仍未明确', rationale: '多专科意见存在资料限制', confidence: 'low', limitations: ['报告未记载病变分布'], source_refs: ['S2'], evidence_refs: [], candidates: [] }],
  judgment_reviews: [{ source_ref: 'S2', disposition: 'qualify', rationale: '资料限制', evidence_refs: [], required_response: false }],
  judgment_boundaries: [{ boundary_id: 'B1', problem_id: 'P1', established: '肺部病变存在', undetermined: '无法确认 UIP', reason: '报告未记载病变分布及蜂窝征', decision_impact: '不能确定模式', source_refs: ['S2'] }],
  disagreements: [],
  issues: [{ issue_id: 'I1', origin: 'chair', kind: 'clarification', target_specialties: ['pulmonology'], raised_by: [], question: '现有检查能否解释急性变化？', decision_impact: '区分慢性与急性因素', assignments: [{ specialty: 'pulmonology', question: '请说明急性变化的解释' }], closure_criterion: '明确归因或判断边界', source_refs: ['S2'], evidence_refs: [] }],
  issue_dispositions: [{ issue_id: 'S1', outcome: 'covered', rationale: '影像科已说明无法确认模式', source_refs: ['S2'], response_refs: [], linked_issue_ids: [] }],
  evidence_needs: [{ need_id: 'N1', problem_id: 'P1', information: '病变分布及蜂窝征的文字记录', status: 'missing', action: 'retain_boundary', available_information: '影像文字报告', missing_information: '病变分布文字', branches: [], feasibility_and_burden: '本轮无法获取', if_unavailable: '保留模式判断边界', source_refs: ['S2'] }],
  conditional_contributions: [], dissent: [], synthesis_rationale: '按临床问题整合', coverage_review: '已核对资料限制',
}
export const chairResult = { schema_version: 'mdt_chair.v13', case_id: 'case-1', team_synthesis: team }
