import { Alert } from 'antd'
import { TeamSynthesis } from '../../components/TeamSynthesis'

export function FinalReport({ report }) {
  if (report.schema_version !== 'mdt_final_report.v8' || report.team_synthesis?.schema_version !== 'team_synthesis.v2') {
    return <Alert type="warning" title="旧版报告仅保留在历史产物中，请按新结构重新生成。" />
  }
  return <TeamSynthesis team={report.team_synthesis} stopReason={report.stop_reason} discussionRounds={report.discussion_rounds} final />
}
