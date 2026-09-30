import { useMutation, useQuery } from '@tanstack/react-query'
import { Alert, Button, Card, Layout, Table, Tag, Typography } from 'antd'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api } from '../api'
import { Brand } from '../components/Brand'

const { Header, Content } = Layout
const { Title, Text } = Typography

const labels = { queued: '排队中', running: '运行中', completed: '已完成', failed: '失败', skipped: '已跳过', interrupted: '已中断', stopped: '已停止' }
const colors = { completed: 'success', failed: 'error', skipped: 'warning', interrupted: 'error', running: 'processing' }

export function BatchPage() {
  const { batchId } = useParams()
  const navigate = useNavigate()
  const query = useQuery({ queryKey: ['batch', batchId], queryFn: () => api.batch(batchId), refetchInterval: (current) => ['queued', 'running'].includes(current.state.data?.status) ? 2000 : false })
  const retry = useMutation({ mutationFn: () => api.retryBatch(batchId), onSuccess: (value) => navigate(`/batches/${encodeURIComponent(value.id)}`) })
  const value = query.data
  if (query.isLoading) return null
  if (query.isError) return <Alert type="error" message="无法读取批次" description={query.error.message} />
  const columns = [
    { title: '病例', dataIndex: 'case_id', render: (item) => <Text strong>{item}</Text> },
    { title: '运行', dataIndex: 'run_id', render: (item) => <Link to={`/runs/${encodeURIComponent(item)}/overview`}>{item}</Link> },
    { title: '状态', dataIndex: 'status', render: (item) => <Tag color={colors[item]}>{labels[item] || item}</Tag> },
    { title: '失败原因', dataIndex: 'error', render: (item) => item || '—' },
  ]
  const failed = value.summary.failed + value.summary.interrupted + (value.summary.stopped || 0)
  return <Layout className="root-layout">
    <Header className="global-header"><Brand /><Link to="/runs">返回运行列表</Link></Header>
    <Content className="page-content narrow-content">
      <div className="page-heading"><div><Text className="eyebrow">BATCH RUN</Text><Title level={2}>{value.kind === 'discussion' ? '批量 MDT 团队讨论' : '批量 MDT 运行'}</Title><Text type="secondary">共 {value.summary.total} 项：完成 {value.summary.completed}，失败 {value.summary.failed}，跳过 {value.summary.skipped}，中断 {value.summary.interrupted}，停止 {value.summary.stopped || 0}</Text></div></div>
      {failed > 0 && <Alert className="section-gap" type="warning" showIcon title="批次含未完成病例" description="失败、中断或停止的病例不影响其余病例；可新建批次仅重跑这些病例。" action={<Button size="small" loading={retry.isPending} onClick={() => retry.mutate()}>重跑未完成病例</Button>} />}
      {retry.isError && <Alert className="section-gap" type="error" showIcon title="无法创建重跑批次" description={retry.error.message} />}
      <Card className="table-card" title="批次项目"><Table rowKey="run_id" columns={columns} dataSource={value.items} pagination={false} /></Card>
    </Content>
  </Layout>
}
