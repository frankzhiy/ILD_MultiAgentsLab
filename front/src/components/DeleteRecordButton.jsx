import { useMutation, useQueryClient } from '@tanstack/react-query'
import { App, Button, Popconfirm } from 'antd'
import { api } from '../api'

export function DeleteRecordButton({ id, batchKind, disabled, onDeleted }) {
  const client = useQueryClient()
  const { message } = App.useApp()
  const label = batchKind ? '删除批次' : '删除运行'
  const scope = batchKind === 'run'
    ? '将从工作台移除此批次及其创建的全部运行。'
    : batchKind === 'discussion'
      ? '将从工作台移除此讨论批次，保留关联运行。'
      : '将从工作台移除此运行及其在各批次中的条目。'
  const deletion = useMutation({
    mutationFn: () => batchKind ? api.deleteBatch(id) : api.deleteRun(id),
    onSuccess: () => {
      onDeleted?.()
      for (const key of ['runs', 'batches', 'run', 'batch']) {
        client.invalidateQueries({ queryKey: [key] })
      }
      message.success(batchKind ? '已删除批次' : '已删除运行')
    },
    onError: (error) => message.error(error.message),
  })

  return <Popconfirm
    title={`确认${label}？`}
    description={<>{id}<br />{scope}本地文件会保留。</>}
    okText="确认删除"
    cancelText="取消"
    okButtonProps={{ danger: true }}
    disabled={disabled}
    onConfirm={() => deletion.mutateAsync().catch(() => {})}
  >
    <Button danger type="text" disabled={disabled} loading={deletion.isPending}>{label}</Button>
  </Popconfirm>
}
