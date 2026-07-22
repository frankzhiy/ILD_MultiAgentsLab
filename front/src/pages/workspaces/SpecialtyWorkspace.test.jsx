import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../../api'
import { EvidenceDrawer } from '../../components/EvidenceDrawer'
import { useWorkbenchStore } from '../../store'
import { SpecialtyWorkspace } from './SpecialtyWorkspace'

vi.mock('../../api', () => ({
  api: {
    specialties: vi.fn(),
    guidelines: vi.fn().mockResolvedValue([]),
    guidelineUrl: vi.fn((name, page) => `/api/guidelines/${name}#page=${page}`),
  },
}))

const pointer = {
  evidence_ids: ['ev-1'],
  graph_unit_id: 'gu-1',
  quote: '长期进行性呼吸困难。',
}

function formalOutput(question = '判断疾病层面的首轮工作诊断') {
  return {
    professional_conclusions: {
      specialty_question: question,
      assessability: 'partially_assessable',
      conclusions: [{
        conclusion_id: 'conclusion-1',
        role: 'primary',
        conclusion_type: 'working_diagnosis',
        statement: '倾向慢性纤维化性间质性肺病',
        status: 'favored',
        medical_basis: '病程和现有肺部资料形成连贯解释。',
        decision_impact: '影响后续病因审阅。',
        evidence: { supporting: [pointer], weakening: [pointer], discriminating: [], background: [] },
        guideline_evidence: [{ guideline_id: 'guide-1', source_file: 'guide.pdf', page: 3 }],
        limitations: ['缺少原始影像。'],
      }],
      interspecialty_questions: [{
        target_specialty: 'thoracic_radiology',
        question: '现有资料能否支持 UIP 形态模式？',
        why_it_matters: '影响疾病层工作诊断。',
        decision_unlocked: '调整诊断强度。',
        related_evidence: [pointer],
      }],
      evidence_gaps: [{
        available_information: '仅有影像报告摘录。',
        missing_information: '缺少原始薄层 CT。',
        why_it_matters: '不能可靠判断形态模式。',
        decision_unlocked: '完成影像模式判断。',
        related_evidence: [pointer],
      }],
      boundaries: ['本结论不是最终 MDT 诊断。'],
    },
    clinical_reasoning: {
      problem_representation: '慢性进展性纤维化性肺病，病因资料不完整。',
      candidate_explanations: [{
        candidate_id: 'candidate-1',
        explanation: '特发性纤维化性间质性肺病',
        role: 'leading',
        fit_summary: '能够解释当前核心综合征。',
        evidence: { supporting: [pointer], weakening: [], discriminating: [pointer], background: [] },
        guideline_evidence: [],
        remaining_uncertainty: '继发病因尚未充分排除。',
      }],
      evidence_comparisons: [{
        comparison_id: 'comparison-1',
        effect: 'discriminates',
        candidate_ids: ['candidate-1'],
        interpretation: '慢性进展病程具有鉴别价值。',
        evidence: { supporting: [], weakening: [], discriminating: [pointer], background: [] },
      }],
      consistency_checks: [{
        check_id: 'check-1',
        dimension: 'time',
        status: 'consistent',
        finding: '时间进程与慢性纤维化过程一致。',
        implication: '支持主导解释。',
        evidence: { supporting: [pointer], weakening: [], discriminating: [], background: [] },
      }],
      boundary_reviews: [{
        review_id: 'review-1',
        boundary_type: 'pattern_is_not_disease',
        finding: '未将形态模式直接升级为疾病诊断。',
        impact: '结论保持在工作诊断层。',
        evidence: { supporting: [], weakening: [], discriminating: [], background: [pointer] },
      }],
      synthesis: '综合病程、证据鉴别力与当前边界，形成校准后的工作结论。',
    },
  }
}

function result(specialty, label, output, status = output ? 'completed' : 'pending') {
  return { specialty, label, status, output }
}

function payload(firstOutput = formalOutput()) {
  return {
    case_id: 'case-1',
    results: [
      result('pulmonology', '呼吸科', firstOutput),
      result('thoracic_radiology', '胸部影像科', formalOutput('判断影像形态模式')),
      result('rheumatology', '风湿免疫科', null),
      result('pathology', '病理科', null),
    ],
  }
}

function renderWorkspace({ run = { status: 'running' }, drawer = false } = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/runs/run-1/specialties']}>
        <Routes>
          <Route path="/runs/:runId/:view" element={<><SpecialtyWorkspace runId="run-1" run={run} />{drawer && <EvidenceDrawer />}</>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  api.specialties.mockReset()
  api.guidelines.mockClear()
  useWorkbenchStore.setState({ evidence: null, evidenceOpen: false })
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

describe('SpecialtyWorkspace', () => {
  it('renders the two formal boards with every required reasoning section and traceable evidence', async () => {
    api.specialties.mockResolvedValue(payload())
    renderWorkspace({ run: { status: 'completed' }, drawer: true })

    expect(await screen.findByText('专业结论与外部需求')).toBeInTheDocument()
    expect(screen.getByText('临床推理论证')).toBeInTheDocument()
    ;['专科问题定位', '专业结论', '需要其他专科回答的问题', '决策相关证据缺口', '本专科判断边界', '问题表征', '候选解释', '鉴别性证据比较', '机制与时间一致性', '反证、限制与边界复核', '综合理由'].forEach((label) => expect(screen.getByText(label)).toBeInTheDocument())
    ;['支持证据', '削弱证据', '鉴别证据', '背景证据', '指南依据'].forEach((label) => expect(screen.getAllByText(label).length).toBeGreaterThan(0))
    fireEvent.click(screen.getAllByRole('button', { name: /gu-1/ })[0])
    expect(await screen.findByText('证据检查器')).toBeInTheDocument()
    expect(screen.getByText('长期进行性呼吸困难。')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '定位到语义图' })).toBeInTheDocument()
  })

  it('switches specialties without leaking the previous specialty content', async () => {
    api.specialties.mockResolvedValue(payload())
    renderWorkspace({ run: { status: 'completed' } })

    expect(await screen.findByText('判断疾病层面的首轮工作诊断')).toBeInTheDocument()
    fireEvent.click(screen.getByText('胸部影像科'))
    expect(await screen.findByText('判断影像形态模式')).toBeInTheDocument()
    expect(screen.queryByText('判断疾病层面的首轮工作诊断')).not.toBeInTheDocument()
  })

  it('shows an explicit pending state and an explicit legacy warning instead of raw fallback', async () => {
    api.specialties.mockResolvedValue(payload(null))
    const view = renderWorkspace({ run: { status: 'failed' } })
    expect(await screen.findByText('呼吸科尚无首轮正式输出，页面会自动刷新')).toBeInTheDocument()

    view.unmount()
    api.specialties.mockResolvedValue(payload({ domain_reviews: ['内部旧字段'], diagnostic_formulation: {} }))
    renderWorkspace({ run: { status: 'completed' } })
    expect(await screen.findByText('旧版专科输出不在此页面展示')).toBeInTheDocument()
    expect(screen.queryByText('内部旧字段')).not.toBeInTheDocument()
  })

  it('surfaces a request failure and recovers through retry', async () => {
    api.specialties.mockRejectedValueOnce(new Error('network down')).mockResolvedValueOnce(payload())
    renderWorkspace({ run: { status: 'completed' } })

    expect(await screen.findByText('数据加载失败')).toBeInTheDocument()
    expect(screen.getByText('network down')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /重试/ }))
    expect(await screen.findByText('专业结论与外部需求')).toBeInTheDocument()
  })

  it('polls an active run until the specialty output is ready', async () => {
    vi.useFakeTimers()
    api.specialties.mockResolvedValueOnce(payload(null)).mockResolvedValue(payload())
    renderWorkspace()

    await act(async () => { await vi.advanceTimersByTimeAsync(1) })
    expect(screen.getByText('呼吸科尚无首轮正式输出，页面会自动刷新')).toBeInTheDocument()
    await act(async () => { await vi.advanceTimersByTimeAsync(3000) })
    expect(screen.getByText('专业结论与外部需求')).toBeInTheDocument()
    expect(api.specialties).toHaveBeenCalledTimes(2)
  })
})
