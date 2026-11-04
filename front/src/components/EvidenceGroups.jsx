import { Tag } from 'antd'
import { CitationGroup } from './Citation'

const directions = { supports: '支持', weakens: '削弱', neutral: '中性' }
const functions = { foundational: '基础依据', discriminating: '鉴别', qualifying: '限定', background: '背景' }

export function EvidenceGroups({ evidence = {}, guidelineEvidence = [], claimStatement }) {
  const caseEvidence = (evidence.evidence_relations || []).map(item => ({
    ...item,
    claim_statement: claimStatement,
    relations: [
      item.direction !== 'neutral' && {
        relation: item.direction === 'weakens' ? 'weakening' : 'supports',
        target_claim_id: item.target_claim_id,
        rationale: `相对本条陈述的证据方向：${directions[item.direction]}`,
      },
      item.function !== 'foundational' && {
        relation: item.function, target_claim_id: item.target_claim_id,
        rationale: `证据功能：${functions[item.function]}`,
      },
    ].filter(Boolean),
  }))
  if (!caseEvidence.length && !guidelineEvidence.length) return null
  return <div className="evidence-groups">
    {caseEvidence.length > 0 && <div className="evidence-group"><Tag color="cyan">病历原文</Tag><CitationGroup refs={caseEvidence} /></div>}
    {guidelineEvidence.length > 0 && <div className="evidence-group"><Tag color="purple">指南依据</Tag><CitationGroup refs={guidelineEvidence} /></div>}
  </div>
}
