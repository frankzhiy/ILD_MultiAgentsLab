import { Tag } from 'antd'
import { CitationGroup } from './Citation'

const GROUPS = [
  ['supporting', '支持证据', 'green'],
  ['weakening', '削弱证据', 'red'],
  ['discriminating', '鉴别证据', 'blue'],
  ['background', '背景证据', 'default'],
]

export function EvidenceGroups({ evidence = {}, guidelineEvidence = [] }) {
  const groups = GROUPS.filter(([key]) => evidence[key]?.length)
  if (!groups.length && !guidelineEvidence?.length) return null
  return (
    <div className="evidence-groups">
      {groups.map(([key, label, color]) => (
        <div className="evidence-group" key={key}>
          <Tag color={color}>{label}</Tag>
          <CitationGroup refs={evidence[key]} />
        </div>
      ))}
      {guidelineEvidence?.length > 0 && (
        <div className="evidence-group">
          <Tag color="purple">指南依据</Tag>
          <CitationGroup refs={guidelineEvidence} />
        </div>
      )}
    </div>
  )
}
