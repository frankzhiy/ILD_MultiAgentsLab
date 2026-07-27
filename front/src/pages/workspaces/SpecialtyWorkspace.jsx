import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Alert, Card, Empty, Segmented, Skeleton, Space, Tag, Typography } from 'antd'
import { api } from '../../api'
import { CitationGroup } from '../../components/Citation'
import { EvidenceGroups } from '../../components/EvidenceGroups'
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
  primary: '主要判断',
  important_alternative: '重要替代解释',
  cannot_safely_ignore: '不能安全忽略',
  scope_or_evaluability: '范围或可评价性判断',
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

const valueLabel = (value, labels = {}) => labels[value] || value?.replaceAll?.('_', ' ') || value

function EmptyList({ description = '当前无此项' }) {
  return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={description} />
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

function Assessment({ item }) {
  return (
    <article className="formal-item conclusion-item">
      <Space size={[6, 6]} wrap>
        <Tag color={STATUS_COLORS[item.status]}>{valueLabel(item.status, STATUS_LABELS)}</Tag>
        {item.role && <Tag>{valueLabel(item.role, ROLE_LABELS)}</Tag>}
        {item.assessment_type && <Tag>{valueLabel(item.assessment_type, TYPE_LABELS)}</Tag>}
        <Text code>{item.assessment_id}</Text>
      </Space>
      <Title level={5}>{item.statement}</Title>
      {item.medical_basis && <Paragraph>{item.medical_basis}</Paragraph>}
      {item.decision_impact && <Paragraph type="secondary"><Text strong>决策影响：</Text>{item.decision_impact}</Paragraph>}
      <EvidenceGroups evidence={item.evidence} guidelineEvidence={item.guideline_evidence} />
      {item.limitations?.length > 0 && <div className="limitation-note"><Text strong>判断限制：</Text>{item.limitations.join('；')}</div>}
    </article>
  )
}

function SpecialtyAssessments({ value }) {
  return (
    <Card title="专科初步判断" className="section-card formal-output-card professional-card">
      <Section title="专科问题定位">
        <Paragraph className="lead-text">{value.specialty_question}</Paragraph>
        <Tag color={STATUS_COLORS[value.assessability]}>{valueLabel(value.assessability, STATUS_LABELS)}</Tag>
      </Section>

      <Section title="初步判断" description="每项判断同时说明推理依据、反证、限制和决策影响。">
        {value.assessments?.length
          ? <div className="formal-list">{value.assessments.map((item) => <Assessment item={item} key={item.assessment_id} />)}</div>
          : <EmptyList description="当前没有可形成的专科初步判断" />}
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

function InterspecialtyQuestionsCard({ value }) {
  return (
    <Card title="需其他专科回答的问题" className="section-card formal-output-card reasoning-card">
      <Section title="需其他专科回答的问题" description="仅列出需要其他专科基于其专业视角作出判断的问题。">
        {value.questions?.length ? (
          <div className="formal-list">
            {value.questions.map((item, index) => (
              <article className="formal-item" key={`${item.target_specialty}-${item.question}-${index}`}>
                <Tag color="geekblue">请 {SPECIALTY_LABELS[item.target_specialty] || item.target_specialty} 回答</Tag>
                <Title level={5}>{item.question}</Title>
                <Paragraph><Text strong>提问理由：</Text>{item.why_it_matters}</Paragraph>
                {item.decision_unlocked && <Paragraph type="secondary"><Text strong>将影响：</Text>{item.decision_unlocked}</Paragraph>}
                {item.related_assessment_ids?.length > 0 && <Paragraph type="secondary"><Text strong>关联初步判断：</Text>{item.related_assessment_ids.join('、')}</Paragraph>}
                <CitationGroup refs={item.related_evidence || []} />
              </article>
            ))}
          </div>
        ) : <EmptyList description="当前没有需其他专科回答的问题" />}
      </Section>
    </Card>
  )
}

function isFormalOutput(output) {
  return Boolean(
    output
    && Object.keys(output).length === 2
    && output.specialty_assessments
    && output.interspecialty_questions,
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
          description="该运行缺少新的“专科初步判断”和“需其他专科回答的问题”结构。原始历史产物仍可在“产物审计”中查看。"
        />
      ) : (
        <div className="formal-output-grid">
          <SpecialtyAssessments value={output.specialty_assessments} />
          <InterspecialtyQuestionsCard value={output.interspecialty_questions} />
        </div>
      )}
    </div>
  )
}
