import { useMutation, useQuery } from '@tanstack/react-query'
import { ArrowLeftOutlined, FileTextOutlined, SettingOutlined } from '@ant-design/icons'
import { Alert, App, Button, Card, Col, Collapse, Form, Input, InputNumber, Layout, Radio, Row, Select, Space, Switch, Table, Typography } from 'antd'
import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../api'
import { Brand } from '../components/Brand'

const { Header, Content } = Layout
const { Title, Text } = Typography

const AGENT_LABELS = {
  semantic_graphing: 'Semantic Graphing', pulmonology: '呼吸科', thoracic_radiology: '胸部影像科',
  rheumatology: '风湿免疫科', pathology: '病理科', mdt_chair: 'MDT 会前整合',
}

export function NewRunPage({ initialMode = 'single' }) {
  const [form] = Form.useForm()
  const navigate = useNavigate()
  const { message } = App.useApp()
  const [agentConfig, setAgentConfig] = useState({})
  const [selectedCases, setSelectedCases] = useState([])
  const [caseSearch, setCaseSearch] = useState('')
  const [bulkModel, setBulkModel] = useState('')
  const casesQuery = useQuery({ queryKey: ['cases'], queryFn: api.cases })
  const cases = casesQuery.data || []
  const caseId = Form.useWatch('case_id', form)
  const mode = Form.useWatch('mode', form) || initialMode
  const { data: runs = [] } = useQuery({ queryKey: ['runs'], queryFn: api.runs, enabled: mode !== 'batch' })
  useEffect(() => { form.setFieldValue('parent_run_id', undefined) }, [caseId, form])
  const modelQuery = useQuery({ queryKey: ['models'], queryFn: api.models })
  const modelData = modelQuery.data
  const agents = modelData?.agents || []
  useEffect(() => {
    if (!agents.length) return
    setAgentConfig((current) => Object.keys(current).length ? current : Object.fromEntries(agents.map((item) => [item.agent_id, { model: item.model, reasoning_effort: item.reasoning_effort || 'none' }])))
  }, [modelData])
  const mutation = useMutation({
    mutationFn: ({ mode, parent_run_id, ...payload }) => mode === 'batch' ? api.createBatch({ ...payload, kind: 'run', source: 'library' }) : api.createRun({ ...payload, parent_run_id }),
    onSuccess: (value) => navigate(value.kind === 'run' ? `/batches/${encodeURIComponent(value.id)}` : `/runs/${encodeURIComponent(value.id)}/overview`),
    onError: (error) => message.error(`启动失败：${error.message}`),
  })
  const updateAgent = (agentId, key, value) => setAgentConfig((current) => ({ ...current, [agentId]: { ...current[agentId], [key]: value } }))
  const filteredCases = cases.filter((item) => item.id.toLowerCase().includes(caseSearch.trim().toLowerCase()))
  const submit = (values) => {
    if (mode === 'batch' && !selectedCases.length) return message.error('请至少选择一个病例')
    if (agents.some((agent) => !agentConfig[agent.agent_id]?.model?.trim())) return message.error('请填写每个 Agent 的模型')
    const settings = Object.fromEntries(Object.entries(agentConfig).map(([id, config]) => [id, { ...config, model: config.model.trim() }]))
    mutation.mutate({ ...values, ...(mode === 'batch' ? { case_ids: selectedCases } : {}), agents: settings })
  }
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
        <div className="page-heading"><div><Text className="eyebrow">NEW EXPERIMENT</Text><Title level={2}>{mode === 'batch' ? '新建批量实验' : '配置一次 MDT 运行'}</Title><Text type="secondary">{mode === 'batch' ? '每个 Agent 可使用不同模型，所有选中病例共用本次配置。' : '病例原文保持只读；每个 Agent 的模型设置与最终产物一起记录。'}</Text></div></div>
        <Alert className="section-gap" type="info" showIcon title="运行会调用真实 Agent" description="完整流程依次执行 Semantic Graphing、四个并行专科、MDT 会前整合整合、团队讨论和最终报告；批量运行中单个病例失败不会中断其他病例。" />
        {casesQuery.isError && <Alert type="error" showIcon title="无法读取病例库" description={casesQuery.error.message} />}
        {modelQuery.isError && <Alert type="error" showIcon title="无法读取模型配置" description={modelQuery.error.message} />}
        <Form form={form} layout="vertical" disabled={mutation.isPending} initialValues={{ mode: initialMode, source: 'library', max_concurrency: 6, max_case_concurrency: 2, max_request_concurrency: 6 }} onFinish={submit}>
          <Form.Item name="mode" label="运行方式"><Radio.Group options={[{ value: 'single', label: '单个病例' }, { value: 'batch', label: '批量病例库运行' }]} /></Form.Item>
          <Card title={<Space><FileTextOutlined />病例输入</Space>} className="section-card">
            <Form.Item noStyle shouldUpdate={(before, after) => before.mode !== after.mode || before.source !== after.source}>
              {({ getFieldValue }) => getFieldValue('mode') === 'batch'
                ? <>
                  <Space wrap className="section-gap">
                    <Input placeholder="搜索病例 ID" aria-label="搜索病例" allowClear value={caseSearch} onChange={(event) => setCaseSearch(event.target.value)} />
                    <Button onClick={() => setSelectedCases((current) => [...new Set([...current, ...filteredCases.map((item) => item.id)])])}>全选{caseSearch ? '搜索结果' : '病例'}</Button>
                    <Button onClick={() => setSelectedCases([])}>清空选择</Button><Text>已选 {selectedCases.length} / {cases.length} 例</Text>
                  </Space>
                  <Table size="small" rowKey="id" loading={casesQuery.isLoading} dataSource={filteredCases} pagination={{ pageSize: 8 }} rowSelection={{ selectedRowKeys: selectedCases, onChange: setSelectedCases, preserveSelectedRowKeys: true, getCheckboxProps: () => ({ disabled: mutation.isPending }) }} columns={[{ title: '病例', dataIndex: 'id', width: 160 }, { title: '原文预览', dataIndex: 'preview', ellipsis: true }]} />
                </>
                : <><Form.Item name="source" label="输入方式"><Radio.Group options={[{ value: 'library', label: '病例库' }, { value: 'paste', label: '粘贴原文' }]} /></Form.Item>{getFieldValue('source') === 'library'
                  ? <Form.Item name="case_id" label="data/raw_cases" rules={[{ required: true }]}><Select showSearch placeholder="选择病例" options={cases.map((item) => ({ value: item.id, label: `${item.filename} · ${item.bytes} bytes` }))} /></Form.Item>
                  : <><Form.Item name="case_id" label="病例 ID" rules={[{ required: true, pattern: /^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$/ }]}><Input placeholder="例如 pilot-001" /></Form.Item><Form.Item name="raw_text" label="病例原文" rules={[{ required: true }]}><Input.TextArea rows={10} placeholder="粘贴去标识化病例原文" /></Form.Item></>}</>}
            </Form.Item>
            {mode !== 'batch' && <Form.Item name="max_concurrency" label="Semantic Graphing 最大并发" tooltip="仅影响语义图阶段的并行任务数"><InputNumber min={1} max={16} /></Form.Item>}
            {mode !== 'batch' && <Form.Item name="parent_run_id" label="前序会诊（后续资料时选择）" extra="新运行保存本次完整病例快照，并关联前序会诊；请提供包含补充信息的完整病例原文。"><Select allowClear disabled={!caseId} placeholder="独立运行，无前序关联" options={runs.filter(r => r.case_id === caseId).map(r => ({ value: r.id, label: r.id }))} /></Form.Item>}
            {mode === 'batch' && <>
              <Form.Item name="max_case_concurrency" label="同时运行病例数" extra="其余病例等待空位；单个病例失败不会中断其他病例。"><InputNumber min={1} max={16} /></Form.Item>
              <Collapse items={[{ key: 'advanced', label: '高级并发设置', children: <Row gutter={16}>
                <Col xs={24} sm={12}><Form.Item name="max_request_concurrency" label="模型请求并发上限" extra="限制服务中同时进行的模型请求；多个批次运行时采用最小上限。"><InputNumber min={1} max={64} /></Form.Item></Col>
                <Col xs={24} sm={12}><Form.Item name="max_concurrency" label="语义图任务并发上限"><InputNumber min={1} max={16} /></Form.Item></Col>
              </Row> }]} />
            </>}
          </Card>
          <Card title={<Space><SettingOutlined />Agent 模型矩阵</Space>} className="section-card">
            {mode === 'batch' && <Space wrap className="section-gap"><Input value={bulkModel} onChange={(event) => setBulkModel(event.target.value)} placeholder="输入统一模型名称" aria-label="统一模型" /><Button disabled={!bulkModel.trim() || !agents.length} onClick={() => setAgentConfig((current) => Object.fromEntries(agents.map((agent) => [agent.agent_id, { ...current[agent.agent_id], model: bulkModel.trim() }])))}>应用到所有 Agent</Button><Text type="secondary">应用后仍可逐个调整。</Text></Space>}
            <Table rowKey="agent_id" columns={columns} dataSource={agents} pagination={false} size="middle" />
          </Card>
          <Card className="section-card">
            <Row gutter={24} align="middle"><Col flex="auto"><Title level={5}>启动完整 MDT 流程</Title><Text type="secondary">Semantic Graphing → unit 分发 → 四专科并行评估 → MDT 会前整合 → 团队讨论 → 最终报告</Text></Col><Col><Button type="primary" htmlType="submit" size="large" loading={mutation.isPending} disabled={modelQuery.isLoading || modelQuery.isError || (mode === 'batch' && !selectedCases.length)}>{mode === 'batch' ? `开始批量运行（${selectedCases.length} 例）` : '开始运行'}</Button></Col></Row>
          </Card>
        </Form>
      </Content>
    </Layout>
  )
}
