import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { App } from 'antd'
import { afterEach, expect, it, vi } from 'vitest'
import { api } from '../api'
import { NewRunPage } from './NewRunPage'

vi.mock('../api', () => ({ api: { cases: vi.fn(), runs: vi.fn(), models: vi.fn(), createRun: vi.fn(), createBatch: vi.fn() } }))
afterEach(() => { cleanup(); vi.clearAllMocks() })
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

it('selects search results and shares individually configured agents across a batch', async () => {
  api.cases.mockResolvedValue([{ id: 'case-a', preview: 'A' }, { id: 'case-b', preview: 'B' }, { id: 'other', preview: 'C' }])
  api.models.mockResolvedValue({ agents: [
    { agent_id: 'semantic_graphing', model: 'default' },
    { agent_id: 'pulmonology', model: 'default' },
  ] })
  api.createBatch.mockResolvedValue({ kind: 'run', id: 'batch-1' })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<MemoryRouter><QueryClientProvider client={client}><App><NewRunPage initialMode="batch" /></App></QueryClientProvider></MemoryRouter>)
  await screen.findByText('case-a')
  await screen.findByLabelText('pulmonology model')
  fireEvent.change(screen.getByLabelText('搜索病例'), { target: { value: 'case-' } })
  expect(screen.queryByText('other')).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '全选搜索结果' }))
  expect(screen.getByText('已选 2 / 3 例')).toBeInTheDocument()
  fireEvent.change(screen.getByLabelText('统一模型'), { target: { value: 'shared-model' } })
  fireEvent.click(screen.getByRole('button', { name: '应用到所有 Agent' }))
  fireEvent.change(screen.getByLabelText('pulmonology model'), { target: { value: 'specialty-model' } })
  fireEvent.click(screen.getByRole('button', { name: '开始批量运行（2 例）' }))
  await waitFor(() => expect(api.createBatch).toHaveBeenCalledWith(expect.objectContaining({
    kind: 'run', source: 'library', case_ids: ['case-a', 'case-b'], max_case_concurrency: 2,
    agents: { semantic_graphing: { model: 'shared-model', reasoning_effort: 'none' }, pulmonology: { model: 'specialty-model', reasoning_effort: 'none' } },
  })))
  expect(api.createRun).not.toHaveBeenCalled()
})
