import { useMutation, useQuery } from '@tanstack/react-query'
import { ArrowLeftOutlined, FileTextOutlined, SettingOutlined } from '@ant-design/icons'
import { Alert, App, Button, Card, Col, Form, Input, InputNumber, Layout, Radio, Row, Select, Space, Switch, Table, Typography } from 'antd'
import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../api'
import { Brand } from '../components/Brand'

const { Header, Content } = Layout
const { Title, Text } = Typography

const AGENT_LABELS = {
  semantic_graphing: 'Semantic Graphing', pulmonology: '呼吸科', thoracic_radiology: '胸部影像科',
  rheumatology: '风湿免疫科', pathology: '病理科', mdt_chair: 'MDT 主持人',
}

export function NewRunPage() {
  const [form] = Form.useForm()
  const navigate = useNavigate()
  const { message } = App.useApp()
  const [agentConfig, setAgentConfig] = useState({})
  const { data: cases = [] } = useQuery({ queryKey: ['cases'], queryFn: api.cases })
  const { data: modelData } = useQuery({ queryKey: ['models'], queryFn: api.models })
  const agents = modelData?.agents || []
  useEffect(() => {
    if (!agents.length) return
    setAgentConfig(Object.fromEntries(agents.map((item) => [item.agent_id, { model: item.model, reasoning_effort: item.reasoning_effort || 'none' }])))
  }, [modelData])
  const mutation = useMutation({
    mutationFn: ({ mode, ...payload }) => mode === 'batch' ? api.createBatch({ ...payload, kind: 'run', source: 'library' }) : api.createRun(payload),
    onSuccess: (value) => navigate(value.kind === 'run' ? `/batches/${encodeURIComponent(value.id)}` : `/runs/${encodeURIComponent(value.id)}/overview`),
    onError: (error) => message.error(`启动失败：${error.message}`),
  })
  const updateAgent = (agentId, key, value) => setAgentConfig((current) => ({ ...current, [agentId]: { ...current[agentId], [key]: value } }))
  const columns = [
    { title: 'Agent', dataIndex: 'agent_id', render: (value) => <Text strong>{AGENT_LABELS[value] || value}</Text> },
    { title: 'Provider', dataIndex: 'provider', width: 130 },
    { title: 'Model', dataIndex: 'model', render: (value, row) => <Input value={agentConfig[row.agent_id]?.model ?? value} onChange={(event) => updateAgent(row.agent_id, 'model', event.target.value)} aria-label={`${row.agent_id} model`} /> },
    { title: '推理强度', dataIndex: 'reasoning_effort', width: 160, render: (value, row) => <Select value={agentConfig[row.agent_id]?.reasoning_effort ?? value ?? 'none'} onChange={(next) => updateAgent(row.agent_id, 'reasoning_effort', next)} style={{ width: '100%' }} options={['none', 'low', 'medium', 'high'].map((item) => ({ value: item, label: item }))} /> },
    { title: '结构化输出', dataIndex: 'supports_json_schema', width: 120, render: (value) => <Switch checked={value} disabled /> },
  ]
  return (
    <Layout className="root-layout">
      <Header className="global-header"><Brand /><Button icon={<ArrowLeftOutlined />}><Link to="/runs">返回运行列表</Link></Button></Header>
      <Content className="page-content narrow-content">
        <div className="page-heading"><div><Text className="eyebrow">NEW EXPERIMENT</Text><Title level={2}>配置一次 MDT 运行</Title><Text type="secondary">病例原文保持只读；每个 Agent 的模型设置与最终产物一起记录。</Text></div></div>
        <Alert className="section-gap" type="info" showIcon title="运行会调用真实 Agent" description="完整流程依次执行 Semantic Graphing、四个并行专科和 MDT 主持人整合；批量运行中单个病例失败不会中断其他病例。" />
        <Form form={form} layout="vertical" initialValues={{ mode: 'single', source: 'library', max_concurrency: 6, max_case_concurrency: 2, max_request_concurrency: 6 }} onFinish={(values) => mutation.mutate({ ...values, agents: agentConfig })}>
          <Form.Item name="mode" label="运行方式"><Radio.Group options={[{ value: 'single', label: '单个病例' }, { value: 'batch', label: '批量病例库运行' }]} /></Form.Item>
          <Card title={<Space><FileTextOutlined />病例输入</Space>} className="section-card">
            <Form.Item noStyle shouldUpdate={(before, after) => before.mode !== after.mode || before.source !== after.source}>
              {({ getFieldValue }) => getFieldValue('mode') === 'batch'
                ? <Form.Item name="case_ids" label="data/raw_cases" rules={[{ required: true, type: 'array', min: 1, message: '请至少选择一个病例' }]}><Select mode="multiple" showSearch placeholder="选择多个病例" options={cases.map((item) => ({ value: item.id, label: `${item.filename} · ${item.bytes} bytes` }))} /></Form.Item>
                : <><Form.Item name="source" label="输入方式"><Radio.Group options={[{ value: 'library', label: '病例库' }, { value: 'paste', label: '粘贴原文' }]} /></Form.Item>{getFieldValue('source') === 'library'
                  ? <Form.Item name="case_id" label="data/raw_cases" rules={[{ required: true }]}><Select showSearch placeholder="选择病例" options={cases.map((item) => ({ value: item.id, label: `${item.filename} · ${item.bytes} bytes` }))} /></Form.Item>
                  : <><Form.Item name="case_id" label="病例 ID" rules={[{ required: true, pattern: /^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$/ }]}><Input placeholder="例如 pilot-001" /></Form.Item><Form.Item name="raw_text" label="病例原文" rules={[{ required: true }]}><Input.TextArea rows={10} placeholder="粘贴去标识化病例原文" /></Form.Item></>}</>}
            </Form.Item>
            <Form.Item name="max_concurrency" label="Semantic Graphing 最大并发" tooltip="仅影响语义图阶段的并行任务数"><InputNumber min={1} max={16} /></Form.Item>
            <Form.Item noStyle shouldUpdate={(before, after) => before.mode !== after.mode}>{({ getFieldValue }) => getFieldValue('mode') === 'batch' && <Row gutter={16}><Col span={12}><Form.Item name="max_case_concurrency" label="批次病例并发" tooltip="同时执行完整流程的病例数"><InputNumber min={1} max={16} style={{ width: '100%' }} /></Form.Item></Col><Col span={12}><Form.Item name="max_request_concurrency" label="全局模型请求并发" tooltip="本批次所有阶段的在途模型请求上限"><InputNumber min={1} max={64} style={{ width: '100%' }} /></Form.Item></Col></Row>}</Form.Item>
          </Card>
          <Card title={<Space><SettingOutlined />Agent 模型矩阵</Space>} className="section-card">
            <Table rowKey="agent_id" columns={columns} dataSource={agents} pagination={false} size="middle" />
          </Card>
          <Card className="section-card">
            <Row gutter={24} align="middle"><Col flex="auto"><Title level={5}>启动完整 MDT 流程</Title><Text type="secondary">Semantic Graphing → unit 分发 → 四专科并行评估 → 主持人跨专科整合</Text></Col><Col><Button type="primary" htmlType="submit" size="large" loading={mutation.isPending}>开始运行</Button></Col></Row>
          </Card>
        </Form>
      </Content>
    </Layout>
  )
}
