import { useMutation, useQueryClient } from '@tanstack/react-query'
import { App, Button, Popconfirm } from 'antd'
import { api } from '../api'

export function DeleteRecordButton({ id, batchKind, disabled, onDeleted }) {
  const client = useQueryClient()
  const { message } = App.useApp()
  const label = batchKind ? '删除批次' : '删除运行'
  const scope = batchKind === 'run'
    ? '将永久删除此批次及其创建的全部运行和本地产物。'
    : batchKind === 'discussion'
      ? '将永久删除此讨论批次文件，保留关联运行。'
      : '将永久删除此运行的本地目录和产物，并从关联批次中移除。'
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
    description={<>{id}<br />{scope}</>}
    okText="确认删除"
    cancelText="取消"
    okButtonProps={{ danger: true }}
    disabled={disabled}
    onConfirm={() => deletion.mutateAsync().catch(() => {})}
  >
    <Button danger type="text" disabled={disabled} loading={deletion.isPending}>{label}</Button>
  </Popconfirm>
}
