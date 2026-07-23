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

function semanticUnit(data, graphUnitId) {
  return (data?.segments || [])
    .flatMap((segment) => (segment.units || []).map((unit) => ({ ...unit, segment })))
    .find((unit) => unit.graph_unit_id === graphUnitId)
}

function localPropositionId(value) {
  return String(value || '').split('::').at(-1)
}

export function EvidenceDrawer() {
  const { runId } = useParams()
  const navigate = useNavigate()
  const location = useLocation()
  const { evidence, evidenceOpen, closeEvidence } = useWorkbenchStore()
  const { data: guidelines = [] } = useQuery({ queryKey: ['guidelines'], queryFn: api.guidelines })
  const { data: semantic } = useQuery({
    queryKey: ['semantic', runId],
    queryFn: () => api.semantic(runId),
    enabled: Boolean(evidenceOpen && runId && evidence?.graph_unit_id),
  })
  const guideline = evidence?.kind === 'guideline'
    ? guidelines.find((item) => item.filename === evidence.source_file || item.filename.includes(evidence.guideline_id || evidence.document_id || ''))
    : null
  const unit = semanticUnit(semantic, evidence?.graph_unit_id)
  const propositionIds = evidence?.proposition_ids?.length
    ? evidence.proposition_ids
    : (evidence?.propositions || []).map((item) => item.proposition_id)
  const propositions = evidence?.propositions?.length
    ? evidence.propositions
    : (unit?.clinical_propositions?.propositions || []).filter((item) => (
      propositionIds.map(localPropositionId).includes(item.proposition_id)
    ))
  const evidenceIds = evidence?.evidence_ids || []
  const fragments = evidence?.evidence_fragments?.length
    ? evidence.evidence_fragments
    : (unit?.clinical_propositions?.evidence_blocks || []).filter((item) => evidenceIds.includes(item.evidence_id))
  const nodeIds = new Set([
    ...(evidence?.node_ids || []),
    ...propositionIds.map((item) => item.includes('::') ? item : `${evidence?.graph_unit_id}::${item}`),
  ])
  const graphNodes = evidence?.graph_nodes?.length
    ? evidence.graph_nodes
    : (unit?.local_graph?.nodes || []).filter((item) => nodeIds.has(item.node_id))
  const graphNodeIds = new Set(graphNodes.map((item) => item.node_id))
  const graphEdges = evidence?.graph_edges?.length
    ? evidence.graph_edges
    : (unit?.local_graph?.edges || []).filter((item) => (
      graphNodeIds.has(item.source_node_id) || graphNodeIds.has(item.target_node_id)
    ))

  const locate = () => {
    if (!runId || !evidence?.graph_unit_id) return
    closeEvidence()
    const nodeId = graphNodes[0]?.node_id || evidence.node_ids?.[0]
    const query = new URLSearchParams({ unit: evidence.graph_unit_id })
    if (nodeId) query.set('node', nodeId)
    navigate(`/runs/${encodeURIComponent(runId)}/semantic?${query}`)
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
          {evidence.claim_statement && (
            <div>
              <Text type="secondary">这条证据支持或限定的判断</Text>
              <Paragraph>{evidence.claim_statement}</Paragraph>
              {evidence.interpretation && <Paragraph type="secondary">{evidence.interpretation}</Paragraph>}
            </div>
          )}
          <div>
            <Text type="secondary">原文定位</Text>
            <Paragraph className="evidence-quote">{evidence.quote || fragments.map((item) => item.text).filter(Boolean).join('\n') || evidence.text || '该引用未携带原文摘录'}</Paragraph>
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
          {unit?.text && (
            <div>
              <Text type="secondary">完整 Graph Unit 上下文</Text>
              <Paragraph>{unit.text}</Paragraph>
            </div>
          )}
          {fragments.length > 0 && (
            <div>
              <Title level={5}>原文证据块</Title>
              {fragments.map((item) => (
                <div key={item.evidence_id}>
                  <Text code>{item.evidence_id}</Text>
                  <Paragraph className="evidence-quote">{item.text}</Paragraph>
                </div>
              ))}
            </div>
          )}
          {propositions.length > 0 && (
            <div>
              <Title level={5}>临床命题及语义状态</Title>
              {propositions.map((item) => (
                <Descriptions size="small" column={1} bordered key={item.proposition_id}>
                  <Descriptions.Item label="命题">{item.proposition_id?.includes('::') ? item.proposition_id : `${evidence.graph_unit_id}::${item.proposition_id}`}</Descriptions.Item>
                  <Descriptions.Item label="医学含义">{item.concept_text || item.label}</Descriptions.Item>
                  <Descriptions.Item label="状态 / 确定性">{values([item.status, item.certainty].filter(Boolean))}</Descriptions.Item>
                  {item.modifiers?.length > 0 && <Descriptions.Item label="修饰信息">{item.modifiers.map((modifier) => modifier.text || modifier.value || JSON.stringify(modifier)).join('；')}</Descriptions.Item>}
                </Descriptions>
              ))}
            </div>
          )}
          {graphNodes.length > 0 && (
            <div>
              <Title level={5}>关联图节点与关系</Title>
              {graphNodes.map((item) => <Paragraph key={item.node_id}><Text code>{item.node_id}</Text> · {item.label} · {values([item.status, item.certainty].filter(Boolean))}</Paragraph>)}
              {graphEdges.map((item, index) => <Paragraph type="secondary" key={item.edge_id || index}>{item.source_node_id} — {item.edge_type} → {item.target_node_id}</Paragraph>)}
            </div>
          )}
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
