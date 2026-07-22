import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Alert, Card, Empty, Segmented, Skeleton, Space, Tag, Typography } from 'antd'
import { api } from '../../api'
import { CitationGroup } from '../../components/Citation'
import { EmptyState } from '../../components/EmptyState'
import { QueryError } from '../../components/QueryState'

const { Paragraph, Text, Title } = Typography

const SPECIALTY_LABELS = {
  pulmonology: '呼吸科',
  thoracic_radiology: '胸部影像科',
  rheumatology: '风湿免疫科',
  pathology: '病理科',
}

const STATUS_LABELS = {
  support: '支持',
  supported: '支持',
  favored: '倾向',
  leaning: '倾向',
  possible: '可能',
  unclassifiable: '不能分类',
  not_assessable: '不可评价',
  not_applicable: '不适用',
  assessable: '可评价',
  partially_assessable: '部分可评价',
  consistent: '一致',
  partially_consistent: '部分一致',
  inconsistent: '不一致',
}

const STATUS_COLORS = {
  support: 'success',
  supported: 'success',
  favored: 'processing',
  leaning: 'processing',
  possible: 'warning',
  unclassifiable: 'default',
  not_assessable: 'default',
  not_applicable: 'default',
  assessable: 'success',
  partially_assessable: 'warning',
  consistent: 'success',
  partially_consistent: 'warning',
  inconsistent: 'error',
}

const ROLE_LABELS = {
  primary: '主要结论',
  important_alternative: '重要替代解释',
  cannot_safely_ignore: '不能安全忽略',
  scope_or_evaluability: '范围或可评价性',
  leading: '主导解释',
  not_currently_assessable: '当前不可评价',
}

const TYPE_LABELS = {
  working_diagnosis: '工作诊断', morphologic_pattern: '形态模式', etiologic_attribution: '病因归因',
  severity_or_risk: '严重度或风险', material_evaluability: '材料可评价性', imaging_interpretation: '影像解释',
  rheumatic_disease: '风湿病判断', ild_attribution: 'ILD 归因', progression: '进展判断',
  assessability: '可评价性', etiologic_association: '病因关联', other: '其他',
  mechanism: '机制', time: '时间', mechanism_and_time: '机制与时间',
  specialty_scope: '专科权限', missing_is_not_negative: '未提及不等于阴性', pattern_is_not_disease: '模式不等于疾病',
  association_is_not_causation: '相关不等于因果', evidence_sufficiency: '证据充分性',
  material_representativeness: '材料代表性',
}

const EVIDENCE_GROUPS = [
  ['supporting', '支持证据', 'green'],
  ['weakening', '削弱证据', 'red'],
  ['discriminating', '鉴别证据', 'blue'],
  ['background', '背景证据', 'default'],
]

const valueLabel = (value, labels = {}) => labels[value] || value?.replaceAll?.('_', ' ') || value

function EmptyList({ description = '当前无此项' }) {
  return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={description} />
}

function EvidenceGroups({ evidence = {}, guidelineEvidence = [] }) {
  const groups = EVIDENCE_GROUPS.filter(([key]) => evidence?.[key]?.length)
  if (!groups.length && !guidelineEvidence?.length) return null
  return (
    <div className="evidence-groups">
      {groups.map(([key, label, color]) => (
        <div className="evidence-group" key={key}>
          <Tag color={color}>{label}</Tag>
          <CitationGroup refs={evidence[key]} />
        </div>
      ))}
      {guidelineEvidence?.length > 0 && (
        <div className="evidence-group">
          <Tag color="purple">指南依据</Tag>
          <CitationGroup refs={guidelineEvidence} />
        </div>
      )}
    </div>
  )
}

function Section({ title, description, children }) {
  return (
    <section className="formal-section">
      <div className="formal-section-heading">
        <Title level={5}>{title}</Title>
        {description && <Text type="secondary">{description}</Text>}
      </div>
      {children}
    </section>
  )
}

function Conclusion({ item }) {
  return (
    <article className="formal-item conclusion-item">
      <Space size={[6, 6]} wrap>
        <Tag color={STATUS_COLORS[item.status]}>{valueLabel(item.status, STATUS_LABELS)}</Tag>
        {item.role && <Tag>{valueLabel(item.role, ROLE_LABELS)}</Tag>}
        {item.conclusion_type && <Tag>{valueLabel(item.conclusion_type, TYPE_LABELS)}</Tag>}
        <Text code>{item.conclusion_id}</Text>
      </Space>
      <Title level={5}>{item.statement}</Title>
      {item.medical_basis && <Paragraph>{item.medical_basis}</Paragraph>}
      {item.decision_impact && <Paragraph type="secondary"><Text strong>决策影响：</Text>{item.decision_impact}</Paragraph>}
      <EvidenceGroups evidence={item.evidence} guidelineEvidence={item.guideline_evidence} />
      {item.limitations?.length > 0 && <div className="limitation-note"><Text strong>结论限制：</Text>{item.limitations.join('；')}</div>}
    </article>
  )
}

function ProfessionalConclusions({ value }) {
  return (
    <Card title="专业结论与外部需求" className="section-card formal-output-card professional-card">
      <Section title="专科问题定位">
        <Paragraph className="lead-text">{value.specialty_question}</Paragraph>
        <Tag color={STATUS_COLORS[value.assessability]}>{valueLabel(value.assessability, STATUS_LABELS)}</Tag>
      </Section>

      <Section title="专业结论" description="主导结论、替代解释与当前不可评价方向均在此校准表达。">
        {value.conclusions?.length
          ? <div className="formal-list">{value.conclusions.map((item) => <Conclusion item={item} key={item.conclusion_id} />)}</div>
          : <EmptyList description="当前没有可形成的专业结论" />}
      </Section>

      <Section title="需要其他专科回答的问题">
        {value.interspecialty_questions?.length ? (
          <div className="formal-list">
            {value.interspecialty_questions.map((item, index) => (
              <article className="formal-item" key={`${item.target_specialty}-${item.question}-${index}`}>
                <Tag color="geekblue">请 {SPECIALTY_LABELS[item.target_specialty] || item.target_specialty} 回答</Tag>
                <Title level={5}>{item.question}</Title>
                <Paragraph>{item.why_it_matters}</Paragraph>
                {item.decision_unlocked && <Paragraph type="secondary"><Text strong>将影响：</Text>{item.decision_unlocked}</Paragraph>}
                <CitationGroup refs={item.related_evidence || []} />
              </article>
            ))}
          </div>
        ) : <EmptyList description="当前没有需要其他专科回答的问题" />}
      </Section>

      <Section title="决策相关证据缺口">
        {value.evidence_gaps?.length ? (
          <div className="formal-list">
            {value.evidence_gaps.map((item, index) => (
              <article className="formal-item" key={`${item.missing_information}-${index}`}>
                <Title level={5}>{item.missing_information}</Title>
                {item.available_information && <Paragraph><Text strong>已有信息：</Text>{item.available_information}</Paragraph>}
                <Paragraph><Text strong>缺口意义：</Text>{item.why_it_matters}</Paragraph>
                {item.decision_unlocked && <Paragraph type="secondary"><Text strong>补充后可改善：</Text>{item.decision_unlocked}</Paragraph>}
                <CitationGroup refs={item.related_evidence || []} />
              </article>
            ))}
          </div>
        ) : <EmptyList description="当前没有决策相关证据缺口" />}
      </Section>

      <Section title="本专科判断边界">
        {value.boundaries?.length
          ? <ul className="boundary-list">{value.boundaries.map((item, index) => <li key={`${item}-${index}`}>{item}</li>)}</ul>
          : <EmptyList description="未声明额外判断边界" />}
      </Section>
    </Card>
  )
}

function Candidate({ item }) {
  return (
    <article className="formal-item candidate-item">
      <Space size={[6, 6]} wrap>
        <Tag color={item.role === 'leading' ? 'blue' : 'default'}>{valueLabel(item.role, ROLE_LABELS)}</Tag>
        <Text code>{item.candidate_id}</Text>
      </Space>
      <Title level={5}>{item.explanation}</Title>
      {item.fit_summary && <Paragraph>{item.fit_summary}</Paragraph>}
      <EvidenceGroups evidence={item.evidence} guidelineEvidence={item.guideline_evidence} />
      {item.remaining_uncertainty && <div className="limitation-note"><Text strong>剩余不确定性：</Text>{item.remaining_uncertainty}</div>}
    </article>
  )
}

function ClinicalReasoning({ value }) {
  return (
    <Card title="临床推理论证" className="section-card formal-output-card reasoning-card">
      <Section title="问题表征"><Paragraph className="lead-text">{value.problem_representation}</Paragraph></Section>

      <Section title="候选解释">
        {value.candidate_explanations?.length
          ? <div className="formal-list">{value.candidate_explanations.map((item) => <Candidate item={item} key={item.candidate_id} />)}</div>
          : <EmptyList description="当前没有可比较的候选解释" />}
      </Section>

      <Section title="鉴别性证据比较">
        {value.evidence_comparisons?.length ? (
          <div className="formal-list">
            {value.evidence_comparisons.map((item) => (
              <article className="formal-item" key={item.comparison_id}>
                <Space size={[6, 6]} wrap>
                  <Tag color={{ supports: 'green', weakens: 'red', discriminates: 'blue', background: 'default' }[item.effect]}>{valueLabel(item.effect, { supports: '支持', weakens: '削弱', discriminates: '鉴别', background: '背景' })}</Tag>
                  {(item.candidate_ids || []).map((candidateId) => <Tag key={candidateId}>{candidateId}</Tag>)}
                </Space>
                <Paragraph>{item.interpretation}</Paragraph>
                <EvidenceGroups evidence={item.evidence} />
              </article>
            ))}
          </div>
        ) : <EmptyList description="当前没有单列的鉴别性证据比较" />}
      </Section>

      <Section title="机制与时间一致性">
        {value.consistency_checks?.length ? (
          <div className="formal-list">
            {value.consistency_checks.map((item) => (
              <article className="formal-item" key={item.check_id}>
                <Space size={[6, 6]} wrap><Tag>{valueLabel(item.dimension, TYPE_LABELS)}</Tag><Tag color={STATUS_COLORS[item.status]}>{valueLabel(item.status, STATUS_LABELS)}</Tag></Space>
                <Paragraph>{item.finding}</Paragraph>
                {item.implication && <Paragraph type="secondary"><Text strong>推理影响：</Text>{item.implication}</Paragraph>}
                <EvidenceGroups evidence={item.evidence} />
              </article>
            ))}
          </div>
        ) : <EmptyList description="当前没有可完成的一致性检验" />}
      </Section>

      <Section title="反证、限制与边界复核">
        {value.boundary_reviews?.length ? (
          <div className="formal-list">
            {value.boundary_reviews.map((item) => (
              <article className="formal-item" key={item.review_id}>
                <Tag color="orange">{valueLabel(item.boundary_type, TYPE_LABELS)}</Tag>
                <Paragraph>{item.finding}</Paragraph>
                {item.impact && <Paragraph type="secondary"><Text strong>结论影响：</Text>{item.impact}</Paragraph>}
                <EvidenceGroups evidence={item.evidence} />
              </article>
            ))}
          </div>
        ) : <EmptyList description="当前没有额外边界复核项" />}
      </Section>

      <Section title="综合理由"><Paragraph className="synthesis-text">{value.synthesis}</Paragraph></Section>
    </Card>
  )
}

function isFormalOutput(output) {
  return Boolean(
    output
    && Object.keys(output).length === 2
    && output.professional_conclusions
    && output.clinical_reasoning,
  )
}

export function SpecialtyWorkspace({ runId, run }) {
  const [active, setActive] = useState('pulmonology')
  const query = useQuery({
    queryKey: ['specialties', runId],
    queryFn: () => api.specialties(runId),
    refetchInterval: (current) => {
      const results = current.state.data?.results
      if (results?.length && results.every((item) => item.status === 'completed')) return false
      return ['failed', 'cancelled'].includes(run?.status) ? false : 3000
    },
  })

  if (query.isError) return <QueryError error={query.error} retry={query.refetch} />
  if (query.isLoading) return <Skeleton active paragraph={{ rows: 12 }} />

  const results = query.data?.results || []
  const selected = results.find((item) => item.specialty === active) || results[0]
  const output = selected?.output

  return (
    <div className="specialty-workspace">
      <div className="workspace-title">
        <div><Text className="eyebrow">SPECIALTY AGENTS</Text><Title level={3}>首轮专科评估</Title></div>
        <Segmented
          value={selected?.specialty || active}
          onChange={setActive}
          options={results.map((item) => ({
            value: item.specialty,
            label: <Space size={6}><span>{item.label}</span><span className={`specialty-status-dot ${item.status}`} aria-label={item.status === 'completed' ? '已完成' : '等待中'} /></Space>,
          }))}
        />
      </div>

      {!selected || !output ? (
        <EmptyState description={`${selected?.label || '该专科'}尚无首轮正式输出，页面会自动刷新`} />
      ) : !isFormalOutput(output) ? (
        <Alert
          type="warning"
          showIcon
          title="旧版专科输出不在此页面展示"
          description="该运行缺少新的“专业结论”和“临床推理论证”结构。原始历史产物仍可在“产物审计”中查看。"
        />
      ) : (
        <div className="formal-output-grid">
          <ProfessionalConclusions value={output.professional_conclusions} />
          <ClinicalReasoning value={output.clinical_reasoning} />
        </div>
      )}
    </div>
  )
}
