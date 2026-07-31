import { AimOutlined, ApartmentOutlined, FileTextOutlined, NodeIndexOutlined, TeamOutlined } from '@ant-design/icons'
import { Button, Popover, Space, Tag, Tooltip, Typography } from 'antd'
import { useWorkbenchStore } from '../store'

const { Text } = Typography

const SPECIALTIES = {
  pulmonology: '呼吸科',
  thoracic_radiology: '胸部影像科',
  rheumatology: '风湿免疫科',
  pathology: '病理科',
}

const SOURCE_TYPES = {
  specialty_assessment: '专科结论',
  native_conclusion: '专科结论',
  interspecialty_question: '专科问题',
  native_question: '专科问题',
  assessment_evidence_need: '证据缺口',
  evidence_gap: '证据缺口',
  discussion_answer: '专科回答',
  working_diagnosis: '工作诊断',
  morphologic_pattern: '形态模式结论',
  imaging_interpretation: '影像结论',
  etiologic_attribution: '病因判断',
  rheumatic_disease: '风湿病判断',
  ild_attribution: 'ILD归因判断',
  progression: '进展判断',
  severity_or_risk: '严重度判断',
  assessability: '可评价性判断',
}

const RELATIONS = {
  supports: '支持',
  supporting: '支持',
  contradicts: '反证',
  weakening: '反证',
  discriminates: '鉴别',
  discriminating: '鉴别',
  qualifies: '限定',
  qualifying: '限定',
  background: '背景',
}

function sourceLabel(value) {
  return SOURCE_TYPES[value?.source_subtype]
    || SOURCE_TYPES[value?.source_type]
    || '专科来源'
}

export function citationCategory(value) {
  if (value?.guideline_id || value?.document_id || value?.source_file?.endsWith?.('.pdf')) return ['guideline', '指南依据']
  if (value?.source_ref || value?.source_path) {
    const label = sourceLabel(value)
    return [`specialty_${value?.source_subtype || value?.source_type || 'source'}`, label]
  }
  if (value?.graph_unit_id) return ['patient_graph', '患者证据图']
  if (value?.node_id) return ['graph_node', '图节点']
  if (value?.evidence_ids?.length || value?.evidence_fragments?.length) return ['evidence_block', '原文证据']
  if (value?.node_ids?.length) return ['graph_node', '图节点']
  if (value?.proposition_ids?.length || value?.propositions?.length) return ['proposition', '临床命题']
  if (value?.graph_unit_id) return ['graph_unit', '证据单元']
  if (value?.segment_id) return ['source_fragment', '原文片段']
  return ['other', '其他依据']
}

export function citationLabel(value, collection = [value], index = 0) {
  const [kind, label] = citationCategory(value)
  if (kind.startsWith('specialty_')) {
    return `${SPECIALTIES[value?.specialty] || value?.specialty || '专科'}｜${label}`
  }
  if (kind === 'patient_graph') {
    const relations = [...new Set((value?.relations || []).map((item) => (
      RELATIONS[item.relation || item] || item.relation || item
    )))]
    return relations.length ? `${label}｜${relations.join('/')}` : label
  }
  return label
}

function normalize(value) {
  return { ...value, kind: citationCategory(value)[0] }
}

function mergedQuote(quotes) {
  const unique = [...new Set(quotes.filter(Boolean))]
  return unique
    .filter((quote) => !unique.some((other) => quote !== other && other.includes(quote)))
    .join('\n')
}

export function Citation({ value, collection, index = 0 }) {
  const selectEvidence = useWorkbenchStore((state) => state.selectEvidence)
  const items = (collection || [value]).map(normalize)
  const kind = citationCategory(value)[0]
  const icon = kind === 'guideline' || kind === 'source_fragment'
    ? <FileTextOutlined />
    : kind.startsWith('specialty_')
      ? <TeamOutlined />
      : kind === 'graph_node' || kind === 'proposition'
        ? <NodeIndexOutlined />
        : kind === 'graph_unit'
          ? <ApartmentOutlined />
          : <AimOutlined />
  return (
    <Tooltip title={value?.quote || value?.text || '打开证据检查器'}>
      <Button size="small" className="citation-button" icon={icon} onClick={() => selectEvidence(items[index], items, index)}>
        {citationLabel(value, items, index)}
      </Button>
    </Tooltip>
  )
}

export function CitationGroup({ sourceCitations = [], caseEvidence = [], refs = [], collection, startAt = 0 }) {
  if (!sourceCitations.length && !caseEvidence.length && !refs.length) return null
  const rawItems = [...sourceCitations, ...caseEvidence, ...refs.filter((item) => typeof item !== 'string')]
  const items = aggregateCitations(rawItems)
  const evidenceList = (collection ? aggregateCitations(collection) : items)
    .filter((item) => typeof item !== 'string')
  let evidenceIndex = startAt
  return (
    <Space size={[6, 6]} wrap className="citation-group">
      {aggregateCitations([...sourceCitations, ...caseEvidence, ...refs]).map((item, index) => {
        if (typeof item === 'string') return <Tag key={`text-${index}`}>{item}</Tag>
        const currentIndex = evidenceIndex
        evidenceIndex += 1
        return <Citation key={item.evidence_ref || item.source_ref || item.guideline_id || item.node_id || index} value={item} collection={evidenceList} index={currentIndex} />
      })}
    </Space>
  )
}

export function CitationSummary({ sourceCitations = [], caseEvidence = [], refs = [] }) {
  const allItems = aggregateCitations([...sourceCitations, ...caseEvidence, ...refs])
  const total = allItems.length
  if (!total) return <Text type="secondary">暂无</Text>
  const groups = [...allItems.reduce((result, item) => {
    const [kind, label] = typeof item === 'string' ? ['other', '其他依据'] : citationCategory(item)
    if (!result.has(kind)) result.set(kind, { kind, label, items: [] })
    result.get(kind).items.push(item)
    return result
  }, new Map()).values()]
  const items = groups.flatMap((group) => group.items.filter((item) => typeof item !== 'string'))
  let startAt = 0
  return (
    <Popover
      placement="bottomRight"
      trigger="click"
      title={`可追溯依据（${total} 项来源）`}
      content={<div className="citation-popover-content">
        {groups.map((group) => {
          const groupStart = startAt
          startAt += group.items.filter((item) => typeof item !== 'string').length
          return <div key={group.kind}><Text strong>{group.label}</Text><CitationGroup refs={group.items} collection={items} startAt={groupStart} /></div>
        })}
      </div>}
    >
      <Button size="small" className="citation-summary-button">{total} 项来源</Button>
    </Popover>
  )
}

export function aggregateCitations(items = []) {
  const result = []
  const positions = new Map()
  items.forEach((item) => {
    if (typeof item === 'string') {
      result.push(item)
      return
    }
    const [kind] = citationCategory(item)
    const key = kind === 'patient_graph'
      ? `patient_graph:${item.graph_unit_id}`
      : item.source_ref
        ? `source:${item.source_ref}`
        : item.guideline_id || item.document_id
          ? `guideline:${item.guideline_id || item.document_id}`
          : null
    if (!key || !positions.has(key)) {
      if (key) positions.set(key, result.length)
      result.push({
        ...item,
        relations: item.relations || [],
        quotes: item.quotes || (item.quote ? [item.quote] : []),
      })
      return
    }
    const current = result[positions.get(key)]
    current.evidence_ids = [...new Set([...(current.evidence_ids || []), ...(item.evidence_ids || [])])]
    current.proposition_ids = [...new Set([...(current.proposition_ids || []), ...(item.proposition_ids || [])])]
    current.node_ids = [...new Set([...(current.node_ids || []), ...(item.node_ids || [])])]
    current.relations = [...current.relations, ...(item.relations || [])]
    current.quotes = [...new Set([...current.quotes, ...(item.quotes || []), ...(item.quote ? [item.quote] : [])])]
    current.quote = mergedQuote(current.quotes)
  })
  return result
}
