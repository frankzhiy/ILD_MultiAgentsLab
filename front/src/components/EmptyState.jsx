import { Empty } from 'antd'

export function EmptyState({ description = '当前阶段尚无产物' }) {
  return <div className="empty-state"><Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={description} /></div>
}
