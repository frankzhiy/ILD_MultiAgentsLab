import { Alert, Button, Space, Tag, Typography } from 'antd'
import { PrinterOutlined } from '@ant-design/icons'
import { Citation, CitationGroup } from './Citation'
import { assessabilityLabels, FacetJudgment } from './JudgmentDimensions'
import { confidenceLabels as confidence, candidatePositionLabels as position, diagnosisStatusLabels as diagnosisStatus, acceptanceLabels as acceptance, candidateOutcomeLabels as outcomes, diagnosticDimensionLabels as dimensions, nonselectionLabels, candidateName, problemRelationshipLabels as relationship, evidenceNeedStatusLabels, evidenceNeedActionLabels as action } from './clinicalConcepts'

const { Title, Paragraph, Text } = Typography
const evidenceStatus = { available: [evidenceNeedStatusLabels.available, 'green'], partially_available: [evidenceNeedStatusLabels.partially_available, 'gold'], missing: [evidenceNeedStatusLabels.missing, 'orange'] }
const specialty = { pulmonology: '呼吸科', thoracic_radiology: '影像科', rheumatology: '风湿免疫科', pathology: '病理科' }

function Section({ id, number, title, children }) {
  return <section id={id} aria-label={title} className="clinical-report-section">
    <div className="clinical-section-heading"><span>{number}</span><Title level={4}>{title}</Title></div>
    {children}
  </section>
}

export function ClinicalReport({ team, final, stopReason, discussionRounds, renderSources, reviews, questions, conditionalContributions, diagnosisConclusion }) {
  const opinion = team.diagnostic_opinion
  const resolution = team.diagnostic_resolution
  const primary = team.problems.filter(p => p.relationship === 'primary')
  const other = team.problems.filter(p => p.relationship !== 'primary')
  const clinicalText = value => value?.split(/(\bS\d+\b)/).map((part, i) => team.source_catalog[part]
    ? <span key={i}><Citation value={team.source_catalog[part]} /><span className="clinical-print-source">{specialty[team.source_catalog[part].specialty]}意见（{part}）</span></span> : part)
  const sourceDetails = (refs = [], evidence = [], label = '查看原文与专科依据') => <details className="clinical-source-details"><summary>{label}</summary>{renderSources(refs, evidence)}</details>
  const evidenceLinks = (refs, role) => <CitationGroup caseEvidence={(refs || []).map(ref => ({
    ...(team.evidence_catalog[ref] || { evidence_ref: ref, quote: `引用未找到：${ref}` }),
    relations: [{ relation: role }],
  }))} />
  const sections = [['diagnosis', '诊断结论与候选'], ['basis', '诊断依据与取舍'], ['boundaries', '判断边界'], ['needs', '证据需求及满足状态'], ['meeting', final ? '团队意见与讨论结果' : '本次讨论议程']]
  return <div className="clinical-report">
    <header className="clinical-report-header">
      <div><Text className="eyebrow">{final ? 'MDT 诊断报告' : '会前初步判断与讨论议程'}</Text>
        {!final && <Space wrap className="clinical-diagnosis-status"><Tag color="blue">{diagnosisStatus[resolution.status]}</Tag>{primary.length === 1 && <Tag color={primary[0].confidence === 'low' ? 'gold' : 'default'}>{confidence[primary[0].confidence]}</Tag>}</Space>}
        <Title level={3}>{final && diagnosisConclusion ? diagnosisConclusion : resolution.primary_diagnosis}</Title>
        {!final && <Paragraph className="clinical-diagnosis-statement">{opinion.diagnosis}</Paragraph>}
      </div>
      {final && <Button className="no-print" icon={<PrinterOutlined />} onClick={() => window.print()}>打印</Button>}
    </header>
    <div className="clinical-report-meta"><Text>团队意见：{acceptance[team.acceptance]}</Text><Text type="secondary">第 {team.revision} 版{discussionRounds != null && ` · 已完成 ${discussionRounds} 轮讨论`}</Text></div>
    {['budget', 'no_progress'].includes(team.stop_kind) && <Alert showIcon type="warning" title={team.stop_kind === 'budget' ? '达到讨论轮数上限，仍有未决事项' : '本轮已无可推进的讨论路径，未决事项保留'} />}
    <nav className="clinical-report-nav no-print" aria-label="报告栏目导航">{sections.map(([id, label], i) => <a key={id} href={`#clinical-${id}`}>{String(i + 1).padStart(2, '0')} {label}</a>)}</nav>

    <Section id="clinical-diagnosis" number="01" title="诊断结论与候选">
      {team.problems.some(p => p.candidates.some(c => !c.candidate_id)) && <Paragraph type="secondary">旧版候选未记录统一编号，以下保留原始名称，不自动合并或重新诊断。</Paragraph>}
      {primary.map(p => <article className="clinical-problem" key={p.problem_id}>
        <div className="clinical-problem-heading"><Title level={5}>{p.title}</Title><Space wrap><Tag>{confidence[p.confidence]}</Tag><Text type="secondary">{assessabilityLabels[p.assessability]}</Text></Space></div>
        <Paragraph className="synthesis-statement">{p.statement}</Paragraph>
        {p.candidates.length ? <div className="clinical-table-wrap"><table className="clinical-table candidate-table"><caption className="sr-only">{p.title}的当前候选比较</caption>
          <thead><tr><th scope="col">诊断候选</th><th scope="col">当前判断</th><th scope="col">支持与取舍</th></tr></thead>
          <tbody>{p.candidates.map((c, index) => <tr key={index} className={c.position === 'preferred' ? 'is-preferred' : ''}>
            <th scope="row">{candidateName(team, c)}</th><td data-label="当前判断"><Tag color={c.position === 'preferred' ? 'blue' : 'default'}>{position[c.position]}</Tag><div>{confidence[c.confidence]}</div></td>
            <td data-label="支持与取舍"><Paragraph>{c.rationale}</Paragraph>{c.limitations.length > 0 && <Paragraph type="secondary">判断限制：{c.limitations.join('；')}</Paragraph>}
              <details><summary>查看此候选的支持、反对及限定依据</summary>
                <Text strong>支持证据</Text>{evidenceLinks(c.supporting_evidence_refs, 'supporting')}
                <Text strong className="clinical-evidence-label">反对或削弱证据</Text>{c.opposing_evidence_refs.length ? evidenceLinks(c.opposing_evidence_refs, 'weakening') : <Paragraph type="secondary">未单列反对证据，不代表已排除其他解释。</Paragraph>}
                {renderSources(c.source_refs, c.supporting_evidence_refs)}
                {c.opposing_evidence_refs.length > 0 && renderSources([], c.opposing_evidence_refs)}
              </details>
            </td>
          </tr>)}</tbody></table></div> : <Paragraph type="secondary">本版未在该临床问题下单列具体候选。{team.diagnostic_exploration.insufficient_candidate_basis || '候选探索及审定记录见第 02 栏。'}</Paragraph>}
      </article>)}
      {other.length > 0 && <div className="clinical-associated"><Title level={5}>急性叠加、并存问题及其他发现</Title>
        <Paragraph type="secondary">以下问题分别解释，不作为主要 ILD 的平行鉴别诊断。</Paragraph>
        {other.map(p => <article key={p.problem_id}><Space wrap><Tag>{relationship[p.relationship]}</Tag><Text strong>{p.statement}</Text><Tag>{confidence[p.confidence]}</Tag></Space>
          <Paragraph>{p.rationale}</Paragraph>
          {p.candidates.length > 1 && <details><summary>查看该伴随问题的鉴别解释</summary>{p.candidates.map((c, i) => <div key={i}><Text strong>{candidateName(team, c)} · {position[c.position]} · {confidence[c.confidence]}</Text><Paragraph>{c.rationale}</Paragraph>{sourceDetails(c.source_refs, [...c.supporting_evidence_refs, ...c.opposing_evidence_refs])}</div>)}</details>}
          {sourceDetails(p.source_refs, p.evidence_refs)}
        </article>)}
      </div>}
    </Section>

    <Section id="clinical-basis" number="02" title="诊断依据与取舍">
      <div className="clinical-key-reason"><Text strong>首选与备选的关键区别</Text><Paragraph>{resolution.goal_review}</Paragraph></div>
      <Title level={5}>综合诊断依据</Title><Paragraph>{opinion.reasoning}</Paragraph>
      {sourceDetails(opinion.source_refs, opinion.evidence_refs, '查看综合诊断引用的病例原文、专科判断与指南')}
      <details><summary>完整病例认识与鉴别论证</summary><Paragraph>{opinion.case_formulation}</Paragraph><Paragraph>{opinion.differential_reasoning}</Paragraph>
        {primary.map(p => <div key={p.problem_id}><Text strong>{p.title}</Text><Paragraph>{p.rationale}</Paragraph>{sourceDetails(p.source_refs, p.evidence_refs)}</div>)}
      </details>
      {team.diagnostic_exploration.candidates.length > 0 && <details><summary>候选探索与逐项审定记录</summary>
        <Paragraph type="secondary">保留候选形成过程；当前诊断及候选以第 01 栏为准。</Paragraph>
        <Paragraph>{team.diagnostic_exploration.problem_representation}</Paragraph>
        {team.diagnostic_exploration.candidates.map(c => {
          const decision = resolution.candidate_dispositions.find(d => d.candidate_id === c.candidate_id)
          return <article className="synthesis-note" key={c.candidate_id}><Text strong>{c.diagnosis}</Text> <Tag>{outcomes[decision?.outcome] || '未记录取舍'}</Tag>
            {decision?.outcome === 'rejected' && <Paragraph>未保留原因：{nonselectionLabels[decision.nonselection_reason] || '旧版未区分原因，不视为已排除'}</Paragraph>}
            {decision?.exclusion_evidence_refs?.length > 0 && <><Text strong>排除所据证据</Text>{evidenceLinks(decision.exclusion_evidence_refs, 'weakening')}{sourceDetails([], decision.exclusion_evidence_refs)}</>}
            <Paragraph>能解释：{c.explains}</Paragraph><Paragraph>不能充分解释：{c.fails_to_explain}</Paragraph><Paragraph>本版审定依据：{decision?.rationale || '未记录'}</Paragraph>
            {sourceDetails([], c.supporting_evidence_refs)}
            <details><summary>原始探索内容</summary><Paragraph>{c.discriminating_information}</Paragraph><Paragraph>{c.retrieval_question}</Paragraph></details>
          </article>
        })}
        <Paragraph>组合表型：{team.diagnostic_exploration.combined_phenotype_review}</Paragraph><Paragraph>其他发现的解释：{team.diagnostic_exploration.unrepresented_findings}</Paragraph>
      </details>}
    </Section>

    <Section id="clinical-boundaries" number="03" title="判断边界">
      <Paragraph type="secondary">当前结论的适用范围；未记载、不可评价与明确阴性分别保留。</Paragraph>
      {team.judgment_boundaries.length ? team.judgment_boundaries.map(b => <article className="clinical-boundary" key={b.boundary_id}>
        <Text strong>{team.problems.find(p => p.problem_id === b.problem_id)?.title || b.problem_id}</Text>
        <dl className="clinical-facts"><div><dt>已经能判断</dt><dd>{clinicalText(b.established)}</dd></div><div><dt>尚不能判断</dt><dd>{clinicalText(b.undetermined)}</dd></div><div><dt>对结论的影响</dt><dd>{clinicalText(b.decision_impact)}</dd></div></dl>
        <Paragraph type="secondary">原因：{clinicalText(b.reason)}</Paragraph>{sourceDetails(b.source_refs)}
      </article>) : <Paragraph type="secondary">本版未单列判断边界，请结合下方限制阅读。</Paragraph>}
      {(opinion.limitations.length > 0 || team.problems.some(p => p.limitations.length) || resolution.remaining_gaps.length > 0) && <details><summary>其他已记录的判断限制与未决问题</summary>
        {opinion.limitations.map((text, i) => <Paragraph key={i}>{text}</Paragraph>)}
        {team.problems.filter(p => p.limitations.length).map(p => <div key={p.problem_id}><Text strong>{p.title}</Text><ul>{p.limitations.map((text, i) => <li key={i}>{text}</li>)}</ul></div>)}
        {resolution.remaining_gaps.map((g, i) => <div key={i}><Tag>{g.kind === 'inference' ? '待讨论推理' : '患者资料缺口'}</Tag><Text strong>{g.question}</Text><Paragraph>{g.why_unresolved}</Paragraph></div>)}
      </details>}
      {team.diagnostic_facets?.length > 0 && <details><summary>按诊断层级核对</summary>{team.diagnostic_facets.map((f, i) => <article key={i} className="synthesis-note"><Text strong>{team.problems.find(p => p.problem_id === f.problem_id)?.title} · {dimensions[f.dimension]}</Text><FacetJudgment item={f} /><Paragraph>{f.statement}</Paragraph><Paragraph>{f.rationale}</Paragraph><Paragraph>{f.limitations.join('；')}</Paragraph>{sourceDetails(f.source_refs)}</article>)}</details>}
    </Section>

    <Section id="clinical-needs" number="04" title="证据需求及满足状态">
      <div className="clinical-needs-summary">{Object.entries(evidenceStatus).map(([key, [label, color]]) => <Tag key={key} color={color}>{label} {team.evidence_needs.filter(n => n.status === key).length} 项</Tag>)}</div>
      <Paragraph type="secondary">状态针对每项所需信息，不等于疾病已确诊；本轮不再追索也不等于证据已满足。</Paragraph>
      {team.evidence_needs.length ? <div className="clinical-table-wrap"><table className="clinical-table needs-table"><caption className="sr-only">证据需求、满足状态及诊断影响</caption>
        <thead><tr><th scope="col">证据需求 / 关联问题</th><th scope="col">满足状态</th><th scope="col">已有与仍缺信息</th><th scope="col">对判断的影响 / 后续处理</th></tr></thead>
        <tbody>{team.evidence_needs.map(n => <tr key={n.need_id}>
          <th scope="row">{n.information}<span className="clinical-cell-note">{team.problems.find(p => p.problem_id === n.problem_id)?.title || n.problem_id}</span></th>
          <td data-label="满足状态"><Tag color={evidenceStatus[n.status][1]}>{evidenceStatus[n.status][0]}</Tag></td>
          <td data-label="已有与仍缺信息"><Text strong>已有</Text><Paragraph>{n.available_information ? clinicalText(n.available_information) : '未记录已有信息'}</Paragraph><Text strong>仍缺</Text><Paragraph>{n.missing_information ? clinicalText(n.missing_information) : '无'}</Paragraph></td>
          <td data-label="对判断的影响 / 后续处理"><Tag>{action[n.action]}</Tag><Paragraph><Text strong>不可得时：</Text>{clinicalText(n.if_unavailable)}</Paragraph>
            <details><summary>什么结果会改变判断</summary>{n.branches.length ? <ul>{n.branches.map((b, i) => <li key={i}><Text strong>{clinicalText(b.result)}</Text><Paragraph>→ {clinicalText(b.changed_decision)}</Paragraph></li>)}</ul> : <Paragraph>本版未记录结果分支。</Paragraph>}<Paragraph>获取价值与负担：{clinicalText(n.feasibility_and_burden)}</Paragraph></details>
            {sourceDetails(n.source_refs)}
          </td>
        </tr>)}</tbody></table></div> : <Paragraph>本版未记录额外证据需求，不代表所有诊断条件均已满足。</Paragraph>}
      {conditionalContributions && <details><summary>病理可能结果及其条件性诊断价值</summary>{conditionalContributions}</details>}
    </Section>

    <Section id="clinical-meeting" number="05" title={final ? '团队意见与讨论结果' : '本次讨论议程'}>
      {final && <Paragraph>团队意见：{acceptance[team.acceptance]}。{discussionRounds != null && `已完成 ${discussionRounds} 轮讨论。`}</Paragraph>}
      {final && <details><summary>主持人原始全病例意见</summary><Paragraph>{opinion.diagnosis}</Paragraph></details>}
      {team.disagreements.map(d => <article className="clinical-boundary" key={d.disagreement_id}>
        <Tag color={d.status === 'resolved' ? 'green' : 'gold'}>{({ open: '未解决', resolved: '已解决', bounded: '形成判断边界' })[d.status]}</Tag><Text strong>{d.comparison_target}</Text>
        <Paragraph>{clinicalText(d.resolution || d.explanation)}</Paragraph><Paragraph type="secondary">决策影响：{clinicalText(d.decision_impact)}</Paragraph>
        <details><summary>查看各专科立场及处理依据</summary>{d.positions.map((p, i) => <div key={i}><Text strong>{specialty[p.specialty]}</Text><Paragraph>{p.statement}</Paragraph>{sourceDetails(p.source_refs)}</div>)}<Paragraph>{d.explanation}</Paragraph></details>
      </article>)}
      {team.dissent.length > 0 && <Alert type="warning" showIcon title="保留的重要异议" description={<ul>{team.dissent.map((d, i) => <li key={i}>{d}</li>)}</ul>} />}
      {team.issues.length > 0 ? <><Title level={5}>{final ? '仍保留的开放议题' : '需要讨论解决的问题'}</Title>{team.issues.map((issue, i) => <article className="clinical-agenda-item" key={issue.issue_id}>
        <Title level={5}>{i + 1}. {issue.question}</Title><Paragraph><Text strong>为什么讨论：</Text>{issue.decision_impact}</Paragraph>
        {issue.assignments.map(a => <Paragraph key={a.specialty}><Text strong>{specialty[a.specialty]}回答：</Text>{a.question}</Paragraph>)}
        <Paragraph type="secondary">结束标准：{issue.closure_criterion}</Paragraph>{sourceDetails(issue.source_refs, issue.evidence_refs)}
      </article>)}</> : <Paragraph>{final ? '本版未保留开放讨论议题；判断边界和资料缺口仍按第 03、04 栏保留。' : '本版未保留需要分派的开放议题。'}</Paragraph>}
      {stopReason && <Paragraph className="report-stop-reason"><Text strong>讨论结束原因：</Text>{stopReason}</Paragraph>}
      <details><summary>专科确认、判断采纳及指南记录</summary>{reviews}</details>
      <details><summary>原始问题与讨论处理记录</summary>{questions}</details>
    </Section>
  </div>
}
