import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { App } from 'antd'
import { afterEach, expect, it, vi } from 'vitest'
import { api } from '../api'
import { NewRunPage } from './NewRunPage'

vi.mock('../api', () => ({ api: { cases: vi.fn(), runs: vi.fn(), models: vi.fn(), createRun: vi.fn() } }))
afterEach(cleanup)
it('submits a followup with its chosen prior consultation and complete new text', async () => {
  api.cases.mockResolvedValue([])
  api.runs.mockResolvedValue([{ id: 'prior-consultation', case_id: 'case-1' }, { id: 'unrelated', case_id: 'case-2' }])
  api.models.mockResolvedValue({ agents: [] })
  api.createRun.mockResolvedValue({ id: 'followup' })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<MemoryRouter><QueryClientProvider client={client}><App><NewRunPage /></App></QueryClientProvider></MemoryRouter>)
  fireEvent.click(screen.getByLabelText('粘贴原文'))
  fireEvent.change(screen.getByLabelText('病例 ID'), { target: { value: 'case-1' } })
  fireEvent.change(screen.getByLabelText('病例原文'), { target: { value: '原有记录；补充肺功能结果' } })
  const prior = screen.getByRole('combobox', { name: '前序会诊（后续资料时选择）' })
  await waitFor(() => expect(prior).not.toBeDisabled())
  fireEvent.keyDown(prior, { key: 'ArrowDown', keyCode: 40, which: 40 })
  expect(await screen.findByRole('option', { name: 'prior-consultation' })).toBeInTheDocument()
  fireEvent.keyDown(prior, { key: 'Enter', keyCode: 13, which: 13 })
  expect(screen.queryByText('unrelated')).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '开始运行' }))
  await waitFor(() => expect(api.createRun).toHaveBeenCalledWith(expect.objectContaining({ case_id: 'case-1', raw_text: '原有记录；补充肺功能结果', parent_run_id: 'prior-consultation' })))
})
