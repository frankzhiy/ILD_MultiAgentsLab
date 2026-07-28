import { useQuery } from '@tanstack/react-query'
import { ArrowRightOutlined, ExperimentOutlined, PlusOutlined } from '@ant-design/icons'
import { Button, Card, Col, Layout, Row, Space, Statistic, Table, Typography } from 'antd'
import dayjs from 'dayjs'
import { useNavigate } from 'react-router-dom'
import { api } from '../api'
import { Brand } from '../components/Brand'
import { StatusTag } from '../components/StatusTag'

const { Header, Content } = Layout
const { Title, Text } = Typography

export function RunListPage() {
  const navigate = useNavigate()
  const { data: runs = [], isLoading } = useQuery({ queryKey: ['runs'], queryFn: api.runs })
  const { data: cases = [] } = useQuery({ queryKey: ['cases'], queryFn: api.cases })
  const completed = runs.filter((item) => item.status === 'completed').length
  const columns = [
    { title: '病例', dataIndex: 'case_id', width: 150, render: (value) => <Text strong>{value}</Text> },
    { title: '运行 ID', dataIndex: 'id', ellipsis: true, render: (value) => <Text code>{value}</Text> },
    { title: '阶段', dataIndex: 'status', width: 150, render: (value) => <StatusTag status={value} /> },
    { title: '专科完成', dataIndex: 'completed_specialties', width: 110, render: (value) => `${value.length}/4` },
    { title: '产物', dataIndex: 'artifact_count', width: 90 },
    { title: '更新时间', dataIndex: 'updated_at', width: 170, render: (value) => dayjs(value).format('YYYY-MM-DD HH:mm') },
    { title: '', width: 48, render: (_, row) => <Button type="text" icon={<ArrowRightOutlined />} onClick={() => navigate(`/runs/${encodeURIComponent(row.id)}/overview`)} /> },
  ]
  return (
    <Layout className="root-layout">
      <Header className="global-header">
        <Brand />
        <Button type="primary" icon={<PlusOutlined />} onClick={() => navigate('/runs/new')}>新建运行</Button>
      </Header>
      <Content className="page-content">
        <div className="page-heading">
          <div><Text className="eyebrow">RESEARCH RUNS</Text><Title level={2}>运行与复现实验</Title><Text type="secondary">从病例输入到四专科首轮评估，保留每一次数据流转、证据定位与模型配置。</Text></div>
        </div>
        <Row gutter={16} className="metric-row">
          <Col span={8}><Card><Statistic title="已发现运行" value={runs.length} prefix={<ExperimentOutlined />} /></Card></Col>
          <Col span={8}><Card><Statistic title="已完成运行" value={completed} /></Card></Col>
          <Col span={8}><Card><Statistic title="当前可用病例" value={cases.length} /></Card></Col>
        </Row>
        <Card className="table-card" title="实验记录" extra={<Space><Text type="secondary">本地科研数据</Text></Space>}>
          <Table rowKey="id" loading={isLoading} columns={columns} dataSource={runs} pagination={{ pageSize: 12 }} onRow={(row) => ({ onDoubleClick: () => navigate(`/runs/${encodeURIComponent(row.id)}/overview`) })} />
        </Card>
      </Content>
    </Layout>
  )
}
