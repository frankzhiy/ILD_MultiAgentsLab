import { AimOutlined, FileTextOutlined, NodeIndexOutlined } from '@ant-design/icons'
import { Button, Space, Tag, Tooltip } from 'antd'
import { useWorkbenchStore } from '../store'

function kindOf(value) {
  if (value?.guideline_id || value?.document_id || value?.source_file?.endsWith?.('.pdf')) return 'guideline'
  if (value?.node_id || value?.node_ids?.length) return 'node'
  return 'evidence'
}

export function Citation({ value, label }) {
  const selectEvidence = useWorkbenchStore((state) => state.selectEvidence)
  const kind = kindOf(value)
  const icon = kind === 'node' ? <NodeIndexOutlined /> : kind === 'guideline' ? <FileTextOutlined /> : <AimOutlined />
  const text = label || value?.evidence_ref || value?.source_ref || value?.graph_unit_id || value?.node_id || value?.guideline_id || '查看证据'
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
