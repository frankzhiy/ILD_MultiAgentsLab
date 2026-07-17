import { useQuery } from '@tanstack/react-query'
import { Alert, Button, Card, Collapse, Empty, Space, Tag, Typography } from 'antd'
import { CopyOutlined, WarningOutlined } from '@ant-design/icons'
import { api } from '../../api'
import { QueryError } from '../../components/QueryState'

const { Paragraph, Text, Title } = Typography

export function ErrorWorkspace({ runId }) {
  const query = useQuery({ queryKey: ['errors', runId], queryFn: () => api.errors(runId) })
  if (query.isError) return <QueryError error={query.error} retry={query.refetch} />
  const errors = query.data || []
  return (
    <div>
      <div className="workspace-title"><div><Text className="eyebrow">DIAGNOSTICS</Text><Title level={3}>错误与诊断</Title></div><Tag color={errors.length ? 'error' : 'success'}>{errors.length ? `${errors.length} error artifacts` : '未发现错误产物'}</Tag></div>
      {!errors.length ? <Card><Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="该运行没有 error / failure trace 产物" /></Card> : <Alert type="warning" showIcon title="科研运行保留失败尝试" description="失败产物不会因最终阶段成功而被隐藏。请结合 stage、attempts 与最终 JSON 判断是否影响结论。" className="section-gap" />}
      <Space orientation="vertical" size="middle" style={{ width: '100%' }}>
        {errors.map((item) => {
          const diagnostic = JSON.stringify(item.payload, null, 2)
          return <Card key={item.artifact} title={<Space><WarningOutlined /><Text strong>{item.artifact}</Text></Space>} extra={<Button icon={<CopyOutlined />} onClick={() => navigator.clipboard?.writeText(diagnostic)}>复制诊断</Button>}>
            <Space wrap><Tag color="error">{item.agent_id}</Tag>{item.failed_stage && <Tag>{item.failed_stage}</Tag>}{item.error_type && <Tag>{item.error_type}</Tag>}</Space>
            <Paragraph className="error-summary">{item.error}</Paragraph>
            <Collapse ghost items={[{ key: 'attempts', label: `结构化尝试 (${item.attempts?.length || 0})`, children: <pre className="diagnostic-pre">{JSON.stringify(item.attempts || [], null, 2)}</pre> }, { key: 'traceback', label: 'Traceback / 完整 payload', children: <pre className="diagnostic-pre">{item.traceback || diagnostic}</pre> }]} />
          </Card>
        })}
      </Space>
    </div>
  )
}
