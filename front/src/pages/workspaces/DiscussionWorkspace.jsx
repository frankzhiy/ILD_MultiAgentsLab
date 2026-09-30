import { useCallback, useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ApiOutlined, CheckCircleFilled, ClockCircleOutlined, DisconnectOutlined,
  ExclamationCircleFilled, FileSearchOutlined, PlayCircleOutlined, ReloadOutlined,
  TeamOutlined,
} from '@ant-design/icons'
import { Alert, Button, Card, Collapse, Empty, InputNumber, Modal, Progress, Segmented, Skeleton, Space, Spin, Table, Tag, Timeline, Typography } from 'antd'
import { api } from '../../api'
import { Citation, CitationGroup } from '../../components/Citation'
import { QueryError } from '../../components/QueryState'
import { StatusTag } from '../../components/StatusTag'
import { ChairResultTabs } from './ChairWorkspace'

const { Paragraph, Text, Title } = Typography

const SPECIALTIES = {
  pulmonology: '呼吸科',
  thoracic_radiology: '胸部影像科',
  rheumatology: '风湿免疫科',
  pathology: '病理科',
}

const TASK_STATUS = {
  waiting: ['等待中', 'default'],
  running: ['分析中', 'processing'],
  completed: ['已完成', 'success'],
  failed: ['失败', 'error'],
}

const ANSWERABILITY = {
  answered: ['已回答', 'success'],
  partially_answered: ['部分回答', 'warning'],
  not_assessable: ['不可评价', 'default'],
}

const REVIEW_OUTCOME = {
  accept_answer: ['接受回答', 'success'],
  accept_boundary: ['接受本轮判断边界', 'success'],
  request_clarification: ['请求原专科澄清', 'processing'],
  request_corroboration: ['请求其他专科佐证', 'processing'],
  flag_incompatibility: ['提出方发现不兼容', 'error'],
  identify_conflict: ['提出方发现不兼容', 'error'],
  convert_to_evidence_need: ['转为证据需求', 'warning'],
}

const JUDGMENT_CHANGE = {
  initial: ['首轮判断', 'default'],
  maintain: ['保持', 'default'],
  supplement: ['补充', 'blue'],
  qualify: ['限定', 'gold'],
  revise: ['修正', 'purple'],
  withdraw: ['撤回', 'red'],
  create: ['新增', 'green'],
}

const PROFESSIONAL_LEVEL = {
  observation: '病例观察',
  morphologic_pattern: '形态模式',
  disease_diagnosis: '疾病诊断',
  etiologic_attribution: '病因归属',
  severity_or_trajectory: '严重度或病程',
  assessability: '可评价性',
}

const DISCUSSION_EVENTS = [
  'manual_stage_started',
  'discussion_started',
  'discussion_round_started',
  'discussion_task_started',
  'discussion_task_completed',
  'discussion_task_failed',
  'discussion_judgment_update_started',
  'discussion_judgment_update_completed',
  'discussion_review_started',
  'discussion_review_completed',
  'discussion_chair_started',
  'discussion_round_completed',
  'discussion_report_started',
  'discussion_completed',
  'discussion_failed',
]

const CHAIR_SECTIONS = [
  ['integrated_conclusions', '整合结论', 'conclusion_id'],
  ['assessment_boundaries', '判断边界', 'boundary_id'],
  ['conflicts', '真实冲突', 'conflict_id'],
  ['questions', '待回答问题', 'question_id'],
  ['evidence_needs', '证据需求', 'need_id'],
]

export function chairChangeSummary(current, previous) {
  if (!previous) return ['首轮主持人更新，作为后续轮次的比较基线']
  const changes = CHAIR_SECTIONS.flatMap(([key, label, idKey]) => {
    const before = new Map((previous[key] || []).map((item, index) => [item[idKey] || index, item]))
    const after = new Map((current?.[key] || []).map((item, index) => [item[idKey] || index, item]))
    const added = [...after.keys()].filter((id) => !before.has(id)).length
    const removed = [...before.keys()].filter((id) => !after.has(id)).length
    const updated = [...after].filter(([id, item]) => before.has(id) && JSON.stringify(before.get(id)) !== JSON.stringify(item)).length
    const parts = [
      added && `新增 ${added}`,
      updated && `更新 ${updated}`,
      removed && `移除 ${removed}`,
    ].filter(Boolean)
    return parts.length ? [`${label}：${parts.join('、')}`] : []
  })
  return changes.length ? changes : ['相比上一轮，五个板块无结构性变化']
}

function specialtyLabel(value) {
  return SPECIALTIES[value] || value
}

function formatClock(value) {
  if (!value) return '—'
  return new Date(value).toLocaleTimeString('zh-CN', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

function formatDuration(start, end) {
  if (!start) return ''
  const seconds = Math.max(0, Math.round((new Date(end || Date.now()) - new Date(start)) / 1000))
  const minutes = Math.floor(seconds / 60)
  return minutes ? `${minutes}分${seconds % 60}秒` : `${seconds}秒`
}

function useElapsed(activeRound, running) {
  const [, setTick] = useState(0)
  useEffect(() => {
    if (!running || !activeRound?.started_at) return undefined
    const timer = window.setInterval(() => setTick((value) => value + 1), 1000)
    return () => window.clearInterval(timer)
  }, [activeRound?.started_at, running])
  return running ? formatDuration(activeRound?.started_at) : ''
}

function useDiscussionEvents(runId, onEvent) {
  const [connection, setConnection] = useState('connecting')
  useEffect(() => {
    if (typeof EventSource === 'undefined') {
      setConnection('unavailable')
      return undefined
    }
    const source = new EventSource(`/api/runs/${encodeURIComponent(runId)}/stream`)
    const notify = () => onEvent()
    source.onopen = () => {
      setConnection('connected')
      onEvent()
    }
    source.onerror = () => setConnection('disconnected')
    DISCUSSION_EVENTS.forEach((type) => source.addEventListener(type, notify))
    return () => {
      DISCUSSION_EVENTS.forEach((type) => source.removeEventListener(type, notify))
      source.close()
    }
  }, [onEvent, runId])
  return connection
}

function taskAnswers(round) {
  const answers = {}
  round?.specialty_responses?.forEach((response) => response.answers?.forEach((answer) => { answers[answer.task_id] = answer }))
  Object.entries(round?.task_progress || {}).forEach(([taskId, progress]) => {
    if (progress.answer) answers[taskId] = progress.answer
  })
  return answers
}

function roundReviews(round) {
  const reviews = [...(round?.answer_reviews || [])]
  Object.values(round?.review_progress || {}).forEach((progress) => {
    if (progress.review && !reviews.some((item) => item.review_id === progress.review.review_id)) {
      reviews.push(progress.review)
    }
  })
  return reviews
}

function taskStatus(round, taskId, answers) {
  return round?.task_progress?.[taskId]?.status || (answers[taskId] ? 'completed' : 'waiting')
}

function chairStatus(round) {
  if (round?.chair_result) return 'completed'
  if (round?.status === 'failed') return 'failed'
  return round?.chair_status || 'waiting'
}

function StatusIcon({ status }) {
  if (status === 'running') return <Spin size="small" />
  if (status === 'completed') return <CheckCircleFilled className="discussion-icon-success" />
  if (status === 'failed') return <ExclamationCircleFilled className="discussion-icon-error" />
  return <ClockCircleOutlined className="discussion-icon-waiting" />
}

function TaskAssignment({ round, selectedTaskId, onSelect }) {
  const answers = useMemo(() => taskAnswers(round), [round])
  const rows = useMemo(() => {
    const groups = Object.keys(SPECIALTIES).flatMap((specialty) => {
      const tasks = (round?.tasks || []).filter((task) => task.specialty === specialty)
      return tasks.map((task, index) => ({ ...task, specialtySpan: index === 0 ? tasks.length : 0 }))
    })
    return groups.length ? groups : (round?.tasks || []).map((task) => ({ ...task, specialtySpan: 1 }))
  }, [round])
  const columns = [
    {
      title: '专科', dataIndex: 'specialty', width: 80,
      onCell: (record) => ({ rowSpan: record.specialtySpan }),
      render: (value) => <Text strong>{specialtyLabel(value)}</Text>,
    },
    {
      title: '需其他专科回答的问题', dataIndex: 'prompt',
      render: (_, task) => (
        <Button type="link" className="discussion-question-link" onClick={() => onSelect(task.task_id)}>
          {task.prompt}
        </Button>
      ),
    },
    {
      title: '状态', width: 74,
      render: (_, task) => {
        const status = taskStatus(round, task.task_id, answers)
        const [label, color] = TASK_STATUS[status] || [status, 'default']
        return <Tag color={color}>{label}</Tag>
      },
    },
    {
      title: '证据包', width: 92,
      render: (_, task) => {
        const first = task.evidence_candidates?.[0]
        if (!first) return <Text type="secondary">0 组</Text>
        return <Space size={4}><Citation value={first} collection={task.evidence_candidates} />{task.evidence_candidates.length > 1 && <Text type="secondary">+{task.evidence_candidates.length - 1}</Text>}</Space>
      },
    },
  ]
  return (
    <Card title="本轮任务分配" className="discussion-panel discussion-task-panel" extra={<Text type="secondary">{rows.length} 个任务</Text>}>
      <Table
        size="small"
        rowKey="task_id"
        columns={columns}
        dataSource={rows}
        pagination={false}
        tableLayout="fixed"
        rowClassName={(task) => task.task_id === selectedTaskId ? 'discussion-selected-row' : ''}
        onRow={(task) => ({ onClick: () => onSelect(task.task_id) })}
        locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="本轮尚未分配任务" /> }}
      />
    </Card>
  )
}

function DiscussionTrace({ round, selectedTaskId, onSelect, reportStatus }) {
  const answers = useMemo(() => taskAnswers(round), [round])
  const tasks = round?.tasks || []
  const chairProgress = chairStatus(round)
  const updateEntries = Object.entries(round?.judgment_update_progress || {})
  const updateStatuses = updateEntries.map(([, progress]) => progress.status)
  const updateStatus = !updateStatuses.length
    ? ((round?.judgment_updates || []).length || (round?.specialty_responses || []).some((item) => item.judgment_update) || round?.chair_result ? 'completed' : 'waiting')
    : updateStatuses.some((status) => status === 'failed') ? 'failed'
      : updateStatuses.every((status) => status === 'completed') ? 'completed'
        : updateStatuses.some((status) => status === 'running') ? 'running' : 'waiting'
  const reviewEntries = Object.values(round?.review_progress || {})
  const reviewStatuses = reviewEntries.map((progress) => progress.status)
  const reviewStatus = reviewStatuses.some((status) => status === 'failed') ? 'failed'
    : reviewStatuses.some((status) => status === 'running') ? 'running'
      : reviewStatuses.length && reviewStatuses.every((status) => status === 'completed') ? 'completed'
        : round?.chair_result || updateEntries.length || (round?.judgment_updates || []).length ? 'completed' : 'waiting'
  const items = [
    {
      color: 'blue',
      content: <div className="discussion-trace-root"><Text strong>主持人提出本轮临床问题</Text><Text type="secondary">共 {tasks.length} 个定向任务，已按专科并行分配</Text></div>,
    },
    {
      color: tasks.some((task) => taskStatus(round, task.task_id, answers) === 'running') ? 'blue' : 'green',
      content: (
        <div className="discussion-trace-group">
          <Text strong>专科并行分析</Text>
          {tasks.map((task) => {
            const status = taskStatus(round, task.task_id, answers)
            const progress = round?.task_progress?.[task.task_id] || {}
            return (
              <button type="button" className={`discussion-agent-node ${selectedTaskId === task.task_id ? 'selected' : ''}`} key={task.task_id} onClick={() => onSelect(task.task_id)}>
                <StatusIcon status={status} />
                <span className="discussion-agent-copy">
                  <strong>{specialtyLabel(task.specialty)}</strong>
                  <small>{status === 'running' ? '正在分析问题与证据' : status === 'completed' ? '已提交验证后回答' : status === 'failed' ? progress.error || '任务失败' : '等待执行'}</small>
                </span>
                <span className="discussion-agent-meta">
                  <small>{formatClock(progress.completed_at || progress.started_at)}</small>
                  <Tag>{task.evidence_candidates?.length || 0} 组证据</Tag>
                </span>
              </button>
            )
          })}
        </div>
      ),
    },
    {
      color: reviewStatus === 'failed' ? 'red' : reviewStatus === 'completed' ? 'green' : reviewStatus === 'running' ? 'blue' : 'gray',
      icon: <StatusIcon status={reviewStatus} />,
      content: <div className="discussion-trace-root"><Text strong>提问专科复核回答</Text><Text type="secondary">{reviewStatus === 'running' ? '正在判断回答是否解决原问题' : reviewStatus === 'completed' ? reviewEntries.length || (round?.answer_reviews || []).length ? '已记录接受、边界、澄清、佐证或不兼容结果' : '本轮无需提问专科复核' : reviewStatus === 'failed' ? '提问专科复核失败' : '等待专科回答完成'}</Text></div>,
    },
    {
      color: updateStatus === 'failed' ? 'red' : updateStatus === 'completed' ? 'green' : updateStatus === 'running' ? 'blue' : 'gray',
      icon: <StatusIcon status={updateStatus} />,
      content: (
        <div className="discussion-trace-root">
          <Text strong>受影响的专科更新自己的正式判断</Text>
          <Text type="secondary">{updateStatus === 'running' ? '正在结合专科回答与复核结果，比较当前有效版本' : updateStatus === 'completed' ? '已记录保持、补充、限定、修正、撤回或新增' : updateStatus === 'failed' ? '判断版本更新失败' : '等待回答与复核完成'}</Text>
        </div>
      ),
    },
    {
      color: chairProgress === 'failed' ? 'red' : chairProgress === 'completed' ? 'green' : chairProgress === 'running' ? 'blue' : 'gray',
      icon: <StatusIcon status={chairProgress} />,
      content: <div className="discussion-trace-root"><Text strong>MDT 主持人整合</Text><Text type="secondary">{chairProgress === 'running' ? '正在汇总专科回应并更新五个板块' : chairProgress === 'completed' ? '本轮主持人更新已完成' : chairProgress === 'failed' ? '主持人整合失败' : '等待全部专科回应'}</Text></div>,
    },
    {
      color: reportStatus === 'failed' ? 'red' : reportStatus === 'completed' ? 'green' : reportStatus === 'running' ? 'blue' : 'gray',
      icon: <StatusIcon status={reportStatus} />,
      content: <div className="discussion-trace-root"><Text strong>最终 MDT 统一报告</Text><Text type="secondary">{reportStatus === 'running' ? '正在生成最终报告' : reportStatus === 'completed' ? '最终报告已生成' : reportStatus === 'failed' ? '最终报告生成失败' : '等待讨论结束'}</Text></div>,
    },
  ]
  return (
    <Card title="讨论过程" className="discussion-panel discussion-trace-panel" extra={<Text type="secondary">仅展示可审计活动与验证后输出</Text>}>
      <Timeline items={items} />
    </Card>
  )
}

function QuestionDetail({ task, answer, progress, reviews = [] }) {
  const [expanded, setExpanded] = useState(false)
  useEffect(() => setExpanded(false), [task?.task_id])
  if (!task) return <Card title="问题与回答" className="discussion-panel"><Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="请选择一个问题查看详情" /></Card>
  const evidenceRows = answer?.evidence_uses?.length
    ? answer.evidence_uses
    : (task.evidence_candidates || []).map((item) => ({ ...item, interpretation: '等待专科完成分析后形成结论' }))
  const [answerLabel, answerColor] = ANSWERABILITY[answer?.answerability] || ['等待回答', 'default']
  const existingViews = [...(task.specialty_context || []), ...(task.prior_answers || [])]
  const evidenceColumns = [
    { title: '引用类别', width: 86, render: (_, item, index) => <Citation value={item} collection={evidenceRows} index={index} /> },
    { title: '引用原文（quote）', dataIndex: 'quote', width: 150, render: (value) => value || '该引用未携带原文' },
    { title: '支持的结论', dataIndex: 'interpretation', render: (value) => value || '—' },
  ]
  return (
    <Card title="问题与回答" className="discussion-panel discussion-detail-panel" extra={<Tag color={answerColor}>回答方自评：{answerLabel}</Tag>}>
      <div className="discussion-detail-section">
        <Text className="discussion-detail-label">问题</Text>
        <Title level={5}>{task.prompt}</Title>
        <Space size={[5, 5]} wrap><Tag color="blue">{specialtyLabel(task.specialty)}</Tag><Text code>{task.issue_id}</Text>{task.why_it_matters && <Text type="secondary">{task.why_it_matters}</Text>}</Space>
      </div>
      {task.current_result && (
        <div className="discussion-detail-section">
          <Text className="discussion-detail-label">主持人当前判断</Text>
          <Paragraph>{task.current_result}</Paragraph>
        </div>
      )}
      {existingViews.length > 0 && (
        <div className="discussion-detail-section">
          <Text className="discussion-detail-label">已有专科观点</Text>
          {existingViews.map((item, index) => (
            <div className="discussion-existing-view" key={`${item.specialty || 'source'}-${item.round_number || index}`}>
              <Space size={5} wrap>
                {item.specialty && <Tag>{specialtyLabel(item.specialty)}</Tag>}
                {item.round_number && <Tag>第 {item.round_number} 轮</Tag>}
                {item.relation && <Text type="secondary">{item.relation}</Text>}
              </Space>
              <Paragraph>{item.answer || item.position || item.quote}</Paragraph>
            </div>
          ))}
        </div>
      )}
      {task.remaining_clarification && (
        <div className="discussion-detail-section">
          <Text className="discussion-detail-label">主持人关注的未决点（回答方向参考）</Text>
          <Paragraph type="secondary">{task.remaining_clarification}</Paragraph>
        </div>
      )}
      <div className="discussion-detail-section">
        <Text className="discussion-detail-label">回答</Text>
        {answer
          ? <>
            {answer.answer_claims?.length
              ? answer.answer_claims.map((claim) => (
                <Paragraph className="discussion-answer" key={claim.claim_id || claim.statement}>
                  {claim.statement}{' '}
                  <CitationGroup refs={[
                    ...(claim.evidence_uses || []).map((item) => ({ ...item, claim_statement: claim.statement })),
                    ...(claim.guideline_evidence || []).map((item) => ({ ...item, claim_statement: claim.statement })),
                  ]} />
                </Paragraph>
              ))
              : <>
                <Paragraph className={`discussion-answer ${expanded ? 'expanded' : ''}`}>{answer.answer}</Paragraph>
                {answer.answer?.length > 260 && <Button type="link" className="discussion-answer-toggle" onClick={() => setExpanded((value) => !value)}>{expanded ? '收起完整回答' : '展开完整回答'}</Button>}
              </>}
            <Collapse
              ghost
              size="small"
              className="discussion-basis-collapse"
              items={[{
                key: 'basis',
                label: '查看医学依据与判断限制',
                children: <><Paragraph><Text strong>医学依据：</Text>{answer.medical_basis}</Paragraph>{answer.remaining_limitation && <div className="limitation-note"><Text strong>仍受限于：</Text>{answer.remaining_limitation}</div>}</>,
              }]}
            />
            {reviews.length > 0 && (
              <div className="discussion-detail-section">
                <Text className="discussion-detail-label">提问专科复核</Text>
                {reviews.map((review) => {
                  const [label, color] = REVIEW_OUTCOME[review.outcome] || [review.outcome, 'default']
                  return (
                    <div className="discussion-existing-view" key={review.review_id}>
                      <Space size={5} wrap>
                        <Tag>{specialtyLabel(review.reviewer_specialty)}</Tag>
                        <Tag color={color}>{label}</Tag>
                      </Space>
                      <Paragraph>{review.rationale}</Paragraph>
                      {review.follow_up_question?.question && <Paragraph type="secondary"><Text strong>后续问题：</Text>{review.follow_up_question.question}</Paragraph>}
                    </div>
                  )
                })}
              </div>
            )}
          </>
          : progress?.status === 'failed'
            ? <Alert type="error" showIcon title="该任务失败" description={progress.error} />
            : <div className="discussion-answer-pending">{progress?.status === 'running' ? <Spin size="small" /> : <ClockCircleOutlined />}<Text type="secondary">{progress?.status === 'running' ? '专科正在使用证据形成回答…' : '等待专科开始分析'}</Text></div>}
      </div>
      <div className="discussion-detail-section">
        <div className="discussion-evidence-heading"><Text className="discussion-detail-label">证据使用路径</Text><Text type="secondary">引用类别 → 引用原文 → 支持的结论</Text></div>
        <Table size="small" rowKey={(item) => item.evidence_ref} columns={evidenceColumns} dataSource={evidenceRows} pagination={false} tableLayout="fixed" locale={{ emptyText: '没有可用证据' }} />
        {answer?.guideline_evidence?.length > 0 && <div className="discussion-guidelines"><Text strong>指南依据：</Text><CitationGroup refs={answer.guideline_evidence} /></div>}
      </div>
    </Card>
  )
}

function DecisionStatePanel({ state }) {
  const [specialty, setSpecialty] = useState('pulmonology')
  if (!state) return null
  const histories = (state.judgments || []).filter((item) => item.specialty === specialty)
  const eventsByJudgment = (state.change_events || []).reduce((result, event) => {
    result[event.judgment_id] = [...(result[event.judgment_id] || []), event]
    return result
  }, {})
  const rows = histories.map((history) => {
    const current = history.versions.find((item) => item.version_id === history.active_version_id)
    const latest = current || history.versions.at(-1)
    return { ...history, current, latest }
  })
  const columns = [
    {
      title: '当前判断',
      render: (_, row) => (
        <div className="judgment-main">
          <Space size={[5, 5]} wrap>
            <Text code>{row.latest?.version_id}</Text>
            {row.current ? <Tag color="green">当前有效</Tag> : <Tag color="red">已撤回</Tag>}
            <Tag>{PROFESSIONAL_LEVEL[row.latest?.assessment?.conditions?.professional_level] || '未标记层级'}</Tag>
          </Space>
          <Text strong>{row.latest?.assessment?.statement}</Text>
          <Text type="secondary">{row.latest?.assessment?.medical_basis}</Text>
        </div>
      ),
    },
    {
      title: '成立条件', width: 330,
      render: (_, row) => {
        const conditions = row.latest?.assessment?.conditions || {}
        const timeframe = conditions.timeframe || {}
        const scope = conditions.evidence_scope || {}
        return (
          <div className="judgment-conditions">
            <Text><Text strong>对象：</Text>{conditions.subject || '—'}</Text>
            <Text><Text strong>时间：</Text>{timeframe.description || timeframe.kind || '—'}</Text>
            <Text><Text strong>证据：</Text>{scope.evidence_ids?.length || 0} 条患者证据</Text>
            {scope.scope_limitations?.length > 0 && <Text type="secondary">{scope.scope_limitations.join('；')}</Text>}
            {conditions.applicability_conditions?.length > 0 && <Text type="secondary"><Text strong>适用前提：</Text>{conditions.applicability_conditions.join('；')}</Text>}
          </div>
        )
      },
    },
    {
      title: '版本', width: 84,
      render: (_, row) => <Tag>{row.versions.length} 个版本</Tag>,
    },
  ]
  return (
    <Card
      className="decision-state-card section-gap"
      title="当前多专科判断"
      extra={<Space><Tag color="blue">状态版本 {state.revision}</Tag><Text type="secondary">主持人只读取当前有效版本</Text></Space>}
    >
      <Segmented
        value={specialty}
        onChange={setSpecialty}
        options={Object.entries(SPECIALTIES).map(([value, label]) => ({ value, label }))}
      />
      <Table
        className="decision-state-table"
        size="small"
        rowKey="judgment_id"
        columns={columns}
        dataSource={rows}
        pagination={false}
        expandable={{
          expandedRowRender: (row) => (
            <Timeline
              className="judgment-history"
              items={row.versions.map((version) => {
                const [label, color] = JUDGMENT_CHANGE[version.change_type] || [version.change_type, 'default']
                const event = (eventsByJudgment[row.judgment_id] || []).find((item) => item.after_version_id === version.version_id)
                return {
                  color: version.lifecycle_status === 'active' ? 'green' : version.lifecycle_status === 'withdrawn' ? 'red' : 'gray',
                  children: (
                    <div>
                      <Space size={[5, 5]} wrap><Text code>{version.version_id}</Text><Tag color={color}>{label}</Tag>{version.created_in_round > 0 && <Tag>第 {version.created_in_round} 轮</Tag>}{version.trigger_issue_ids?.map((issueId) => <Tag key={issueId}>{issueId}</Tag>)}</Space>
                      <Paragraph>{version.assessment.statement}</Paragraph>
                      <Text type="secondary">{event?.rationale || version.rationale}</Text>
                      {version.changed_fields?.length > 0 && <div><Text type="secondary">变化内容：{version.changed_fields.join('、')}</Text></div>}
                      {version.considered_source_refs?.length > 0 && <div><Text type="secondary">参考的专科意见：{version.considered_source_refs.join('、')}</Text></div>}
                    </div>
                  ),
                }
              })}
            />
          ),
        }}
        locale={{ emptyText: '该专科当前没有正式判断' }}
      />
    </Card>
  )
}

export function DiscussionWorkspace({ runId, run }) {
  const queryClient = useQueryClient()
  const query = useQuery({
    queryKey: ['discussion', runId, 'presented'],
    queryFn: () => api.discussion(runId, true),
    refetchInterval: (current) => current.state.data?.status === 'running' ? 2000 : false,
  })
  const refresh = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ['discussion', runId] })
    queryClient.invalidateQueries({ queryKey: ['run', runId] })
  }, [queryClient, runId])
  const connection = useDiscussionEvents(runId, refresh)
  const mutation = useMutation({
    mutationFn: () => api.runDiscussion(runId),
    onMutate: () => {
      const previous = queryClient.getQueryData(['discussion', runId, 'presented'])
      queryClient.cancelQueries({ queryKey: ['discussion', runId] })
      queryClient.setQueryData(['discussion', runId, 'presented'], (current = {}) => ({
        ...current,
        status: 'running',
        error: null,
      }))
      return { previous }
    },
    onSuccess: (value) => {
      queryClient.setQueryData(['discussion', runId, 'presented'], value)
      queryClient.invalidateQueries({ queryKey: ['run', runId] })
    },
    onError: (_error, _variables, context) => {
      queryClient.setQueryData(['discussion', runId, 'presented'], context?.previous)
    },
  })
  const [selectedRound, setSelectedRound] = useState()
  const [selectedTaskId, setSelectedTaskId] = useState()
  const [batchOpen, setBatchOpen] = useState(false)
  const [batchRunIds, setBatchRunIds] = useState([])
  const [batchCaseConcurrency, setBatchCaseConcurrency] = useState(2)
  const [batchRequestConcurrency, setBatchRequestConcurrency] = useState(6)
  const batchRuns = useQuery({ queryKey: ['runs'], queryFn: api.runs, enabled: batchOpen })
  const batchMutation = useMutation({
    mutationFn: () => api.createBatch({ kind: 'discussion', run_ids: batchRunIds, max_case_concurrency: batchCaseConcurrency, max_request_concurrency: batchRequestConcurrency }),
    onSuccess: (result) => {
      window.history.pushState({}, '', `/batches/${encodeURIComponent(result.id)}`)
      window.dispatchEvent(new PopStateEvent('popstate'))
    },
  })

  const value = query.data || {}
  const running = value.status === 'running' || mutation.isPending
  const rounds = useMemo(() => {
    const completed = value.rounds || []
    if (!value.active_round) return completed
    return [...completed.filter((item) => item.round_number !== value.active_round.round_number), value.active_round]
  }, [value.active_round, value.rounds])
  const activeRoundNumber = value.active_round?.round_number || rounds.at(-1)?.round_number

  useEffect(() => {
    if (activeRoundNumber) setSelectedRound(activeRoundNumber)
  }, [activeRoundNumber])
  const round = rounds.find((item) => item.round_number === selectedRound) || rounds.at(-1)
  const previousChairResult = rounds[rounds.findIndex((item) => item.round_number === round?.round_number) - 1]?.chair_result
  useEffect(() => {
    const tasks = round?.tasks || []
    if (!tasks.some((task) => task.task_id === selectedTaskId)) setSelectedTaskId(tasks[0]?.task_id)
  }, [round, selectedTaskId])

  const answers = useMemo(() => taskAnswers(round), [round])
  const selectedTask = round?.tasks?.find((task) => task.task_id === selectedTaskId)
  const selectedAnswer = selectedTask ? answers[selectedTask.task_id] : null
  const selectedReviews = useMemo(
    () => roundReviews(round).filter((review) => review.answer_id === selectedAnswer?.answer_id),
    [round, selectedAnswer?.answer_id],
  )
  const selectedProgress = selectedTask ? round?.task_progress?.[selectedTask.task_id] : null
  const statuses = (round?.tasks || []).map((task) => taskStatus(round, task.task_id, answers))
  const chairProgress = chairStatus(round)
  const progressTotal = Math.max(1, statuses.length + 1)
  const progressDone = statuses.filter((status) => ['completed', 'failed'].includes(status)).length + (['completed', 'failed'].includes(chairProgress) ? 1 : 0)
  const progressPercent = value.status === 'completed' ? 100 : Math.round(progressDone / progressTotal * 100)
  const elapsed = useElapsed(value.active_round, running)

  if (query.isError) return <QueryError error={query.error} retry={query.refetch} />
  if (query.isLoading) return <Skeleton active paragraph={{ rows: 12 }} />

  const hasResult = rounds.length > 0 || value.final_report
  const roundStatus = running && round?.round_number === value.active_round?.round_number
    ? 'running'
    : round?.status === 'failed' || (value.status === 'failed' && round?.round_number === value.active_round?.round_number)
      ? 'failed'
      : 'completed'
  const connectionLabel = connection === 'connected' ? '实时连接' : connection === 'connecting' ? '正在连接' : connection === 'unavailable' ? '轮询模式' : '连接已断开'
  const connectionColor = connection === 'connected' ? 'success' : connection === 'connecting' ? 'processing' : connection === 'unavailable' ? 'default' : 'error'

  return (
    <div className="chair-workspace discussion-workspace">
      <div className="discussion-topbar">
        <div>
          <Text className="eyebrow">MDT DISCUSSION</Text>
          <Title level={3}>MDT 团队讨论</Title>
        </div>
        <div className="discussion-live-summary">
          <Tag icon={connection === 'connected' ? <ApiOutlined /> : <DisconnectOutlined />} color={connectionColor}>{connectionLabel}</Tag>
          <Text>第 <strong>{value.current_round || 0}</strong> / {value.max_rounds || 3} 轮</Text>
          <Text type="secondary">讨论前主持人基线不计入轮次</Text>
          {elapsed && <Text type="secondary">已用时 {elapsed}</Text>}
          <div className="discussion-overall-progress"><Text type="secondary">总体进度</Text><Progress percent={progressPercent} size="small" /></div>
          <Button onClick={() => { setBatchRunIds([runId]); setBatchOpen(true) }}>批量运行团队讨论</Button>
          <Button type="primary" aria-label={hasResult ? '重新运行团队讨论' : '运行团队讨论'} icon={hasResult ? <ReloadOutlined /> : <PlayCircleOutlined />} loading={running} disabled={!value.runnable || running || run?.status === 'running'} onClick={() => mutation.mutate()}>{hasResult ? '重新运行团队讨论' : '运行团队讨论'}</Button>
        </div>
      </div>

      {value.presentation_status === 'unavailable' && <Alert className="section-gap" type="warning" showIcon title="文字润色暂不可用" description="当前显示讨论原始记录；医学结论和证据记录未受影响。" />}
      {connection === 'disconnected' && <Alert className="section-gap" type="warning" showIcon title="实时事件流已断开" description="页面会自动重连，并每 2 秒从服务端恢复一次讨论进度。" />}
      {running && <Alert className="section-gap" type="info" showIcon title="新一轮团队讨论已启动" description={hasResult ? '正在初始化任务；下方暂时保留上一次运行结果，新进度写入后会自动替换。' : '正在初始化任务与运行资源，新进度写入后会自动显示。'} />}
      {value.status === 'unavailable' && <Alert className="section-gap" type="warning" showIcon title="团队讨论尚不可运行" description={value.error} />}
      {value.status === 'pending' && <Alert className="section-gap" type="info" showIcon title="现有输出已就绪" description={run?.status === 'running' ? '完整运行将自动进入团队讨论。' : '可单独启动或重新运行团队讨论，过程将实时显示。'} />}
      {value.status === 'outdated' && <Alert className="section-gap" type="warning" showIcon title="主持人结果已更新" description="下方是基于旧主持人结果的讨论记录，请重新运行以匹配当前结果。" />}
      {value.status === 'failed' && <Alert className="section-gap" type="error" showIcon title="团队讨论失败；已保留完成的步骤" description={value.error} />}
      {mutation.isError && <Alert className="section-gap" type="error" showIcon title="无法启动团队讨论" description={mutation.error.message} />}
      <Modal title="批量运行团队讨论" open={batchOpen} onCancel={() => setBatchOpen(false)} onOk={() => batchMutation.mutate()} okText="开始批量运行" confirmLoading={batchMutation.isPending} okButtonProps={{ disabled: !batchRunIds.length }} width={820}>
        <Text type="secondary">仅显示拥有当前主持人整合结果的运行。排队期间主持人结果变化的病例会被安全跳过。</Text>
        <Table className="section-gap" size="small" rowKey="id" loading={batchRuns.isLoading} dataSource={(batchRuns.data || []).filter((item) => item.chair_complete)} pagination={{ pageSize: 6 }} rowSelection={{ selectedRowKeys: batchRunIds, onChange: setBatchRunIds }} columns={[{ title: '病例', dataIndex: 'case_id' }, { title: '运行 ID', dataIndex: 'id', ellipsis: true }]} />
        <Space size={16}><span>病例并发 <InputNumber min={1} max={16} value={batchCaseConcurrency} onChange={(value) => setBatchCaseConcurrency(value || 1)} /></span><span>模型请求并发 <InputNumber min={1} max={64} value={batchRequestConcurrency} onChange={(value) => setBatchRequestConcurrency(value || 1)} /></span></Space>
        {batchMutation.isError && <Alert className="section-gap" type="error" showIcon title="无法创建讨论批次" description={batchMutation.error.message} />}
      </Modal>

      {value.decision_state && <DecisionStatePanel state={value.decision_state} />}

      {rounds.length > 0 ? (
        <>
          <div className="discussion-round-switcher">
            <Segmented value={round?.round_number} onChange={setSelectedRound} options={rounds.map((item) => ({ label: `第 ${item.round_number} 轮`, value: item.round_number }))} />
            <Space size={6}><StatusTag status={roundStatus} /><Text type="secondary">点击任务或流程节点查看问题、回答与证据</Text></Space>
          </div>
          <div className="discussion-live-grid">
            <TaskAssignment round={round} selectedTaskId={selectedTaskId} onSelect={setSelectedTaskId} />
            <DiscussionTrace round={round} selectedTaskId={selectedTaskId} onSelect={setSelectedTaskId} reportStatus={value.final_report ? 'completed' : value.report_status || 'waiting'} />
            <QuestionDetail task={selectedTask} answer={selectedAnswer} progress={selectedProgress} reviews={selectedReviews} />
          </div>
          {(round?.chair_result || round?.round_number === value.active_round?.round_number) && (
            <Card className="discussion-chair-tabs" title={<Space><TeamOutlined /><span>主持人第 {round.round_number} 轮更新</span></Space>} extra={<Text type="secondary">专科回应回填后，由主持人更新同一套五板块</Text>}>
              {round.chair_result ? <>
                <div className="discussion-change-summary">
                  <Text strong>{previousChairResult ? '相比上一轮' : '本轮变化'}</Text>
                  <Space size={[6, 6]} wrap>{chairChangeSummary(round.chair_result, previousChairResult).map((item) => <Tag color="blue" key={item}>{item}</Tag>)}</Space>
                </div>
                <ChairResultTabs result={round.chair_result} />
              </> : <div className="discussion-chair-pending">{chairProgress === 'failed' ? <ExclamationCircleFilled className="discussion-icon-error" /> : <Spin />}<Text type="secondary">{chairProgress === 'running' ? '主持人正在整合本轮结果…' : chairProgress === 'failed' ? '主持人整合失败；已保留本轮已生成内容' : '等待全部专科回答后开始整合'}</Text></div>}
            </Card>
          )}
          {round?.round_decision?.stop_reason && (
            <Alert className="section-gap" type="info" showIcon title="本轮决策" description={round.round_decision.stop_reason} />
          )}
        </>
      ) : (
        <Card className="section-card discussion-empty-card"><Empty image={<FileSearchOutlined />} description="尚未产生团队讨论轮次；点击“运行团队讨论”后，这里会实时出现任务与处理进度。" /></Card>
      )}
      {value.stop_reason && <Alert className="section-gap" type="success" showIcon title="讨论停止原因" description={value.stop_reason} />}
    </div>
  )
}
