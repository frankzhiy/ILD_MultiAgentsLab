import { useQuery } from '@tanstack/react-query'
import { Alert, Button, Card, Collapse, Empty, Space, Tag, Typography } from 'antd'
import { CopyOutlined, WarningOutlined } from '@ant-design/icons'
import { api } from '../../api'
import { QueryError } from '../../components/QueryState'

const { Paragraph, Text, Title } = Typography

export function ErrorWorkspace({ runId, run }) {
  const query = useQuery({ queryKey: ['errors', runId, run?.status], queryFn: () => api.errors(runId) })
  if (query.isError) return <QueryError error={query.error} retry={query.refetch} />
  const errors = query.data || []
  const currentErrors = errors.filter((item) => item.current !== false)
  const completed = run?.status === 'completed' && !currentErrors.length
  return (
    <div>
      <div className="workspace-title"><div><Text className="eyebrow">DIAGNOSTICS</Text><Title level={3}>错误与诊断</Title></div><Tag color={currentErrors.length ? 'error' : 'success'}>{currentErrors.length ? `${currentErrors.length} 个当前错误` : completed ? '最新状态：已完成' : '当前未发现错误'}</Tag></div>
      {!currentErrors.length ? <Card><Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={completed ? '运行已顺利完成，当前没有错误' : '当前没有 error / failure trace'} /></Card> : <Alert type="error" showIcon title="当前运行存在错误" description="以下错误尚未被后续成功结果覆盖。" className="section-gap" />}
      <Space orientation="vertical" size="middle" style={{ width: '100%' }}>
        {currentErrors.map((item) => {
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
