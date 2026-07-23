import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { PlayCircleOutlined, ReloadOutlined, RightOutlined } from '@ant-design/icons'
import { Alert, Button, Card, Empty, Skeleton, Space, Tabs, Tag, Typography } from 'antd'
import { api } from '../../api'
import { CitationGroup } from '../../components/Citation'
import { EvidenceGroups } from '../../components/EvidenceGroups'
import { QueryError } from '../../components/QueryState'
import { StatusTag } from '../../components/StatusTag'

const { Paragraph, Text, Title } = Typography

const SPECIALTIES = {
  pulmonology: '呼吸科',
  thoracic_radiology: '胸部影像科',
  rheumatology: '风湿免疫科',
  pathology: '病理科',
  chair: '主持人',
  case_data: '病例资料',
}

const QUESTION_STATUS = {
  answered: ['已回答', 'success'],
  partially_answered: ['部分回答', 'warning'],
  unanswered: ['待回答', 'default'],
  disputed: ['存在分歧', 'error'],
}

const RESPONSE_STATUS = {
  none_responded: ['尚未回应', 'default'],
  partially_responded: ['部分专科已回应', 'warning'],
  all_responded: ['目标专科均已回应', 'success'],
}

const RESOLUTION_STATUS = {
  resolved: ['已解决', 'success'],
  partially_resolved: ['部分解决', 'warning'],
  unresolved: ['尚未解决', 'default'],
  blocked_by_evidence: ['受证据缺口阻断', 'warning'],
  disputed: ['存在分歧', 'error'],
}

const NEED_STATUS = {
  available: ['已满足', 'success'],
  partially_available: ['部分满足', 'warning'],
  missing: ['缺失', 'error'],
}

const CONFLICT_STATUS = {
  unresolved: ['未解决', 'error'],
  pending_clarification: ['等待澄清', 'warning'],
  pending_evidence: ['等待证据', 'warning'],
  pending_clarification_and_evidence: ['等待澄清与证据', 'warning'],
}

const CONFLICT_DOMAIN = {
  diagnostic_interpretation: '诊断解释',
  morphologic_interpretation: '形态/影像解释',
  etiologic_attribution: '病因归因',
  severity_or_trajectory: '严重度/病程',
  assessability_or_scope: '可评价性/证据范围',
}

const CONFLICT_STANCE = {
  affirms: ['肯定该命题', 'success'],
  denies: ['否定该命题', 'error'],
}

const CONCLUSION_STATUS = {
  supported: ['支持', 'success'],
  favored: ['倾向', 'processing'],
  possible: ['可能', 'warning'],
  unclassifiable: ['不能分类', 'default'],
  not_assessable: ['不可评价', 'default'],
  not_applicable: ['不适用', 'default'],
}

const CONCLUSION_ROLE = {
  primary: '主要结论',
  important_alternative: '重要替代解释',
  cannot_safely_ignore: '不能安全忽略',
  scope_or_evaluability: '范围或可评价性',
}

const CONCLUSION_TYPE = {
  working_diagnosis: '工作诊断',
  morphologic_pattern: '形态模式',
  etiologic_attribution: '病因归属',
  severity_or_risk: '严重程度或风险',
  material_evaluability: '材料可评价性',
  imaging_interpretation: '影像解释',
  rheumatic_disease: '风湿病判断',
  ild_attribution: 'ILD 归因',
  progression: '进展判断',
  assessability: '可评价性',
  etiologic_association: '病因关联',
  other: '其他',
}

const BOUNDARY_STATUS = {
  indeterminate: ['尚不确定', 'warning'],
  not_assessable: ['不可评价', 'default'],
  unclassifiable: ['不能分类', 'default'],
  not_applicable: ['不适用', 'default'],
}

const BOUNDARY_SCOPE = {
  clinical: '临床',
  imaging: '影像',
  pathology: '病理',
  rheumatology: '风湿免疫',
  progression: '疾病进展',
  etiology: '病因',
  other: '其他',
}

const specialtyLabel = (value) => SPECIALTIES[value] || value

function SpecialtyTags({ label, specialties = [], color }) {
  if (!specialties.length) return null
  return <>
    <Tag>{label}</Tag>
    {specialties.map((specialty) => <Tag color={color} key={specialty}>{specialtyLabel(specialty)}</Tag>)}
  </>
}

function LabeledStatus({ label, value, labels }) {
  const [valueLabel, color] = labels[value] || [value || '未标记', 'default']
  return <Tag color={color}>{label}：{valueLabel}</Tag>
}

function LabeledTag({ label, value, labels }) {
  const valueLabel = labels[value] || value || '未标记'
  return <Tag>{label}：{valueLabel}</Tag>
}

function EmptyList({ description }) {
  return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={description} />
}

function Sources({ item, label = '来源引用：' }) {
  if (!item.source_citations?.length) return null
  return (
    <div className="chair-sources">
      <Text strong>{label}</Text>
      <CitationGroup sourceCitations={item.source_citations} />
    </div>
  )
}

function IntegratedConclusions({ items = [] }) {
  return (
    <Card title="跨专科整合结论" className="section-card chair-board chair-conclusions">
      {items.length ? <div className="formal-list">
        {items.map((item, index) => (
          <article className="formal-item conclusion-item" key={item.conclusion_id || index}>
            <Space size={[6, 6]} wrap>
              <SpecialtyTags label="支持专科" specialties={item.supporting_specialties ?? item.specialties} color="geekblue" />
              {item.status && <LabeledStatus label="结论状态" value={item.status} labels={CONCLUSION_STATUS} />}
              {item.role && <LabeledTag label="结论定位" value={item.role} labels={CONCLUSION_ROLE} />}
              {item.conclusion_type && <LabeledTag label="结论类型" value={item.conclusion_type} labels={CONCLUSION_TYPE} />}
            </Space>
            <Title level={5}>{item.statement}</Title>
            {item.medical_basis && <Paragraph><Text strong>整合依据：</Text>{item.medical_basis}</Paragraph>}
            {item.decision_impact && <Paragraph type="secondary"><Text strong>决策影响：</Text>{item.decision_impact}</Paragraph>}
            <EvidenceGroups evidence={item.evidence} guidelineEvidence={item.guideline_evidence} />
            <Sources item={item} />
            {item.limitations?.length > 0 && <div className="limitation-note"><Text strong>结论限制：</Text>{item.limitations.join('；')}</div>}
          </article>
        ))}
      </div> : <EmptyList description="本轮未形成新的跨专科整合结论" />}
    </Card>
  )
}

function AssessmentBoundaries({ items = [] }) {
  return (
    <Card title="本轮判断边界（不可评价）" className="section-card chair-board chair-boundaries">
      {items.length ? <div className="formal-list">
        {items.map((item, index) => (
          <article className="formal-item boundary-item" key={item.boundary_id || index}>
            <Space size={[6, 6]} wrap>
              <SpecialtyTags label="涉及专科" specialties={item.specialties} color="default" />
              {item.status && <LabeledStatus label="判断状态" value={item.status} labels={BOUNDARY_STATUS} />}
              {item.scope && <LabeledTag label="判断范围" value={item.scope} labels={BOUNDARY_SCOPE} />}
            </Space>
            <Title level={5}>{item.topic}</Title>
            <Paragraph><Text strong>当前不能判断：</Text>{item.statement}</Paragraph>
            <Paragraph><Text strong>原因：</Text>{item.reason}</Paragraph>
            <Paragraph type="secondary"><Text strong>决策影响：</Text>{item.decision_impact}</Paragraph>
            <EvidenceGroups evidence={item.evidence} guidelineEvidence={item.guideline_evidence} />
            <Sources item={item} />
          </article>
        ))}
      </div> : <EmptyList description="本轮没有需要单独说明的判断边界" />}
    </Card>
  )
}

function RelatedItems({ item, questions, evidenceNeeds }) {
  const relatedQuestions = (item.related_question_ids || []).map((id) => questions.find((question) => question.question_id === id)).filter(Boolean)
  const relatedNeeds = (item.related_evidence_need_ids || []).map((id) => evidenceNeeds.find((need) => need.need_id === id)).filter(Boolean)
  if (!relatedQuestions.length && !relatedNeeds.length) return null
  return (
    <div className="conflict-links">
      <Text strong>已有解决路径：</Text>
      {relatedQuestions.map((question) => <Tag color="purple" key={question.question_id}>问题：{question.question}</Tag>)}
      {relatedNeeds.map((need) => <Tag color="gold" key={need.need_id}>证据需求：{need.required_information}</Tag>)}
    </div>
  )
}

function Conflicts({ items = [], questions = [], evidenceNeeds = [] }) {
  return (
    <Card title="跨专科冲突" className="section-card chair-board chair-conflicts">
      {items.length ? <div className="formal-list">
        {items.map((item, index) => (
          <article className="formal-item conflict-item" key={item.conflict_id || index}>
            <Space size={[6, 6]} wrap>
              <SpecialtyTags label="相关专科" specialties={item.specialties} color="volcano" />
              <LabeledStatus label="冲突状态" value={item.status} labels={CONFLICT_STATUS} />
              <LabeledTag label="冲突类别" value={item.conflict_domain} labels={CONFLICT_DOMAIN} />
            </Space>
            <Title level={5}>{item.topic}</Title>
            <Paragraph><Text strong>共同命题：</Text>{item.shared_claim}</Paragraph>
            <Paragraph type="secondary"><Text strong>比较前提：</Text>{item.comparison_conditions}</Paragraph>
            <div className="conflict-positions">
              {item.positions?.map((position, positionIndex) => (
                <div className="conflict-position" key={`${position.specialty}-${positionIndex}`}>
                  <Tag color="volcano">{specialtyLabel(position.specialty)}</Tag>
                  <LabeledStatus label="立场" value={position.stance} labels={CONFLICT_STANCE} />
                  <Paragraph>{position.position}</Paragraph>
                  <EvidenceGroups evidence={position.evidence} guidelineEvidence={position.guideline_evidence} />
                  <Sources item={position} />
                </div>
              ))}
            </div>
            <Paragraph><Text strong>不可兼容原因：</Text>{item.why_incompatible}</Paragraph>
            <Paragraph type="secondary"><Text strong>对当前讨论的影响：</Text>{item.decision_impact}</Paragraph>
            <div className="clarification-note"><Text strong>解决条件：</Text>{item.resolution_requirement}</div>
            <RelatedItems item={item} questions={questions} evidenceNeeds={evidenceNeeds} />
          </article>
        ))}
      </div> : <EmptyList description="当前未识别到未解决的跨专科冲突" />}
    </Card>
  )
}

function Questions({ items = [] }) {
  return (
    <Card title="待回答问题" className="section-card chair-board chair-questions">
      {items.length ? <div className="formal-list">
        {items.map((item, index) => (
          <article className="formal-item question-item" key={item.question_id || index}>
            <Space size={[6, 6]} wrap>
              {item.response_status
                ? <LabeledStatus label="专科回应情况" value={item.response_status} labels={RESPONSE_STATUS} />
                : <LabeledStatus label="问题状态" value={item.status} labels={QUESTION_STATUS} />}
              {item.resolution_status && <LabeledStatus label="问题解决情况" value={item.resolution_status} labels={RESOLUTION_STATUS} />}
              <SpecialtyTags label="问题提出专科" specialties={item.raised_by} />
              <SpecialtyTags label="目标专科" specialties={item.target_specialties} color="geekblue" />
              <SpecialtyTags label="已回应专科" specialties={item.responded_by} color="green" />
              <SpecialtyTags
                label="仍待回答专科"
                specialties={item.response_status === 'all_responded'
                  ? []
                  : item.awaiting_specialties?.filter((specialty) => !item.responded_by?.includes(specialty))}
                color="orange"
              />
            </Space>
            <Title level={5}>{item.question}</Title>
            {item.why_it_matters && <Paragraph><Text strong>讨论意义：</Text>{item.why_it_matters}</Paragraph>}
            {item.answers?.length > 0 && (
              <div className="chair-answers">
                <Text strong>已有专科回答</Text>
                {item.answers.map((answer, answerIndex) => (
                  <div className="chair-answer" key={`${answer.specialty}-${answerIndex}`}>
                    <Tag color="blue">回答专科：{specialtyLabel(answer.specialty)}</Tag>
                    <Paragraph>{answer.answer}</Paragraph>
                    <div className="chair-answer-evidence">
                      <EvidenceGroups evidence={answer.evidence} guidelineEvidence={answer.guideline_evidence} />
                      <Sources item={answer} label="回答来源：" />
                    </div>
                  </div>
                ))}
              </div>
            )}
            {item.answer_summary && <div className="chair-result"><Text strong>当前结果</Text><Paragraph>{item.answer_summary}</Paragraph></div>}
            {item.remaining_clarification && <div className="clarification-note"><Text strong>仍需解释/澄清：</Text>{item.remaining_clarification}</div>}
            {item.decision_unlocked && <Paragraph type="secondary"><Text strong>将影响：</Text>{item.decision_unlocked}</Paragraph>}
            <Sources item={item} label="问题来源：" />
          </article>
        ))}
      </div> : <EmptyList description="当前没有待回答的跨专科问题" />}
    </Card>
  )
}

function EvidenceNeeds({ items = [] }) {
  return (
    <Card title="证据需求及满足状态" className="section-card chair-board chair-needs">
      {items.length ? <div className="formal-list">
        {items.map((item, index) => (
          <article className="formal-item evidence-need-item" key={item.need_id || index}>
            <Space size={[6, 6]} wrap>
              <LabeledStatus label="满足状态" value={item.status} labels={NEED_STATUS} />
              <SpecialtyTags label="需求提出专科" specialties={item.raised_by} />
            </Space>
            <div className="evidence-need-flow">
              <div className="need-step required-step">
                <Text type="secondary">需要提供</Text>
                <Paragraph>{item.required_information}</Paragraph>
              </div>
              <RightOutlined className="need-arrow" />
              <div className="need-step available-step">
                <Text type="secondary">当前已有</Text>
                <Paragraph>{item.available_information || '尚无可用证据'}</Paragraph>
                {item.provided_by?.length > 0 && <Space size={[4, 4]} wrap><SpecialtyTags label="已提供专科" specialties={item.provided_by} color="green" /></Space>}
              </div>
              <RightOutlined className="need-arrow" />
              <div className="need-step remaining-step">
                <Text type="secondary">仍然缺少</Text>
                <Paragraph>{item.remaining_information || '无'}</Paragraph>
              </div>
            </div>
            {item.why_it_matters && <Paragraph className="need-impact"><Text strong>缺口意义：</Text>{item.why_it_matters}</Paragraph>}
            {item.decision_unlocked && <Paragraph type="secondary"><Text strong>满足后可改善：</Text>{item.decision_unlocked}</Paragraph>}
            <EvidenceGroups evidence={item.evidence} guidelineEvidence={item.guideline_evidence} />
            <Sources item={item} />
          </article>
        ))}
      </div> : <EmptyList description="当前没有新的证据需求" />}
    </Card>
  )
}

export function ChairResultBoards({ result }) {
  return (
    <div className="chair-board-grid">
      <IntegratedConclusions items={result?.integrated_conclusions} />
      <AssessmentBoundaries items={result?.assessment_boundaries} />
      <Conflicts items={result?.conflicts} questions={result?.questions} evidenceNeeds={result?.evidence_needs} />
      <Questions items={result?.questions} />
      <EvidenceNeeds items={result?.evidence_needs} />
    </div>
  )
}

export function ChairResultTabs({ result }) {
  const items = [
    ['consensus', '共识结论', <IntegratedConclusions items={result?.integrated_conclusions} />],
    ['boundaries', '判断边界', <AssessmentBoundaries items={result?.assessment_boundaries} />],
    ['conflicts', '跨专科冲突', <Conflicts items={result?.conflicts} questions={result?.questions} evidenceNeeds={result?.evidence_needs} />],
    ['questions', '待回答问题', <Questions items={result?.questions} />],
    ['evidence', '证据缺口', <EvidenceNeeds items={result?.evidence_needs} />],
  ]
  return <Tabs className="chair-result-tabs" items={items.map(([key, label, children]) => ({ key, label, children }))} />
}

export function ChairWorkspace({ runId, run }) {
  const queryClient = useQueryClient()
  const query = useQuery({
    queryKey: ['chair', runId],
    queryFn: () => api.chair(runId),
    refetchInterval: (current) => {
      const status = current.state.data?.status
      if (status === 'running' || (status === 'pending' && !['completed', 'failed', 'cancelled'].includes(run?.status))) return 2500
      return false
    },
  })
  const mutation = useMutation({
    mutationFn: () => api.runChair(runId),
    onSuccess: (value) => {
      queryClient.setQueryData(['chair', runId], value)
      queryClient.invalidateQueries({ queryKey: ['chair', runId] })
    },
  })

  if (query.isError) return <QueryError error={query.error} retry={query.refetch} />
  if (query.isLoading) return <Skeleton active paragraph={{ rows: 12 }} />

  const value = query.data || {}
  const result = value.result
  const running = value.status === 'running' || mutation.isPending
  const failedWithPrevious = value.status === 'failed' && result

  return (
    <div className="chair-workspace">
      <div className="workspace-title">
        <div>
          <Text className="eyebrow">MDT CHAIR</Text>
          <Title level={3}>主持人跨专科整合</Title>
          <Space size={6}><StatusTag status={running ? 'running' : value.status} /><Text type="secondary">基于当前四个专科的正式输出</Text></Space>
        </div>
        <Button
          type="primary"
          aria-label={result ? '重新运行主持人整合' : '运行主持人整合'}
          icon={result ? <ReloadOutlined /> : <PlayCircleOutlined />}
          loading={running}
          disabled={!value.runnable || running}
          onClick={() => mutation.mutate()}
        >
          {result ? '重新运行主持人整合' : '运行主持人整合'}
        </Button>
      </div>

      <Alert className="section-gap" type="info" showIcon title="开发阶段单独运行入口" description="此按钮只运行 MDT 主持人，会直接使用现有四个专科结果，不会重新运行前序 Agent。" />
      {value.status === 'unavailable' && <Alert className="section-gap" type="warning" showIcon title="主持人尚不可运行" description={value.error} />}
      {value.status === 'outdated' && <Alert className="section-gap" type="warning" showIcon title="现有主持人结果属于旧版结构" description="下方结果仍可查看，请点击重新运行以生成当前五板块完整结果。" />}
      {value.status === 'pending' && <Alert className="section-gap" type="success" showIcon title="四个专科结果已就绪" description="可以单独运行主持人整合。" />}
      {value.status === 'failed' && <Alert className="section-gap" type="error" showIcon title={failedWithPrevious ? '本次重跑失败，下方展示上一次成功结果' : '主持人整合失败'} description={value.error} />}
      {mutation.isError && <Alert className="section-gap" type="error" showIcon title="无法启动主持人" description={mutation.error.message} />}

      <ChairResultBoards result={result} />
    </div>
  )
}
