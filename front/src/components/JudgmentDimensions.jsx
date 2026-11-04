import { Space, Tag, Typography } from 'antd'
import { assessabilityLabels, confidenceLabels, directionLabels, clinicalRoleLabels, answerabilityLabels, legacyStatusLabels } from './clinicalConcepts'
export { assessabilityLabels, confidenceLabels } from './clinicalConcepts'

export function JudgmentDimensions({ item = {} }) {
  if (item.source_type === 'discussion_answer' && !item.answerability) return <Typography.Text type="secondary">旧版回答未独立记录完成情况与把握度，不从旧状态推算诊断支持程度。</Typography.Text>
  if (item.answerability) return <Space wrap className="judgment-dimensions"><Tag>回答完成情况：{answerabilityLabels[item.answerability]}</Tag><Tag>回答把握度：{confidenceLabels[item.answer_confidence] || '未记录'}</Tag></Space>
  if (![item.assessability, item.direction, item.confidence, item.clinical_role].some(Boolean)) return null
  const directions = item.status
    ? { ...directionLabels, supports: '旧版支持方向（按原始陈述理解）', against: '旧版反对方向（未区分支持不足与反证）' } : directionLabels
  return <Space wrap className="judgment-dimensions">
    {[[item.assessability, assessabilityLabels, '可评价性'], [item.direction, directions, '判断方向'], [item.confidence, confidenceLabels, '判断把握度'], [item.clinical_role, clinicalRoleLabels, '临床关系']].filter(([value]) => value != null).map(([value, labels, name]) => <Tag key={name}>{name}：{labels[value] || '未记录'}</Tag>)}
  </Space>
}

export function FacetJudgment({ item }) {
  if (item.assessability && item.direction) return <><Typography.Text>判断对象：{item.subject}</Typography.Text><JudgmentDimensions item={item} /></>
  return <><Tag>旧版层级摘要：{legacyStatusLabels[item.status] || '未记录'}</Tag><Tag>{confidenceLabels[item.confidence]}</Tag><Typography.Text type="secondary">旧版未分别记录可评价性和判断方向，摘要不等于确诊或排除。</Typography.Text></>
}
