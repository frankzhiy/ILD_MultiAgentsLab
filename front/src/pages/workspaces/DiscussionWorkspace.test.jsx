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
      remaining_clarification: '区分肺实质与肺血管因素。',
      current_result: '现有资料不足。',
      evidence_candidates: [{ evidence_ref: 'gu-1:ev-1' }],
    }],
    specialty_responses: [{
      specialty: 'pulmonology',
      answers: [{
        answer_id: 'R01-A001-pulmonology',
        issue_id: 'Q001',
        answerability: 'partially_answered',
        confidence: 'moderate',
        answer: '现有证据支持低氧存在，但不能量化各因素贡献。',
        medical_basis: '原始片段仅证明低氧存在。',
        evidence_uses: [{
          evidence_ref: 'gu-1:ev-1',
          graph_unit_id: 'gu-1',
          quote: '静息低氧',
          evidence_ids: ['ev-1'],
          effect: 'supporting',
          interpretation: '证明低氧存在，但不能单独证明病因。',
          propositions: [{ proposition_id: 'gu-1::prop-1', concept_text: '存在低氧', status: 'present', certainty: 'high' }],
          graph_nodes: [{ node_id: 'gu-1::prop-1', label: '低氧节点' }],
        }],
        guideline_evidence: [{ guideline_id: 'guide-1' }],
      }],
    }],
    chair_result: chairResult,
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

afterEach(cleanup)

describe('DiscussionWorkspace', () => {
  it('starts only the discussion stage from existing outputs', async () => {
    api.discussion.mockResolvedValue({ status: 'pending', runnable: true, rounds: [] })
    api.runDiscussion.mockResolvedValue({ status: 'running', runnable: true, rounds: [] })
    renderWorkspace()

    fireEvent.click(await screen.findByRole('button', { name: '运行团队讨论' }))

    await waitFor(() => expect(api.runDiscussion).toHaveBeenCalledWith('run-1'))
    expect(screen.getByText(/不会重跑语义图、首轮专科或初始主持人/)).toBeInTheDocument()
  })

  it('shows task routing, evidence interpretation, chair update, and final report', async () => {
    api.discussion.mockResolvedValue(completed)
    renderWorkspace()

    expect(await screen.findByText('最终 MDT 统一报告')).toBeInTheDocument()
    expect(screen.getByText('区分肺实质与肺血管因素。')).toBeInTheDocument()
    expect(screen.getByText('现有证据支持低氧存在，但不能量化各因素贡献。')).toBeInTheDocument()
    expect(screen.getByText('证明低氧存在，但不能单独证明病因。')).toBeInTheDocument()
    expect(screen.getByText('Evidence ID：ev-1')).toBeInTheDocument()
    expect(screen.getByText('低氧节点')).toBeInTheDocument()
    expect(screen.getByText('主持人第 1 轮更新')).toBeInTheDocument()
    expect(screen.getByText('跨专科整合结论')).toBeInTheDocument()
  })
})
