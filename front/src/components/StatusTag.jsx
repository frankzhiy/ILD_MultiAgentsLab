import { Tag } from 'antd'

const STATUS = {
  completed: ['已完成', 'success'],
  specialists_running: ['专科运行中', 'processing'],
  routing_pending: ['待分发', 'warning'],
  semantic_running: ['语义处理中', 'processing'],
  failed: ['失败', 'error'],
  queued: ['排队中', 'default'],
  running: ['运行中', 'processing'],
  cancelled: ['已取消', 'default'],
  pending: ['等待中', 'default'],
  prepared: ['已分发', 'success'],
  not_prepared: ['未分发', 'default'],
}

export function StatusTag({ status }) {
  const [label, color] = STATUS[status] || [status || '未知', 'default']
  return <Tag color={color}>{label}</Tag>
}
