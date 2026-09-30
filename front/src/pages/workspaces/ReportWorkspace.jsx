import { useQuery } from '@tanstack/react-query'
import { Alert, Empty, Skeleton, Typography } from 'antd'
import { Link } from 'react-router-dom'
import { api } from '../../api'
import { QueryError } from '../../components/QueryState'
import { FinalReport } from './FinalReport'

const { Text, Title } = Typography

export function ReportWorkspace({ runId, run }) {
  const query = useQuery({
    queryKey: ['report', runId],
    queryFn: () => api.report(runId),
    refetchInterval: (current) => current.state.data?.status === 'running' ? 2000 : false,
  })
  if (query.isLoading) return <Skeleton active paragraph={{ rows: 7 }} />
  if (query.isError) return <QueryError error={query.error} retry={query.refetch} title="无法打开最终报告" />
  const value = query.data
  return <div className="report-workspace">
    <div className="workspace-title">
      <div><Text className="eyebrow">MDT REPORT</Text><Title level={3}>最终 MDT 统一报告</Title><Text type="secondary">{run?.case_id || value.case_id}</Text></div>
    </div>
    {value.presentation_status === 'unavailable' && <Alert className="section-gap" type="warning" showIcon title="文字润色暂不可用" description="当前显示原始报告；医学结论和证据记录未受影响。" />}
    {value.status === 'outdated' && <Alert className="section-gap" type="warning" showIcon title="主持人结果已更新" description="此报告基于旧版讨论结果；重新运行团队讨论后可生成与当前判断一致的报告。" />}
    {value.final_report?.schema_version && value.final_report.schema_version !== 'mdt_final_report.v6' && <Alert className="section-gap" type="info" showIcon title="此报告按旧版规则生成" description="重新运行团队讨论后，新报告会使用疾病类别与分型判断及更新后的临床表述。" />}
    {value.final_report ? <FinalReport report={value.final_report} /> : <Empty className="report-empty" description={value.report_status === 'failed' ? '最终报告生成失败' : '最终报告尚未生成'} />}
    {!value.final_report && <Link to={`/runs/${encodeURIComponent(runId)}/discussion`}>查看 MDT 团队讨论</Link>}
  </div>
}
