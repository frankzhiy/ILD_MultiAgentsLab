import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../../api'
import { ChairWorkspace } from './ChairWorkspace'

vi.mock('../../api', () => ({
  api: {
    chair: vi.fn(),
    runChair: vi.fn(),
  },
}))

const citation = { source_ref: 'pulmonology:conclusion-1', specialty: 'pulmonology', quote: '原始专科结论' }
const evidence = { evidence_ref: 'gu-1', graph_unit_id: 'gu-1', quote: '病例原文' }

const result = {
  schema_version: 'mdt_chair.v2',
  integrated_conclusions: [{
    conclusion_id: 'integrated-1',
    statement: '综合各专科意见，当前更支持慢性纤维化性间质性肺病的工作诊断。',
    medical_basis: '病程、影像与系统性病因审阅形成连贯解释。',
    decision_impact: '作为后续 MDT 讨论的共同起点。',
    status: 'possible',
    role: 'important_alternative',
    conclusion_type: 'ild_attribution',
    specialties: ['pulmonology', 'thoracic_radiology'],
    evidence: { supporting: [evidence], weakening: [evidence], discriminating: [evidence], background: [evidence] },
    guideline_evidence: [{ guideline_id: 'guide-1', source_file: 'guide.pdf' }],
    limitations: ['现有结论受原始影像可获得性限制。'],
    source_citations: [citation],
  }],
  questions: [{
    question_id: 'question-1',
    question: '影像科对当前形态模式的判断边界是什么？',
    raised_by: ['pulmonology'],
    target_specialties: ['thoracic_radiology'],
    status: 'partially_answered',
    answers: [{ specialty: 'thoracic_radiology', answer: '当前描述支持纤维化，但不足以锁定 UIP。', source_citations: [citation] }],
    answer_summary: '已明确纤维化方向，形态归类仍保持限定。',
    remaining_clarification: '请进一步说明不能锁定 UIP 的判读依据。',
  }],
  evidence_needs: [{
    need_id: 'need-1',
    status: 'partially_available',
    raised_by: ['thoracic_radiology'],
    required_information: '原始薄层 CT 序列',
    available_information: '已有影像报告摘录',
    remaining_information: '缺少可直接复核的图像',
    provided_by: ['pulmonology'],
    evidence: { supporting: [], weakening: [], discriminating: [], background: [evidence] },
  }],
}

function renderWorkspace(run = { status: 'completed' }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <ChairWorkspace runId="run-1" run={run} />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  api.chair.mockReset()
  api.runChair.mockReset()
})

afterEach(cleanup)

describe('ChairWorkspace', () => {
  it('renders the three result boards with categorized evidence and discussion-only question results', async () => {
    api.chair.mockResolvedValue({ status: 'completed', runnable: true, result })
    renderWorkspace()

    expect(await screen.findByText('跨专科整合结论')).toBeInTheDocument()
    expect(screen.getByText('待回答问题')).toBeInTheDocument()
    expect(screen.getByText('证据需求及满足状态')).toBeInTheDocument()
    ;['相关专科', '结论状态：可能', '结论定位：重要替代解释', '结论类型：ILD 归因'].forEach((label) => expect(screen.getByText(label)).toBeInTheDocument())
    ;['问题状态：部分回答', '问题提出专科', '待回答专科', '满足状态：部分满足', '需求提出专科', '已提供专科'].forEach((label) => expect(screen.getByText(label)).toBeInTheDocument())
    ;['支持证据', '削弱证据', '鉴别证据', '背景证据', '指南依据'].forEach((label) => expect(screen.getAllByText(label).length).toBeGreaterThan(0))
    expect(screen.getByText('已有专科回答')).toBeInTheDocument()
    expect(screen.getByText('当前结果')).toBeInTheDocument()
    expect(screen.getByText(/仍需解释\/\澄清/)).toBeInTheDocument()
    ;['需要提供', '当前已有', '仍然缺少'].forEach((label) => expect(screen.getByText(label)).toBeInTheDocument())
  })

  it('starts only the chair stage from a ready pending state', async () => {
    api.chair.mockResolvedValueOnce({ status: 'pending', runnable: true, result: null }).mockResolvedValue({ status: 'completed', runnable: true, result })
    api.runChair.mockResolvedValue({ status: 'running', runnable: true, result: null })
    renderWorkspace()

    fireEvent.click(await screen.findByRole('button', { name: '运行主持人整合' }))
    await waitFor(() => expect(api.runChair).toHaveBeenCalledWith('run-1'))
    expect(screen.getByText('此按钮只运行 MDT 主持人，会直接使用现有四个专科结果，不会重新运行前序 Agent。')).toBeInTheDocument()
  })

  it('warns when a failed rerun is displaying the previous successful result', async () => {
    api.chair.mockResolvedValue({ status: 'failed', runnable: true, result, error: 'schema validation failed' })
    renderWorkspace()

    expect(await screen.findByText('本次重跑失败，下方展示上一次成功结果')).toBeInTheDocument()
    expect(screen.getByText('schema validation failed')).toBeInTheDocument()
    expect(screen.getByText('综合各专科意见，当前更支持慢性纤维化性间质性肺病的工作诊断。')).toBeInTheDocument()
  })

  it('marks an older chair structure for rerun while keeping its result visible', async () => {
    api.chair.mockResolvedValue({ status: 'outdated', runnable: true, result })
    renderWorkspace()

    expect(await screen.findByText('现有主持人结果属于旧版结构')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '重新运行主持人整合' })).toBeEnabled()
  })

  it('shows why the chair is unavailable and keeps the action disabled', async () => {
    api.chair.mockResolvedValue({ status: 'unavailable', runnable: false, result: null, error: '缺少专科正式输出' })
    renderWorkspace()

    expect(await screen.findByText('主持人尚不可运行')).toBeInTheDocument()
    expect(screen.getByText('缺少专科正式输出')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '运行主持人整合' })).toBeDisabled()
  })
})
