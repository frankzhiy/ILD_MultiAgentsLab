import { expect, it } from 'vitest'
import { aggregateCitations } from './Citation'

it('preserves distinct guideline page and quote locations', () => {
  const first = { guideline_id: 'G1', source_file: 'guide.pdf', chunk_id: 'c1', page: 3, quote: '第三页原文', quote_start: 1, quote_end: 6 }
  const second = { ...first, chunk_id: 'c2', page: 8, quote: '第八页原文' }
  const items = aggregateCitations([first, second, { ...first }])
  expect(items).toHaveLength(2)
  expect(items.map(item => [item.page, item.quote])).toEqual([[3, '第三页原文'], [8, '第八页原文']])
})

it('preserves evidence selection and atomic claim relations', () => {
  const first = { graph_unit_id: 'U1', evidence_ids: ['E1'], quote: '第一段', claim_statement: '判断A', relations: [{ target_claim_id: 'C1', relation: 'supports' }] }
  const second = { ...first, evidence_ids: ['E2'], quote: '第二段', claim_statement: '判断B', relations: [{ target_claim_id: 'C2', relation: 'weakening' }] }
  const before = structuredClone([first, second])
  const items = aggregateCitations([first, second, { ...first }])
  expect(items).toHaveLength(2)
  expect(items.map(item => [item.quote, item.relations[0].target_claim_id])).toEqual([['第一段', 'C1'], ['第二段', 'C2']])
  expect([first, second]).toEqual(before)
})
