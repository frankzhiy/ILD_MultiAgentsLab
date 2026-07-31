import { Navigate, Route, Routes } from 'react-router-dom'
import { EvidenceDrawer } from './components/EvidenceDrawer'
import { NewRunPage } from './pages/NewRunPage'
import { RunListPage } from './pages/RunListPage'
import { RunWorkspace } from './pages/RunWorkspace'

export function App() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/runs" replace />} />
      <Route path="/runs" element={<RunListPage />} />
      <Route path="/runs/new" element={<NewRunPage />} />
      <Route path="/runs/:runId/:view?" element={<><RunWorkspace /><EvidenceDrawer /></>} />
    </Routes>
  )
}
