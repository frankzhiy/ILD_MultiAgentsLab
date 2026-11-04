import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, expect, it, vi } from 'vitest'
import { api } from '../../api'
import { ChairWorkspace } from './ChairWorkspace'
import { chairResult } from '../../components/teamSynthesisFixture'

vi.mock('../../api', () => ({ api: { chair: vi.fn(), runChair: vi.fn() } }))
afterEach(() => { cleanup(); vi.clearAllMocks() })
function renderWorkspace(value) {
  api.chair.mockResolvedValue(value)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  render(<QueryClientProvider client={client}><ChairWorkspace runId="run-1" /></QueryClientProvider>)
}
it('shows new synthesis, question provenance, boundaries and the complete needs ledger', async () => {
  renderWorkspace({ status: 'completed', runnable: true, result: chairResult })
  expect(await screen.findByRole('heading', { name: 'MDT 会前整合' })).toBeInTheDocument()
  expect(screen.queryByRole('tab')).not.toBeInTheDocument()
  for (const name of ['诊断结论与候选', '诊断依据与取舍', '判断边界', '证据需求及满足状态', '本次讨论议程']) {
    expect(screen.getByRole('region', { name })).toBeVisible()
  }
  const agenda = screen.getByRole('region', { name: '本次讨论议程' })
  expect(within(agenda).getByRole('heading', { name: '1. 现有检查能否解释急性变化？' })).toBeVisible()
  expect(within(agenda).getAllByText('呼吸科回答：')[0]).toBeVisible()
  fireEvent.click(screen.getByText('原始问题与讨论处理记录'))
  expect(screen.getByText('影像支持哪种模式？')).toBeInTheDocument()
  expect(screen.getByText('已有专科意见覆盖')).toBeInTheDocument()
  expect(screen.getByText(/呼吸科 → 影像科/)).toBeInTheDocument()
  expect(screen.getByText(/发起方：主持人/)).toBeInTheDocument()
  expect(screen.getByText('无法确认 UIP')).toBeVisible()
  expect(screen.getByText('尚缺资料')).toBeInTheDocument()
  expect(screen.getByText('保留判断边界')).toBeInTheDocument()
})
it('restarts the remaining pipeline from chair after failure', async () => {
  renderWorkspace({ status: 'failed', runnable: true, result: null, error: '校验失败' })
  api.runChair.mockResolvedValue({ status: 'running', runnable: true, result: null })
  fireEvent.click(await screen.findByRole('button', { name: '从会前整合重新运行' }))
  await waitFor(() => expect(api.runChair).toHaveBeenCalledWith('run-1'))
})
it('requires rerunning outdated structure and hides legacy clinical fields', async () => {
  renderWorkspace({ status: 'outdated', runnable: true, has_previous_result: true, result: { schema_version: 'mdt_chair.v9', integrated_conclusions: [{ statement: '不应显示的旧结论' }] } })
  expect(await screen.findByText('旧版结构需要重新运行')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '从会前整合重新运行' })).toBeEnabled()
  expect(screen.queryByText('不应显示的旧结论')).not.toBeInTheDocument()
})
it('disables an unavailable stage and explains its missing input', async () => {
  renderWorkspace({ status: 'unavailable', runnable: false, result: null, error: '缺少专科正式输出' })
  expect(await screen.findByText('缺少专科正式输出')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '运行会前整合' })).toBeDisabled()
})
