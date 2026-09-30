import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it } from 'vitest'
import { TeamSynthesis } from './TeamSynthesis'

afterEach(cleanup)
it('keeps clinical problems separate and reveals conditional reasoning and adoption', () => {
  const team = {
    schema_version: 'team_synthesis.v2', judgment_boundaries: [], disagreements: [], revision: 2, acceptance: 'explicit_dissent', stop_kind: 'budget',
    problems: [{ problem_id: 'P1', title: '慢性肺病', relationship: 'primary', statement: '病因尚未明确', rationale: '继发原因评估不足', confidence: 'low', limitations: ['不能直接归因特发性'], source_refs: [], evidence_refs: [], candidates: [] },
      { problem_id: 'P2', title: '近期急性变化', relationship: 'acute_contributor', statement: '感染可能参与', rationale: '近期症状改变', confidence: 'low', limitations: [], source_refs: [], evidence_refs: [], candidates: [] }],
    judgment_reviews: [{ source_ref: 'S1', disposition: 'qualify', rationale: '排除不充分', evidence_refs: [], required_response: true }],
    issues: [], issue_dispositions: [], evidence_needs: [], dissent: ['病因归属未达一致'],
    synthesis_rationale: '分别评价慢性与急性过程', coverage_review: '已核对伴随因素',
    conditional_contributions: [{ question: '病理能解决什么', possible_result: '特异形态', decision_if_found: '改变病因归属', cannot_establish: '非特异结果不能定病因', acquisition_value: '当前不值得取材' }],
  }
  render(<TeamSynthesis team={team} final />)
  expect(screen.getByText('已达到讨论轮数上限，未决事项保留')).toBeInTheDocument()
  expect(screen.getByText('病因尚未明确')).toBeInTheDocument()
  expect(screen.getByText('急性叠加因素')).toBeInTheDocument()
  expect(screen.queryByText('七层诊断判断')).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('tab', { name: '证据需求及满足状态' }))
  expect(screen.getByText('获取价值：当前不值得取材')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('tab', { name: '判断采纳与依据' }))
  expect(screen.getByText('限定后采纳')).toBeInTheDocument()
  expect(screen.getByText('病因归属未达一致')).toBeInTheDocument()
})

it('prints every section with opened evidence, stop reason and problem dimensions', async () => {
  const { vi } = await import('vitest')
  const { FinalReport } = await import('../pages/workspaces/FinalReport')
  const { team: fixture } = await import('./teamSynthesisFixture')
  const team = structuredClone(fixture)
  team.problems[0].assessability = 'not_assessable'
  team.problems[0].timeframe = { description: '本次急性期' }
  team.diagnostic_facets = [{ problem_id: 'P1', dimension: 'severity', status: 'not_assessable', confidence: 'unknown', statement: '严重度暂不能判定', rationale: '缺少量化结果', source_refs: ['S2'], limitations: [], timeframe: { description: '本次急性期' } }]
  team.source_catalog.S2.version_id = 'A2.v3'
  team.source_catalog.S2.conditions = { subject: '肺部病变', applicability_conditions: ['仅适用于当前文字报告'], timeframe: { description: '本次' } }
  team.guideline_evidence = [{ guideline_id: 'G1', title: '团队综合指南', page: 12, quote: '指南原文：须核对时间变化', application: '本次缺少时间序列，保留判断边界' }]
  team.source_catalog.S2.guideline_evidence = [{ guideline_id: 'G2', title: '影像指南', page: 3, quote: '影像原文：须核对病变分布', application: '当前文字不足以确认模式' }]
  team.stop_kind = 'completed'
  const print = vi.spyOn(window, 'print').mockImplementation(() => {
    window.dispatchEvent(new Event('beforeprint'))
    window.dispatchEvent(new Event('beforeprint'))
    expect(screen.getAllByRole('tabpanel', { hidden: true })).toHaveLength(6)
    expect(document.querySelectorAll('.team-synthesis details:not([open])')).toHaveLength(0)
    expect(document.querySelector('.report-stop-reason')).toHaveTextContent('实际停止原因')
    expect(screen.getAllByText('正式判断版本：A2.v3').length).toBeGreaterThan(0)
    expect(screen.getAllByText('不可评价').length).toBeGreaterThan(0)
    expect(screen.getByText('严重度暂不能判定')).toBeInTheDocument()
    expect(screen.getByText('获取价值与负担：本轮无法获取')).toBeInTheDocument()
    expect(screen.getByText('团队综合指南 · 第 12 页')).toBeInTheDocument()
    expect(screen.getByText('指南原文：须核对时间变化')).toBeInTheDocument()
    expect(screen.getByText('本次缺少时间序列，保留判断边界')).toBeInTheDocument()
    expect(screen.getAllByText('影像指南 · 第 3 页').length).toBeGreaterThan(0)
    expect(screen.getAllByText('影像原文：须核对病变分布').length).toBeGreaterThan(0)
    window.dispatchEvent(new Event('afterprint'))
  })
  render(<FinalReport report={{ schema_version: 'mdt_final_report.v8', team_synthesis: team, stop_reason: '实际停止原因', discussion_rounds: 2 }} />)
  fireEvent.click(screen.getByRole('button', { name: /打印/ }))
  expect(print).toHaveBeenCalledOnce()
  expect(document.querySelectorAll('.team-synthesis details[open]')).toHaveLength(0)
  print.mockRestore()
})
