import { useCallback, useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ApiOutlined, CheckCircleFilled, ClockCircleOutlined, DisconnectOutlined,
  ExclamationCircleFilled, FileSearchOutlined, PlayCircleOutlined, ReloadOutlined,
  TeamOutlined,
} from '@ant-design/icons'
import { Alert, Button, Card, Collapse, Empty, Progress, Segmented, Skeleton, Space, Spin, Table, Tag, Timeline, Typography } from 'antd'
import { api } from '../../api'
import { Citation, CitationGroup } from '../../components/Citation'
import { QueryError } from '../../components/QueryState'
import { StatusTag } from '../../components/StatusTag'
import { ChairResultTabs } from './ChairWorkspace'
import { FinalReport } from './FinalReport'

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

const DISCUSSION_EVENTS = [
  'manual_stage_started',
  'discussion_started',
  'discussion_round_started',
  'discussion_task_started',
  'discussion_task_completed',
  'discussion_task_failed',
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

export function DiscussionWorkspace({ runId }) {
  const queryClient = useQueryClient()
  const query = useQuery({
    queryKey: ['discussion', runId],
    queryFn: () => api.discussion(runId),
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
      const previous = queryClient.getQueryData(['discussion', runId])
      queryClient.cancelQueries({ queryKey: ['discussion', runId] })
      queryClient.setQueryData(['discussion', runId], (current = {}) => ({
        ...current,
        status: 'running',
        error: null,
      }))
      return { previous }
    },
    onSuccess: (value) => {
      queryClient.setQueryData(['discussion', runId], value)
      queryClient.invalidateQueries({ queryKey: ['run', runId] })
    },
    onError: (_error, _variables, context) => {
      queryClient.setQueryData(['discussion', runId], context?.previous)
    },
  })
  const [selectedRound, setSelectedRound] = useState()
  const [selectedTaskId, setSelectedTaskId] = useState()

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
          <Button type="primary" aria-label={hasResult ? '重新运行团队讨论' : '运行团队讨论'} icon={hasResult ? <ReloadOutlined /> : <PlayCircleOutlined />} loading={running} disabled={!value.runnable || running} onClick={() => mutation.mutate()}>{hasResult ? '重新运行团队讨论' : '运行团队讨论'}</Button>
        </div>
      </div>

      {connection === 'disconnected' && <Alert className="section-gap" type="warning" showIcon title="实时事件流已断开" description="页面会自动重连，并每 2 秒从服务端恢复一次讨论进度。" />}
      {running && <Alert className="section-gap" type="info" showIcon title="新一轮团队讨论已启动" description={hasResult ? '正在初始化任务；下方暂时保留上一次运行结果，新进度写入后会自动替换。' : '正在初始化任务与运行资源，新进度写入后会自动显示。'} />}
      {value.status === 'unavailable' && <Alert className="section-gap" type="warning" showIcon title="团队讨论尚不可运行" description={value.error} />}
      {value.status === 'pending' && <Alert className="section-gap" type="info" showIcon title="现有输出已就绪" description="启动后将实时显示任务分配、专科处理、证据使用和主持人更新。" />}
      {value.status === 'outdated' && <Alert className="section-gap" type="warning" showIcon title="主持人结果已更新" description="下方是基于旧主持人结果的讨论记录，请重新运行以匹配当前结果。" />}
      {value.status === 'failed' && <Alert className="section-gap" type="error" showIcon title="团队讨论失败；已保留完成的步骤" description={value.error} />}
      {mutation.isError && <Alert className="section-gap" type="error" showIcon title="无法启动团队讨论" description={mutation.error.message} />}

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
      <FinalReport report={value.final_report} />
      {value.stop_reason && <Alert className="section-gap" type="success" showIcon title="讨论停止原因" description={value.stop_reason} />}
    </div>
  )
}
