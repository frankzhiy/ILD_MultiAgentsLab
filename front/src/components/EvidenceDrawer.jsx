import { useQuery } from '@tanstack/react-query'
import { Button, Descriptions, Drawer, Empty, Space, Tag, Typography } from 'antd'
import { LinkOutlined } from '@ant-design/icons'
import { useLocation, useNavigate, useParams } from 'react-router-dom'
import { api } from '../api'
import { useWorkbenchStore } from '../store'

const { Paragraph, Text, Title } = Typography

function values(value) {
  return Array.isArray(value) ? value.join(', ') : value || '—'
}

export function EvidenceDrawer() {
  const { runId } = useParams()
  const navigate = useNavigate()
  const location = useLocation()
  const { evidence, evidenceOpen, closeEvidence } = useWorkbenchStore()
  const { data: guidelines = [] } = useQuery({ queryKey: ['guidelines'], queryFn: api.guidelines })
  const guideline = evidence?.kind === 'guideline'
    ? guidelines.find((item) => item.filename === evidence.source_file || item.filename.includes(evidence.guideline_id || evidence.document_id || ''))
    : null

  const locate = () => {
    if (!runId || !evidence?.graph_unit_id) return
    closeEvidence()
    navigate(`/runs/${encodeURIComponent(runId)}/semantic?unit=${encodeURIComponent(evidence.graph_unit_id)}`)
  }

  return (
    <Drawer
      size={520}
      title="证据检查器"
      open={evidenceOpen}
      onClose={closeEvidence}
      destroyOnHidden
      extra={evidence?.kind && <Tag>{evidence.kind}</Tag>}
    >
      {!evidence ? <Empty /> : (
        <Space orientation="vertical" size="large" style={{ width: '100%' }}>
          <div>
            <Text type="secondary">原文定位</Text>
            <Paragraph className="evidence-quote">{evidence.quote || evidence.text || '该引用未携带原文摘录'}</Paragraph>
          </div>
          <Descriptions size="small" column={1} bordered>
            {evidence.specialty && <Descriptions.Item label="来源专科">{evidence.specialty}</Descriptions.Item>}
            {evidence.source_path && <Descriptions.Item label="专科输出路径"><Text code>{evidence.source_path}</Text></Descriptions.Item>}
            {evidence.segment_id && <Descriptions.Item label="片段">{evidence.segment_id}</Descriptions.Item>}
            {evidence.graph_unit_id && <Descriptions.Item label="证据单元">{evidence.graph_unit_id}</Descriptions.Item>}
            {evidence.evidence_ids && <Descriptions.Item label="Evidence IDs">{values(evidence.evidence_ids)}</Descriptions.Item>}
            {evidence.proposition_ids && <Descriptions.Item label="命题">{values(evidence.proposition_ids)}</Descriptions.Item>}
            {evidence.node_ids && <Descriptions.Item label="图节点">{values(evidence.node_ids)}</Descriptions.Item>}
            {(evidence.page || evidence.page_number) && <Descriptions.Item label="指南页码">{evidence.page || evidence.page_number}</Descriptions.Item>}
          </Descriptions>
          <Space wrap>
            {evidence.graph_unit_id && runId && <Button type="primary" onClick={locate}>定位到语义图</Button>}
            {guideline && <Button icon={<LinkOutlined />} href={api.guidelineUrl(guideline.filename, evidence.page || evidence.page_number)} target="_blank">打开指南原文</Button>}
          </Space>
          {guideline && (
            <div className="pdf-preview">
              <Title level={5}>{guideline.filename}</Title>
              <iframe title={guideline.filename} src={api.guidelineUrl(guideline.filename, evidence.page || evidence.page_number)} />
            </div>
          )}
          {!runId && location.pathname === '/runs' && <Text type="secondary">进入一次运行后可定位到语义图节点。</Text>}
        </Space>
      )}
    </Drawer>
  )
}
