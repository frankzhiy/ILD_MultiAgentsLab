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
it('renders the same new synthesis in the final report', async () => {
  renderReport({ status: 'completed', report_status: 'completed', runnable: true, final_report: { schema_version: 'mdt_final_report.v8', team_synthesis: team } })
  expect(await screen.findByText('当前病因仍未明确')).toBeInTheDocument()
  expect(screen.getAllByRole('tab')).toHaveLength(6)
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
