import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, expect, it, vi } from 'vitest'
import { api } from '../../api'
import { ArtifactsWorkspace } from './ArtifactsWorkspace'

vi.mock('../../api', () => ({ api: { artifacts: vi.fn(), artifactText: vi.fn(), artifactUrl: vi.fn((run, name) => `/artifacts/${run}/${name}`) } }))
vi.mock('@monaco-editor/react', () => ({ default: ({ value }) => <pre data-testid="artifact-content">{value}</pre> }))
afterEach(() => { cleanup(); vi.clearAllMocks() })

it('defaults to the current final result and reloads a replaced file', async () => {
  const report = { name: 'case_mdt_final_report.json', kind: 'json', current: true, updated_at: 'v1', bytes: 10 }
  const history = { name: 'old_initial.html', kind: 'html', current: false, updated_at: 'v0', bytes: 10 }
  api.artifacts.mockResolvedValue([history, report])
  api.artifactText.mockResolvedValue('本次诊断A')
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><ArtifactsWorkspace runId="run-1" /></QueryClientProvider>)
  expect(await screen.findByText('本次诊断A')).toBeInTheDocument()
  expect(screen.getByText('当前正式产物')).toBeInTheDocument()
  expect(api.artifactText).toHaveBeenLastCalledWith('run-1', report.name)
  api.artifactText.mockResolvedValue('本次诊断B')
  api.artifacts.mockResolvedValue([history, { ...report, updated_at: 'v2' }])
  await act(async () => { await client.invalidateQueries({ queryKey: ['artifacts', 'run-1'] }) })
  await waitFor(() => expect(screen.getByTestId('artifact-content')).toHaveTextContent('本次诊断B'))
  expect(screen.queryByText('本次诊断A')).not.toBeInTheDocument()
  expect(api.artifactText).toHaveBeenCalledTimes(2)
})
