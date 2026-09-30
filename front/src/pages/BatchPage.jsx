import { useMutation, useQuery } from '@tanstack/react-query'
import { Alert, Button, Card, Col, Collapse, Layout, Row, Space, Statistic, Table, Tag, Typography } from 'antd'
import dayjs from 'dayjs'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api } from '../api'
import { Brand } from '../components/Brand'

const { Header, Content } = Layout
const { Title, Text } = Typography

const labels = { queued: '等待中', running: '运行中', completed: '成功', completed_with_errors: '部分未完成', failed: '失败', skipped: '已跳过', interrupted: '已中断', stopped: '已停止' }
const colors = { completed: 'success', failed: 'error', skipped: 'warning', interrupted: 'error', running: 'processing' }
const stages = { semantic_graphing: '语义图构建', initial_consult: '专科评估', cross_specialty_integration: 'MDT 会前整合', team_discussion: '团队讨论', synthesis_acceptance: '团队结论确认', final_report: '生成最终报告' }
const agentLabels = { semantic_graphing: '语义图构建', pulmonology: '呼吸科', thoracic_radiology: '胸部影像科', rheumatology: '风湿免疫科', pathology: '病理科', mdt_chair: 'MDT 主持人' }

function progressLabel(item) {
  if (item.status === 'queued') return '等待空位'
  if (item.status === 'completed') return '最终报告已生成'
  const progress = item.progress || {}
  if (progress.stage === 'initial_consult') return `专科评估，已完成 ${progress.completed_specialties.length}/4`
  if (progress.stage === 'team_discussion' && progress.round_number) return `团队讨论，第 ${progress.round_number} 轮`
  return stages[progress.stage] || '尚未开始'
}

export function BatchPage() {
  const { batchId } = useParams()
  const navigate = useNavigate()
  const query = useQuery({ queryKey: ['batch', batchId], queryFn: () => api.batch(batchId), refetchInterval: (current) => ['queued', 'running'].includes(current.state.data?.status) ? 2000 : false })
  const retry = useMutation({ mutationFn: () => api.retryBatch(batchId), onSuccess: (value) => navigate(`/batches/${encodeURIComponent(value.id)}`) })
  const value = query.data
  if (query.isLoading) return <Card loading />
  if (query.isError && !value) return <Alert type="error" title="无法读取批次" description={query.error.message} action={<Button onClick={() => query.refetch()}>重新读取</Button>} />
  const active = ['queued', 'running'].includes(value.status)
  const columns = [
    { title: '病例', dataIndex: 'case_id', width: 140, ellipsis: true, render: (item) => <Text strong>{item}</Text> },
    { title: '状态', dataIndex: 'status', width: 110, filters: Object.entries(labels).filter(([key]) => key !== 'completed_with_errors').map(([value, text]) => ({ value, text })), onFilter: (status, row) => row.status === status, render: (item) => <Tag color={colors[item]}>{labels[item] || item}</Tag> },
    { title: '当前阶段', width: 220, render: (_, item) => progressLabel(item) },
    { title: '耗时', dataIndex: 'elapsed_seconds', width: 120, render: (seconds) => seconds == null ? '—' : `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒` },
    { title: '失败原因', dataIndex: 'error', ellipsis: true, render: (item) => item || '—' },
    { title: '操作', width: 120, render: (_, item) => <Link to={`/runs/${encodeURIComponent(item.run_id)}/${item.status === 'completed' ? 'report' : item.status === 'failed' ? 'errors' : 'overview'}`}>{item.status === 'completed' ? '查看结果' : item.status === 'failed' ? '查看错误' : '查看详情'}</Link> },
  ]
  const failed = value.summary.failed + value.summary.interrupted + (value.summary.stopped || 0)
  return <Layout className="root-layout">
    <Header className="global-header"><Brand /><Space><Link to="/batches/new">新建批量实验</Link><Link to="/runs">返回实验记录</Link></Space></Header>
    <Content className="page-content">
      <div className="page-heading"><div><Text className="eyebrow">BATCH RUN</Text><Title level={2}>{value.kind === 'discussion' ? '批量 MDT 团队讨论' : '批量实验进度'}</Title><Space wrap><Tag color={active ? 'processing' : value.summary.completed === value.summary.total ? 'success' : 'warning'}>{active ? '批次运行中' : value.summary.completed === value.summary.total ? '全部成功' : '批次已结束，部分未完成'}</Tag><Text type="secondary">{value.id} · {dayjs(value.created_at).format('YYYY-MM-DD HH:mm')}</Text></Space></div><Button href={`/api/batches/${encodeURIComponent(batchId)}/export`} download>导出病例汇总 CSV</Button></div>
      <Row gutter={[12, 12]} className="metric-row">{[['total', '总病例'], ['queued', '等待中'], ['running', '运行中'], ['completed', '成功'], ['failed', '失败']].map(([key, title]) => <Col xs={12} sm={8} lg={4} key={key}><Card><Statistic title={title} value={value.summary[key]} /></Card></Col>)}</Row>
      <Text type="secondary">页面每 2 秒更新；离开或刷新页面后后台继续执行。跳过 {value.summary.skipped}，中断 {value.summary.interrupted}，停止 {value.summary.stopped || 0}。</Text>
      {query.isError && <Alert className="section-gap" type="warning" showIcon title="进度更新失败，当前显示上次读取的记录" description={query.error.message} action={<Button onClick={() => query.refetch()}>重新读取</Button>} />}
      {value.retry_of && <div className="section-gap"><Link to={`/batches/${encodeURIComponent(value.retry_of)}`}>查看原批次</Link></div>}
      {failed > 0 && <Alert className="section-gap" type="warning" showIcon title="批次含未完成病例" description={active ? '其他病例继续执行；批次结束后可仅重跑未完成病例。' : '重跑生成新记录，保留原病例快照和模型配置。'} action={<Button size="small" disabled={active} loading={retry.isPending} onClick={() => retry.mutate()}>重跑未完成病例</Button>} />}
      {retry.isError && <Alert className="section-gap" type="error" showIcon title="无法创建重跑批次" description={retry.error.message} />}
      <Card className="table-card section-gap" title="病例进度"><Table rowKey="run_id" columns={columns} dataSource={value.items} pagination={{ pageSize: 12 }} scroll={{ x: 1100 }} /></Card>
      <Collapse className="section-gap" items={[{ key: 'settings', label: '本批次配置（只读）', children: <>
        <Text>同时运行病例数：{value.request.max_case_concurrency} · 模型请求并发上限：{value.request.max_request_concurrency}</Text>
        <Table size="small" rowKey="agent_id" pagination={false} dataSource={Object.entries(value.agents || value.request.agents || {}).map(([agent_id, config]) => ({ agent_id, ...config }))} columns={[{ title: 'Agent', dataIndex: 'agent_id', render: (id) => agentLabels[id] || id }, { title: '模型', dataIndex: 'model' }, { title: '推理强度', dataIndex: 'reasoning_effort' }]} />
      </> }]} />
    </Content>
  </Layout>
}
