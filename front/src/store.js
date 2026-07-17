import { create } from 'zustand'

export const useWorkbenchStore = create((set) => ({
  evidence: null,
  evidenceOpen: false,
  selectEvidence: (evidence) => set({ evidence, evidenceOpen: true }),
  closeEvidence: () => set({ evidenceOpen: false }),
}))
