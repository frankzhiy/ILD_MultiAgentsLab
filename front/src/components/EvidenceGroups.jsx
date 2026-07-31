import { Tag } from 'antd'
import { CitationGroup } from './Citation'

const GROUPS = [
  ['supporting', '支持证据', 'green'],
  ['weakening', '反证', 'red'],
  ['discriminating', '鉴别证据', 'blue'],
  ['qualifying', '限定依据', 'gold'],
  ['background', '背景证据', 'default'],
]

const DIRECTION_RELATIONS = {
  supports: 'supports',
  weakens: 'weakening',
}

const FUNCTION_RELATIONS = {
  discriminating: 'discriminating',
  qualifying: 'qualifying',
  background: 'background',
}

function relationDimensions(item) {
  return [
    item.direction && DIRECTION_RELATIONS[item.direction] && {
      relation: DIRECTION_RELATIONS[item.direction],
      rationale: `证据方向：${item.direction}`,
    },
    item.function && FUNCTION_RELATIONS[item.function] && {
      relation: FUNCTION_RELATIONS[item.function],
      rationale: `证据功能：${item.function}`,
    },
  ].filter(Boolean)
}

export function EvidenceGroups({ evidence = {}, guidelineEvidence = [] }) {
  const caseEvidence = evidence.links?.length
    ? evidence.links.map((item) => ({
      ...item,
      relations: [{
        relation: item.relation,
        target_claim_id: item.target_claim_id,
        rationale: item.rationale,
        comparison_target: item.comparison_target,
      }],
    }))
    : evidence.evidence_relations?.length
      ? evidence.evidence_relations.map((item) => ({
        ...item,
        relations: relationDimensions(item),
      }))
      : GROUPS.flatMap(([key, label]) => (evidence[key] || []).map((item) => ({
        ...item,
        relations: [{ relation: key, rationale: `旧版${label}关系，未经过原子结论级重新校验。`, legacy: true }],
      })))
  if (!caseEvidence.length && !guidelineEvidence?.length) return null
  return (
    <div className="evidence-groups">
      {caseEvidence.length > 0 && (
        <div className="evidence-group">
          <Tag color="cyan">患者证据图</Tag>
          <CitationGroup refs={caseEvidence} />
        </div>
      )}
      {guidelineEvidence?.length > 0 && (
        <div className="evidence-group">
          <Tag color="purple">指南依据</Tag>
          <CitationGroup refs={guidelineEvidence} />
        </div>
      )}
    </div>
  )
}
