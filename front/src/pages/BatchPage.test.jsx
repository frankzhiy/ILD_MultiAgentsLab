import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, expect, it, vi } from 'vitest'
import { api } from '../api'
import { BatchPage } from './BatchPage'

vi.mock('../api', () => ({ api: { batch: vi.fn(), retryBatch: vi.fn() } }))
afterEach(() => { cleanup(); vi.clearAllMocks() })

const batch = {
  id: 'batch-1', kind: 'run', status: 'running', created_at: '2026-09-30T10:00:00Z',
  request: { max_case_concurrency: 2, max_request_concurrency: 6 },
  agents: { pulmonology: { model: 'specialty-model', reasoning_effort: 'none' } },
  summary: { total: 4, queued: 0, running: 2, completed: 1, failed: 1, skipped: 0, interrupted: 0, stopped: 0 },
  items: [
    { case_id: 'case-a', run_id: 'run-a', status: 'running', elapsed_seconds: 61, progress: { stage: 'initial_consult', completed_specialties: ['a', 'b', 'c'] } },
    { case_id: 'case-b', run_id: 'run-b', status: 'running', progress: { stage: 'team_discussion', round_number: 2 } },
    { case_id: 'case-c', run_id: 'run-c', status: 'completed', progress: {} },
    { case_id: 'case-d', run_id: 'run-d', status: 'failed', error: 'request timeout', progress: { stage: 'semantic_graphing' } },
  ],
}

function openBatch() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><MemoryRouter initialEntries={['/batches/batch-1']}><Routes>
    <Route path="/batches/:batchId" element={<BatchPage />} />
  </Routes></MemoryRouter></QueryClientProvider>)
  return client
}

it('restores progress, results, configuration and export after opening a batch', async () => {
  api.batch.mockResolvedValue(batch)
  const client = openBatch()
  expect(await screen.findByText('专科评估，已完成 3/4')).toBeInTheDocument()
  expect(screen.getByText('团队讨论，第 2 轮')).toBeInTheDocument()
  expect(screen.getByText('1 分 1 秒')).toBeInTheDocument()
  expect(screen.getByRole('link', { name: '查看结果' })).toHaveAttribute('href', '/runs/run-c/report')
  expect(screen.getByRole('link', { name: '查看错误' })).toHaveAttribute('href', '/runs/run-d/errors')
  expect(screen.getByRole('link', { name: '导出病例汇总 CSV' })).toHaveAttribute('href', '/api/batches/batch-1/export')
  expect(screen.getByRole('button', { name: '重跑未完成病例' })).toBeDisabled()
  fireEvent.click(screen.getByText('本批次配置（只读）'))
  expect(await screen.findByText('specialty-model')).toBeInTheDocument()
  client.setQueryData(['batch', 'batch-1'], { ...batch, status: 'completed_with_errors' })
  await waitFor(() => expect(screen.getByRole('button', { name: '重跑未完成病例' })).toBeEnabled())
  api.retryBatch.mockResolvedValue({ id: 'batch-2' })
  fireEvent.click(screen.getByRole('button', { name: '重跑未完成病例' }))
  await waitFor(() => expect(api.retryBatch).toHaveBeenCalledWith('batch-1'))
  client.clear()
})
