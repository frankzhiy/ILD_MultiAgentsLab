import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Alert, Card, Col, Input, Row, Segmented, Space, Statistic, Tag, Typography } from 'antd'
import { SearchOutlined } from '@ant-design/icons'
import { api } from '../../api'
import { EmptyState } from '../../components/EmptyState'
import { ObjectInspector } from '../../components/ObjectInspector'
import { QueryError } from '../../components/QueryState'

const { Text, Title } = Typography

function collectEvidence(value, path = '$', result = []) {
  if (!value || typeof value !== 'object') return result
  if (value.evidence || value.supporting_evidence || value.case_evidence || value.source_citations || value.guideline_citations) result.push({ path, value })
  if (Array.isArray(value)) value.forEach((item, index) => collectEvidence(item, `${path}[${index}]`, result))
  else Object.entries(value).forEach(([key, item]) => collectEvidence(item, `${path}.${key}`, result))
  return result
}

export function SpecialtyWorkspace({ runId }) {
  const query = useQuery({ queryKey: ['specialties', runId], queryFn: () => api.specialties(runId) })
  const [active, setActive] = useState('pulmonology')
  const [search, setSearch] = useState('')
  if (query.isError) return <QueryError error={query.error} retry={query.refetch} />
  const results = query.data?.results || []
  const selected = results.find((item) => item.specialty === active) || results[0]
  const evidence = useMemo(() => collectEvidence(selected?.output), [selected])
  const filteredOutput = search ? Object.fromEntries(Object.entries(selected?.output || {}).filter(([key, value]) => `${key} ${JSON.stringify(value)}`.toLowerCase().includes(search.toLowerCase()))) : selected?.output
  return (
    <div>
      <div className="workspace-title"><div><Text className="eyebrow">SPECIALTY AGENTS</Text><Title level={3}>专科工作区</Title></div><Segmented value={active} onChange={setActive} options={results.map((item) => ({ value: item.specialty, label: item.label }))} /></div>
      <Row gutter={14} className="metric-row">
        <Col span={6}><Card><Statistic title="Owned units" value={selected?.input_summary.owned_unit_count || 0} /></Card></Col>
        <Col span={6}><Card><Statistic title="Shared context" value={selected?.input_summary.shared_context_unit_count || 0} /></Card></Col>
        <Col span={6}><Card><Statistic title="Evidence locators" value={selected?.input_summary.available_locator_count || 0} /></Card></Col>
        <Col span={6}><Card><Statistic title="Traceable sections" value={evidence.length} /></Card></Col>
      </Row>
      {!selected?.output ? <EmptyState description={`${selected?.label || '该专科'}尚无首轮输出`} /> : (
        <>
          <Alert type="info" showIcon title="专科结论按原始 schema 展示" description="每个专科保留自己的问题结构与推理边界。证据、指南和主持人引用可点击追溯，不被压扁成统一诊断模板。" className="section-gap" />
          <Card title={<Space><Text strong>{selected.label} · 首轮输出</Text><Tag color="success">schema {selected.output.schema_version || 'v1'}</Tag></Space>} extra={<Input prefix={<SearchOutlined />} allowClear placeholder="在字段与结论中筛选" value={search} onChange={(event) => setSearch(event.target.value)} style={{ width: 260 }} />} className="section-card specialty-output-card">
            <ObjectInspector value={filteredOutput} />
          </Card>
          <Card title="结构化生成 trace" className="section-card trace-card"><ObjectInspector value={selected.trace || { message: '无 trace 产物' }} /></Card>
        </>
      )}
    </div>
  )
}
