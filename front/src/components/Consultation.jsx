import { Alert, Card, Tag, Typography } from 'antd'
import { diagnosisStatusLabels as labels, candidateOutcomeLabels as outcomes, nonselectionLabels } from './clinicalConcepts'

const { Paragraph, Text } = Typography

export const consultationTemplates = [
  { value: 'refine_diagnosis', label: '进一步明确诊断' },
  { value: 'assess_ild', label: '判断是否属于ILD' },
  { value: 'review_diagnosis', label: '复核已有诊断' },
]

export function ConsultationBackground({ value }) {
  if (!value) return null
  const label = consultationTemplates.find(item => item.value === value.template)?.label || '会诊背景未指定'
  return <Card title="会诊任务" className="section-card"><Text strong>{label}</Text>
    {value.question && <Paragraph>{value.question}</Paragraph>}
    <Paragraph type="secondary">转诊背景与患者证据分别记录；模板不证明患者具备某项诊断征象。</Paragraph>
  </Card>
}

const levels = { disease: '疾病', syndrome: '综合征', pattern: '形态模式', category: '疾病大类', finding: '异常发现' }

export function DiagnosticResolution({ value, exploration, renderEvidence }) {
  if (!value) return <Alert type="info" title="历史结果未记录诊断目标复核" description="运行完成状态不能说明是否已达到具体诊断层级。" />
  const candidates = exploration?.candidates || []
  const decisionFor = candidate => value.candidate_dispositions?.find(item => item.candidate_id === candidate.candidate_id)
  const main = candidates.filter(candidate => {
    const decision = decisionFor(candidate)
    return decision && decision.scope !== 'other_problem' && decision.outcome !== 'rejected' && ['disease', 'syndrome'].includes(candidate.level)
  })
  const other = candidates.filter(candidate => decisionFor(candidate)?.scope === 'other_problem' && decisionFor(candidate)?.outcome !== 'rejected')
  const audit = candidates.filter(candidate => !main.includes(candidate) && !other.includes(candidate))
  const limited = value.status === 'needs_discussion'
  return <section aria-label="诊断目标复核" className="section-gap">
    <Alert type={limited ? 'warning' : 'info'} showIcon title={labels[value.status]} description={value.goal_review} />
    <Paragraph><Text strong>当前主要判断：</Text>{value.primary_diagnosis} <Tag>{levels[value.achieved_level]}</Tag></Paragraph>
    {value.remaining_gaps?.map((gap, index) => <Paragraph key={index}>
      <Text strong>{gap.kind === 'inference' ? '待解决推理问题：' : '判断的不确定性：'}</Text>{gap.question}；{gap.why_unresolved}{gap.scope === 'other_problem' && <Tag>其他问题</Tag>}{gap.issue_ids?.length > 0 && <Text type="secondary">；讨论议题：{gap.issue_ids.join('、')}</Text>}
    </Paragraph>)}
    {exploration && <details><summary>查看候选形成与取舍</summary>
      <Paragraph>{exploration.problem_representation}</Paragraph>
      <Paragraph><Text strong>组合表型：</Text>{exploration.combined_phenotype_review}</Paragraph>
      {[
        ['ILD 主诊断及必要鉴别', main],
        ['急性叠加与并存问题', other],
        ['模式、大类、发现及未保留候选', audit],
      ].map(([label, items]) => <section key={label} aria-label={label}>
        <Paragraph strong>{label}（{items.length} 项）</Paragraph>
        {items.map(candidate => {
        const decision = decisionFor(candidate)
        return <article key={candidate.candidate_id} className="synthesis-note">
          <Text strong>{candidate.diagnosis}</Text> <Tag>{levels[candidate.level]}</Tag><Tag>{outcomes[decision?.outcome] || '未记录取舍'}</Tag>{decision?.scope === 'other_problem' && <Tag>其他问题</Tag>}
          {decision?.outcome === 'rejected' && <Paragraph>未保留原因：{nonselectionLabels[decision.nonselection_reason] || '旧版未区分原因，不视为已排除'}</Paragraph>}
          {renderEvidence?.(candidate.supporting_evidence_refs)}
          {decision?.exclusion_evidence_refs?.length > 0 && <><Text strong>排除所据证据</Text>{renderEvidence?.(decision.exclusion_evidence_refs)}</>}
          <Paragraph>解释范围：{candidate.explains}</Paragraph><Paragraph>不足：{candidate.fails_to_explain}</Paragraph>
          <Paragraph>判别信息：{candidate.discriminating_information}</Paragraph><Paragraph>知识检索问题：{candidate.retrieval_question}</Paragraph>
          {decision?.issue_ids?.length > 0 && <Paragraph>讨论议题：{decision.issue_ids.join("、")}</Paragraph>}<Paragraph>{decision?.rationale}</Paragraph>
        </article>
      })}</section>)}
      {exploration.insufficient_candidate_basis && <Paragraph>{exploration.insufficient_candidate_basis}</Paragraph>}
      <Paragraph>尚未被候选解释的发现：{exploration.unrepresented_findings}</Paragraph>
    </details>}
  </section>
}
