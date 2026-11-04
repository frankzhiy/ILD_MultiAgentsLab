import { Alert } from 'antd'
import { isCurrentTeam } from '../../components/resultContract'
import { TeamSynthesis } from '../../components/TeamSynthesis'

export function FinalReport({ report, diagnosisConclusion }) {
  if (report.schema_version !== 'mdt_final_report.v10' || !isCurrentTeam(report.team_synthesis)) {
    return <Alert type="warning" title="旧版报告仅保留在历史产物中，请按新结构重新生成。" />
  }
  return <TeamSynthesis team={report.team_synthesis} stopReason={report.stop_reason} discussionRounds={report.discussion_rounds} diagnosisConclusion={diagnosisConclusion} final />
}
