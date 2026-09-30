import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, Button, Empty, Skeleton, Typography } from 'antd'
import { Link } from 'react-router-dom'
import { api } from '../../api'
import { QueryError } from '../../components/QueryState'
import { FinalReport } from './FinalReport'

const { Text, Title } = Typography

export function ReportWorkspace({ runId, run }) {
  const client = useQueryClient()
  const query = useQuery({
    queryKey: ['report', runId], queryFn: () => api.report(runId),
    refetchInterval: (q) => q.state.data?.report_status === 'running' || ['queued', 'running', 'stopping'].includes(run?.status) ? 2000 : false,
  })
  const mutation = useMutation({
    mutationFn: () => api.runReport(runId),
    onSuccess: (value) => {
      client.setQueryData(['report', runId], value)
      client.invalidateQueries({ queryKey: ['run', runId] })
    },
  })
  if (query.isLoading) return <Skeleton active />
  if (query.isError) return <QueryError error={query.error} retry={query.refetch} title="无法打开最终报告" />
  const value = query.data
  const running = value.report_status === 'running' || mutation.isPending
  return <div className="report-workspace">
    <div className="workspace-title">
      <div><Title level={3}>最终 MDT 统一报告</Title><Text type="secondary">{run?.case_id || value.case_id}</Text></div>
      <Button type="primary" loading={running} disabled={!value.runnable || running || ['queued', 'running', 'stopping'].includes(run?.status)} onClick={() => mutation.mutate()}>
        {value.final_report || value.report_status === 'failed' ? '重新运行本阶段' : '生成最终报告'}
      </Button>
    </div>
    <Alert className="section-gap" type="info" title="报告可独立生成" description="直接使用已完成讨论的综合结论，不重新运行专科或讨论。停止的讨论保留为阶段结果，不生成最终报告。" />
    {value.status === 'outdated' && <Alert className="section-gap" type="warning" title="上游结果已更新，请先重新运行团队讨论。" />}
    {value.report_status === 'failed' && <Alert className="section-gap" type="error" title="最终报告生成失败" description={value.error} />}
    {mutation.isError && <Alert type="error" title="无法生成报告" description={mutation.error.message} />}
    {value.final_report ? <FinalReport report={value.final_report} /> : <Empty description={value.runnable ? '讨论已完成，可单独生成最终报告' : value.error || '最终报告尚未生成'} />}
    <Link to={`/runs/${encodeURIComponent(runId)}/discussion`}>查看 MDT 团队讨论</Link>
  </div>
}
