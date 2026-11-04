import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it } from 'vitest'
import { FacetJudgment, JudgmentDimensions } from './JudgmentDimensions'

afterEach(cleanup)

it.each([
  ['insufficient_support', '尚缺支持依据'],
  ['against', '存在反对依据'],
  ['excluded', '已有依据排除'],
])('keeps %s separate from judgment confidence', (direction, label) => {
  render(<JudgmentDimensions item={{ assessability: 'assessable', direction, confidence: 'high', clinical_role: 'competing_explanation' }} />)
  expect(screen.getByText(`判断方向：${label}`)).toBeVisible()
  expect(screen.getByText('判断把握度：高把握度')).toBeVisible()
  expect(screen.queryByText('判断方向：有正向支持')).not.toBeInTheDocument()
})

it('shows answer completion and answer confidence without inferring diagnostic support', () => {
  render(<JudgmentDimensions item={{ source_type: 'discussion_answer', answerability: 'answered', answer_confidence: 'high' }} />)
  expect(screen.getByText('回答完成情况：已回答')).toBeVisible()
  expect(screen.getByText('回答把握度：高把握度')).toBeVisible()
  expect(screen.queryByText(/判断方向|判断把握度/)).not.toBeInTheDocument()
})

it('does not turn an ambiguous historical direction into affirmative counterevidence', () => {
  render(<JudgmentDimensions item={{ status: 'supported', direction: 'against', confidence: 'moderate' }} />)
  expect(screen.getByText('判断方向：旧版反对方向（未区分支持不足与反证）')).toBeVisible()
  expect(screen.queryByText('判断方向：存在反对依据')).not.toBeInTheDocument()
  expect(screen.queryByText('支持')).not.toBeInTheDocument()
})

it('does not derive answer confidence from a historical summary', () => {
  render(<JudgmentDimensions item={{ source_type: 'discussion_answer', status: 'favored' }} />)
  expect(screen.getByText(/旧版回答未独立记录完成情况与把握度/)).toBeVisible()
  expect(screen.queryByText('回答把握度：中等把握度')).not.toBeInTheDocument()
})

it('shows a facet target and its independent dimensions without inventing a clinical role', () => {
  render(<FacetJudgment item={{ subject: '组织学UIP模式', assessability: 'not_assessable', direction: 'indeterminate', confidence: 'unknown' }} />)
  expect(screen.getByText('判断对象：组织学UIP模式')).toBeVisible()
  expect(screen.getByText('可评价性：不可评价')).toBeVisible()
  expect(screen.getByText('判断方向：判断方向未定')).toBeVisible()
  expect(screen.queryByText(/临床关系/)).not.toBeInTheDocument()
})

it('marks legacy facet summaries without inferring new dimensions', () => {
  render(<FacetJudgment item={{ status: 'possible', confidence: 'low' }} />)
  expect(screen.getByText('旧版层级摘要：可能')).toBeVisible()
  expect(screen.getByText(/摘要不等于确诊或排除/)).toBeVisible()
  expect(screen.queryByText(/判断方向：/)).not.toBeInTheDocument()
})
