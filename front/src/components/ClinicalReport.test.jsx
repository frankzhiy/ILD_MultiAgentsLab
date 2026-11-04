import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, expect, it } from 'vitest'
import { TeamSynthesis } from './TeamSynthesis'
import { team as fixture } from './teamSynthesisFixture'
import { useWorkbenchStore } from '../store'

afterEach(cleanup)

it('preserves historical candidates without guessing identity and retains nonselection in the audit', () => {
  const team = structuredClone(fixture)
  team.diagnostic_resolution.primary_diagnosis = '疾病 A'
  team.diagnostic_resolution.status = 'provisional_diagnosis'
  team.problems[0].candidates = [
    { diagnosis: '疾病 A', position: 'preferred', confidence: 'low', rationale: '当前支持 A 的理由', limitations: [], source_refs: ['S2'], supporting_evidence_refs: ['E1'], opposing_evidence_refs: ['E2'] },
    { diagnosis: '讨论后新增候选 B', position: 'alternative', confidence: 'low', rationale: '讨论后保留 B 的理由', limitations: [], source_refs: [], supporting_evidence_refs: [], opposing_evidence_refs: [] },
  ]
  team.diagnostic_exploration.candidates = [{ candidate_id: 'C', diagnosis: '早期候选 C', explains: '解释局部发现', fails_to_explain: '不能解释主要病变', supporting_evidence_refs: [], discriminating_information: '早期曾排第一', retrieval_question: '如何鉴别 C' }]
  team.diagnostic_resolution.candidate_dispositions = [{ candidate_id: 'C', outcome: 'rejected', rationale: '当前不采纳 C' }]
  team.evidence_catalog = {
    E1: { evidence_ref: 'E1', graph_unit_id: 'U1', quote: '患者的真实支持证据', evidence_ids: ['E1'] },
    E2: { evidence_ref: 'E2', graph_unit_id: 'U2', quote: '患者的真实反对证据', evidence_ids: ['E2'] },
  }
  const original = structuredClone(team)
  render(<TeamSynthesis team={team} final />)
  const diagnosis = screen.getByRole('region', { name: '诊断结论与候选' })
  expect(within(diagnosis).getByText('讨论后新增候选 B')).toBeVisible()
  expect(within(diagnosis).queryByText('早期候选 C')).not.toBeInTheDocument()
  expect(screen.queryByText('暂定诊断意见')).not.toBeInTheDocument()
  expect(screen.getByText('早期候选 C')).not.toBeVisible()
  fireEvent.click(within(diagnosis).getAllByText('查看此候选的支持、反对及限定依据')[0])
  expect(within(diagnosis).getByText('患者的真实支持证据')).toBeVisible()
  expect(within(diagnosis).getByText('患者的真实反对证据')).toBeVisible()
  fireEvent.click(within(diagnosis).getByRole('button', { name: /病历原文｜反证/ }))
  expect(useWorkbenchStore.getState().evidence.quote).toBe('患者的真实反对证据')
  fireEvent.click(screen.getByText('候选探索与逐项审定记录'))
  expect(screen.getByText('当前不采纳 C', { exact: false })).toBeVisible()
  expect(screen.getByText('早期曾排第一')).not.toBeVisible()
  expect(screen.getByText('未保留原因：旧版未区分原因，不视为已排除')).toBeVisible()
  expect(team).toEqual(original)
})

it.each([false, true])('uses the registered candidate name in the discussion and report views (final=%s)', final => {
  const team = structuredClone(fixture)
  team.problems[0].candidates = [{ candidate_id: 'C1', diagnosis: '标准名的别称', position: 'alternative', confidence: 'low', rationale: '有线索但尚不能确立', limitations: [], source_refs: [], supporting_evidence_refs: [], opposing_evidence_refs: [] }]
  team.diagnostic_exploration.candidates = [{ candidate_id: 'C1', diagnosis: '统一标准名称', aliases: ['标准名的别称'], supporting_evidence_refs: [] }]
  team.diagnostic_resolution.candidate_dispositions = [{ candidate_id: 'C1', outcome: 'retained' }]
  render(<TeamSynthesis team={team} final={final} />)
  expect(screen.getAllByText('统一标准名称').length).toBeGreaterThan(0)
  expect(screen.queryByText('标准名的别称')).not.toBeInTheDocument()
  expect(screen.getAllByText('保留候选').length).toBeGreaterThan(0)
})

it('shows all evidence statuses separately from follow-up actions, without promoting missing data to satisfied', () => {
  const team = structuredClone(fixture)
  team.evidence_needs = [
    { ...team.evidence_needs[0], need_id: 'available', information: '资料 A', status: 'available', available_information: '已有 A', missing_information: '', action: 'no_further_request' },
    { ...team.evidence_needs[0], need_id: 'partial', information: '资料 B', status: 'partially_available', branches: [{ result: '结果为阳性', changed_decision: '提高候选 B 的支持度' }] },
    { ...team.evidence_needs[0], need_id: 'missing', information: '资料 C', status: 'missing', action: 'no_further_request' },
  ]
  team.issues = []
  render(<TeamSynthesis team={team} final />)
  const needs = screen.getByRole('region', { name: '证据需求及满足状态' })
  for (const text of ['已满足 1 项', '部分满足 1 项', '尚缺资料 1 项']) expect(within(needs).getByText(text)).toBeVisible()
  const missing = within(needs).getByRole('row', { name: /资料 C/ })
  expect(within(missing).getByText('尚缺资料')).toBeVisible()
  expect(within(missing).getByText('本轮不再追索')).toBeVisible()
  expect(within(missing).queryByText('已满足')).not.toBeInTheDocument()
  fireEvent.click(within(needs).getAllByText('什么结果会改变判断')[1])
  expect(within(needs).getByText('提高候选 B 的支持度', { exact: false })).toBeVisible()
  expect(screen.getByText('本版未保留开放讨论议题；判断边界和资料缺口仍按第 03、04 栏保留。')).toBeVisible()
})

it('does not turn an empty evidence-needs ledger into a claim that all requirements are satisfied', () => {
  render(<TeamSynthesis team={{ ...fixture, evidence_needs: [] }} stage="premeeting" />)
  expect(screen.getByText('本版未记录额外证据需求，不代表所有诊断条件均已满足。')).toBeVisible()
  expect(screen.getByRole('region', { name: '本次讨论议程' })).toBeVisible()
  expect(screen.queryByRole('tab')).not.toBeInTheDocument()
})
