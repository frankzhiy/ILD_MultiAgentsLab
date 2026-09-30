import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, expect, it, vi } from 'vitest'
import { api } from '../../api'
import { OverviewWorkspace } from './OverviewWorkspace'

vi.mock('../../api', () => ({
  api: {
    specialties: vi.fn(), semantic: vi.fn(), chair: vi.fn(), discussion: vi.fn(),
  },
}))
vi.mock('@xyflow/react', () => ({
  MarkerType: { ArrowClosed: 'arrow' },
  ReactFlow: ({ nodes }) => <div data-testid="flow" data-nodes={JSON.stringify(nodes)} />,
  Background: () => null,
  Controls: () => null,
}))

class FakeEventSource {
  addEventListener() {}
  close() {}
}

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

it('restores the chair, discussion, and final report stages after refresh', async () => {
  vi.stubGlobal('EventSource', FakeEventSource)
  api.semantic.mockResolvedValue({ summary: { segment_count: 2, unit_count: 3 } })
  api.specialties.mockResolvedValue({ results: ['pulmonology', 'thoracic_radiology', 'rheumatology', 'pathology'].map((specialty) => ({ specialty, label: specialty, status: 'completed' })) })
  api.chair.mockResolvedValue({ status: 'completed' })
  api.discussion.mockResolvedValue({ status: 'completed', current_round: 1, report_status: 'completed', final_report: { primary_conclusion: '工作诊断' } })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<MemoryRouter><QueryClientProvider client={client}><OverviewWorkspace runId="run-1" run={{ manifest: { case_version_id: 'current-version', parent_case_version_id: 'previous-version', parent_run_id: 'prior-consultation' }, status: 'completed', semantic_complete: true, completed_specialties: ['pulmonology', 'thoracic_radiology', 'rheumatology', 'pathology'], chair_complete: true, discussion_complete: true }} /></QueryClientProvider></MemoryRouter>)

  await waitFor(() => expect(screen.getByRole('link', { name: '查看最终报告' })).toHaveAttribute('href', '/runs/run-1/report'))
  expect(screen.getByRole('link', { name: '查看前序会诊' })).toHaveAttribute('href', '/runs/prior-consultation/overview')
  const nodes = JSON.parse(screen.getByTestId('flow').dataset.nodes)
  expect(nodes.filter((node) => ['chair', 'discussion', 'report'].includes(node.id)).map((node) => node.className)).toEqual(['flow-success', 'flow-success', 'flow-success'])
})
