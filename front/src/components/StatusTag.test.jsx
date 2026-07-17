import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { StatusTag } from './StatusTag'

describe('StatusTag', () => {
  it('renders active and completed research states in Chinese', () => {
    const { rerender } = render(<StatusTag status="running" />)
    expect(screen.getByText('运行中')).toBeInTheDocument()
    rerender(<StatusTag status="completed" />)
    expect(screen.getByText('已完成')).toBeInTheDocument()
  })
})
