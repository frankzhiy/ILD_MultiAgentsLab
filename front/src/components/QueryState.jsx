import { Alert, Button, Result, Space, Typography } from 'antd'
import { CopyOutlined, ReloadOutlined } from '@ant-design/icons'

const { Text } = Typography

export function QueryError({ error, retry, title = '数据加载失败' }) {
  const detail = error?.message || String(error || '未知错误')
  const copy = () => navigator.clipboard?.writeText(detail)
  return (
    <Result
      status="error"
      title={title}
      subTitle="该错误不会被静默隐藏。你可以重试，或复制诊断信息后到“错误与诊断”核对后端 trace。"
      extra={<Space><Button type="primary" icon={<ReloadOutlined />} onClick={retry}>重试</Button><Button icon={<CopyOutlined />} onClick={copy}>复制诊断</Button></Space>}
    >
      <Alert type="error" showIcon title="请求详情" description={<Text code>{detail}</Text>} />
    </Result>
  )
}

export function InlineError({ error, retry }) {
  return <Alert type="error" showIcon title="组件加载失败" description={error?.message || String(error)} action={retry && <Button size="small" onClick={retry}>重试</Button>} />
}
