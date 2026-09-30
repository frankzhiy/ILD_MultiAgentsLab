import { Space, Tag } from 'antd'

export const assessabilityLabels = { assessable: '可评价', partially_assessable: '部分可评价', not_assessable: '不可评价' }
export const confidenceLabels = { high: '高把握度', moderate: '中等把握度', low: '低把握度', unknown: '尚不能评定把握度' }
const directionLabels = { supports: '支持', against: '反对', indeterminate: '方向未定' }
const roleLabels = { primary: '主要解释', competing_explanation: '竞争解释', acute_contributor: '急性叠加因素', comorbidity: '并存疾病', unexplained_finding: '尚未解释的发现', boundary: '判断边界' }

export function JudgmentDimensions({ item = {} }) {
  return <Space wrap className="judgment-dimensions">
    {[[item.assessability, assessabilityLabels, '可评价性'], [item.direction, directionLabels, '方向'], [item.confidence, confidenceLabels, '信度'], [item.clinical_role, roleLabels, '临床角色']].map(([value, labels, name]) => <Tag key={name}>{name}：{labels[value] || '未记录'}</Tag>)}
  </Space>
}
