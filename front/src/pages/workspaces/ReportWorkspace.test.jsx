import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, expect, it, vi } from 'vitest'
import { api } from '../../api'
import { ReportWorkspace } from './ReportWorkspace'
import { team } from '../../components/teamSynthesisFixture'

vi.mock('../../api', () => ({ api: { report: vi.fn(), runReport: vi.fn() } }))
afterEach(() => { cleanup(); vi.clearAllMocks() })
function renderReport(value) {
  api.report.mockResolvedValue(value)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  render(<MemoryRouter><QueryClientProvider client={client}><ReportWorkspace runId="run-1" /></QueryClientProvider></MemoryRouter>)
}
it('renders a clinical report without premeeting or discussion tabs', async () => {
  renderReport({ status: 'completed', report_status: 'completed', runnable: true,
    diagnosis_conclusion: '主要问题的审定结论',
    final_report: { schema_version: 'mdt_final_report.v10', team_synthesis: team } })
  expect(await screen.findByRole('heading', { name: '主要问题的审定结论' })).toBeVisible()
  expect(screen.getByText(team.diagnostic_opinion.diagnosis).closest('details')).not.toHaveAttribute('open')
  expect(await screen.findByText('当前病因仍未明确')).toBeInTheDocument()
  expect(screen.queryByRole('tab')).not.toBeInTheDocument()
  for (const name of ['诊断结论与候选', '诊断依据与取舍', '判断边界', '证据需求及满足状态']) {
    expect(screen.getByRole('region', { name })).toBeVisible()
  }
  expect(screen.getByText('病变分布及蜂窝征的文字记录')).toBeVisible()
  expect(screen.getByText('无法确认 UIP')).toBeVisible()
  expect(screen.getByRole('button', { name: '重新运行本阶段' })).toBeEnabled()
})
it('reruns a failed report independently', async () => {
  renderReport({ status: 'failed', report_status: 'failed', runnable: true, error: '报告失败' })
  api.runReport.mockResolvedValue({ status: 'running', report_status: 'running', runnable: false })
  fireEvent.click(await screen.findByRole('button', { name: '重新运行本阶段' }))
  await waitFor(() => expect(api.runReport).toHaveBeenCalledWith('run-1'))
})
it('does not generate a final report from stopped discussion', async () => {
  renderReport({ status: 'stopped', report_status: 'stopped', runnable: false, error: '停止的讨论仅为阶段结果' })
  expect(await screen.findByText('停止的讨论仅为阶段结果')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '生成最终报告' })).toBeDisabled()
})

it('blocks partial historical reports with a current top-level version', async () => {
  const old = structuredClone(team)
  delete old.diagnostic_exploration
  old.diagnostic_opinion.diagnosis = '不应出现的旧诊断'
  renderReport({ status: 'completed', report_status: 'completed', runnable: true,
    final_report: { schema_version: 'mdt_final_report.v10', team_synthesis: old } })
  expect(await screen.findByText('旧版报告仅保留在历史产物中，请按新结构重新生成。')).toBeInTheDocument()
  expect(screen.queryByText('不应出现的旧诊断')).not.toBeInTheDocument()
})
