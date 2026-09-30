import { RightOutlined } from '@ant-design/icons'
import { Card, Empty, Space, Table, Tabs, Tag, Typography } from 'antd'
import { CitationGroup } from '../../components/Citation'
import { EvidenceGroups } from '../../components/EvidenceGroups'

const { Paragraph, Text, Title } = Typography
const DISEASE_CATEGORY = {
  known_cause: '已知病因的 ILD',
  idiopathic_interstitial_pneumonia: '特发性间质性肺炎',
  granulomatous_ild: '肉芽肿性 ILD',
  other_ild: '其他 ILD',
  unclassifiable: '暂不能进一步分类',
}
const DIMENSION = {
  ild_presence: 'ILD 是否存在',
  radiologic_pattern: '影像学模式',
  histopathologic_pattern: '组织学模式',
  mdt_diagnosis: 'MDT 疾病诊断',
  etiologic_attribution: '病因归属',
  disease_behavior: '疾病行为',
  acute_or_comorbid_factors: '急性或共病因素',
}
const STATUS = {
  supported: '支持', favored: '倾向', possible: '可能', indeterminate: '不确定',
  unclassifiable: '不能分类', not_assessable: '不可评价', not_applicable: '不适用',
}
const CONFIDENCE = {
  high: '高把握度', moderate: '中等把握度', low: '低把握度',
  unknown: '把握度未知', not_applicable: '不适用',
}
const ROLE = {
  primary: '主要判断', important_alternative: '重要备选',
  cannot_safely_ignore: '不可忽略', boundary: '判断边界',
}
const NEED_LEVEL = {
  blocking_boundary: '影响当前判断',
  non_blocking_refinement: '值得后续追索',
  limitation_only: '仅记录限制',
}
const STATUS_COLOR = {
  supported: 'success', favored: 'processing', possible: 'warning', indeterminate: 'warning',
  unclassifiable: 'default', not_assessable: 'default', not_applicable: 'default',
}
const ROLE_COLOR = {
  primary: 'blue', important_alternative: 'purple', cannot_safely_ignore: 'volcano', boundary: 'default',
}
const NEED_COLOR = {
  blocking_boundary: 'red', non_blocking_refinement: 'gold', limitation_only: 'default',
}
const SCOPE = {
  clinical: '临床', imaging: '影像', pathology: '病理', rheumatology: '风湿免疫',
  progression: '疾病进展', etiology: '病因', other: '其他',
}
const SPECIALTY = {
  pulmonology: '呼吸科', thoracic_radiology: '胸部影像科',
  rheumatology: '风湿免疫科', pathology: '病理科', chair: '主持人',
}

function ClinicalReport({ report }) {
  const clinical = report.clinical_report
  const matrix = Object.fromEntries((clinical.diagnostic_matrix || []).map((item) => [item.dimension, item]))
  const diagnosis = matrix.mdt_diagnosis
  const patternAndCourse = [matrix.radiologic_pattern, matrix.histopathologic_pattern, matrix.disease_behavior]
    .filter((item) => item && item.status !== 'not_applicable' && (item.dimension !== 'histopathologic_pattern' || item.status !== 'not_assessable'))

  return <>
    <section className="report-primary">
      {diagnosis?.disease_category && <Tag color="blue">{DISEASE_CATEGORY[diagnosis.disease_category]}</Tag>}
      <Title level={3}>{diagnosis?.statement || clinical.overall_conclusion}</Title>
      <Paragraph>{diagnosis?.medical_basis || clinical.integrated_summary}</Paragraph>
      {patternAndCourse.length > 0 && <Paragraph className="report-pattern-course">{patternAndCourse.map((item) => item.statement).join(' ')}</Paragraph>}
    </section>
    {(clinical.secondary_judgments || []).length > 0 && <section className="report-secondary">
      <Title level={4}>其他重要判断</Title>
      {clinical.secondary_judgments.map((item, index) => <article key={index} className="report-secondary-item">
        <Paragraph strong>{item.statement}</Paragraph>
        <Paragraph>{item.medical_basis}</Paragraph>
      </article>)}
    </section>}
  </>
}

function DiagnosticMatrix({ report }) {
  const clinical = report.clinical_report
  const items = Object.keys(DIMENSION)
    .map((dimension) => (clinical.diagnostic_matrix || []).find((item) => item.dimension === dimension))
    .filter(Boolean)
  const columns = [
    {
      title: '诊断层级', dataIndex: 'dimension', width: 155, fixed: 'left',
      render: (dimension, _item, index) => <div className="matrix-dimension"><Text type="secondary">0{index + 1}</Text><Text strong>{DIMENSION[dimension] || dimension}</Text></div>,
    },
    {
      title: '当前判断', dataIndex: 'statement', width: 310,
      render: (statement, item) => <div className="matrix-conclusion">
        <Text strong>{statement}</Text>
        {(item.disease_category || item.specific_disease) && <Space size={[4, 4]} wrap>
          {item.disease_category && <Tag color="blue">{DISEASE_CATEGORY[item.disease_category] || item.disease_category}</Tag>}
          {item.specific_disease && <Tag color="cyan">{item.specific_disease}</Tag>}
        </Space>}
      </div>,
    },
    {
      title: '状态', key: 'state', width: 145,
      render: (_, item) => <Space size={[4, 4]} wrap>
        {item.status && <Tag color={STATUS_COLOR[item.status]}>{STATUS[item.status] || item.status}</Tag>}
        {item.confidence && <Tag>{CONFIDENCE[item.confidence] || item.confidence}</Tag>}
        {item.role && <Tag color={ROLE_COLOR[item.role]}>{ROLE[item.role] || item.role}</Tag>}
      </Space>,
    },
    {
      title: '医学依据', dataIndex: 'medical_basis', width: 350,
      render: (basis) => <Text>{basis}</Text>,
    },
    {
      title: '判断限制', dataIndex: 'limitations', width: 280,
      render: (limitations) => limitations?.length
        ? <ul className="matrix-limitations">{limitations.map((item, index) => <li key={index}>{item}</li>)}</ul>
        : <Text type="secondary">无额外限制</Text>,
    },
  ]
  return <div className="report-matrix">
    <div className="report-tab-intro">
      <div><Title level={4}>七层诊断判断</Title><Text type="secondary">从疾病存在、形态模式到疾病诊断、病因及病程，横向核对结论、把握度与限制。</Text></div>
      <Tag color="blue">7 个固定层级</Tag>
    </div>
    <Table
      className="diagnostic-matrix-table"
      columns={columns}
      dataSource={items}
      rowKey="dimension"
      pagination={false}
      tableLayout="fixed"
      scroll={{ x: 1240 }}
      rowClassName={(item) => item.dimension === 'mdt_diagnosis' ? 'matrix-primary-row' : ''}
    />
    <section className="matrix-differentials">
      <div className="report-section-heading"><div><Text className="section-kicker">DIFFERENTIAL</Text><Title level={5}>鉴别诊断排序</Title></div><Tag>{(clinical.differential_diagnoses || []).length} 项</Tag></div>
      {(clinical.differential_diagnoses || []).length
        ? <div className="differential-list">{clinical.differential_diagnoses.map((item) => <article key={item.rank}>
          <span className="differential-rank">{item.rank}</span>
          <div><Text strong>{item.diagnosis}</Text><Paragraph>{item.rationale}</Paragraph></div>
          <Tag>{CONFIDENCE[item.confidence] || item.confidence}</Tag>
        </article>)}</div>
        : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="目前无有据可排的鉴别诊断" />}
    </section>
  </div>
}

function evidenceCount(trace) {
  const evidence = trace.evidence || {}
  return evidence.links?.length || evidence.evidence_relations?.length
    || ['supporting', 'weakening', 'discriminating', 'qualifying', 'background']
      .reduce((total, key) => total + (evidence[key]?.length || 0), 0)
}

function ReportSectionHeading({ kicker, title, description, count, color }) {
  return <div className="report-section-heading">
    <div><Text className="section-kicker">{kicker}</Text><Title level={4}>{title}</Title>{description && <Text type="secondary">{description}</Text>}</div>
    <Tag color={color}>{count} 项</Tag>
  </div>
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
  const traceColumns = [
    {
      title: '诊断判断', dataIndex: 'claim_statement', width: '34%',
      render: (statement) => <Text strong>{statement}</Text>,
    },
    { title: '医学依据', dataIndex: 'medical_basis', width: '46%' },
    {
      title: '可追溯来源', key: 'sources', width: '20%',
      render: (_, trace) => <Space size={[4, 4]} wrap>
        <Tag color="cyan">病历 {evidenceCount(trace)}</Tag>
        <Tag color="purple">指南 {trace.guideline_evidence?.length || 0}</Tag>
        <Tag color="blue">专科 {trace.source_citations?.length || 0}</Tag>
      </Space>,
    },
  ]
  const boundaries = report.assessment_boundaries || []
  const conflicts = report.unresolved_conflicts || []
  return <div className="report-reasoning">
    <div className="reasoning-summary">
      <div><Text type="secondary">诊断判断</Text><strong>{traces.length}</strong></div>
      <div><Text type="secondary">判断边界</Text><strong>{boundaries.length}</strong></div>
      <div><Text type="secondary">证据需求</Text><strong>{needs.length}</strong></div>
      <div><Text type="secondary">未解决分歧</Text><strong>{conflicts.length}</strong></div>
    </div>

    <section className="report-panel-section evidence-chain-section">
      <ReportSectionHeading kicker="EVIDENCE CHAIN" title="逐条诊断依据" description="每一项结论对应医学依据及可回溯的病历、指南和专科意见。" count={traces.length} color="blue" />
      {traces.length ? <Table
        className="reasoning-table"
        size="middle"
        columns={traceColumns}
        dataSource={traces}
        rowKey="claim_id"
        pagination={false}
        tableLayout="fixed"
        scroll={{ x: 900 }}
        expandable={{
          expandRowByClick: true,
          expandedRowRender: (trace) => <div className="reasoning-source-detail">
            <EvidenceGroups evidence={trace.evidence} guidelineEvidence={trace.guideline_evidence} />
            <div className="chair-sources"><Text strong>专科意见：</Text><CitationGroup sourceCitations={trace.source_citations} /></div>
            {trace.limitations?.length > 0 && <div className="limitation-note"><Text strong>该项限制：</Text>{trace.limitations.join('；')}</div>}
          </div>,
        }}
      /> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无逐条诊断依据" />}
    </section>

    <section className="report-panel-section boundary-section">
      <ReportSectionHeading kicker="ASSESSMENT BOUNDARIES" title="当前判断边界" description="明确哪些问题现在不能回答、原因是什么，以及它会影响哪一步决策。" count={boundaries.length} color="gold" />
      {boundaries.length ? <div className="report-boundary-grid">{boundaries.map((item, index) => <article key={item.boundary_id || index}>
        <div className="report-item-topline"><Space size={[4, 4]} wrap><Tag color="gold">{SCOPE[item.scope] || item.scope || '判断边界'}</Tag>{item.status && <Tag>{STATUS[item.status] || item.status}</Tag>}</Space><Text type="secondary">{item.boundary_id}</Text></div>
        <Title level={5}>{item.topic}</Title>
        <Paragraph className="boundary-statement">{item.statement}</Paragraph>
        <div className="boundary-detail-grid">
          <div><Text type="secondary">为什么不能判断</Text><Paragraph>{item.reason || '当前证据不足。'}</Paragraph></div>
          <div><Text type="secondary">对 MDT 的影响</Text><Paragraph>{item.decision_impact || '限制当前判断的明确度。'}</Paragraph></div>
        </div>
        <EvidenceGroups evidence={item.evidence} guidelineEvidence={item.guideline_evidence} />
        <CitationGroup sourceCitations={item.source_citations} />
      </article>)}</div> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="目前无额外判断边界" />}
    </section>

    <section className="report-panel-section evidence-needs-section">
      <ReportSectionHeading kicker="EVIDENCE NEEDS" title="待补充证据与决策价值" description="把资料缺口直接连接到它影响的判断，区分真正待办与仅记录的限制。" count={needs.length} color="orange" />
      {needs.length ? <div className="evidence-needs-list">{needs.map((item, index) => {
        const need = item.need || item
        return <article key={item.group_id || item.need_id || index}>
          <div className="report-item-topline"><Tag color={NEED_COLOR[item.decision_role]}>{NEED_LEVEL[item.decision_role] || '证据需求'}</Tag><Text type="secondary">{item.group_id || item.need_id}</Text></div>
          <div className="report-need-flow">
            <div><Text type="secondary">需要补充什么</Text><Paragraph>{item.required_information}</Paragraph></div>
            <RightOutlined />
            <div><Text type="secondary">为什么影响判断</Text><Paragraph>{need.why_it_matters || need.remaining_information || '用于说明当前判断的证据边界。'}</Paragraph></div>
            <RightOutlined />
            <div><Text type="secondary">补齐后能明确什么</Text><Paragraph>{need.decision_unlocked || (item.decision_role === 'limitation_only' ? '记录当前限制，不形成新增待办。' : '提高当前判断的明确度。')}</Paragraph></div>
          </div>
          <CitationGroup sourceCitations={item.source_citations || need.source_citations} />
        </article>
      })}</div> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="目前无需要单列的资料缺口" />}
    </section>

    <section className="report-panel-section conflict-section">
      <ReportSectionHeading kicker="OPEN DISAGREEMENTS" title="未解决的专科分歧" description="并列呈现不同专业立场、分歧影响和解决条件。" count={conflicts.length} color="red" />
      {conflicts.length ? <div className="report-conflict-list">{conflicts.map((item, index) => <article key={item.conflict_id || index}>
        <div className="report-item-topline"><Tag color="red">未解决</Tag><Text type="secondary">{item.conflict_id}</Text></div>
        <Title level={5}>{item.topic || item.summary}</Title>
        {item.comparison_target && <Paragraph><Text strong>比较目标：</Text>{item.comparison_target}</Paragraph>}
        {item.positions?.length > 0 && <div className="report-conflict-positions">{item.positions.map((position, positionIndex) => <div key={`${position.specialty}-${positionIndex}`}>
          <Tag color="volcano">{SPECIALTY[position.specialty] || position.specialty}</Tag>
          <Paragraph>{position.position}</Paragraph>
        </div>)}</div>}
        {item.decision_impact && <Paragraph><Text strong>决策影响：</Text>{item.decision_impact}</Paragraph>}
        {item.resolution_requirement && <div className="conflict-resolution"><Text strong>解决条件：</Text>{item.resolution_requirement}</div>}
      </article>)}</div> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="目前无未解决的正式分歧" />}
    </section>

    <section className="report-panel-section guideline-section">
      <ReportSectionHeading kicker="GUIDELINES" title="指南依据" description="报告中实际引用且可核验的指南片段。" count={guidelines.length} color="purple" />
      {guidelines.length ? <CitationGroup refs={guidelines} /> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="本次报告无可核验的指南引用" />}
    </section>
  </div>
}

function LegacyReport({ report }) {
  return <section className="report-primary">
    <Title level={3}>{report.primary_conclusion}</Title>
    <Paragraph><Text strong>诊断把握度：</Text>{report.diagnostic_confidence}</Paragraph>
    <Paragraph>{report.integrated_summary}</Paragraph>
  </section>
}

export function FinalReport({ report }) {
  if (!report) return null
  return <Card className="section-card final-report-card">
    {report.clinical_report ? <Tabs items={[
      { key: 'clinical', label: 'MDT 最终报告', children: <ClinicalReport report={report} /> },
      { key: 'matrix', label: '分层诊断矩阵', children: <DiagnosticMatrix report={report} /> },
      { key: 'reasoning', label: '诊断依据与证据缺口', children: <ReasoningLayer report={report} /> },
    ]} /> : <LegacyReport report={report} />}
  </Card>
}
