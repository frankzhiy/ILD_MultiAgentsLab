import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { PlayCircleOutlined, ReloadOutlined } from '@ant-design/icons'
import { Alert, Button, Skeleton, Space, Typography } from 'antd'
import { api } from '../../api'
import { QueryError } from '../../components/QueryState'
import { StatusTag } from '../../components/StatusTag'
import { TeamSynthesis } from '../../components/TeamSynthesis'

const { Text, Title } = Typography

export function ChairResultTabs({ result }) {
  if (!result) return null
  if (result.schema_version !== 'mdt_chair.v11' || result.team_synthesis?.schema_version !== 'team_synthesis.v2') {
    return <Alert type="warning" title="旧版结果仅保留在历史产物中，请重新运行本阶段。" />
  }
  return <TeamSynthesis team={result.team_synthesis} />
}

export function ChairWorkspace({ runId, run }) {
  const client = useQueryClient()
  const query = useQuery({
    queryKey: ['chair', runId, 'presented'], queryFn: () => api.chair(runId, true),
    refetchInterval: (q) => q.state.data?.status === 'running' || ['queued', 'running', 'stopping'].includes(run?.status) ? 2000 : false,
  })
  const mutation = useMutation({
    mutationFn: () => api.runChair(runId),
    onSuccess: (value) => {
      client.setQueryData(['chair', runId, 'presented'], value)
      ;['run', 'discussion', 'report'].forEach(key => client.invalidateQueries({ queryKey: [key, runId] }))
    },
  })
  if (query.isError) return <QueryError error={query.error} retry={query.refetch} />
  if (query.isLoading) return <Skeleton active />
  const value = query.data || {}
  const running = value.status === 'running' || mutation.isPending
  const rerun = value.result || value.has_previous_result || ['failed', 'outdated'].includes(value.status)
  return <div className="chair-workspace">
    <div className="workspace-title">
      <div><Title level={3}>MDT 会前整合</Title><Space><StatusTag status={running ? 'running' : value.status} /><Text type="secondary">基于四个专科首轮意见的初步综合与讨论议程</Text></Space></div>
      <Button type="primary" aria-label={rerun ? '从会前整合重新运行' : '运行会前整合'} icon={rerun ? <ReloadOutlined /> : <PlayCircleOutlined />} loading={running}
        disabled={!value.runnable || running || ['queued', 'running', 'stopping'].includes(run?.status)} onClick={() => mutation.mutate()}>
        {rerun ? '从会前整合重新运行' : '运行会前整合'}
      </Button>
    </div>
    <Alert className="section-gap" type="info" title="从会前整合继续完整流程" description="复用已有四个专科结果，依次完成会前整合、团队讨论及最终报告。旧产物保留在历史记录中。" />
    {value.status === 'outdated' && <Alert className="section-gap" type="warning" title="旧版结构需要重新运行" description="历史产物仍可下载；当前页面和讨论仅使用新结构。" />}
    {['failed', 'unavailable'].includes(value.status) && <Alert className="section-gap" type="error" title={value.status === 'failed' ? '会前整合失败' : '会前整合尚不可运行'} description={value.error} />}
    {mutation.isError && <Alert type="error" title="无法启动会前整合" description={mutation.error.message} />}
    <ChairResultTabs result={value.result} />
  </div>
}
