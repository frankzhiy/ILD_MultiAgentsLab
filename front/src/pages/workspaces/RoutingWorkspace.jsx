import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Card, Input, Table, Tag, Typography } from 'antd'
import { CheckOutlined, SearchOutlined } from '@ant-design/icons'
import { api } from '../../api'
import { Citation } from '../../components/Citation'
import { QueryError } from '../../components/QueryState'

const { Text, Title } = Typography
export function RoutingWorkspace({ runId }) {
  const query = useQuery({ queryKey: ['routing', runId], queryFn: () => api.routing(runId) })
  const [search, setSearch] = useState('')
  if (query.isError) return <QueryError error={query.error} retry={query.refetch} />
  const specialties = query.data?.specialties || []
  const summary = query.data?.summary || {}
  const units = query.data?.units || []
  const filtered = units.filter((item) => !search || `${item.graph_unit_id} ${item.text}`.toLowerCase().includes(search.toLowerCase()))
  const columns = [
    { title: '证据单元 / Graph Unit', dataIndex: 'graph_unit_id', width: 170, fixed: 'left', render: (value, row) => <Citation value={{ graph_unit_id: value, segment_id: row.segment_id, quote: row.text }} /> },
    { title: '证据单元原文', dataIndex: 'text', width: 300, ellipsis: true },
    ...specialties.map((specialty) => ({ title: specialty.label, key: specialty.specialty, width: 125, align: 'center', render: (_, unit) => unit.mdt_specialty.includes('shared_context') || unit.mdt_specialty.includes(specialty.specialty) ? <CheckOutlined className="routing-check" aria-label={`${specialty.label}主责`} /> : null })),
    { title: '定位状态', dataIndex: 'locator_status', width: 105, align: 'center', render: (value) => value === 'degraded' ? <Tag color="warning">降级</Tag> : <Tag color="success">可定位</Tag> },
  ]
  return (
    <div>
      <div className="workspace-title"><div><Text className="eyebrow">EVIDENCE ROUTING</Text><Title level={3}>证据分发</Title></div></div>
      <Card size="small" className="routing-summary">
        <span>共 <strong>{summary.unit_count || 0}</strong> 个证据单元</span><span>通用背景 <strong>{summary.shared_context_unit_count || 0}</strong></span><span>专科归属证据 <strong>{summary.specialty_unit_count || 0}</strong></span><span><strong>{summary.available_locator_count || 0}</strong> / {summary.unit_count || 0} 可定位</span>
      </Card>
      <Card title="Unit × 专科归属矩阵" extra={<Input prefix={<SearchOutlined />} allowClear placeholder="搜索 unit" value={search} onChange={(event) => setSearch(event.target.value)} style={{ width: 240 }} />} className="section-card">
        <Table rowKey="graph_unit_id" size="small" loading={query.isLoading} columns={columns} dataSource={filtered} scroll={{ x: 980, y: 520 }} pagination={false} />
      </Card>
    </div>
  )
}
