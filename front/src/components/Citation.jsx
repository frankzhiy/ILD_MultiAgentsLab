import { AimOutlined, FileTextOutlined, NodeIndexOutlined } from '@ant-design/icons'
import { Button, Popover, Space, Tag, Tooltip, Typography } from 'antd'
import { useWorkbenchStore } from '../store'

const { Text } = Typography

function kindOf(value) {
  if (value?.guideline_id || value?.document_id || value?.source_file?.endsWith?.('.pdf')) return 'guideline'
  if (value?.node_id || value?.node_ids?.length) return 'node'
  return 'evidence'
}

export function citationLabel(value, label) {
  if (label && !/^E\d+$/i.test(label)) return label
  const propositionId = value?.proposition_ids?.[0]
    || value?.propositions?.[0]?.proposition_id
  return propositionId
    || value?.evidence_ids?.[0]
    || value?.graph_unit_id
    || value?.node_id
    || value?.source_ref
    || value?.guideline_id
    || value?.document_id
    || '查看证据'
}

export function Citation({ value, label }) {
  const selectEvidence = useWorkbenchStore((state) => state.selectEvidence)
  const kind = kindOf(value)
  const icon = kind === 'node' ? <NodeIndexOutlined /> : kind === 'guideline' ? <FileTextOutlined /> : <AimOutlined />
  const text = citationLabel(value, label)
  return (
    <Tooltip title={value?.quote || value?.text || '打开证据检查器'}>
      <Button size="small" className="citation-button" icon={icon} onClick={() => selectEvidence({ ...value, kind })}>
        {text}
      </Button>
    </Tooltip>
  )
}

export function CitationGroup({ sourceCitations = [], caseEvidence = [], refs = [] }) {
  if (!sourceCitations.length && !caseEvidence.length && !refs.length) return null
  return (
    <Space size={[6, 6]} wrap className="citation-group">
      {sourceCitations.map((item, index) => <Citation key={`s-${index}`} value={item} />)}
      {caseEvidence.map((item, index) => <Citation key={`e-${index}`} value={item} />)}
      {refs.map((item, index) => typeof item === 'string' ? <Tag key={index}>{item}</Tag> : <Citation key={index} value={item} />)}
    </Space>
  )
}

export function CitationSummary({ sourceCitations = [], caseEvidence = [], refs = [] }) {
  const total = sourceCitations.length + caseEvidence.length + refs.length
  if (!total) return <Text type="secondary">暂无</Text>
  return (
    <Popover
      placement="bottomRight"
      trigger="click"
      title={`可追溯依据（${total}）`}
      content={<div className="citation-popover-content">
        {sourceCitations.length > 0 && <div><Text strong>专科原文</Text><CitationGroup sourceCitations={sourceCitations} /></div>}
        {caseEvidence.length > 0 && <div><Text strong>患者证据</Text><CitationGroup caseEvidence={caseEvidence} /></div>}
        {refs.length > 0 && <div><Text strong>其他依据</Text><CitationGroup refs={refs} /></div>}
      </div>}
    >
      <Button size="small" className="citation-summary-button">查看依据（{total}）</Button>
    </Popover>
  )
}
