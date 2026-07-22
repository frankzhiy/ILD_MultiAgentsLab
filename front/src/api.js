const json = async (response) => {
  if (!response.ok) {
    const body = await response.text()
    throw new Error(body || `${response.status} ${response.statusText}`)
  }
  return response.json()
}

export const api = {
  cases: () => fetch('/api/cases').then(json),
  case: (caseId) => fetch(`/api/cases/${encodeURIComponent(caseId)}`).then(json),
  models: () => fetch('/api/models').then(json),
  runs: () => fetch('/api/runs').then(json),
  createRun: (payload) => fetch('/api/runs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }).then(json),
  run: (runId) => fetch(`/api/runs/${encodeURIComponent(runId)}`).then(json),
  semantic: (runId) => fetch(`/api/runs/${encodeURIComponent(runId)}/semantic`).then(json),
  routing: (runId) => fetch(`/api/runs/${encodeURIComponent(runId)}/routing`).then(json),
  specialties: (runId) => fetch(`/api/runs/${encodeURIComponent(runId)}/specialties`).then(json),
  chair: (runId) => fetch(`/api/runs/${encodeURIComponent(runId)}/chair`).then(json),
  runChair: (runId) => fetch(`/api/runs/${encodeURIComponent(runId)}/chair`, { method: 'POST' }).then(json),
  artifacts: (runId) => fetch(`/api/runs/${encodeURIComponent(runId)}/artifacts`).then(json),
  errors: (runId) => fetch(`/api/runs/${encodeURIComponent(runId)}/errors`).then(json),
  guidelines: () => fetch('/api/guidelines').then(json),
  artifactUrl: (runId, name) => `/api/runs/${encodeURIComponent(runId)}/artifacts/${name.split('/').map(encodeURIComponent).join('/')}`,
  artifactText: async (runId, name) => {
    const response = await fetch(`/api/runs/${encodeURIComponent(runId)}/artifacts/${name.split('/').map(encodeURIComponent).join('/')}`)
    if (!response.ok) throw new Error(await response.text())
    return response.text()
  },
  guidelineUrl: (name, page) => `/api/guidelines/${encodeURIComponent(name)}${page ? `#page=${page}` : ''}`,
}
