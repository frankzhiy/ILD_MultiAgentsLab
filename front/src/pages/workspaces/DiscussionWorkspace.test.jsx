import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../../api'
import { chairChangeSummary, DiscussionWorkspace } from './DiscussionWorkspace'

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

const diagnosticDimensions = [
  ['ild_presence', '存在纤维化性间质性肺病。', 'favored', 'moderate', 'primary'],
  ['radiologic_pattern', '影像所见支持双肺异常，具体模式尚不能分类。', 'unclassifiable', 'low', 'boundary'],
  ['histopathologic_pattern', '本轮无病理资料，无法作组织学判断。', 'not_assessable', 'unknown', 'boundary'],
  ['mdt_diagnosis', '纤维化性间质性肺病工作诊断，具体类型待分类。', 'favored', 'moderate', 'primary'],
  ['etiologic_attribution', '病因未分类。', 'unclassifiable', 'low', 'boundary'],
  ['disease_behavior', '缺少纵向资料，PPF 不可评价。', 'not_assessable', 'unknown', 'boundary'],
  ['acute_or_comorbid_factors', '近期低氧可能为多因素参与。', 'possible', 'low', 'cannot_safely_ignore'],
]

const completedV2 = {
  ...completed,
  final_report: {
    schema_version: 'mdt_final_report.v2',
    consensus_status: 'consensus_with_boundaries',
    discussion_rounds: 1,
    report_scope: 'diagnostic_only',
    clinical_report: {
      overall_conclusion: '纤维化性间质性肺病工作诊断，具体类型待分类。',
      overall_confidence: 'moderate',
      integrated_summary: '模式诊断与病因诊断分别保留边界。',
      diagnostic_matrix: diagnosticDimensions.map(([dimension, statement, status, confidence, role]) => ({
        dimension, statement, status, confidence, role, medical_basis: '基于现有 MDT 整合。', chair_item_ids: ['IC001'], limitations: [],
      })),
      differential_diagnoses: [{ rank: 1, diagnosis: '特发性肺纤维化', confidence: 'low', rationale: '现有影像所见和临床资料尚不足以支持该病因。', chair_item_ids: ['IC001'] }],
    },
    reasoning_trace: [{
      claim_id: 'DX01',
      claim_statement: '存在纤维化性间质性肺病。',
      chair_item_ids: ['IC001'],
      medical_basis: '呼吸科判断与影像所见一致。',
      source_citations: [{ source_ref: 'S001', specialty: 'pulmonology', source_path: 'specialty_assessments.assessments[0]', quote: '呼吸科工作诊断原话。' }],
      evidence: { supporting: [{ evidence_ref: 'E001', graph_unit_id: 'gu-1', evidence_ids: ['ev-1'], quote: '静息低氧' }] },
      guideline_evidence: [{ chunk_id: 'guide-1', quote_unit_ids: ['unit-1'], guideline_id: 'guide-1', title: 'ILD 诊断指南', source_file: 'guide.pdf', page: 3, quote: '多学科综合诊断。' }],
      limitations: [],
    }],
    assessment_boundaries: [],
    evidence_needs: [],
    evidence_need_groups: [
      { source_refs: ['S011'], required_information: '既有抗体结果', decision_role: 'blocking_boundary' },
      { source_refs: ['S012'], required_information: '相关职业暴露史', decision_role: 'non_blocking_refinement' },
      { source_refs: ['S013'], required_information: '可比影像报告', decision_role: 'limitation_only' },
    ],
    unresolved_conflicts: [],
    discussion_audit: {
      decisions: [{
        issue_id: 'Q001', issue_type: 'question', question: '低氧的主要归因是什么？', why_it_matters: '影响急性问题归因。', baseline_result: '现有资料不足。', final_status: 'closed', final_result: '接受多因素边界。', decision_impact: '避免将低氧直接归因于 ILD 进展。',
        rounds: [{ round_number: 1, task_id: 'R01-Q001-pulmonology', specialty: 'pulmonology', prompt: '低氧的主要归因是什么？', answer: '不能量化各因素贡献。', answerability: 'partially_answered', confidence: 'moderate', changed_from_previous: false, reviews: [{ reviewer_specialty: 'thoracic_radiology', outcome: 'accept_boundary', rationale: '接受边界。' }], chair_result_after_round: '转为带边界共识。', closure: 'accept_boundary' }],
      }],
      conflicts: [],
      stop_reason: '当前仅剩判断边界。',
    },
    research_metrics: { diagnostic_claims: 7, claims_with_specialty_citations: 1, claims_with_patient_evidence: 1, claims_with_guideline_citations: 0, discussion_issues: 1, closed_issues: 1, formal_conflicts: 0, resolved_formal_conflicts: 0, unresolved_formal_conflicts: 0, assessment_boundaries: 0 },
    judgment_changes: [{ transaction_id: 'R01-pulmonology-judgment-update', specialty: 'pulmonology', judgment_id: 'pulmonology_001', change_type: 'qualify', before_version_id: 'pulmonology_001@v001', after_version_id: 'pulmonology_001@v002', round_number: 1, trigger_issue_ids: ['Q001'], considered_source_refs: ['R01-Q001-thoracic_radiology-A'], rationale: '影像科意见要求收紧判断边界。', changed_fields: ['statement', 'limitations'] }],
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
    api.discussion.mockResolvedValue({
      status: 'pending', runnable: true, rounds: [],
      decision_state: {
        judgments: [{ judgment_id: 'pulmonology_001', specialty: 'pulmonology', active_version_id: 'v1', versions: [{ version_id: 'v1', assessment: { statement: '首轮专科判断。' } }] }],
      },
    })
    api.runDiscussion.mockResolvedValue({ status: 'running', runnable: true, rounds: [] })
    renderWorkspace()

    expect(await screen.findByText('尚未产生团队讨论轮次；点击“运行团队讨论”后，这里会实时出现任务与处理进度。')).toBeInTheDocument()
    expect(screen.getByText('首轮专科判断。')).toBeInTheDocument()
    fireEvent.click(await screen.findByRole('button', { name: '运行团队讨论' }))

    await waitFor(() => expect(api.runDiscussion).toHaveBeenCalledWith('run-1'))
    expect(screen.getByText('新一轮团队讨论已启动')).toBeInTheDocument()
  })

  it('immediately replaces a previous failure with a stable rerun status', async () => {
    let finishStart
    api.discussion.mockResolvedValue({
      ...active,
      status: 'failed',
      error: '上一次运行失败',
      active_round: { ...active.active_round, status: 'failed' },
    })
    api.runDiscussion.mockImplementation(() => new Promise((resolve) => { finishStart = resolve }))
    renderWorkspace()

    fireEvent.click(await screen.findByRole('button', { name: '重新运行团队讨论' }))

    expect(await screen.findByText('新一轮团队讨论已启动')).toBeInTheDocument()
    expect(screen.getByText(/下方暂时保留上一次运行结果/)).toBeInTheDocument()
    expect(screen.queryByText('团队讨论失败；已保留完成的步骤')).not.toBeInTheDocument()

    finishStart({ ...active, status: 'running', error: null })
    await waitFor(() => expect(api.runDiscussion).toHaveBeenCalledWith('run-1'))
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
    expect(screen.queryByText('gu-1::prop-1')).not.toBeInTheDocument()
    expect(screen.queryByText('E006')).not.toBeInTheDocument()
    expect(screen.getByText('主持人第 1 轮更新')).toBeInTheDocument()
    expect(screen.getByText('首轮主持人更新，作为后续轮次的比较基线')).toBeInTheDocument()
    expect(screen.getAllByText('跨专科整合结论').length).toBeGreaterThan(1)
    expect(screen.getByText('本轮判断边界（不可评价）')).toBeInTheDocument()
    expect(screen.getByText('跨专科真实冲突')).toBeInTheDocument()
    expect(screen.getByText('仍需其他专科回答的问题')).toBeInTheDocument()
    expect(screen.getByText('证据需求及满足状态')).toBeInTheDocument()
    expect(screen.getByText('讨论前主持人基线不计入轮次')).toBeInTheDocument()
    expect(screen.getByText('本轮决策')).toBeInTheDocument()
  })

  it('shows a clinical report and its diagnostic evidence without research audit text', async () => {
    api.discussion.mockResolvedValue(completedV2)
    const { container } = renderWorkspace()

    expect(await screen.findByRole('tab', { name: 'MDT 最终报告' })).toBeInTheDocument()
    expect(container.querySelector('.final-report-summary').textContent).toContain('纤维化性间质性肺病工作诊断，具体类型待分类。')
    expect(screen.queryByText('分层诊断矩阵')).not.toBeInTheDocument()
    expect(screen.queryByText('鉴别诊断')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /S001/ })).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('tab', { name: '诊断依据' }))
    expect(await screen.findByText('呼吸科判断与影像所见一致。')).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: /(病历原文|专科)/ }).length).toBeGreaterThan(0)
    expect(screen.getByText('判断边界')).toBeInTheDocument()
    expect(screen.getByText('资料缺口与证据需求')).toBeInTheDocument()
    expect(screen.getByText('影响当前判断：')).toBeInTheDocument()
    expect(screen.getByText('值得后续追索：')).toBeInTheDocument()
    expect(screen.getByText('仅记录限制：')).toBeInTheDocument()
    expect(screen.getAllByText('指南依据').length).toBeGreaterThan(0)
    expect(screen.getByText('未解决的专科分歧')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /S001/ })).not.toBeInTheDocument()

    expect(screen.queryByRole('tab', { name: '讨论与研究审计' })).not.toBeInTheDocument()
    expect(screen.queryByText('诊断型报告')).not.toBeInTheDocument()
  })

  it('renders one narrative from the matrix diagnosis and secondary judgments', async () => {
    const result = structuredClone(completedV2)
    result.final_report.schema_version = 'mdt_final_report.v5'
    const diagnosis = result.final_report.clinical_report.diagnostic_matrix.find((item) => item.dimension === 'mdt_diagnosis')
    diagnosis.statement = '首选考虑特发性肺纤维化，尚未确诊。'
    diagnosis.medical_basis = 'HRCT 呈纤维化表型，但 UIP 模式尚不能确认。'
    Object.assign(result.final_report.clinical_report, {
      overall_conclusion: '旧版独立结论不应显示。',
      integrated_summary: '旧版独立摘要不应显示。',
      secondary_judgments: [{ statement: '结缔组织病相关间质性肺病仍需鉴别。', medical_basis: '存在有限自身免疫线索。', chair_item_ids: ['IC001'] }],
    })
    api.discussion.mockResolvedValue(result)
    const { container } = renderWorkspace()

    expect(await screen.findByRole('tab', { name: 'MDT 最终报告' })).toBeInTheDocument()
    const text = container.querySelector('.final-report-summary').textContent
    const positions = ['首选考虑特发性肺纤维化', 'HRCT 呈纤维化表型', '结缔组织病相关间质性肺病仍需鉴别', '存在有限自身免疫线索'].map((part) => text.indexOf(part))
    expect(positions.every((position) => position >= 0)).toBe(true)
    expect(positions).toEqual([...positions].sort((a, b) => a - b))
    expect(text).not.toContain('旧版独立')
    expect(text).not.toContain('主要诊断：')
  })

  it('shows the current specialty judgment and preserves its version history', async () => {
    api.discussion.mockResolvedValue({
      ...completed,
      decision_state: {
        revision: 2,
        judgments: [{
          judgment_id: 'pulmonology_001',
          specialty: 'pulmonology',
          active_version_id: 'pulmonology_001@v002',
          versions: [
            { version_id: 'pulmonology_001@v001', change_type: 'initial', lifecycle_status: 'superseded', created_in_round: 0, rationale: '首轮专科判断。', changed_fields: [], assessment: { statement: '现有资料支持纤维化性间质性肺病。', medical_basis: '根据病例文字。', conditions: { subject: '纤维化性间质性肺病', professional_level: 'disease_diagnosis', timeframe: { description: '当前评估时点。' }, evidence_scope: { evidence_ids: ['ev_001'], scope_limitations: [] } } } },
            { version_id: 'pulmonology_001@v002', change_type: 'qualify', lifecycle_status: 'active', created_in_round: 1, considered_source_refs: ['R01-Q001-thoracic_radiology-A'], rationale: '缺少原始 HRCT，需要收紧分型边界。', changed_fields: ['statement', 'limitations'], assessment: { statement: '现有资料仅支持未分类间质性肺病。', medical_basis: '已纳入影像科的可评价性意见。', conditions: { subject: '未分类间质性肺病', professional_level: 'disease_diagnosis', timeframe: { description: '当前评估时点。' }, evidence_scope: { evidence_ids: ['ev_001'], scope_limitations: ['缺少原始 HRCT。'] } } } },
          ],
        }],
        change_events: [{ judgment_id: 'pulmonology_001', after_version_id: 'pulmonology_001@v002', rationale: '缺少原始 HRCT，需要收紧分型边界。' }],
      },
    })
    renderWorkspace()

    expect(await screen.findByText('当前多专科判断')).toBeInTheDocument()
    expect(screen.getByText('现有资料仅支持未分类间质性肺病。')).toBeInTheDocument()
    expect(screen.getByText('主持人只读取当前有效版本')).toBeInTheDocument()
    expect(screen.getByText('2 个版本')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Expand row' }))
    expect(await screen.findByText('现有资料支持纤维化性间质性肺病。')).toBeInTheDocument()
    expect(screen.getByText('缺少原始 HRCT，需要收紧分型边界。')).toBeInTheDocument()
  })

  it('summarizes structural changes between chair rounds', () => {
    const current = {
      ...chairResult,
      integrated_conclusions: [{ conclusion_id: 'IC001', statement: '形成新结论' }],
      questions: [{ question_id: 'Q001', question: '原问题', status: 'closed' }],
    }
    const previous = {
      ...chairResult,
      questions: [{ question_id: 'Q001', question: '原问题', status: 'open' }],
    }

    expect(chairChangeSummary(current, previous)).toEqual(['整合结论：新增 1', '待回答问题：更新 1'])
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

  it('refreshes discussion state when the backend event stream reconnects', async () => {
    vi.stubGlobal('EventSource', FakeEventSource)
    api.discussion.mockResolvedValue(active)
    renderWorkspace()

    expect(await screen.findByText('专科正在使用证据形成回答…')).toBeInTheDocument()
    await act(async () => FakeEventSource.instances[0].onopen())

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
