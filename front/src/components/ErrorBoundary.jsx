import React from 'react'
import { Button, Result, Space, Typography } from 'antd'
import { CopyOutlined, ReloadOutlined } from '@ant-design/icons'

const { Paragraph } = Typography

export class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props)
    this.state = { error: null, info: null }
  }

  static getDerivedStateFromError(error) {
    return { error }
  }

  componentDidCatch(error, info) {
    this.setState({ info })
  }

  render() {
    const { error, info } = this.state
    if (!error) return this.props.children
    const diagnostic = `${error.stack || error.message}\n${info?.componentStack || ''}`
    return (
      <div className="fatal-error">
        <Result
          status="error"
          title="前端渲染发生未处理错误"
          subTitle="错误边界已阻止白屏，并保留了组件栈。"
          extra={<Space><Button type="primary" icon={<ReloadOutlined />} onClick={() => window.location.reload()}>重新加载</Button><Button icon={<CopyOutlined />} onClick={() => navigator.clipboard?.writeText(diagnostic)}>复制组件栈</Button></Space>}
        >
          <Paragraph><pre className="diagnostic-pre">{diagnostic}</pre></Paragraph>
        </Result>
      </div>
    )
  }
}
