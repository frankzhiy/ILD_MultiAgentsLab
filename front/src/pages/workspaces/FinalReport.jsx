import { Card, Empty, Tabs, Typography } from 'antd'
import { CitationGroup } from '../../components/Citation'
import { EvidenceGroups } from '../../components/EvidenceGroups'

const { Paragraph, Text, Title } = Typography
const NEED_LEVEL = {
  blocking_boundary: '影响当前判断',
  non_blocking_refinement: '值得后续追索',
  limitation_only: '仅记录限制',
}

function ClinicalLayer({ report }) {
  const clinical = report.clinical_report
  const diagnosis = clinical.diagnostic_matrix?.find((item) => item.dimension === 'mdt_diagnosis')
  const narrative = clinical.clinical_narrative || [
    diagnosis?.statement || clinical.overall_conclusion,
    diagnosis?.medical_basis || clinical.integrated_summary,
    ...(clinical.secondary_judgments || []).flatMap((item) => [item.statement, item.medical_basis]),
  ].filter(Boolean).join('')
  return <Paragraph className="final-report-summary">{narrative}</Paragraph>
}

function ReasoningLayer({ report }) {
  const traces = report.reasoning_trace || []
  const needGroups = report.evidence_need_groups || []
  const needs = [
    ...needGroups.map((group) => ({
      ...group,
      need: (report.evidence_needs || []).find((item) =>
        item.source_refs?.some((ref) => group.source_refs?.includes(ref))),
    })),
    ...(report.evidence_needs || []).filter((need) => !needGroups.some((group) =>
      need.source_refs?.some((ref) => group.source_refs?.includes(ref)))).map((need) => ({
      ...need, decision_role: 'non_blocking_refinement', need,
    })),
  ]
  const guidelines = [...new Map(traces.flatMap((trace) => trace.guideline_evidence || [])
    .map((item) => [`${item.chunk_id}:${(item.quote_unit_ids || []).join(',')}`, item])).values()]
  return <div className="formal-list">
    {traces.length ? traces.map((trace) => <article className="formal-item" key={trace.claim_id}>
      <Title level={5}>{trace.claim_statement}</Title>
      <Paragraph><Text strong>诊断依据：</Text>{trace.medical_basis}</Paragraph>
      <EvidenceGroups evidence={trace.evidence} guidelineEvidence={trace.guideline_evidence} />
      <div className="chair-sources"><Text strong>专科意见：</Text><CitationGroup sourceCitations={trace.source_citations} /></div>
    </article>) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无逐条诊断依据" />}
    <Title level={5}>判断边界</Title>
    {(report.assessment_boundaries || []).length
      ? report.assessment_boundaries.map((item, index) => <Paragraph key={item.boundary_id || index}><Text strong>{item.topic}：</Text>{item.statement}</Paragraph>)
      : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="目前无额外判断边界" />}
    <Title level={5}>资料缺口与证据需求</Title>
    {needs.length ? needs.map((item, index) => <div key={item.group_id || item.need_id || index}>
      <Paragraph><Text strong>{NEED_LEVEL[item.decision_role]}：</Text>{item.required_information}
        {item.need && <>；{item.need.why_it_matters}；{item.need.decision_unlocked}</>}
      </Paragraph>
      <CitationGroup sourceCitations={item.source_citations || item.need?.source_citations} />
    </div>)
      : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="目前无需要单列的资料缺口" />}
    <Title level={5}>指南依据</Title>
    {guidelines.length ? <CitationGroup refs={guidelines} /> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="本次报告无可核验的指南引用" />}
    <Title level={5}>未解决的专科分歧</Title>
    {(report.unresolved_conflicts || []).length
      ? report.unresolved_conflicts.map((item, index) => <Paragraph key={item.conflict_id || index}>{item.topic || item.summary}</Paragraph>)
      : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="目前无未解决的正式分歧" />}
  </div>
}

function LegacyFinalReport({ report }) {
  return <>
    <Title level={5}>{report.primary_conclusion}</Title>
    <Paragraph><Text strong>诊断把握度：</Text>{report.diagnostic_confidence}</Paragraph>
    <Paragraph><Text strong>整合摘要：</Text>{report.integrated_summary}</Paragraph>
  </>
}

export function FinalReport({ report }) {
  if (!report) return null
  return <Card title="最终 MDT 统一报告" className="section-card discussion-final-report">
    {!report.clinical_report ? <LegacyFinalReport report={report} /> : <Tabs items={[
      { key: 'clinical', label: 'MDT 最终报告', children: <ClinicalLayer report={report} /> },
      { key: 'reasoning', label: '诊断依据', children: <ReasoningLayer report={report} /> },
    ]} />}
  </Card>
}
