import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { PlayCircleOutlined, ReloadOutlined } from '@ant-design/icons'
import { Alert, Button, Card, Empty, Skeleton, Space, Tag, Typography } from 'antd'
import { api } from '../../api'
import { QueryError } from '../../components/QueryState'
import { StatusTag } from '../../components/StatusTag'
import { ChairResultBoards } from './ChairWorkspace'

const { Paragraph, Text, Title } = Typography

const SPECIALTIES = {
  pulmonology: '呼吸科',
  thoracic_radiology: '胸部影像科',
  rheumatology: '风湿免疫科',
  pathology: '病理科',
}

const EFFECTS = {
  supporting: ['支持', 'green'],
  weakening: ['削弱', 'red'],
  discriminating: ['鉴别', 'purple'],
  background: ['背景', 'default'],
}

const ANSWERABILITY = {
  answered: ['已回答', 'success'],
  partially_answered: ['部分回答', 'warning'],
  not_assessable: ['不可评价', 'default'],
}

function specialtyLabel(value) {
  return SPECIALTIES[value] || value
}

function EvidenceUse({ item }) {
  const [effect, color] = EFFECTS[item.effect] || [item.effect, 'default']
  return (
    <div className="discussion-evidence-use">
      <Space size={[5, 5]} wrap>
        <Tag color={color}>{effect}</Tag>
        <Text code>{item.evidence_ref}</Text>
        {item.graph_unit_id && <Text type="secondary">图单元 {item.graph_unit_id}</Text>}
        {item.evidence_ids?.map((evidenceId) => <Tag key={evidenceId}>Evidence ID：{evidenceId}</Tag>)}
      </Space>
      {item.quote && <blockquote>{item.quote}</blockquote>}
      {item.propositions?.length > 0 && (
        <div className="discussion-propositions">
          {item.propositions.map((proposition) => (
            <Tag key={proposition.proposition_id}>{proposition.concept_text} · {proposition.status} · {proposition.certainty}</Tag>
          ))}
        </div>
      )}
      {item.graph_nodes?.length > 0 && (
        <div className="discussion-propositions">
          <Text strong>相关图节点：</Text>
          {item.graph_nodes.map((node) => <Tag key={node.node_id}>{node.label || node.node_id}</Tag>)}
        </div>
      )}
      <Paragraph><Text strong>证据如何影响判断：</Text>{item.interpretation}</Paragraph>
    </div>
  )
}

function RoundView({ round }) {
  return (
    <div className="discussion-round">
      <Card title={`第 ${round.round_number} 轮任务分配`} className="section-card discussion-task-card">
        {round.tasks?.length ? round.tasks.map((task) => (
          <article className="formal-item" key={task.task_id}>
            <Space size={[5, 5]} wrap>
              <Tag color={task.issue_type === 'conflict' ? 'volcano' : 'purple'}>{task.issue_type === 'conflict' ? '冲突' : '待回答问题'}</Tag>
              <Tag color="blue">{specialtyLabel(task.specialty)}</Tag>
              <Text code>{task.issue_id}</Text>
            </Space>
            <Title level={5}>{task.remaining_clarification || task.prompt}</Title>
            {task.current_result && <Paragraph><Text strong>主持人当前结果：</Text>{task.current_result}</Paragraph>}
            <Text type="secondary">已向专科提供 {task.evidence_candidates?.length || 0} 组原始证据包</Text>
          </article>
        )) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="本轮没有任务" />}
      </Card>

      <Card title={`第 ${round.round_number} 轮专科回应`} className="section-card discussion-response-card">
        {round.specialty_responses?.map((response) => (
          <section className="discussion-specialty" key={response.specialty}>
            <Title level={5}>{specialtyLabel(response.specialty)}</Title>
            {response.answers?.map((answer) => {
              const [label, color] = ANSWERABILITY[answer.answerability] || [answer.answerability, 'default']
              return (
                <article className="formal-item" key={answer.answer_id}>
                  <Space size={[5, 5]} wrap>
                    <Tag color={color}>{label}</Tag>
                    <Tag>置信度：{answer.confidence}</Tag>
                    <Text code>{answer.issue_id}</Text>
                  </Space>
                  <Paragraph className="discussion-answer">{answer.answer}</Paragraph>
                  <Paragraph><Text strong>医学依据：</Text>{answer.medical_basis}</Paragraph>
                  {answer.evidence_uses?.map((item) => <EvidenceUse item={item} key={`${answer.answer_id}-${item.evidence_ref}`} />)}
                  {answer.guideline_evidence?.length > 0 && (
                    <Space size={[5, 5]} wrap>
                      <Text strong>指南依据：</Text>
                      {answer.guideline_evidence.map((item) => <Tag color="gold" key={item.guideline_id || item.chunk_id}>{item.guideline_id || item.chunk_id}</Tag>)}
                    </Space>
                  )}
                  {answer.remaining_limitation && <div className="limitation-note"><Text strong>仍受限于：</Text>{answer.remaining_limitation}</div>}
                </article>
              )
            })}
          </section>
        ))}
      </Card>

      <div className="discussion-chair-heading">
        <Title level={4}>主持人第 {round.round_number} 轮更新</Title>
        <Text type="secondary">专科回应已回填为正式来源，再由主持人更新以下五个板块。</Text>
      </div>
      <ChairResultBoards result={round.chair_result} />
    </div>
  )
}

function FinalReport({ report }) {
  if (!report) return null
  const rows = [
    ['主要结论', report.primary_conclusion],
    ['诊断把握度', report.diagnostic_confidence],
    ['整合摘要', report.integrated_summary],
    ['讨论过程摘要', report.discussion_summary],
  ]
  return (
    <Card title="最终 MDT 统一报告" className="section-card discussion-final-report">
      <Space size={[5, 5]} wrap>
        <Tag color="blue">{report.consensus_status}</Tag>
        <Tag>共 {report.discussion_rounds} 轮</Tag>
      </Space>
      {rows.map(([label, value]) => value && <Paragraph key={label}><Text strong>{label}：</Text>{value}</Paragraph>)}
      {[
        ['证据基础', report.evidence_basis],
        ['判断边界', report.assessment_boundaries],
        ['未解决冲突', report.unresolved_conflicts],
        ['后续证据需求', report.evidence_needs],
      ].map(([label, values]) => values?.length > 0 && (
        <div className="discussion-report-list" key={label}><Text strong>{label}：</Text><ul>{values.map((value, index) => <li key={`${label}-${index}`}>{value}</li>)}</ul></div>
      ))}
    </Card>
  )
}

export function DiscussionWorkspace({ runId }) {
  const queryClient = useQueryClient()
  const query = useQuery({
    queryKey: ['discussion', runId],
    queryFn: () => api.discussion(runId),
    refetchInterval: (current) => current.state.data?.status === 'running' ? 2500 : false,
  })
  const mutation = useMutation({
    mutationFn: () => api.runDiscussion(runId),
    onSuccess: (value) => {
      queryClient.setQueryData(['discussion', runId], value)
      queryClient.invalidateQueries({ queryKey: ['discussion', runId] })
      queryClient.invalidateQueries({ queryKey: ['run', runId] })
    },
  })

  if (query.isError) return <QueryError error={query.error} retry={query.refetch} />
  if (query.isLoading) return <Skeleton active paragraph={{ rows: 12 }} />

  const value = query.data || {}
  const running = value.status === 'running' || mutation.isPending
  const hasResult = value.rounds?.length > 0 || value.final_report
  const rounds = value.rounds || []

  return (
    <div className="chair-workspace discussion-workspace">
      <div className="workspace-title">
        <div>
          <Text className="eyebrow">MDT DISCUSSION</Text>
          <Title level={3}>MDT 团队讨论</Title>
          <Space size={6}><StatusTag status={running ? 'running' : value.status} /><Text type="secondary">主持人总结 → 相关专科处理 → 主持人更新，最多三轮</Text></Space>
        </div>
        <Button
          type="primary"
          aria-label={hasResult ? '重新运行团队讨论' : '运行团队讨论'}
          icon={hasResult ? <ReloadOutlined /> : <PlayCircleOutlined />}
          loading={running}
          disabled={!value.runnable || running}
          onClick={() => mutation.mutate()}
        >
          {hasResult ? '重新运行团队讨论' : '运行团队讨论'}
        </Button>
      </div>

      <Alert className="section-gap" type="info" showIcon title="开发阶段直接运行入口" description="直接使用当前主持人五板块、四个专科正式输出和现有证据图；不会重跑语义图、首轮专科或初始主持人。任务按待回答问题的目标专科、冲突的相关专科自动分配。" />
      {value.status === 'unavailable' && <Alert className="section-gap" type="warning" showIcon title="团队讨论尚不可运行" description={value.error} />}
      {value.status === 'pending' && <Alert className="section-gap" type="success" showIcon title="现有输出已就绪" description="可以直接启动团队讨论。" />}
      {value.status === 'outdated' && <Alert className="section-gap" type="warning" showIcon title="主持人结果已更新" description="下方是基于旧主持人结果的讨论记录。请重新运行团队讨论以匹配当前结果。" />}
      {value.status === 'failed' && <Alert className="section-gap" type="error" showIcon title="团队讨论失败" description={value.error} />}
      {mutation.isError && <Alert className="section-gap" type="error" showIcon title="无法启动团队讨论" description={mutation.error.message} />}
      {value.stop_reason && <Alert className="section-gap" type="success" showIcon title="讨论停止原因" description={value.stop_reason} />}

      <FinalReport report={value.final_report} />
      {rounds.length > 0
        ? <div className="discussion-rounds">{rounds.map((round) => <RoundView round={round} key={round.round_number} />)}</div>
        : <Card className="section-card"><Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚未产生团队讨论轮次" /></Card>}
    </div>
  )
}
