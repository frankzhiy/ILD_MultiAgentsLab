import { useEffect, useMemo, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import cytoscape from 'cytoscape'
import { Badge, Button, Card, Empty, Input, Space, Spin, Tag, Typography } from 'antd'
import { ApartmentOutlined, MedicineBoxOutlined, ReloadOutlined, SearchOutlined } from '@ant-design/icons'
import { useSearchParams } from 'react-router-dom'
import { api } from '../../api'
import { Citation } from '../../components/Citation'
import { QueryError } from '../../components/QueryState'

const { Paragraph, Text, Title } = Typography

const SOURCE_LABELS = {
  demographics: '人口学信息 · demographics', chief_complaint: '主诉/入院主因 · chief complaint',
  present_illness: '现病史/病程 · present illness', past_medical_history: '既往史/合并症 · past medical history',
  exposure_history: '暴露史 · exposure history', family_history: '家族史 · family history',
  medication_history: '用药史 · medication history', general_condition: '一般状态 · general condition',
  physical_exam: '体格检查 · physical exam', imaging_findings: '影像发现 · imaging findings',
  laboratory_findings: '实验室发现 · laboratory findings', ctd_related_findings: 'CTD 相关 · CTD related',
  bronchoscopy_findings: '支气管镜检查 · bronchoscopy', pulmonary_function_findings: '肺功能发现 · pulmonary function',
  pathology_findings: '病理发现 · pathology findings', treatment: '治疗 · treatment',
  clinician_assessment: '医生评估 · clinical assessment', other: '其他 · other',
}
const SOURCE_COLORS = { demographics: '#e5e7eb', chief_complaint: '#a7f3d0', present_illness: '#bfdbfe', past_medical_history: '#bbf7d0', exposure_history: '#ddd6fe', family_history: '#fbcfe8', medication_history: '#fde68a', general_condition: '#d1fae5', physical_exam: '#fecaca', imaging_findings: '#c7d2fe', laboratory_findings: '#d9f99d', ctd_related_findings: '#fecdd3', bronchoscopy_findings: '#ccfbf1', pulmonary_function_findings: '#bae6fd', pathology_findings: '#f5d0fe', treatment: '#fed7aa', clinician_assessment: '#fdba74', other: '#e5e7eb' }
const SPECIALTY_LABELS = { pulmonology: '呼吸科 · pulmonology', thoracic_radiology: '胸部影像 · thoracic radiology', pathology: '病理科 · pathology', rheumatology: '风湿免疫 · rheumatology', shared_context: '共享背景 · shared context' }
const SPECIALTY_COLORS = { pulmonology: '#bae6fd', thoracic_radiology: '#c7d2fe', pathology: '#f5d0fe', rheumatology: '#fecaca', shared_context: '#e5e7eb' }
const FRAME_LABELS = { symptom_episode: '症状病程事件', encounter: '诊疗接触事件', standalone_examination: '独立检查事件', clinical_assessment: '独立临床判断事件', treatment_course: '独立治疗过程', background_context: '背景上下文' }
const SEGMENT_COLORS = ['#bfdbfe', '#bbf7d0', '#fed7aa', '#c7d2fe', '#fecaca', '#d9f99d', '#f5d0fe', '#bae6fd', '#ddd6fe', '#a7f3d0']
const NODE_LABELS = { graph_unit: 'GRAPH UNIT', event: 'EVENT', proposition: 'PROPOSITION', modifier: 'MODIFIER', source_actor: 'SOURCE' }
const EDGE_LABELS = { organizes_as: '组织为', contains_proposition: '包含陈述', has_modifier: '修饰', attributed_to: '来源' }

function graphStyle() {
  return [
    { selector: 'node', style: { label: 'data(displayLabel)', 'text-wrap': 'wrap', 'text-max-width': 150, 'font-size': 11, 'font-weight': 600, color: '#1e3a5f', 'background-color': '#eff6ff', 'border-width': 1.5, 'border-color': '#93c5fd', shape: 'round-rectangle', width: 172, height: 58, 'text-valign': 'center', 'text-halign': 'center', 'overlay-opacity': 0, 'shadow-blur': 10, 'shadow-color': '#64748b', 'shadow-opacity': 0.15, 'shadow-offset-y': 3 } },
    { selector: 'node[type = "graph_unit"]', style: { 'background-color': '#172554', 'border-color': '#172554', color: '#fff', width: 190, height: 64, 'font-size': 12 } },
    { selector: 'node[type = "event"]', style: { 'background-color': '#fff7ed', 'border-color': '#fb923c', color: '#9a3412', width: 190, height: 64, 'font-size': 12, 'border-width': 2.5 } },
    { selector: 'node[type = "modifier"]', style: { 'background-color': '#faf5ff', 'border-color': '#d8b4fe', color: '#6b21a8', width: 142, height: 46, 'font-size': 10, 'border-style': 'dashed' } },
    { selector: 'node[type = "source_actor"]', style: { 'background-color': '#ecfdf5', 'border-color': '#6ee7b7', color: '#065f46', width: 142, height: 46, 'font-size': 10 } },
    { selector: 'node[status = "absent"], node[status = "not_performed"]', style: { 'border-style': 'dashed', 'border-width': 2.5, 'background-color': '#f8fafc', 'border-color': '#94a3b8', color: '#64748b' } },
    { selector: 'node[status = "possible"], node[status = "planned"]', style: { 'border-style': 'double', 'border-width': 3 } },
    { selector: 'edge', style: { 'curve-style': 'round-taxi', 'taxi-direction': 'downward', 'taxi-radius': 10, 'target-arrow-shape': 'triangle', 'arrow-scale': 0.7, 'line-color': '#cbd5e1', 'target-arrow-color': '#94a3b8', width: 1.4, label: 'data(displayLabel)', 'font-size': 8, color: '#64748b', 'text-background-color': '#f8fafc', 'text-background-opacity': 1, 'text-background-padding': 3, 'overlay-opacity': 0 } },
    { selector: 'edge[type = "organizes_as"]', style: { 'line-color': '#64748b', 'target-arrow-color': '#475569', width: 2 } },
    { selector: 'edge[type = "has_modifier"]', style: { 'line-color': '#d8b4fe', 'target-arrow-color': '#c084fc', 'line-style': 'dashed' } },
    { selector: 'edge[type = "attributed_to"]', style: { 'line-color': '#6ee7b7', 'target-arrow-color': '#34d399', 'line-style': 'dashed' } },
    { selector: '.faded', style: { opacity: 0.1, 'text-opacity': 0.1 } },
    { selector: '.focused', style: { 'border-width': 3, 'border-color': '#4f46e5', 'line-color': '#6366f1', 'target-arrow-color': '#4f46e5', 'shadow-color': '#4f46e5', 'shadow-opacity': 0.3 } },
  ]
}

function LocalGraph({ unit, node, onNode }) {
  const container = useRef(null)
  const cyRef = useRef(null)
  const graph = unit?.local_graph
  const fit = () => cyRef.current?.fit(undefined, 36)
  const reset = () => { cyRef.current?.elements().removeClass('faded focused'); cyRef.current?.layout({ name: 'breadthfirst', directed: true, roots: [graph?.root_node_id], spacingFactor: 1.15, padding: 36, animate: true }).run() }
  useEffect(() => {
    if (!container.current || graph?.build_status !== 'built') return undefined
    const elements = [
      ...(graph.nodes || []).map((item) => ({ data: { id: item.node_id, type: item.node_type, raw: item, displayLabel: `${NODE_LABELS[item.node_type] || item.node_type}\n${item.node_type === 'event' ? (FRAME_LABELS[item.label] || item.label) : item.label}` } })),
      ...(graph.edges || []).map((item) => ({ data: { id: item.edge_id, source: item.source_node_id, target: item.target_node_id, type: item.edge_type, displayLabel: EDGE_LABELS[item.edge_type] || item.edge_type } })),
    ]
    const cy = cytoscape({ container: container.current, elements, style: graphStyle(), layout: { name: 'breadthfirst', directed: true, roots: [graph.root_node_id], spacingFactor: 1.15, padding: 36 }, minZoom: 0.2, maxZoom: 2.5 })
    cy.fit(undefined, 36)
    cy.on('tap', 'node', (event) => { cy.elements().addClass('faded').removeClass('focused'); event.target.closedNeighborhood().removeClass('faded').addClass('focused'); onNode?.(event.target.data('raw')) })
    cy.on('tap', (event) => { if (event.target === cy) cy.elements().removeClass('faded focused') })
    cyRef.current = cy
    return () => { cy.destroy(); cyRef.current = null }
  }, [graph, onNode])
  useEffect(() => {
    const cy = cyRef.current
    if (!cy || !node?.node_id) return
    const selectedNode = cy.getElementById(node.node_id)
    if (!selectedNode.length) return
    cy.elements().addClass('faded').removeClass('focused')
    selectedNode.closedNeighborhood().removeClass('faded').addClass('focused')
    cy.animate({ fit: { eles: selectedNode.closedNeighborhood(), padding: 48 }, duration: 250 })
  }, [node?.node_id])
  if (!graph || graph.build_status !== 'built') return <Empty description={graph?.build_issues?.[0]?.message || '当前单元没有可用的 Local Graph'} />
  return <div className="local-graph-view"><div className="graph-toolbar"><Space><Text strong>Local evidence graph</Text><Text type="secondary">{graph.nodes.length} nodes · {graph.edges.length} relations</Text></Space><Space><Button size="small" onClick={fit}>适应画布</Button><Button size="small" icon={<ReloadOutlined />} onClick={reset}>重置</Button></Space></div><div className="graph-reader"><div className="cytoscape-canvas" ref={container} /><GraphDetail node={node} unit={unit} /></div><div className="graph-legend"><span>● Graph unit</span><span>◆ Event</span><span>● Proposition</span><span>▭ Modifier</span><span>● Source actor</span></div></div>
}

function GraphDetail({ node, unit }) {
  if (!unit) return null
  if (!node) return <aside className="graph-detail"><Title level={5}>节点详情</Title><Paragraph>点击图节点查看语义、状态和原文证据。</Paragraph></aside>
  return <aside className="graph-detail"><Title level={5}>{node.node_type === 'event' ? (FRAME_LABELS[node.label] || node.label) : node.label}</Title><Space wrap><Tag>{node.node_type}</Tag><Tag>{node.semantic_type}</Tag>{node.status && <Tag color="blue">{node.status}</Tag>}{node.certainty && <Tag color="gold">{node.certainty}</Tag>}</Space><Text strong>证据原文</Text><Paragraph className="graph-quote">{node.evidence?.quote || '无'}</Paragraph><Citation value={{ ...node.evidence, node_id: node.node_id, graph_unit_id: unit.graph_unit_id, segment_id: unit.segment.segment_id }} label="检查节点证据" /></aside>
}

function RawSegments({ segments, selected }) {
  return <div className="raw-segment-list">{segments.map((segment, index) => { const unit = selected?.segment_id === segment.segment_id ? selected : null; const start = unit?.segment_start_char; const end = unit?.segment_end_char; const text = segment.text || ''; return <article className="raw-segment-card" key={segment.segment_id} style={{ borderLeftColor: SEGMENT_COLORS[index % SEGMENT_COLORS.length] }}><div className="raw-segment-header"><Text strong>{segment.segment_id}</Text><Text type="secondary">{segment.unit_type} · {segment.temporal_anchor || '无时间锚点'} · {text.length} 字符</Text></div><Paragraph className="raw-document">{start != null && end != null ? <>{text.slice(0, start)}<mark>{text.slice(start, end)}</mark>{text.slice(end)}</> : text}</Paragraph></article> })}</div>
}

function UnitItem({ item, selected, onSelect }) {
  return <div className={`unit-item ${selected ? 'selected-unit' : ''}`} onClick={() => onSelect(item)}><Space wrap><Text code>{item.graph_unit_id}</Text><Tag color="blue">{FRAME_LABELS[item.primary_frame] || item.primary_frame}</Tag></Space><div className="unit-tags"><Tag style={{ background: SOURCE_COLORS[item.source_type] }}>{SOURCE_LABELS[item.source_type] || item.source_type}</Tag>{item.mdt_specialty?.map((value) => <Tag icon={<MedicineBoxOutlined />} key={value} style={{ borderColor: SPECIALTY_COLORS[value], background: '#fff' }}>{SPECIALTY_LABELS[value] || value}</Tag>)}</div><Paragraph ellipsis={{ rows: 2 }}>{item.text}</Paragraph></div>
}

export function SemanticWorkspace({ runId }) {
  const query = useQuery({ queryKey: ['semantic', runId], queryFn: () => api.semantic(runId) })
  const [params, setParams] = useSearchParams()
  const [search, setSearch] = useState('')
  const [node, setNode] = useState(null)
  const units = useMemo(() => (query.data?.segments || []).flatMap((segment) => segment.units.map((unit) => ({ ...unit, segment }))), [query.data])
  const filtered = units.filter((item) => !search || `${item.graph_unit_id} ${item.text} ${item.source_type} ${item.mdt_specialty?.join(' ')}`.toLowerCase().includes(search.toLowerCase()))
  const selectedId = params.get('unit') || units[0]?.graph_unit_id
  const selected = units.find((item) => item.graph_unit_id === selectedId) || units[0]
  const selectedNodeId = params.get('node')
  useEffect(() => {
    if (!selectedNodeId || !selected?.local_graph?.nodes) return
    setNode(selected.local_graph.nodes.find((item) => item.node_id === selectedNodeId) || null)
  }, [selected?.graph_unit_id, selectedNodeId])
  const selectUnit = (unit) => { setNode(null); setParams({ unit: unit.graph_unit_id }) }
  if (query.isLoading) return <div className="center-spin"><Spin size="large" /></div>
  if (query.isError) return <QueryError error={query.error} retry={query.refetch} />
  return <div className="semantic-workspace"><div className="workspace-title"><div><Text className="eyebrow">SEMANTIC GRAPHING</Text><Title level={3}>语义图构建</Title></div><Space><Tag>{query.data.summary.segment_count} segments</Tag><Tag>{query.data.summary.unit_count} units</Tag><Tag color="success">{query.data.summary.built_graph_count || 0} graphs built</Tag></Space></div><Card size="small" title={<Space><ApartmentOutlined />病例原文 · Segments</Space>} className="panel-card raw-panel"><RawSegments segments={query.data.segments || []} selected={selected} /></Card><div className="semantic-bottom"><Card size="small" title="Graph Units" extra={<Badge count={filtered.length} showZero color="#64748b" />} className="panel-card units-panel"><Input allowClear prefix={<SearchOutlined />} placeholder="搜索 unit / 原文 / 类型" value={search} onChange={(event) => setSearch(event.target.value)} /><div className="unit-list">{filtered.map((item) => <UnitItem key={item.graph_unit_id} item={item} selected={item.graph_unit_id === selected?.graph_unit_id} onSelect={selectUnit} />)}</div></Card><Card size="small" title={selected ? `Local Graph · ${selected.graph_unit_id}` : 'Local Graph'} className="panel-card graph-panel"><LocalGraph unit={selected} node={node} onNode={setNode} /></Card></div></div>
}
