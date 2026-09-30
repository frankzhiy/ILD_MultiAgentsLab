import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, expect, it, vi } from 'vitest'
import { api } from '../../api'
import { ReportWorkspace } from './ReportWorkspace'

vi.mock('../../api', () => ({ api: { report: vi.fn() } }))

afterEach(() => { cleanup(); api.report.mockReset() })

it('shows the doctor report, layered matrix, evidence basis, and evidence needs', async () => {
  api.report.mockResolvedValue({
    status: 'completed', case_id: '142-IPF', final_report: {
      clinical_report: {
        overall_conclusion: '旧版独立结论。', integrated_summary: '旧版独立摘要。',
        diagnostic_matrix: [
          { dimension: 'ild_presence', status: 'supported', confidence: 'high', role: 'primary', statement: '存在纤维化性间质性肺病。', medical_basis: '影像报告支持。' },
          { dimension: 'radiologic_pattern', status: 'indeterminate', confidence: 'low', role: 'boundary', statement: '现有描述尚不能确定 UIP 影像模式。', medical_basis: '关键征象描述不足。' },
          { dimension: 'histopathologic_pattern', status: 'not_assessable', confidence: 'unknown', role: 'boundary', statement: '组织学模式无法判断。', medical_basis: '无可评价病理材料。' },
          { dimension: 'mdt_diagnosis', status: 'favored', confidence: 'moderate', role: 'primary', disease_category: 'idiopathic_interstitial_pneumonia', specific_disease: '特发性肺纤维化', statement: '首选考虑特发性肺纤维化，但尚不能确诊。', medical_basis: '长期纤维化病程及影像报告的 UIP 印象支持这一判断。', limitations: ['缺少完整薄层征象描述'] },
          { dimension: 'etiologic_attribution', status: 'indeterminate', confidence: 'low', role: 'boundary', statement: '病因归属尚不确定。', medical_basis: '暴露和自身免疫证据不足。' },
          { dimension: 'disease_behavior', status: 'indeterminate', confidence: 'low', role: 'boundary', statement: '纤维化进展情况无法判定。', medical_basis: '缺少纵向肺功能。' },
          { dimension: 'acute_or_comorbid_factors', status: 'possible', confidence: 'low', role: 'cannot_safely_ignore', statement: '需关注低氧。', medical_basis: '现有记录提示低氧。' },
        ],
        differential_diagnoses: [{ rank: 1, diagnosis: '结缔组织病相关 ILD', confidence: 'low', rationale: '存在自身免疫线索。' }],
        secondary_judgments: [{ statement: '自身免疫相关间质性肺病仍是重要鉴别。', medical_basis: '存在自身免疫线索，但病因归属尚未确立。' }],
      },
      reasoning_trace: [{ claim_id: 'DX01', claim_statement: '特发性肺纤维化为首选诊断。', medical_basis: '病程与影像共同支持。', evidence: {}, source_citations: [] }],
      assessment_boundaries: [{ boundary_id: 'B01', topic: '影像模式', statement: '关键征象描述不足。' }],
      evidence_needs: [{ need_id: 'N01', required_information: '补充纵向肺功能', why_it_matters: '用于判断进展', decision_unlocked: '明确疾病行为', source_refs: [] }],
      unresolved_conflicts: [{ conflict_id: 'C01', topic: '病因归属仍有分歧' }],
    },
  })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<MemoryRouter><QueryClientProvider client={client}><ReportWorkspace runId="run-1" run={{ case_id: '142-IPF' }} /></QueryClientProvider></MemoryRouter>)

  expect(await screen.findByRole('heading', { name: '首选考虑特发性肺纤维化，但尚不能确诊。' })).toBeInTheDocument()
  expect(screen.getByText('特发性间质性肺炎')).toBeInTheDocument()
  expect(screen.getByText(/现有描述尚不能确定 UIP 影像模式。.*纤维化进展情况无法判定。/)).toBeInTheDocument()
  expect(screen.getByText('自身免疫相关间质性肺病仍是重要鉴别。')).toBeInTheDocument()
  expect(screen.getAllByRole('tab')).toHaveLength(3)

  fireEvent.click(screen.getByRole('tab', { name: '分层诊断矩阵' }))
  expect(screen.getByRole('columnheader', { name: '诊断层级' })).toBeInTheDocument()
  expect(screen.getByRole('row', { name: /01ILD 是否存在/ })).toBeInTheDocument()
  expect(screen.getByRole('row', { name: /07急性或共病因素/ })).toBeInTheDocument()
  expect(screen.getByText('缺少完整薄层征象描述')).toBeInTheDocument()
  expect(screen.getByText(/结缔组织病相关 ILD/)).toBeInTheDocument()

  fireEvent.click(screen.getByRole('tab', { name: '诊断依据与证据缺口' }))
  expect(screen.getByRole('heading', { name: '逐条诊断依据' })).toBeInTheDocument()
  expect(screen.getByText('特发性肺纤维化为首选诊断。')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: /Expand row|展开行/ }))
  expect(screen.getByText('专科意见：')).toBeInTheDocument()
  expect(screen.getByText(/补充纵向肺功能/)).toBeInTheDocument()
  expect(screen.getByText('用于判断进展')).toBeInTheDocument()
  expect(screen.getByText('明确疾病行为')).toBeInTheDocument()
  expect(screen.getByText('病因归属仍有分歧')).toBeInTheDocument()
  expect(screen.queryByText('旧版独立结论。')).not.toBeInTheDocument()
})
