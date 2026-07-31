import { create } from 'zustand'

export const useWorkbenchStore = create((set) => ({
  evidence: null,
  evidenceList: [],
  evidenceIndex: 0,
  evidenceOpen: false,
  selectEvidence: (evidence, evidenceList = [evidence], evidenceIndex = 0) => set({
    evidence,
    evidenceList,
    evidenceIndex,
    evidenceOpen: true,
  }),
  setEvidenceIndex: (evidenceIndex) => set((state) => ({
    evidence: state.evidenceList[evidenceIndex],
    evidenceIndex,
  })),
  closeEvidence: () => set({ evidenceOpen: false }),
}))
