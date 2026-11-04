// Clinical meanings are shared across screens; labels never infer a diagnosis.
export const assessabilityLabels = { assessable: '可评价', partially_assessable: '部分可评价', not_assessable: '不可评价' }
export const confidenceLabels = { high: '高把握度', moderate: '中等把握度', low: '低把握度', unknown: '尚不能评定把握度', not_applicable: '不适用' }
export const directionLabels = { supports: '有正向支持', insufficient_support: '尚缺支持依据', against: '存在反对依据', excluded: '已有依据排除', indeterminate: '判断方向未定' }
export const clinicalRoleLabels = { primary: '主要解释', competing_explanation: '竞争解释', acute_contributor: '急性叠加因素', comorbidity: '并存问题', unexplained_finding: '尚未解释的发现', boundary: '判断边界' }
export const candidatePositionLabels = { preferred: '当前首选', alternative: '保留候选', unresolved: '尚未择定' }
export const candidateOutcomeLabels = { preferred: '当前首选', retained: '保留候选', rejected: '本次未保留', needs_discussion: '待讨论' }
export const nonselectionLabels = { insufficient_support: '尚缺支持依据', less_explanatory: '相对解释力较弱', counterevidence: '存在反对依据', excluded: '已有明确依据排除', out_of_scope: '不属于当前问题范围' }
export const diagnosisStatusLabels = { specific_diagnosis: '具体诊断意见', provisional_diagnosis: '暂定诊断意见', specific_differential: '有限鉴别诊断', needs_discussion: '诊断推理待讨论' }
export const acceptanceLabels = { jointly_accepted: '共同接受', accepted_with_boundaries: '带边界共同接受', chair_adjudicated: '主持人综合，尚未共同接受', explicit_dissent: '保留明确分歧' }
export const legacyStatusLabels = { supported: '支持', support: '支持', favored: '倾向', leaning: '倾向', possible: '可能', indeterminate: '未定', unclassifiable: '不能分类', not_assessable: '不可评价', not_applicable: '不适用' }
export const diagnosticDimensionLabels = { ild_presence: 'ILD 是否存在', radiologic_pattern: '影像模式', histopathologic_pattern: '组织学模式', etiologic_attribution: '病因归属', disease_behavior: '疾病行为', acute_or_comorbid_factors: '急性与伴随因素', severity: '严重度' }
export const answerabilityLabels = { answered: '已回答', partially_answered: '部分回答', not_assessable: '当前无法回答' }
export const candidateName = (team, candidate) => team.diagnostic_exploration?.candidates?.find(c => c.candidate_id === candidate.candidate_id)?.diagnosis || candidate.diagnosis

export const problemRelationshipLabels = { primary: '主要临床问题', acute_contributor: '急性叠加因素', comorbidity: '并存问题', unexplained_finding: '尚未解释的发现' }
export const evidenceNeedStatusLabels = { available: '已满足', partially_available: '部分满足', missing: '尚缺资料' }
export const evidenceNeedActionLabels = { retrieve_existing: '核对已有结果', obtain_new: '建议获取新资料', retain_boundary: '保留判断边界', no_further_request: '本轮不再追索' }
