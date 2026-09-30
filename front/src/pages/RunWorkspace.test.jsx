import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, expect, it, vi } from 'vitest'
import { api } from '../api'
import { RunWorkspace } from './RunWorkspace'

vi.mock('../api', () => ({ api: { run: vi.fn(), stopRun: vi.fn() } }))
vi.mock('./workspaces/OverviewWorkspace', () => ({ OverviewWorkspace: () => <div>总览内容</div> }))
vi.mock('./workspaces/ChairWorkspace', () => ({ ChairWorkspace: () => <div>会前内容</div> }))
vi.mock('./workspaces/DiscussionWorkspace', () => ({ DiscussionWorkspace: () => <div>讨论内容</div> }))
vi.mock('./workspaces/ReportWorkspace', () => ({ ReportWorkspace: () => <div>报告内容</div> }))
afterEach(() => { cleanup(); vi.clearAllMocks() })

function show(view, status = 'running') {
  const value = { id: 'run-1', case_id: 'case-1', status }
  api.run.mockResolvedValue(value)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } })
  render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[`/runs/run-1/${view}`]}>
    <Routes><Route path="/runs/:runId/:view" element={<RunWorkspace />} /></Routes>
  </MemoryRouter></QueryClientProvider>)
  return { client, value }
}

it.each(['overview', 'chair', 'discussion', 'report'])('stops the entire run from %s', async (view) => {
  const { client, value } = show(view)
  api.stopRun.mockImplementation(async () => {
    const stopping = { ...value, status: 'stopping' }
    api.run.mockResolvedValue(stopping)
    return stopping
  })
  fireEvent.click(await screen.findByRole('button', { name: '停止运行' }))
  await waitFor(() => expect(api.stopRun).toHaveBeenCalledWith('run-1'))
  expect(await screen.findByText('正在停止运行')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '正在停止' })).toBeDisabled()
  api.run.mockResolvedValue({ ...value, status: 'stopped' })
  await client.invalidateQueries({ queryKey: ['run', 'run-1'] })
  expect(await screen.findByText('运行已停止')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /停止/ })).not.toBeInTheDocument()
})

it.each(['completed', 'failed', 'incomplete'])('does not offer to stop an inactive %s run', async (status) => {
  show('overview', status)
  expect(await screen.findByText('case-1')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: '停止运行' })).not.toBeInTheDocument()
})

it('explains a rejected stop without hiding the running case', async () => {
  show('overview')
  api.stopRun.mockRejectedValue(new Error('请求失败'))
  fireEvent.click(await screen.findByRole('button', { name: '停止运行' }))
  expect(await screen.findByText('停止运行失败')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '停止运行' })).toBeEnabled()
})
