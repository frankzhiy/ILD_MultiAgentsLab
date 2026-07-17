import { useQuery } from '@tanstack/react-query'
import { Alert, Card, Col, Collapse, Row, Space, Tag, Typography } from 'antd'
import { WarningOutlined } from '@ant-design/icons'
import { api } from '../../api'
import { CitationGroup } from '../../components/Citation'
import { EmptyState } from '../../components/EmptyState'
import { QueryError } from '../../components/QueryState'

const { Paragraph, Text, Title } = Typography

function Conclusion({ item }) {
  return <div className="conclusion-card"><Space wrap><Tag color="blue">{item.status || item.confidence || '结论'}</Tag>{item.label && <Text strong>{item.label}</Text>}</Space><Paragraph>{item.conclusion || item.text || item.summary || item.reasoning_summary}</Paragraph>{item.reasoning_summary && item.reasoning_summary !== item.text && <Paragraph type="secondary">{item.reasoning_summary}</Paragraph>}<CitationGroup sourceCitations={item.source_citations || []} caseEvidence={item.case_evidence || []} /></div>
}

export function ChairWorkspace({ runId }) {
  const query = useQuery({ queryKey: ['chair', runId], queryFn: () => api.chair(runId) })
  if (query.isError) return <QueryError error={query.error} retry={query.refetch} />
  const output = query.data?.output
  if (!output) return <><div className="workspace-title"><div><Text className="eyebrow">MDT CHAIR</Text><Title level={3}>主持人汇总</Title></div></div><EmptyState description="四专科未全部完成或主持人尚未生成汇总" /></>
  return (
    <div>
      <div className="workspace-title"><div><Text className="eyebrow">MDT CHAIR</Text><Title level={3}>主持人汇总</Title></div><Space><Tag color="success">{output.phase}</Tag><Tag>{output.schema_version}</Tag></Space></div>
      <Alert type="warning" showIcon title="这是可追溯的科研综合，不是无证据的最终裁决" description="主持人保留专科冲突、未解决问题和原始证据路径。点击 E / S 引用可回查病例语义图或专科输出。" className="section-gap" />
      <Row gutter={14}>
        {(output.specialty_summaries || []).map((summary) => <Col span={12} key={summary.specialty}><Card title={<Space><Text strong>{summary.specialty}</Text><Tag>{(summary.core_conclusions || []).length} conclusions</Tag></Space>} className="section-card chair-specialty-card">{(summary.core_conclusions || []).map((item, index) => <Conclusion item={item} key={index} />)}</Card></Col>)}
      </Row>
      <Card title={<Space><WarningOutlined />专科冲突</Space>} className="section-card">
        {(output.conflicts || []).length ? <Collapse items={output.conflicts.map((item) => ({ key: item.conflict_id, label: <Space><Tag color={item.status === 'resolved' ? 'success' : 'warning'}>{item.status}</Tag><Text strong>{item.conflict_id}</Text><Text>{item.topic || item.question}</Text></Space>, children: <><Paragraph>{item.analysis}</Paragraph><CitationGroup sourceCitations={item.source_citations || []} caseEvidence={item.case_evidence || []} /><div className="position-grid">{(item.positions || []).map((position, index) => <div key={index}><Text strong>{position.specialty}</Text><Paragraph>{position.position}</Paragraph><CitationGroup sourceCitations={position.source_citations || []} caseEvidence={position.case_evidence || []} /></div>)}</div></> }))} /> : <Text type="secondary">无显式冲突</Text>}
      </Card>
      <Card title="未解决问题" className="section-card open-issues-grid">
        {(output.open_issues || []).map((item) => <div className="open-issue" key={item.issue_id}><Space><Tag color="warning">{item.issue_id}</Tag><Text strong>{item.issue_type}</Text></Space><Title level={5}>{item.question}</Title><Paragraph>{item.current_barrier}</Paragraph><Text type="secondary">需要：{item.required_information_or_answer}</Text><CitationGroup sourceCitations={item.source_citations || []} caseEvidence={item.case_evidence || []} /></div>)}
      </Card>
    </div>
  )
}
