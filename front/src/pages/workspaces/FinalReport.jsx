import { Card, Collapse, Descriptions, Empty, Space, Table, Tabs, Tag, Timeline, Typography } from 'antd'
import { CitationGroup, CitationSummary } from '../../components/Citation'
import { EvidenceGroups } from '../../components/EvidenceGroups'

const { Paragraph, Text, Title } = Typography

const DIMENSIONS = {
  ild_presence: 'ILD 存在性',
  radiologic_pattern: '影像学模式',
  histopathologic_pattern: '组织学模式',
  mdt_diagnosis: 'MDT 疾病诊断',
  etiologic_attribution: '病因归属',
  disease_behavior: '疾病行为 / PPF',
  acute_or_comorbid_factors: '急性问题与伴随因素',
}

const STATUS = {
  supported: ['支持', 'success'],
  favored: ['倾向', 'processing'],
  possible: ['可能', 'warning'],
  indeterminate: ['尚不确定', 'warning'],
  unclassifiable: ['不能分类', 'default'],
  not_assessable: ['不可评价', 'default'],
  not_applicable: ['不适用', 'default'],
}

const CONFIDENCE = {
  high: ['高', 'success'],
  moderate: ['中等', 'processing'],
  low: ['低', 'warning'],
  unknown: ['未知', 'default'],
  not_applicable: ['不适用', 'default'],
}

const SPECIALTIES = {
  pulmonology: '呼吸科',
  thoracic_radiology: '胸部影像科',
  rheumatology: '风湿免疫科',
  pathology: '病理科',
}

const REVIEW_OUTCOME = {
  accept_answer: '接受回答',
  accept_boundary: '接受判断边界',
  request_clarification: '请求澄清',
  request_corroboration: '请求佐证',
  flag_incompatibility: '发现不兼容',
  convert_to_evidence_need: '转为证据需求',
}

const DECISION_STATUS = {
  closed: '已闭环',
  closed_this_round: '本轮已闭环',
  waiting_for_new_evidence: '等待新证据',
  awaiting_answer: '等待回答',
  awaiting_requester_review: '等待提问专科复核',
  awaiting_clarification: '等待澄清',
  unresolved: '未解决',
}

const CONFLICT_OUTCOME = {
  resolved: '已解决',
  unresolved: '未解决',
  not_confirmed_as_formal_conflict: '未被主持人确认为正式冲突',
}

function LabeledTag({ label, value, labels }) {
  const [text, color] = labels[value] || [value || '未标记', 'default']
  return <Tag color={color}>{label ? `${label}：` : ''}{text}</Tag>
}

function caseEvidence(trace) {
  return Object.values(trace?.evidence || {}).flat()
}

function ClinicalLayer({ report }) {
  const clinical = report.clinical_report
  const traces = report.reasoning_trace || []
  const traceFor = (item) => traces.find((trace) => trace.claim_statement === item.statement)
  const columns = [
    {
      title: '诊断层级', dataIndex: 'dimension', width: 132,
      render: (value) => <Text strong>{DIMENSIONS[value] || value}</Text>,
    },
    {
      title: '当前判断', dataIndex: 'statement',
      render: (value, item) => <div><Paragraph>{value}</Paragraph>{item.limitations?.length > 0 && <Text type="secondary">限制：{item.limitations.join('；')}</Text>}</div>,
    },
    {
      title: '状态与信度', width: 136,
      render: (_, item) => <Space size={[4, 4]} wrap><LabeledTag label="状态" value={item.status} labels={STATUS} /><LabeledTag label="信度" value={item.confidence} labels={CONFIDENCE} /></Space>,
    },
    {
      title: '依据', width: 116,
      render: (_, item) => {
        const trace = traceFor(item)
        return <CitationSummary sourceCitations={trace?.source_citations} caseEvidence={caseEvidence(trace)} />
      },
    },
  ]
  const differentialColumns = [
    { title: '排序', dataIndex: 'rank', width: 64 },
    { title: '鉴别诊断', dataIndex: 'diagnosis', width: 200, render: (value) => <Text strong>{value}</Text> },
    { title: '信度', dataIndex: 'confidence', width: 100, render: (value) => <LabeledTag label="" value={value} labels={CONFIDENCE} /> },
    { title: '保留理由', dataIndex: 'rationale' },
  ]
  const needColumns = [
    { title: '状态', dataIndex: 'status', width: 84, render: (value) => <Tag color={value === 'missing' ? 'error' : value === 'partially_available' ? 'warning' : 'success'}>{value === 'missing' ? '缺失' : value === 'partially_available' ? '部分具备' : '已具备'}</Tag> },
    { title: '需要补充', dataIndex: 'required_information', width: '30%' },
    { title: '决策价值', render: (_, item) => <><Paragraph>{item.why_it_matters}</Paragraph><Text type="secondary">补充后：{item.decision_unlocked}</Text></> },
    { title: '依据', width: 116, render: (_, item) => <CitationSummary sourceCitations={item.source_citations} caseEvidence={caseEvidence(item)} /> },
  ]
  return <Space orientation="vertical" size="large" style={{ width: '100%' }}>
    <div className="final-report-summary">
      <Space size={[6, 6]} wrap>
        <LabeledTag label="总体信度" value={clinical.overall_confidence} labels={CONFIDENCE} />
        <Tag>诊断型报告</Tag>
      </Space>
      <Title level={4}>{clinical.overall_conclusion}</Title>
      <Paragraph>{clinical.integrated_summary}</Paragraph>
    </div>
    <div>
      <Title level={5}>分层诊断矩阵</Title>
      <Table className="final-report-table" rowKey="dimension" size="small" columns={columns} dataSource={clinical.diagnostic_matrix} pagination={false} tableLayout="fixed" scroll={{ x: 860 }} />
    </div>
    <div>
      <Title level={5}>鉴别诊断</Title>
      <Table rowKey="rank" size="small" columns={differentialColumns} dataSource={clinical.differential_diagnoses || []} pagination={false} locale={{ emptyText: '当前没有需要单列的鉴别诊断' }} />
    </div>
    <div>
      <Title level={5}>判断边界</Title>
      {report.assessment_boundaries?.length ? <Collapse items={report.assessment_boundaries.map((item, index) => ({
        key: item.boundary_id || index,
        label: <Space wrap><Text strong>{item.topic}</Text><LabeledTag label="" value={item.status} labels={STATUS} /></Space>,
        children: <div><Paragraph><Text strong>当前不能判断：</Text>{item.statement}</Paragraph><Paragraph><Text strong>原因：</Text>{item.reason}</Paragraph><Paragraph type="secondary"><Text strong>决策影响：</Text>{item.decision_impact}</Paragraph><EvidenceGroups evidence={item.evidence} guidelineEvidence={item.guideline_evidence} /><CitationGroup sourceCitations={item.source_citations} /></div>,
      }))} /> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="没有额外判断边界" />}
    </div>
    <div>
      <Title level={5}>证据需求</Title>
      <Table className="final-report-table" rowKey={(item) => item.need_id || item.required_information} size="small" columns={needColumns} dataSource={report.evidence_needs || []} pagination={false} tableLayout="fixed" scroll={{ x: 900 }} locale={{ emptyText: '没有待补充的关键证据' }} />
    </div>
  </Space>
}

function ReasoningLayer({ report }) {
  const traces = report.reasoning_trace || []
  if (!traces.length) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="该报告没有可用的逐条证据链" />
  return <div className="formal-list">
    {traces.map((trace) => <article className="formal-item" key={trace.claim_id}>
      <Space size={[6, 6]} wrap><Tag color="blue">{trace.claim_id}</Tag>{trace.chair_item_ids?.map((item) => <Tag key={item}>{item}</Tag>)}</Space>
      <Title level={5}>{trace.claim_statement}</Title>
      <Paragraph><Text strong>整合依据：</Text>{trace.medical_basis}</Paragraph>
      <EvidenceGroups evidence={trace.evidence} guidelineEvidence={trace.guideline_evidence} />
      <div className="chair-sources"><Text strong>专科意见来源：</Text><CitationGroup sourceCitations={trace.source_citations} /></div>
      {trace.limitations?.length > 0 && <div className="limitation-note"><Text strong>判断限制：</Text>{trace.limitations.join('；')}</div>}
    </article>)}
  </div>
}

function AuditLayer({ report }) {
  const audit = report.discussion_audit || {}
  const metrics = report.research_metrics || {}
  return <Space orientation="vertical" size="large" style={{ width: '100%' }}>
    <div>
      <Title level={5}>证据与讨论客观计数</Title>
      <Descriptions size="small" bordered column={3} items={[
        { key: 'specialty', label: '有专科来源的诊断条目', children: `${metrics.claims_with_specialty_citations || 0} / ${metrics.diagnostic_claims || 0}` },
        { key: 'patient', label: '有患者原文的诊断条目', children: `${metrics.claims_with_patient_evidence || 0} / ${metrics.diagnostic_claims || 0}` },
        { key: 'guideline', label: '有指南规则的诊断条目', children: `${metrics.claims_with_guideline_citations || 0} / ${metrics.diagnostic_claims || 0}` },
        { key: 'issues', label: '已闭环议题', children: `${metrics.closed_issues || 0} / ${metrics.discussion_issues || 0}` },
        { key: 'conflicts', label: '正式冲突：已解决 / 未解决', children: `${metrics.resolved_formal_conflicts || 0} / ${metrics.unresolved_formal_conflicts || 0}` },
        { key: 'boundaries', label: '明确判断边界', children: metrics.assessment_boundaries || 0 },
      ]} />
      <Paragraph type="secondary">这些是程序统计的覆盖与闭环数量，不代表诊断正确性或系统自评分。</Paragraph>
    </div>
    <div>
      <Title level={5}>议题级决策记录</Title>
      {audit.decisions?.length ? <Collapse items={audit.decisions.map((decision) => ({
        key: decision.issue_id,
        label: <Space wrap><Tag>{decision.issue_id}</Tag><Text strong>{decision.question}</Text><Tag color={decision.final_status === 'closed' || decision.final_status === 'closed_this_round' ? 'success' : 'warning'}>{DECISION_STATUS[decision.final_status] || decision.final_status}</Tag></Space>,
        children: <div>
          {decision.baseline_result && <Paragraph><Text strong>讨论前判断：</Text>{decision.baseline_result}</Paragraph>}
          {decision.why_it_matters && <Paragraph type="secondary"><Text strong>为什么重要：</Text>{decision.why_it_matters}</Paragraph>}
          <Timeline items={decision.rounds.map((item) => ({
            color: item.changed_from_previous ? 'blue' : 'green',
            content: <div className="discussion-audit-round"><Space wrap><Tag>第 {item.round_number} 轮</Tag><Tag>{SPECIALTIES[item.specialty] || item.specialty}</Tag><LabeledTag label="回答信度" value={item.confidence} labels={CONFIDENCE} /></Space><Paragraph><Text strong>专科回答：</Text>{item.answer || '未形成回答'}</Paragraph>{item.reviews?.map((review, index) => <Paragraph type="secondary" key={`${review.reviewer_specialty}-${index}`}><Text strong>{SPECIALTIES[review.reviewer_specialty] || review.reviewer_specialty}复核：</Text>{REVIEW_OUTCOME[review.outcome] || review.outcome}；{review.rationale}</Paragraph>)}<Paragraph><Text strong>主持人更新：</Text>{item.chair_result_after_round}</Paragraph></div>,
          }))} />
          {decision.final_result && <Paragraph><Text strong>最终处置：</Text>{decision.final_result}</Paragraph>}
          {decision.decision_impact && <Paragraph type="secondary"><Text strong>决策影响：</Text>{decision.decision_impact}</Paragraph>}
        </div>,
      }))} /> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="本次没有进入会中处理的议题" />}
    </div>
    <div>
      <Title level={5}>冲突历史</Title>
      {audit.conflicts?.length ? <div className="formal-list">{audit.conflicts.map((item) => <article className="formal-item" key={`${item.kind}-${item.issue_id}`}><Space wrap><Tag color={item.kind === 'formal_conflict' ? 'error' : 'warning'}>{item.kind === 'formal_conflict' ? '正式冲突' : '疑似不兼容'}</Tag><Tag>{CONFLICT_OUTCOME[item.outcome] || item.outcome}</Tag><Text code>{item.issue_id}</Text></Space><Title level={5}>{item.topic}</Title><Paragraph>{item.summary}</Paragraph><Text type="secondary">出现轮次：{item.first_round}—{item.last_round}</Text></article>)}</div> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="讨论中未记录正式冲突或疑似不兼容" />}
    </div>
    {audit.stop_reason && <Paragraph type="secondary"><Text strong>讨论停止原因：</Text>{audit.stop_reason}</Paragraph>}
  </Space>
}

function LegacyFinalReport({ report }) {
  return <>
    <Title level={5}>{report.primary_conclusion}</Title>
    <Paragraph><Text strong>诊断把握度：</Text>{report.diagnostic_confidence}</Paragraph>
    <Paragraph><Text strong>整合摘要：</Text>{report.integrated_summary}</Paragraph>
    <Paragraph><Text strong>讨论过程摘要：</Text>{report.discussion_summary}</Paragraph>
    <Tag color="warning">旧版报告：重新运行后可生成分层诊断与完整证据链</Tag>
  </>
}

export function FinalReport({ report }) {
  if (!report) return null
  const extra = <Space><Tag color="blue">{report.consensus_status}</Tag><Tag>共 {report.discussion_rounds} 轮</Tag></Space>
  return <Card title="最终 MDT 统一报告" className="section-card discussion-final-report" extra={extra}>
    {!report.clinical_report ? <LegacyFinalReport report={report} /> : <Tabs items={[
      { key: 'clinical', label: 'MDT 最终报告', children: <ClinicalLayer report={report} /> },
      { key: 'reasoning', label: '证据与整合依据', children: <ReasoningLayer report={report} /> },
      { key: 'audit', label: '讨论与研究审计', children: <AuditLayer report={report} /> },
    ]} />}
  </Card>
}
