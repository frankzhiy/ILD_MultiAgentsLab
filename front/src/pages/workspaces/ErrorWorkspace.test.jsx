import { render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, expect, it, vi } from 'vitest'
import { api } from '../../api'
import { ErrorWorkspace } from './ErrorWorkspace'

vi.mock('../../api', () => ({ api: { errors: vi.fn() } }))

beforeEach(() => api.errors.mockReset())

it('shows the latest completed status instead of recovered errors', async () => {
  api.errors.mockResolvedValue([{
    artifact: 'case_mdt_chair_failure_trace.json',
    agent_id: 'mdt_chair',
    current: false,
    error: 'old failure',
    payload: {},
  }])
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

  render(
    <QueryClientProvider client={client}>
      <ErrorWorkspace runId="run-1" run={{ status: 'completed' }} />
    </QueryClientProvider>,
  )

  await waitFor(() => expect(api.errors).toHaveBeenCalledWith('run-1'))
  expect(screen.getByText('最新状态：已完成')).toBeInTheDocument()
  expect(screen.getByText('运行已顺利完成，当前没有错误')).toBeInTheDocument()
  expect(screen.queryByText('case_mdt_chair_failure_trace.json')).not.toBeInTheDocument()
})
