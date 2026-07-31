import { useQuery } from '@tanstack/react-query'
import { useEffect, useRef } from 'react'
import { Button, Collapse, Descriptions, Drawer, Empty, Segmented, Space, Tag, Typography } from 'antd'
import { FileSearchOutlined, LeftOutlined, LinkOutlined, RightOutlined } from '@ant-design/icons'
import { useLocation, useNavigate, useParams } from 'react-router-dom'
import { api } from '../api'
import { useWorkbenchStore } from '../store'
import { citationCategory, citationLabel } from './Citation'

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

function normalizedText(value) {
  return String(value || '').replace(/\s+/g, ' ').trim()
}

function sameText(left, right) {
  return Boolean(normalizedText(left) && normalizedText(left) === normalizedText(right))
}

function uniqueFragments(items) {
  const seen = new Set()
  return (items || []).filter((item) => {
    const key = `${item.evidence_id || ''}::${normalizedText(item.text)}`
    if (!normalizedText(item.text) || seen.has(key)) return false
    seen.add(key)
    return true
  })
}

function excerptLabel(kind, categoryLabel) {
  if (kind.startsWith('specialty_')) return '专科原文'
  if (kind === 'evidence_block') return '引用原文（Evidence ID 命中）'
  if (kind === 'graph_node') return '图节点携带原文'
  if (kind === 'proposition') return '临床命题携带原文'
  if (kind === 'graph_unit') return '证据单元原文（Graph Unit）'
  if (kind === 'source_fragment') return '原始片段原文（Segment）'
  if (kind === 'patient_graph') return '被引用的患者原文'
  if (kind === 'guideline') return '指南原文摘录'
  return `${categoryLabel}摘录`
}

const RELATION_LABELS = {
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

function HighlightedText({ text, quote }) {
  if (!text || !quote) return text
  const start = text.indexOf(quote)
  if (start < 0) return text
  return <>{text.slice(0, start)}<mark>{quote}</mark>{text.slice(start + quote.length)}</>
}

function EvidenceGraph({ nodes, edges, relatedNodeIds }) {
  const container = useRef(null)
  const graphRef = useRef(null)
  const relatedKey = relatedNodeIds.join('|')
  useEffect(() => {
    if (!container.current || !nodes.length) return undefined
    let graph
    let cancelled = false
    const nodeIdSet = new Set(nodes.map((item) => item.node_id))
    const root = nodes.find((item) => item.node_type === 'graph_unit' || item.node_id?.endsWith('::graph_unit'))
    const related = new Set(relatedKey.split('|').filter(Boolean))
    import('cytoscape').then(({ default: cytoscape }) => {
      if (cancelled || !container.current) return
      graph = cytoscape({
        container: container.current,
        elements: [
          ...nodes.map((item) => ({
            data: { id: item.node_id, label: item.label || item.node_id?.split('::').at(-1), type: item.node_type },
            classes: related.has(item.node_id) ? 'current' : '',
          })),
          ...edges.filter((item) => nodeIdSet.has(item.source_node_id) && nodeIdSet.has(item.target_node_id)).map((item, index) => ({
            data: { id: item.edge_id || `edge-${index}`, source: item.source_node_id, target: item.target_node_id, label: item.edge_type },
          })),
        ],
        layout: { name: 'breadthfirst', directed: true, roots: root ? [root.node_id] : undefined, padding: 24, spacingFactor: 1.1 },
        style: [
          { selector: 'node', style: { label: 'data(label)', width: 142, height: 52, shape: 'round-rectangle', 'background-color': '#eff6ff', 'border-color': '#93c5fd', 'border-width': 1.5, color: '#1e3a5f', 'font-size': 9, 'font-weight': 600, 'text-wrap': 'wrap', 'text-max-width': 126, 'text-valign': 'center', 'text-halign': 'center' } },
          { selector: 'node[type = "graph_unit"]', style: { 'background-color': '#172554', 'border-color': '#172554', color: '#fff' } },
          { selector: 'node[type = "event"]', style: { 'background-color': '#fff7ed', 'border-color': '#fb923c', color: '#9a3412' } },
          { selector: 'node[type = "modifier"]', style: { 'background-color': '#faf5ff', 'border-color': '#c084fc', color: '#6b21a8', 'border-style': 'dashed' } },
          { selector: 'node[type = "source_actor"]', style: { 'background-color': '#ecfdf5', 'border-color': '#6ee7b7', color: '#065f46' } },
          { selector: 'node.current', style: { 'border-color': '#2563eb', 'border-width': 4 } },
          { selector: 'edge', style: { label: 'data(label)', width: 1.4, 'line-color': '#cbd5e1', 'target-arrow-color': '#94a3b8', 'target-arrow-shape': 'triangle', 'curve-style': 'round-taxi', 'taxi-direction': 'downward', color: '#64748b', 'font-size': 7, 'text-background-color': '#fff', 'text-background-opacity': 1, 'text-background-padding': 2 } },
        ],
        minZoom: .25,
        maxZoom: 2.5,
      })
      graph.fit(undefined, 24)
      if (graph.zoom() < .55) {
        graph.zoom(.55)
        graph.center(root ? graph.getElementById(root.node_id) : undefined)
      }
      graphRef.current = graph
    })
    return () => {
      cancelled = true
      graph?.destroy()
      graphRef.current = null
    }
  }, [edges, nodes, relatedKey])
  if (!nodes.length) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="该引用没有可用图结构" />
  return (
    <div className="evidence-graph">
      <div className="evidence-graph-meta">
        <Text type="secondary">{nodes.length} 个节点 · {edges.length} 条关系</Text>
        <Space size={4}>
          <Tag color="blue">蓝框为当前引用节点</Tag>
          <Button size="small" onClick={() => graphRef.current?.fit(undefined, 24)}>查看全图</Button>
          <Button size="small" aria-label="缩小图结构" onClick={() => graphRef.current?.zoom(graphRef.current.zoom() / 1.25)}>−</Button>
          <Button size="small" aria-label="放大图结构" onClick={() => graphRef.current?.zoom(graphRef.current.zoom() * 1.25)}>+</Button>
        </Space>
      </div>
      <div ref={container} className="evidence-graph-canvas" role="img" aria-label={`完整证据图，${nodes.length} 个节点，${edges.length} 条关系`} />
    </div>
  )
}

export function EvidenceDrawer() {
  const { runId } = useParams()
  const navigate = useNavigate()
  const location = useLocation()
  const sourceRef = useRef(null)
  const { evidence, evidenceList, evidenceIndex, evidenceOpen, setEvidenceIndex, closeEvidence } = useWorkbenchStore()
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
  const fragments = uniqueFragments(evidence?.evidence_fragments?.length
    ? evidence.evidence_fragments
    : (unit?.clinical_propositions?.evidence_blocks || []).filter((item) => evidenceIds.includes(item.evidence_id)))
  const nodeIds = new Set([
    ...(evidence?.node_ids || []),
    ...propositionIds.map((item) => item.includes('::') ? item : `${evidence?.graph_unit_id}::${item}`),
  ])
  const graphNodes = evidence?.graph_nodes?.length
    ? evidence.graph_nodes
    : (unit?.local_graph?.nodes || []).filter((item) => nodeIds.has(item.node_id))
  const graphNodeIds = new Set(graphNodes.map((item) => item.node_id))
  const fullGraphNodes = unit?.local_graph?.nodes?.length ? unit.local_graph.nodes : evidence?.graph_nodes || []
  const fullGraphEdges = unit?.local_graph?.edges?.length ? unit.local_graph.edges : evidence?.graph_edges || []
  const quote = evidence?.quote || fragments.map((item) => item.text).filter(Boolean).join('\n\n') || evidence?.text || ''
  const graphUnitText = unit?.text || ''
  const segmentText = unit?.segment?.text || ''
  const showGraphUnitContext = Boolean(graphUnitText && !sameText(graphUnitText, quote))
  const showSegmentContext = Boolean(segmentText
    && !sameText(segmentText, graphUnitText)
    && !sameText(segmentText, quote))
  const quoteCoversGraphUnit = Boolean(graphUnitText && sameText(graphUnitText, quote))
  const currentLabel = evidence ? citationLabel(evidence, evidenceList, evidenceIndex) : ''
  const [categoryKind, categoryLabel] = evidence ? citationCategory(evidence) : ['', '']

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
      title={<div><Text strong>证据检查器</Text>{evidenceList.length > 1 && <Text type="secondary" className="evidence-drawer-position">{currentLabel} · {evidenceIndex + 1} / {evidenceList.length}</Text>}</div>}
      open={evidenceOpen}
      onClose={closeEvidence}
      destroyOnHidden
      extra={categoryLabel && <Tag>{categoryLabel}</Tag>}
    >
      {!evidence ? <Empty /> : (
        <Space orientation="vertical" size="large" style={{ width: '100%' }}>
          {evidenceList.length > 1 && (
            <div className="evidence-switcher-row">
              <Button aria-label="上一条证据" icon={<LeftOutlined />} disabled={evidenceIndex === 0} onClick={() => setEvidenceIndex(evidenceIndex - 1)} />
              <Segmented
                className="evidence-switcher"
                value={evidenceIndex}
                onChange={setEvidenceIndex}
                options={evidenceList.map((item, index) => ({ label: citationLabel(item, evidenceList, index), value: index }))}
              />
              <Button aria-label="下一条证据" icon={<RightOutlined />} disabled={evidenceIndex === evidenceList.length - 1} onClick={() => setEvidenceIndex(evidenceIndex + 1)} />
            </div>
          )}
          {evidence.claim_statement && (
            <div>
              <Text type="secondary">{evidence.kind?.startsWith('specialty_') ? '该专科来源对应的判断' : '该引用支持或限定的判断'}</Text>
              <Paragraph>{evidence.claim_statement}</Paragraph>
              {evidence.interpretation && <Paragraph type="secondary">{evidence.interpretation}</Paragraph>}
            </div>
          )}
          {evidence.relations?.length > 0 && (
            <div>
              <Text type="secondary">该证据图与当前原子判断的关系</Text>
              {evidence.relations.map((relation, index) => (
                <Paragraph key={`${relation.target_claim_id || 'claim'}-${relation.relation || relation}-${index}`}>
                  <Tag color={relation.legacy ? 'default' : 'blue'}>
                    {RELATION_LABELS[relation.relation || relation] || relation.relation || relation}
                  </Tag>
                  {relation.target_claim_id && <Text code>{relation.target_claim_id}</Text>}
                  {relation.rationale && <> · {relation.rationale}</>}
                  {relation.comparison_target && <Text type="secondary">；鉴别对象：{relation.comparison_target}</Text>}
                </Paragraph>
              ))}
            </div>
          )}
          <div ref={!showGraphUnitContext && !showSegmentContext ? sourceRef : undefined}>
            <Space size={6} wrap>
              <Text type="secondary">{excerptLabel(categoryKind, categoryLabel)}</Text>
              {quoteCoversGraphUnit && <Tag>覆盖整个 Graph Unit，不重复展示</Tag>}
            </Space>
            <Paragraph className="evidence-quote">{quote || evidence.text || '该引用未携带原文摘录'}</Paragraph>
          </div>
          {showGraphUnitContext && (
            <div ref={!showSegmentContext ? sourceRef : undefined}>
              <Text type="secondary">证据单元上下文（Graph Unit）</Text>
              <Paragraph className="evidence-source-context"><HighlightedText text={graphUnitText} quote={quote} /></Paragraph>
            </div>
          )}
          {showSegmentContext && (
            <div ref={sourceRef}>
              <Text type="secondary">原始片段上下文（Segment）</Text>
              <Paragraph className="evidence-source-context"><HighlightedText text={segmentText} quote={graphUnitText || quote} /></Paragraph>
            </div>
          )}
          <Space wrap>
            {(quote || graphUnitText || segmentText) && <Button icon={<FileSearchOutlined />} onClick={() => sourceRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' })}>在原文中定位</Button>}
            {evidence.graph_unit_id && runId && <Button type="primary" onClick={locate}>定位到语义图</Button>}
            {guideline && <Button icon={<LinkOutlined />} href={api.guidelineUrl(guideline.filename, evidence.page || evidence.page_number)} target="_blank">打开指南原文</Button>}
          </Space>
          {(fullGraphNodes.length > 0 || evidence.graph_unit_id) && (
            <div>
              <Title level={5}>完整图结构</Title>
              <EvidenceGraph nodes={fullGraphNodes} edges={fullGraphEdges} relatedNodeIds={[...graphNodeIds]} />
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
          <Collapse items={[
            {
              key: 'technical',
              label: '引用类别与技术定位',
              children: <Descriptions size="small" column={1} bordered>
                <Descriptions.Item label="当前引用类别">{categoryLabel}</Descriptions.Item>
                {evidence.specialty && <Descriptions.Item label="来源专科">{evidence.specialty}</Descriptions.Item>}
                {evidence.source_path && <Descriptions.Item label="专科输出路径"><Text code>{evidence.source_path}</Text></Descriptions.Item>}
                {(evidence.segment_id || unit?.segment?.segment_id) && <Descriptions.Item label="原始片段 / Segment">{evidence.segment_id || unit.segment.segment_id}</Descriptions.Item>}
                {evidence.graph_unit_id && <Descriptions.Item label="证据单元 / Graph Unit">{evidence.graph_unit_id}</Descriptions.Item>}
                {evidence.evidence_ids && <Descriptions.Item label="原文证据块 / Evidence IDs">{values(evidence.evidence_ids)}</Descriptions.Item>}
                {propositionIds.length > 0 && <Descriptions.Item label="临床命题 / Proposition IDs">{values(propositionIds)}</Descriptions.Item>}
                {evidence.node_ids && <Descriptions.Item label="图节点 / Node IDs">{values(evidence.node_ids)}</Descriptions.Item>}
                {(evidence.page || evidence.page_number) && <Descriptions.Item label="指南页码">{evidence.page || evidence.page_number}</Descriptions.Item>}
              </Descriptions>,
            },
            fragments.length > 0 && {
              key: 'evidence-map',
              label: `Evidence ID → 原文证据块（${fragments.length}）`,
              children: fragments.map((item) => (
                <div key={`${item.evidence_id}-${normalizedText(item.text)}`}>
                  <Text code>{item.evidence_id}</Text>
                  <Paragraph className="evidence-quote">{item.text}</Paragraph>
                </div>
              )),
            },
          ].filter(Boolean)} />
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
