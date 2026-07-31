import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../../api'
import { citationLabel } from '../../components/Citation'
import { EvidenceDrawer } from '../../components/EvidenceDrawer'
import { useWorkbenchStore } from '../../store'
import { SpecialtyWorkspace } from './SpecialtyWorkspace'

vi.mock('../../api', () => ({
  api: {
    specialties: vi.fn(),
    semantic: vi.fn(),
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
    specialty_assessments: {
      specialty_question: question,
      assessability: 'partially_assessable',
      assessments: [{
        assessment_id: 'assessment-1',
        role: 'primary',
        assessment_type: 'working_diagnosis',
        statement: '倾向慢性纤维化性间质性肺病',
        status: 'favored',
        medical_basis: '病程和现有肺部资料形成连贯解释。',
        decision_impact: '影响后续病因审阅。',
        evidence: {
          evidence_relations: [{
            ...pointer,
            direction: 'supports',
            function: 'qualifying',
          }],
        },
        guideline_evidence: [{ guideline_id: 'guide-1', source_file: 'guide.pdf', page: 3 }],
        limitations: ['缺少原始影像。'],
      }],
      evidence_gaps: [{
        available_information: '仅有影像报告摘录。',
        missing_information: '缺少原始薄层 CT。',
        why_it_matters: '不能可靠判断形态模式。',
        decision_unlocked: '完成影像模式判断。',
        related_assessment_ids: ['assessment-1'], related_evidence: [pointer],
      }],
      boundaries: ['本结论不是最终 MDT 诊断。'],
    },
    interspecialty_questions: {
      questions: [{
        target_specialty: 'thoracic_radiology',
        question: '现有资料能否支持 UIP 形态模式？',
        why_it_matters: '影响疾病层工作诊断。',
        decision_unlocked: '调整诊断强度。',
        related_assessment_ids: ['assessment-1'],
        related_evidence: [pointer],
      }],
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
  api.semantic.mockReset()
  api.semantic.mockResolvedValue({
    segments: [{
      segment_id: 'seg-1',
      units: [{
        graph_unit_id: 'gu-1',
        text: '患者长期进行性呼吸困难。',
        clinical_propositions: {
          evidence_blocks: [{ evidence_id: 'ev-1', text: '长期进行性呼吸困难。' }],
          propositions: [{
            proposition_id: 'prop-1',
            concept_text: '进行性呼吸困难',
            status: 'present',
            certainty: 'high',
            modifiers: [{ text: '长期' }],
          }],
        },
        local_graph: { nodes: [], edges: [] },
      }],
    }],
  })
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

    expect(await screen.findByText('专科初步判断')).toBeInTheDocument()
    expect(screen.getAllByText('需其他专科回答的问题').length).toBeGreaterThan(0)
    ;['专科问题定位', '初步判断', '决策相关证据缺口', '本专科判断边界'].forEach((label) => expect(screen.getByText(label)).toBeInTheDocument())
    expect(screen.queryByText('临床推理论证')).not.toBeInTheDocument()
    ;['患者证据图', '指南依据'].forEach((label) => expect(screen.getAllByText(label).length).toBeGreaterThan(0))
    fireEvent.click(screen.getAllByRole('button', { name: /患者证据图｜支持\/限定/ })[0])
    expect(await screen.findByText('证据检查器')).toBeInTheDocument()
    expect(screen.getByText('被引用的患者原文')).toBeInTheDocument()
    expect(screen.queryByText('原文证据摘录')).not.toBeInTheDocument()
    expect(screen.getByText('长期进行性呼吸困难。')).toBeInTheDocument()
    expect(await screen.findByText('证据单元上下文（Graph Unit）')).toBeInTheDocument()
    expect(document.querySelector('.evidence-source-context')).toHaveTextContent('患者长期进行性呼吸困难。')
    expect(document.querySelector('.evidence-source-context mark')).toHaveTextContent('长期进行性呼吸困难。')
    expect(screen.getAllByText('该证据图与当前原子判断的关系').length).toBeGreaterThan(0)
    expect(screen.getByText('支持')).toBeInTheDocument()
    expect(screen.getByText('限定')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Evidence ID → 原文证据块/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /引用类别与技术定位/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '定位到语义图' })).toBeInTheDocument()
  })

  it('does not repeat the context when an Evidence ID covers the entire graph unit', async () => {
    api.specialties.mockResolvedValue(payload())
    api.semantic.mockResolvedValue({
      segments: [{
        segment_id: 'seg-1',
        text: pointer.quote,
        units: [{
          graph_unit_id: 'gu-1',
          text: pointer.quote,
          clinical_propositions: {
            evidence_blocks: [
              { evidence_id: 'ev-1', text: pointer.quote },
              { evidence_id: 'ev-1', text: pointer.quote },
            ],
            propositions: [],
          },
          local_graph: { nodes: [], edges: [] },
        }],
      }],
    })
    renderWorkspace({ run: { status: 'completed' }, drawer: true })

    fireEvent.click((await screen.findAllByRole('button', { name: /患者证据图/ }))[0])
    expect(await screen.findByText('覆盖整个 Graph Unit，不重复展示')).toBeInTheDocument()
    expect(screen.queryByText('证据单元上下文（Graph Unit）')).not.toBeInTheDocument()
    expect(document.querySelectorAll('.evidence-quote')).toHaveLength(1)
    expect(screen.getByRole('button', { name: /Evidence ID → 原文证据块（1）/ })).toBeInTheDocument()
  })

  it('keeps source kinds distinct without presenting graph locators as numbered evidence', () => {
    const items = [
      { segment_id: 'seg-1' },
      { graph_unit_id: 'gu-1' },
      { evidence_ids: ['ev-1'] },
      { node_id: 'node-1' },
      { source_ref: 'S001', specialty: 'pulmonology', source_type: 'interspecialty_question' },
      { guideline_id: 'guide-1' },
      { evidence_ids: ['ev-2'] },
    ]
    expect(items.map((item, index) => citationLabel(item, items, index))).toEqual([
      '原文片段',
      '患者证据图',
      '原文证据',
      '图节点',
      '呼吸科｜专科问题',
      '指南依据',
      '原文证据',
    ])
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
    expect(await screen.findByText('专科初步判断')).toBeInTheDocument()
  })

  it('polls an active run until the specialty output is ready', async () => {
    vi.useFakeTimers()
    api.specialties.mockResolvedValueOnce(payload(null)).mockResolvedValue(payload())
    renderWorkspace()

    await act(async () => { await vi.advanceTimersByTimeAsync(1) })
    expect(screen.getByText('呼吸科尚无首轮正式输出，页面会自动刷新')).toBeInTheDocument()
    await act(async () => { await vi.advanceTimersByTimeAsync(3000) })
    expect(screen.getByText('专科初步判断')).toBeInTheDocument()
    expect(api.specialties).toHaveBeenCalledTimes(2)
  })
})
