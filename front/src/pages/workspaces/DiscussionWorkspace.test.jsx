import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../../api'
import { DiscussionWorkspace } from './DiscussionWorkspace'

vi.mock('../../api', () => ({
  api: {
    discussion: vi.fn(),
    runDiscussion: vi.fn(),
  },
}))

const chairResult = {
  integrated_conclusions: [],
  assessment_boundaries: [],
  conflicts: [],
  questions: [],
  evidence_needs: [],
}

const completed = {
  status: 'completed',
  runnable: true,
  current_round: 1,
  stop_reason: '没有仍需专科处理的问题或冲突。',
  rounds: [{
    round_number: 1,
    tasks: [{
      task_id: 'R01-Q001-pulmonology',
      issue_type: 'question',
      issue_id: 'Q001',
      specialty: 'pulmonology',
      prompt: '低氧的主要归因是什么？',
      remaining_clarification: '区分肺实质与肺血管因素。',
      current_result: '现有资料不足。',
      specialty_context: [{
        specialty: 'thoracic_radiology',
        relation: 'partial_answer',
        answer: '影像提示肺实质因素可能参与。',
      }],
      evidence_candidates: [{ evidence_ref: 'gu-1:ev-1' }],
    }],
    specialty_responses: [{
      specialty: 'pulmonology',
      answers: [{
        answer_id: 'R01-A001-pulmonology',
        task_id: 'R01-Q001-pulmonology',
        issue_id: 'Q001',
        answerability: 'partially_answered',
        confidence: 'moderate',
        answer: '现有证据支持低氧存在，但不能量化各因素贡献。',
        medical_basis: '原始片段仅证明低氧存在。',
        answer_claims: [{
          claim_id: 'R01-A001-pulmonology-C001',
          statement: '现有证据支持低氧存在，但不能量化各因素贡献。',
          evidence_uses: [{
            evidence_ref: 'E006',
            graph_unit_id: 'gu-1',
            quote: '静息低氧',
            evidence_ids: ['ev-1'],
            proposition_ids: ['gu-1::prop-1'],
            effect: 'supporting',
            interpretation: '证明低氧存在，但不能单独证明病因。',
          }],
          guideline_evidence: [],
        }],
        evidence_uses: [{
          evidence_ref: 'E006',
          graph_unit_id: 'gu-1',
          quote: '静息低氧',
          evidence_ids: ['ev-1'],
          proposition_ids: ['gu-1::prop-1'],
          effect: 'supporting',
          interpretation: '证明低氧存在，但不能单独证明病因。',
          propositions: [{ proposition_id: 'gu-1::prop-1', concept_text: '存在低氧', status: 'present', certainty: 'high' }],
          graph_nodes: [{ node_id: 'gu-1::prop-1', label: '低氧节点' }],
        }],
        guideline_evidence: [{ guideline_id: 'guide-1' }],
      }],
    }],
    chair_result: chairResult,
    round_decision: {
      continue_discussion: false,
      stop_reason: '当前已无仍需专科处理的问题或真实冲突。',
    },
  }],
  final_report: {
    consensus_status: 'consensus_with_boundaries',
    discussion_rounds: 1,
    primary_conclusion: '慢性纤维化性间质性肺病。',
    diagnostic_confidence: '中等。',
    integrated_summary: '在当前证据边界内形成共识。',
    discussion_summary: '完成一轮定向讨论。',
    evidence_basis: ['病例原文与指南解释规则。'],
  },
}

const active = {
  status: 'running',
  runnable: true,
  current_round: 1,
  max_rounds: 3,
  rounds: [],
  report_status: 'waiting',
  active_round: {
    round_number: 1,
    status: 'running',
    started_at: '2026-07-23T00:00:00Z',
    chair_status: 'waiting',
    chair_result: null,
    tasks: completed.rounds[0].tasks,
    task_progress: {
      'R01-Q001-pulmonology': {
        status: 'running',
        started_at: '2026-07-23T00:00:01Z',
        completed_at: '',
        answer: null,
        error: '',
      },
    },
  },
}

class FakeEventSource {
  static instances = []
  constructor(url) {
    this.url = url
    this.listeners = {}
    FakeEventSource.instances.push(this)
  }
  addEventListener(type, listener) { this.listeners[type] = listener }
  removeEventListener(type) { delete this.listeners[type] }
  emit(type) { this.listeners[type]?.({ data: '{}' }) }
  close() {}
}

function renderWorkspace() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <DiscussionWorkspace runId="run-1" />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  api.discussion.mockReset()
  api.runDiscussion.mockReset()
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  FakeEventSource.instances = []
})

describe('DiscussionWorkspace', () => {
  it('starts only the discussion stage from existing outputs', async () => {
    api.discussion.mockResolvedValue({ status: 'pending', runnable: true, rounds: [] })
    api.runDiscussion.mockResolvedValue({ status: 'running', runnable: true, rounds: [] })
    renderWorkspace()

    fireEvent.click(await screen.findByRole('button', { name: '运行团队讨论' }))

    await waitFor(() => expect(api.runDiscussion).toHaveBeenCalledWith('run-1'))
    expect(screen.getByText(/实时显示任务分配、专科处理、证据使用和主持人更新/)).toBeInTheDocument()
  })

  it('shows task routing, evidence interpretation, chair update, and final report', async () => {
    api.discussion.mockResolvedValue(completed)
    renderWorkspace()

    expect((await screen.findAllByText('最终 MDT 统一报告')).length).toBeGreaterThan(1)
    expect(screen.getAllByText('低氧的主要归因是什么？').length).toBeGreaterThan(1)
    expect(screen.getByText('现有资料不足。')).toBeInTheDocument()
    expect(screen.getByText('影像提示肺实质因素可能参与。')).toBeInTheDocument()
    expect(screen.getByText('区分肺实质与肺血管因素。')).toBeInTheDocument()
    expect(screen.getByText('现有证据支持低氧存在，但不能量化各因素贡献。')).toBeInTheDocument()
    expect(screen.getByText('证明低氧存在，但不能单独证明病因。')).toBeInTheDocument()
    expect(screen.getAllByText('gu-1::prop-1').length).toBeGreaterThan(0)
    expect(screen.queryByText('E006')).not.toBeInTheDocument()
    expect(screen.getByText('主持人第 1 轮更新')).toBeInTheDocument()
    expect(screen.getAllByText('跨专科整合结论').length).toBeGreaterThan(1)
    expect(screen.getByText('本轮判断边界（不可评价）')).toBeInTheDocument()
    expect(screen.getByText('跨专科真实冲突')).toBeInTheDocument()
    expect(screen.getByText('仍需其他专科回答的问题')).toBeInTheDocument()
    expect(screen.getByText('证据需求及满足状态')).toBeInTheDocument()
    expect(screen.getByText('讨论前主持人基线不计入轮次')).toBeInTheDocument()
    expect(screen.getByText('本轮决策')).toBeInTheDocument()
  })

  it('refreshes visible task progress when a discussion event arrives', async () => {
    vi.stubGlobal('EventSource', FakeEventSource)
    api.discussion.mockResolvedValue(active)
    renderWorkspace()

    expect(await screen.findByText('专科正在使用证据形成回答…')).toBeInTheDocument()
    expect(screen.getAllByText('分析中').length).toBeGreaterThan(0)

    const answer = completed.rounds[0].specialty_responses[0].answers[0]
    api.discussion.mockResolvedValue({
      ...active,
      active_round: {
        ...active.active_round,
        task_progress: {
          'R01-Q001-pulmonology': {
            ...active.active_round.task_progress['R01-Q001-pulmonology'],
            status: 'completed',
            completed_at: '2026-07-23T00:00:08Z',
            answer,
          },
        },
      },
    })
    FakeEventSource.instances[0].emit('discussion_task_completed')

    expect(await screen.findByText(answer.answer)).toBeInTheDocument()
    await waitFor(() => expect(api.discussion).toHaveBeenCalledTimes(2))
  })

  it('keeps failed partial output visible without presenting it as running', async () => {
    api.discussion.mockResolvedValue({
      ...active,
      status: 'failed',
      error: '主持人结构化输出失败',
      active_round: {
        ...active.active_round,
        status: 'failed',
        chair_status: 'waiting',
      },
    })
    renderWorkspace()

    expect(await screen.findByText('团队讨论失败；已保留完成的步骤')).toBeInTheDocument()
    expect(screen.getByText('主持人整合失败；已保留本轮已生成内容')).toBeInTheDocument()
    expect(screen.queryByText(/已用时/)).not.toBeInTheDocument()
    expect(screen.getAllByText('失败').length).toBeGreaterThan(0)
  })
})
