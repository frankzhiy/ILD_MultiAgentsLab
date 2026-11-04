import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it } from 'vitest'
import { ConsultationBackground, DiagnosticResolution } from './Consultation'

afterEach(cleanup)

it('shows specific diagnosis, retained alternatives and their explanation limits', () => {
  render(<DiagnosticResolution value={{ status: 'provisional_diagnosis', achieved_level: 'disease',
    primary_diagnosis: '具体疾病A', goal_review: '形成具体首选，确定性有限。',
    candidate_dispositions: [{ candidate_id: 'C1', outcome: 'retained', rationale: '仍有匹配表现。' }],
    remaining_gaps: [] }} exploration={{ problem_representation: '患者组合表现',
      combined_phenotype_review: '需考虑并存过程', candidates: [{ candidate_id: 'C1', diagnosis: '具体疾病B',
      level: 'disease', explains: '解释局部发现', fails_to_explain: '无法覆盖全部病程',
      discriminating_information: '需比较分布' }], unrepresented_findings: '另一个未解发现' }} />)
  expect(screen.getByText('暂定诊断意见')).toBeInTheDocument()
  expect(screen.getByText('具体疾病B')).toBeInTheDocument()
  expect(screen.getByText('不足：无法覆盖全部病程')).toBeInTheDocument()
})

it('presents a provisional opinion at its supported level without an incomplete diagnosis warning', () => {
  render(<DiagnosticResolution value={{ status: 'provisional_diagnosis', achieved_level: 'category',
    primary_diagnosis: '慢性肺实质病变', goal_review: '已比较主要解释，形成当前判断',
    remaining_gaps: [{ kind: 'patient_evidence', question: '病变分布', why_unresolved: '影响亚型判断' }] }} />)
  expect(screen.getByText('暂定诊断意见')).toBeInTheDocument()
  expect(screen.getByText('慢性肺实质病变')).toBeInTheDocument()
  expect(screen.getByText('判断的不确定性：')).toBeInTheDocument()
  expect(screen.queryByText('资料限制，具体诊断尚未完成')).not.toBeInTheDocument()
})

it('does not infer a diagnosis review for historical results', () => {
  render(<DiagnosticResolution />)
  expect(screen.getByText('历史结果未记录诊断目标复核')).toBeInTheDocument()
})

it('shows the selected consultation template and additional question', () => {
  render(<ConsultationBackground value={{ template: 'review_diagnosis', question: '复核原文的疾病依据' }} />)
  expect(screen.getByText('复核已有诊断')).toBeInTheDocument()
  expect(screen.getByText('复核原文的疾病依据')).toBeInTheDocument()
})


it('separates primary alternatives from comorbid findings and rejected exploration', () => {
  const candidates = [
    { candidate_id: 'C1', diagnosis: 'ILD疾病A', level: 'disease' },
    { candidate_id: 'C2', diagnosis: '气道异常', level: 'finding' },
    { candidate_id: 'C3', diagnosis: '网格模式', level: 'pattern' },
    { candidate_id: 'C4', diagnosis: '排除疾病B', level: 'disease' },
  ]
  render(<DiagnosticResolution value={{ status: 'provisional_diagnosis', primary_diagnosis: 'ILD疾病A',
    candidate_dispositions: candidates.map((c, index) => ({ candidate_id: c.candidate_id,
      scope: index === 1 ? 'other_problem' : 'primary_diagnosis', outcome: index === 3 ? 'rejected' : 'retained' })),
  }} exploration={{ candidates }} />)
  expect(screen.getByRole('region', { name: 'ILD 主诊断及必要鉴别' })).toHaveTextContent('ILD疾病A')
  expect(screen.getByRole('region', { name: 'ILD 主诊断及必要鉴别' })).not.toHaveTextContent('气道异常')
  expect(screen.getByRole('region', { name: '急性叠加与并存问题' })).toHaveTextContent('气道异常')
  const audit = screen.getByRole('region', { name: '模式、大类、发现及未保留候选' })
  expect(audit).toHaveTextContent('网格模式')
  expect(audit).toHaveTextContent('排除疾病B')
})
