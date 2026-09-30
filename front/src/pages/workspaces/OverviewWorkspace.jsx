import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Background, Controls, MarkerType, ReactFlow } from '@xyflow/react'
import { Alert, Card, Col, Row, Space, Statistic, Tag, Timeline, Typography } from 'antd'
import { ApiOutlined, CheckCircleOutlined, ClockCircleOutlined, DisconnectOutlined } from '@ant-design/icons'
import { Link } from 'react-router-dom'
import { api } from '../../api'
import { QueryError } from '../../components/QueryState'

const { Title, Text } = Typography
const specialtyNodes = [
  ['pulmonology', '呼吸科', 250], ['thoracic_radiology', '胸部影像科', 335],
  ['rheumatology', '风湿免疫科', 420], ['pathology', '病理科', 505],
]
const stageClass = (done, active, failed) => done ? 'flow-success' : failed ? 'flow-failed' : active ? 'flow-active' : 'flow-pending'

function useRunEvents(runId) {
  const [events, setEvents] = useState([])
  const [connection, setConnection] = useState('connecting')
  useEffect(() => {
    const source = new EventSource(`/api/runs/${encodeURIComponent(runId)}/stream`)
    source.onopen = () => setConnection('connected')
    source.onmessage = (event) => setEvents((current) => [...current, JSON.parse(event.data)].slice(-100))
    ;['run_started', 'stage_started', 'stage_completed', 'agent_message', 'discussion_started', 'discussion_round_started', 'discussion_round_completed', 'discussion_report_started', 'discussion_completed', 'discussion_failed', 'run_completed', 'run_failed', 'run_cancelled', 'agent_error'].forEach((type) => {
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
  const chair = useQuery({ queryKey: ['chair', runId], queryFn: () => api.chair(runId), refetchInterval: terminal ? false : 4000 })
  const discussion = useQuery({ queryKey: ['discussion', runId], queryFn: () => api.discussion(runId), refetchInterval: terminal ? false : 4000 })
  const { events, connection } = useRunEvents(runId)
  const chairStatus = chair.data?.status || 'pending'
  const discussionStatus = discussion.data?.status || 'pending'
  const reportStatus = discussion.data?.final_report ? 'completed' : discussion.data?.report_status || 'waiting'
  const graph = useMemo(() => {
    const nodes = [
      { id: 'input', position: { x: 0, y: 365 }, data: { label: '病例原文' }, className: 'flow-source' },
      { id: 'semantic', position: { x: 170, y: 365 }, data: { label: 'Semantic Graphing' }, className: run?.semantic_complete ? 'flow-success' : 'flow-active' },
      { id: 'router', position: { x: 390, y: 365 }, data: { label: '证据路由' }, className: 'flow-router' },
      ...specialtyNodes.map(([id, label, y]) => ({ id, position: { x: 600, y }, data: { label }, className: run?.completed_specialties?.includes(id) ? 'flow-success' : 'flow-pending' })),
      { id: 'chair', position: { x: 820, y: 365 }, data: { label: '主持人整合' }, className: stageClass(run?.chair_complete, chairStatus === 'running', chairStatus === 'failed') },
      { id: 'discussion', position: { x: 1030, y: 365 }, data: { label: 'MDT 团队讨论' }, className: stageClass(run?.discussion_complete, discussionStatus === 'running', discussionStatus === 'failed') },
      { id: 'report', position: { x: 1240, y: 365 }, data: { label: '最终报告' }, className: stageClass(reportStatus === 'completed', reportStatus === 'running', reportStatus === 'failed') },
    ]
    const arrow = { type: 'smoothstep', markerEnd: { type: MarkerType.ArrowClosed }, animated: false }
    const edges = [
      { id: 'e-input', source: 'input', target: 'semantic', ...arrow },
      { id: 'e-semantic', source: 'semantic', target: 'router', ...arrow },
      ...specialtyNodes.map(([id]) => ({ id: `e-${id}`, source: 'router', target: id, ...arrow })),
      ...specialtyNodes.map(([id]) => ({ id: `e-${id}-chair`, source: id, target: 'chair', ...arrow })),
      { id: 'e-chair-discussion', source: 'chair', target: 'discussion', ...arrow },
      { id: 'e-discussion-report', source: 'discussion', target: 'report', ...arrow },
    ]
    return { nodes, edges }
  }, [run, chairStatus, discussionStatus, reportStatus])
  if (specialties.isError || semantic.isError || chair.isError || discussion.isError) return <QueryError error={specialties.error || semantic.error || chair.error || discussion.error} retry={() => { specialties.refetch(); semantic.refetch(); chair.refetch(); discussion.refetch() }} />
  const completed = specialties.data?.results.filter((item) => item.status === 'completed').length || 0
  const lifecycle = events.length ? events.map((item) => ({ color: item.type.includes('failed') || item.type.includes('error') ? 'red' : item.type.includes('completed') ? 'green' : 'blue', children: <><Text strong>{item.stage || item.agent_id || item.type}</Text><br /><Text type="secondary">{item.type}</Text></> })) : [
    { color: run?.semantic_complete ? 'green' : 'blue', children: '语义图产物' },
    { color: completed === 4 ? 'green' : 'gray', children: `专科初评 ${completed}/4` },
    { color: run?.chair_complete ? 'green' : 'gray', children: '主持人整合' },
    { color: run?.discussion_complete ? 'green' : discussionStatus === 'failed' ? 'red' : 'gray', children: `团队讨论 ${discussion.data?.current_round || 0} 轮` },
    { color: reportStatus === 'completed' ? 'green' : reportStatus === 'failed' ? 'red' : 'gray', children: '最终报告' },
  ]
  return (
    <div>
      <div className="workspace-title"><div><Text className="eyebrow">RUN OVERVIEW</Text><Title level={3}>运行总览</Title></div><Tag icon={connection === 'connected' ? <ApiOutlined /> : <DisconnectOutlined />} color={connection === 'connected' ? 'success' : connection === 'connecting' ? 'processing' : 'error'}>{connection === 'connected' ? '事件流已连接' : connection === 'connecting' ? '连接事件流' : '事件流断开'}</Tag></div>
      {connection === 'disconnected' && <Alert type="warning" showIcon title="实时事件流已断开" description="历史产物仍可查看；浏览器会自动尝试重连。若持续失败，请到“错误与诊断”检查后端服务。" className="section-gap" />}
      <Row gutter={14} className="metric-row">
        <Col span={4}><Card><Statistic title="Discourse segments" value={semantic.data?.summary.segment_count || 0} /></Card></Col>
        <Col span={4}><Card><Statistic title="Graph units" value={semantic.data?.summary.unit_count || 0} /></Card></Col>
        <Col span={4}><Card><Statistic title="专科完成" value={completed} suffix="/ 4" /></Card></Col>
        <Col span={4}><Card><Statistic title="主持人整合" value={run?.chair_complete ? '已完成' : chairStatus === 'failed' ? '失败' : '等待中'} /></Card></Col>
        <Col span={4}><Card><Statistic title="讨论轮次" value={discussion.data?.current_round || 0} /></Card></Col>
        <Col span={4}><Card><Statistic title="最终报告" value={reportStatus === 'completed' ? '已完成' : reportStatus === 'failed' ? '失败' : '等待中'} /></Card></Col>
      </Row>
      <Card title="多智能体执行拓扑" className="section-card flow-card">
        <div className="pipeline-flow"><ReactFlow nodes={graph.nodes} edges={graph.edges} fitView nodesDraggable={false} nodesConnectable={false} elementsSelectable={false}><Background gap={18} color="#dce4f0" /><Controls showInteractive={false} /></ReactFlow></div>
      </Card>
      <Row gutter={14}>
        <Col span={15}><Card title="阶段状态" className="section-card"><div className="agent-status-list">{(specialties.data?.results || []).map((item) => <div className="agent-status-row" key={item.specialty}><div className="agent-status-icon">{item.status === 'completed' ? <CheckCircleOutlined className="success-icon" /> : <ClockCircleOutlined />}</div><div className="agent-status-text"><Text strong>{item.label}</Text><Text type="secondary">owned {item.input_summary?.owned_unit_count || 0} · shared {item.input_summary?.shared_context_unit_count || 0} · degraded {item.input_summary?.degraded_locator_count || 0}</Text></div><Tag color={item.status === 'completed' ? 'success' : 'default'}>{item.status === 'completed' ? '已完成' : '等待中'}</Tag></div>)}{[
          ['chair', '主持人整合', run?.chair_complete ? 'completed' : chairStatus],
          ['discussion', `MDT 团队讨论 · 第 ${discussion.data?.current_round || 0} 轮`, discussionStatus],
          ['report', '最终报告', reportStatus],
        ].map(([id, label, status]) => <div className="agent-status-row" key={id}><div className="agent-status-icon">{status === 'completed' ? <CheckCircleOutlined className="success-icon" /> : <ClockCircleOutlined />}</div><div className="agent-status-text"><Text strong>{label}</Text></div><Tag color={status === 'completed' ? 'success' : status === 'failed' ? 'error' : status === 'running' ? 'processing' : 'default'}>{status === 'completed' ? '已完成' : status === 'failed' ? '失败' : status === 'running' ? '进行中' : '等待中'}</Tag></div>)}</div>{reportStatus === 'completed' && <Link to={`/runs/${encodeURIComponent(runId)}/report`}>查看最终报告</Link>}</Card></Col>
        <Col span={9}><Card title="生命周期" className="section-card"><Timeline items={lifecycle} /></Card></Col>
      </Row>
    </div>
  )
}
