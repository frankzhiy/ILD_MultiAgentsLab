import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Background, Controls, MarkerType, ReactFlow } from '@xyflow/react'
import { Alert, Card, Col, Row, Space, Statistic, Tag, Timeline, Typography } from 'antd'
import { ApiOutlined, CheckCircleOutlined, ClockCircleOutlined, DisconnectOutlined } from '@ant-design/icons'
import { api } from '../../api'
import { QueryError } from '../../components/QueryState'

const { Title, Text } = Typography
const specialtyNodes = [
  ['pulmonology', '呼吸科', 250], ['thoracic_radiology', '胸部影像科', 335],
  ['rheumatology', '风湿免疫科', 420], ['pathology', '病理科', 505],
]

function useRunEvents(runId) {
  const [events, setEvents] = useState([])
  const [connection, setConnection] = useState('connecting')
  useEffect(() => {
    const source = new EventSource(`/api/runs/${encodeURIComponent(runId)}/stream`)
    source.onopen = () => setConnection('connected')
    source.onmessage = (event) => setEvents((current) => [...current, JSON.parse(event.data)].slice(-100))
    ;['run_started', 'stage_started', 'stage_completed', 'agent_message', 'run_completed', 'run_failed', 'run_cancelled', 'agent_error'].forEach((type) => {
      source.addEventListener(type, (event) => setEvents((current) => [...current, JSON.parse(event.data)].slice(-100)))
    })
    source.onerror = () => setConnection('disconnected')
    return () => source.close()
  }, [runId])
  return { events, connection }
}

export function OverviewWorkspace({ runId, run }) {
  const terminal = ['completed', 'failed', 'cancelled'].includes(run?.status)
  const specialties = useQuery({ queryKey: ['specialties', runId], queryFn: () => api.specialties(runId), refetchInterval: terminal ? false : 4000 })
  const semantic = useQuery({ queryKey: ['semantic', runId], queryFn: () => api.semantic(runId), refetchInterval: terminal ? false : 5000 })
  const { events, connection } = useRunEvents(runId)
  const graph = useMemo(() => {
    const nodes = [
      { id: 'input', position: { x: 0, y: 365 }, data: { label: '病例原文' }, className: 'flow-source' },
      { id: 'semantic', position: { x: 170, y: 365 }, data: { label: 'Semantic Graphing' }, className: run?.semantic_complete ? 'flow-success' : 'flow-active' },
      { id: 'router', position: { x: 390, y: 365 }, data: { label: '证据路由' }, className: 'flow-router' },
      ...specialtyNodes.map(([id, label, y]) => ({ id, position: { x: 600, y }, data: { label }, className: run?.completed_specialties?.includes(id) ? 'flow-success' : 'flow-pending' })),
    ]
    const arrow = { type: 'smoothstep', markerEnd: { type: MarkerType.ArrowClosed }, animated: false }
    const edges = [
      { id: 'e-input', source: 'input', target: 'semantic', ...arrow },
      { id: 'e-semantic', source: 'semantic', target: 'router', ...arrow },
      ...specialtyNodes.map(([id]) => ({ id: `e-${id}`, source: 'router', target: id, ...arrow })),
    ]
    return { nodes, edges }
  }, [run])
  if (specialties.isError || semantic.isError) return <QueryError error={specialties.error || semantic.error} retry={() => { specialties.refetch(); semantic.refetch() }} />
  const completed = specialties.data?.results.filter((item) => item.status === 'completed').length || 0
  const lifecycle = events.length ? events.map((item) => ({ color: item.type.includes('failed') || item.type.includes('error') ? 'red' : item.type.includes('completed') ? 'green' : 'blue', children: <><Text strong>{item.stage || item.agent_id || item.type}</Text><br /><Text type="secondary">{item.type}</Text></> })) : [
    { color: run?.semantic_complete ? 'green' : 'blue', children: '语义图产物' },
    { color: completed === 4 ? 'green' : 'gray', children: `专科初评 ${completed}/4` },
  ]
  return (
    <div>
      <div className="workspace-title"><div><Text className="eyebrow">RUN OVERVIEW</Text><Title level={3}>运行总览</Title></div><Tag icon={connection === 'connected' ? <ApiOutlined /> : <DisconnectOutlined />} color={connection === 'connected' ? 'success' : connection === 'connecting' ? 'processing' : 'error'}>{connection === 'connected' ? '事件流已连接' : connection === 'connecting' ? '连接事件流' : '事件流断开'}</Tag></div>
      {connection === 'disconnected' && <Alert type="warning" showIcon title="实时事件流已断开" description="历史产物仍可查看；浏览器会自动尝试重连。若持续失败，请到“错误与诊断”检查后端服务。" className="section-gap" />}
      <Row gutter={14} className="metric-row">
        <Col span={6}><Card><Statistic title="Discourse segments" value={semantic.data?.summary.segment_count || 0} /></Card></Col>
        <Col span={6}><Card><Statistic title="Graph units" value={semantic.data?.summary.unit_count || 0} /></Card></Col>
        <Col span={6}><Card><Statistic title="专科完成" value={completed} suffix="/ 4" /></Card></Col>
        <Col span={6}><Card><Statistic title="产物文件" value={run?.artifact_count || 0} /></Card></Col>
      </Row>
      <Card title="多智能体执行拓扑" className="section-card flow-card">
        <div className="pipeline-flow"><ReactFlow nodes={graph.nodes} edges={graph.edges} fitView nodesDraggable={false} nodesConnectable={false} elementsSelectable={false}><Background gap={18} color="#dce4f0" /><Controls showInteractive={false} /></ReactFlow></div>
      </Card>
      <Row gutter={14}>
        <Col span={15}><Card title="Agent 状态" className="section-card"><div className="agent-status-list">{(specialties.data?.results || []).map((item) => <div className="agent-status-row" key={item.specialty}><div className="agent-status-icon">{item.status === 'completed' ? <CheckCircleOutlined className="success-icon" /> : <ClockCircleOutlined />}</div><div className="agent-status-text"><Text strong>{item.label}</Text><Text type="secondary">owned {item.input_summary?.owned_unit_count || 0} · shared {item.input_summary?.shared_context_unit_count || 0} · degraded {item.input_summary?.degraded_locator_count || 0}</Text></div><Tag color={item.status === 'completed' ? 'success' : 'default'}>{item.status === 'completed' ? '已完成' : '等待中'}</Tag></div>)}</div></Card></Col>
        <Col span={9}><Card title="生命周期" className="section-card"><Timeline items={lifecycle} /></Card></Col>
      </Row>
    </div>
  )
}
