import { confidenceLabels as confidence, candidatePositionLabels as position, acceptanceLabels, diagnosticDimensionLabels as dimensions, candidateName, problemRelationshipLabels as relation, evidenceNeedStatusLabels as evidenceStatus, evidenceNeedActionLabels as evidenceAction } from './clinicalConcepts'
import { isCurrentTeam } from './resultContract'
import { DiagnosticResolution } from './Consultation'
import { ClinicalReport } from './ClinicalReport'
import { useEffect, useRef, useState } from 'react'
import { Alert, Button, Card, Empty, Space, Tabs, Tag, Typography } from 'antd'
import { assessabilityLabels, JudgmentDimensions, FacetJudgment } from './JudgmentDimensions'
import { aggregateCitations, CitationGroup } from './Citation'

const { Title, Paragraph, Text } = Typography
const adoption = { adopt: '采纳', qualify: '限定后采纳', defer: '暂缓采纳', reject: '不采纳' }
const specialty = { pulmonology: '呼吸科', thoracic_radiology: '影像科', rheumatology: '风湿免疫科', pathology: '病理科' }
const disposition = { covered: '已有专科意见覆盖', continue: '继续讨论', answered: '已有回答', bounded: '形成有限判断', deferred: '保留未决', merged: '合并处理' }

const evidenceRoles = { supporting: '支持证据', weakening: '反对证据', discriminating: '鉴别证据', qualifying: '限定依据', background: '背景证据' }

function Timeframe({ value }) {
  return <Text type="secondary">时间：{value?.description || '时间未明确'}{(value?.start || value?.end) && `（${value.start || '未明确'} — ${value.end || '未明确'}）`}</Text>
}

function Guidelines({ items = [] }) {
  return <><CitationGroup refs={items} />{items.map((g, i) => <section key={i}>
    <Text strong>{g.title || g.source_file || g.guideline_id}{g.page != null && ` · 第 ${g.page} 页`}</Text>
    <Paragraph>{g.quote || g.text}</Paragraph><Paragraph>{g.application || g.relevance}</Paragraph>
  </section>)}</>
}

function SourceEvidence({ items }) {
  const citations = aggregateCitations(items)
  return <><CitationGroup caseEvidence={citations} />{citations.map((item, index) => <blockquote key={`${item.evidence_ref || item.graph_unit_id || index}-${index}`}>
    <Space wrap><Text type="secondary">{item.graph_unit_id || item.evidence_ref}</Text>
      {[...new Set((item.relations || []).map(r => r.relation))].map(role => <Tag key={role}>{evidenceRoles[role] || role}</Tag>)}
    </Space><Paragraph>{item.quote || '原文未保留'}</Paragraph>
  </blockquote>)}</>
}

function Sources({ team, refs = [], evidence = [] }) {
  const citations = ids => ids.map(ref => team.evidence_catalog?.[ref] || { evidence_ref: ref, quote: `引用未找到：${ref}` })
  const evidenceView = items => items.length > 0 && <SourceEvidence items={items} />
  return <div className="synthesis-sources">
    {refs.map(ref => {
      const source = team.source_catalog?.[ref]
      if (!source) return <Paragraph key={ref}>来源未保留：{ref}</Paragraph>
      return <article className="synthesis-note" key={ref}>
        <CitationGroup sourceCitations={[source]} />
        <Paragraph>{source.quote}</Paragraph>
        <JudgmentDimensions item={source} />
        {(source.version_id || team.dependency_versions?.[ref]) && <Paragraph>正式判断版本：{source.version_id || team.dependency_versions[ref]}</Paragraph>}
        {source.conditions && <><Paragraph>判断对象：{source.conditions.subject}</Paragraph><Timeframe value={source.conditions.timeframe} />
          <Paragraph>成立前提：{source.conditions.applicability_conditions?.join('；') || '未附加前提'}</Paragraph>
          <Paragraph>证据边界：{source.conditions.evidence_scope?.scope_limitations?.join('；') || '未附加限制'}</Paragraph></>}
        {source.claims?.map(claim => <section key={claim.claim_id}>
          <Text code>{claim.claim_id}</Text><Paragraph>{claim.statement}</Paragraph>
          {evidenceView((claim.evidence_relations || []).map(item => ({ ...(team.evidence_catalog?.[item.evidence_ref] || { quote: `引用未找到：${item.evidence_ref}` }), evidence_ref: item.evidence_ref, claim_statement: claim.statement,
            relations: [{ relation: item.direction === 'weakens' ? 'weakening' : item.direction === 'supports' ? 'supporting' : item.function, target_claim_id: claim.claim_id },
              item.direction !== 'neutral' && item.function !== 'foundational' && { relation: item.function, target_claim_id: claim.claim_id }].filter(Boolean),
          })))}
        </section>)}
        {!source.claims?.length && evidenceView(Object.entries(source.evidence_relations || {}).flatMap(([role, ids]) => citations(ids).map(item => ({ ...item, relations: [{ relation: role }] }))))}
        <Guidelines items={source.guideline_evidence} />
      </article>
    })}
    {evidenceView(citations(evidence))}
  </div>
}

export function ConditionalContributions({ items = [] }) {
  if (!items.length) return null
  return <section className="conditional-contributions"><Title level={4}>病理的条件性贡献</Title>
    <Text type="secondary">以下为不同可能结果的诊断意义，不代表患者已出现这些结果，也不自动构成取材建议。</Text>
    {items.map((item, index) => <article className="synthesis-note" key={index}>
      <Text strong>{item.question}</Text><Paragraph>若出现：{item.possible_result}</Paragraph>
      <Paragraph>{item.decision_if_found}</Paragraph><Paragraph type="secondary">仍不能确定：{item.cannot_establish}</Paragraph>
      <Paragraph>获取价值：{item.acquisition_value}</Paragraph>
    </article>)}
  </section>
}

export function TeamSynthesis({ team, final = false, stopReason, discussionRounds, stage, diagnosisConclusion }) {
  const [activeTab, setActiveTab] = useState('clinical')
  const root = useRef(null)
  useEffect(() => {
    let folded = null
    const expand = () => {
      if (folded !== null) return
      folded = [...(root.current?.querySelectorAll('details:not([open])') || [])]
      folded.forEach(item => { item.open = true })
    }
    const restore = () => { folded?.forEach(item => { item.open = false }); folded = null }
    window.addEventListener('beforeprint', expand)
    window.addEventListener('afterprint', restore)
    return () => { window.removeEventListener('beforeprint', expand); window.removeEventListener('afterprint', restore) }
  }, [])
  if (!team) return null
  if (!isCurrentTeam(team)) return <Alert type="warning" title="请按新结构重新运行本阶段。" />
  const opinion = team.diagnostic_opinion
  const clinical = <div className="synthesis-layout">
    <nav className="synthesis-nav" aria-label="临床问题导航">
      <Text className="eyebrow">临床问题</Text>
      {team.problems.map((p, index) => <a key={p.problem_id} href={`#problem-${p.problem_id}`}><span>{String(index + 1).padStart(2, '0')}</span>{p.title}</a>)}
    </nav>
    <div className="synthesis-problems">{[
      ['ILD 主要诊断问题', team.problems.filter(p => p.relationship === 'primary')],
      ['急性叠加、伴随问题及其他发现（不计入 ILD 主诊断鉴别）', team.problems.filter(p => p.relationship !== 'primary')],
    ].filter(([, problems]) => problems.length).map(([label, problems]) => <section key={label} aria-label={label}>
      <Title level={4}>{label}</Title>{problems.map(p => <article id={`problem-${p.problem_id}`} key={p.problem_id} className={`synthesis-problem ${p.relationship === 'primary' ? 'is-primary' : ''}`}>
      <Space wrap><Tag color={p.relationship === 'primary' ? 'blue' : 'default'}>{relation[p.relationship]}</Tag><Tag>{confidence[p.confidence]}</Tag><Tag>{assessabilityLabels[p.assessability] || '可评价性未记录'}</Tag></Space>
      <Title level={4}>{p.title}</Title><Timeframe value={p.timeframe} /><Paragraph className="synthesis-statement">{p.statement}</Paragraph><Paragraph>{p.rationale}</Paragraph>
      {p.limitations.length > 0 && <div className="synthesis-limits"><Text strong>关键限制</Text><ul>{p.limitations.map((text, i) => <li key={i}>{text}</li>)}</ul></div>}
      {(p.relationship === 'primary' ? p.candidates.length > 0 : p.candidates.length > 1) && <section className="synthesis-candidates"><Title level={5}>{p.relationship === 'primary' ? 'ILD 主诊断比较' : '该伴随问题的鉴别解释'}</Title>
        {p.candidates.map((candidate, i) => <div className="synthesis-candidate" key={i}>
          <div className="candidate-heading"><Text strong>{candidateName(team, candidate)}</Text><Space wrap><Tag>{position[candidate.position]}</Tag><Tag>{confidence[candidate.confidence]}</Tag></Space></div>
          <Timeframe value={candidate.timeframe} /><Paragraph>{candidate.rationale}</Paragraph>
          {candidate.limitations.length > 0 && <Paragraph type="secondary">{candidate.limitations.join('；')}</Paragraph>}
          <details><summary>查看此候选的依据与反证</summary><Sources team={team} refs={candidate.source_refs} evidence={candidate.supporting_evidence_refs} />
            {candidate.opposing_evidence_refs.length > 0 && <><Text strong>反对证据</Text><Sources team={team} evidence={candidate.opposing_evidence_refs} /></>}
          </details>
        </div>)}
      </section>}
      <details><summary>展开原始证据与专科来源</summary><Sources team={team} refs={p.source_refs} evidence={p.evidence_refs} /></details>
    </article>)}</section>)}
      {team.diagnostic_facets?.length > 0 && <details className="synthesis-problem"><summary>按诊断层级核对</summary>
        {team.diagnostic_facets.map(f => <article className="synthesis-note" key={`${f.problem_id}-${f.dimension}-${JSON.stringify(f.timeframe)}-${f.subject ?? ''}`}><Text strong>{team.problems.find(p => p.problem_id === f.problem_id)?.title} · {dimensions[f.dimension]}</Text><FacetJudgment item={f} /><Timeframe value={f.timeframe} /><Paragraph>{f.statement}</Paragraph><Paragraph type="secondary">{f.rationale}</Paragraph>{f.limitations.length > 0 && <Paragraph>{f.limitations.join('；')}</Paragraph>}<Sources team={team} refs={f.source_refs} /></article>)}
      </details>}
    </div>
  </div>
  const reviews = <div className="synthesis-reviews"><Paragraph>{team.coverage_review}</Paragraph><Paragraph>{team.synthesis_rationale}</Paragraph>
    {team.judgment_reviews.map(review => <details key={review.source_ref}>
      <summary><Tag color={review.disposition === 'adopt' ? 'green' : 'gold'}>{adoption[review.disposition]}</Tag>{specialty[team.source_catalog?.[review.source_ref]?.specialty] || '专科判断'} · {review.source_ref}</summary>
      <Paragraph>{review.rationale}</Paragraph>{review.required_response && <Tag color="orange">需要原专科回应</Tag>}<Sources team={team} refs={[review.source_ref]} evidence={review.evidence_refs} />
    </details>)}
    {team.guideline_evidence?.length > 0 && <details><summary>展开所用指南</summary><Guidelines items={team.guideline_evidence} /></details>}
    <section><Title level={4}>各专科对本版综合的明确态度</Title><Paragraph type="secondary">以下是专科 Agent 的版本确认记录。</Paragraph>
      {(team.acceptance_records || []).map(r => <article className="synthesis-note" key={r.specialty}>
        <Text strong>{specialty[r.specialty]} · 第 {r.revision} 版 · {({ accept: '接受', accept_with_boundaries: '带边界接受', dissent: '保留异议' })[r.decision]}</Text>
        <Paragraph>{r.rationale}</Paragraph>{r.boundaries?.length > 0 && <Paragraph>接受边界：{r.boundaries.join('；')}</Paragraph>}
      </article>)}
      {!team.acceptance_records?.length && <Paragraph>尚无专科明确确认记录。</Paragraph>}
    </section>
    {team.dissent.length > 0 && <Alert type="warning" showIcon title="保留的重要异议" description={<ul>{team.dissent.map((d, i) => <li key={i}>{d}</li>)}</ul>} />}
  </div>
  const questions = <div>
    <Title level={4}>专科原始问题及处理结果</Title>
    {Object.entries(team.source_catalog || {}).filter(([, s]) => s.source_type === 'interspecialty_question').map(([ref, source]) => {
      const handled = team.issue_dispositions.find(d => d.issue_id === ref)
      return <article className="synthesis-note" key={ref}>
        <Text type="secondary">{specialty[source.specialty]} → {specialty[source.target_specialty] || '目标专科'} · {ref}</Text>
        <Paragraph strong>{source.quote}</Paragraph>
        <Tag>{handled ? disposition[handled.outcome] : '尚未说明去向'}</Tag>
        <Paragraph>{handled?.rationale}</Paragraph>
        <Sources team={team} refs={[...new Set([...(handled?.source_refs || []), ...Object.entries(team.source_catalog).filter(([, source]) => source.answer_id && handled?.response_refs?.includes(source.answer_id)).map(([ref]) => ref)])]} />
        {handled?.response_refs?.length > 0 && <Paragraph>对应会中答复：{handled.response_refs.join('、')}</Paragraph>}
        {handled?.linked_issue_ids?.map(id => team.issues.some(i => i.issue_id === id) ? <a href={`#issue-${id}`} key={id}>查看讨论议题 {id} </a> : <Tag key={id}>已处理议题 {id}</Tag>)}
      </article>
    })}
    <Title level={4}>待回答的讨论议题</Title>
    {!team.issues.length && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="本轮没有需要分派的开放议题" />}
    {team.issues.map(i => <article className="synthesis-note" id={`issue-${i.issue_id}`} key={i.issue_id}>
      <Tag>{i.origin === 'chair' ? '主持人发起澄清' : '专科发起'}</Tag>
      <Text type="secondary">发起方：{i.origin === 'chair' ? '主持人' : i.raised_by.map(s => specialty[s]).join('、')} · {i.issue_id}</Text>
      <Paragraph strong>{i.question}</Paragraph><Paragraph>{i.decision_impact}</Paragraph>
      {i.assignments.map(a => <Paragraph key={a.specialty}><Text strong>{specialty[a.specialty]}回答：</Text>{a.question}</Paragraph>)}
      <Paragraph type="secondary">结束标准：{i.closure_criterion}</Paragraph><Sources team={team} refs={i.source_refs} evidence={i.evidence_refs} />
    </article>)}
    <details><summary>查看历轮议题处理记录</summary>{team.issue_dispositions.filter(d => team.source_catalog?.[d.issue_id]?.source_type !== 'interspecialty_question').map(d => <article className="synthesis-note" key={d.issue_id}><Tag>{disposition[d.outcome]}</Tag><Text>{d.issue_id}</Text><Paragraph>{d.rationale}</Paragraph><Sources team={team} refs={d.source_refs} />{d.response_refs?.length > 0 && <Paragraph>对应答复：{d.response_refs.join("、")}</Paragraph>}{d.linked_issue_ids?.length > 0 && <Paragraph>关联议题：{d.linked_issue_ids.join("、")}</Paragraph>}</article>)}</details>
  </div>
  const boundaries = <div>{team.judgment_boundaries.length ? team.judgment_boundaries.map(b => <article className="synthesis-note" key={b.boundary_id}>
    <Title level={4}>{team.problems.find(p => p.problem_id === b.problem_id)?.title}</Title>
    <Paragraph><Text strong>当前能判断：</Text>{b.established}</Paragraph>
    <Paragraph><Text strong>尚不能判断：</Text>{b.undetermined}</Paragraph>
    <Paragraph><Text strong>原因：</Text>{b.reason}</Paragraph><Paragraph><Text strong>决策影响：</Text>{b.decision_impact}</Paragraph>
    <Sources team={team} refs={b.source_refs} />
  </article>) : <Empty description="本轮未识别到需要单列的判断边界" />}</div>
  const disagreements = <div>{team.disagreements.length ? team.disagreements.map(d => <article className="synthesis-note" key={d.disagreement_id}>
    <Tag color={d.kind === 'incompatible_judgments' ? 'red' : 'gold'}>{d.kind === 'incompatible_judgments' ? '真正不兼容的判断' : '待澄清差异'}</Tag>
    <Tag>{({ open: '未解决', resolved: '已解决', bounded: '形成判断边界' })[d.status]}</Tag><Title level={4}>{d.comparison_target}</Title>
    {d.positions.map((p, index) => <div key={index}><Text strong>{specialty[p.specialty]}：</Text><Paragraph>{p.statement}</Paragraph><Sources team={team} refs={p.source_refs} /></div>)}
    <Paragraph>{d.explanation}</Paragraph><Paragraph>决策影响：{d.decision_impact}</Paragraph><Paragraph>{d.resolution}</Paragraph>
    {d.linked_issue_ids.map(id => <Button type="link" onClick={() => setActiveTab('questions')} key={id}>查看讨论议程 · {id}</Button>)}
  </article>) : <Empty description="本轮未发现专科分歧；共同资料不足不构成冲突" />}</div>
  const next = <div className="synthesis-next">
    {!team.evidence_needs.length && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="本轮没有资料需求" />}
    {team.evidence_needs.map(need => <article className="synthesis-note" key={need.need_id}>
      <Text type="secondary">{need.need_id} · 关联问题：{team.problems.find(p => p.problem_id === need.problem_id)?.title || need.problem_id}</Text><Title level={4}>{need.information}</Title><Tag>{evidenceStatus[need.status]}</Tag><Tag>{evidenceAction[need.action]}</Tag>
      <Paragraph><Text strong>当前已有：</Text>{need.available_information || '暂无'}</Paragraph>
      <Paragraph><Text strong>仍然缺少：</Text>{need.missing_information || '无'}</Paragraph>
      <ul>{need.branches.map((b, j) => <li key={j}><Text strong>{b.result}</Text> → {b.changed_decision}</li>)}</ul>
      <Paragraph>获取价值与负担：{need.feasibility_and_burden}</Paragraph><Paragraph>不可得时的判断：{need.if_unavailable}</Paragraph>
      <Sources team={team} refs={need.source_refs} />
    </article>)}<ConditionalContributions items={team.conditional_contributions} />
  </div>
  if (final || stage === 'premeeting') return <Card className="team-synthesis clinical-report-card" ref={root}>
    <ClinicalReport team={team} final={final} stopReason={stopReason} discussionRounds={discussionRounds} diagnosisConclusion={diagnosisConclusion}
      renderSources={(refs, evidence) => <Sources team={team} refs={refs} evidence={evidence} />}
      reviews={reviews} questions={questions}
      conditionalContributions={team.conditional_contributions?.length > 0 && <ConditionalContributions items={team.conditional_contributions} />} />
  </Card>
  return <Card className="team-synthesis" ref={root}>
    <header className="synthesis-header"><div><Text className="eyebrow">MDT 综合意见</Text><Title level={3}>{team.diagnostic_resolution.primary_diagnosis}</Title></div>
      <Space wrap><Tag color={team.acceptance === 'explicit_dissent' ? 'orange' : 'blue'}>{acceptanceLabels[team.acceptance] || '尚未记录接受情况'}</Tag><Tag>第 {team.revision} 版</Tag></Space>
    </header>
    <DiagnosticResolution value={team.diagnostic_resolution} exploration={team.diagnostic_exploration} renderEvidence={refs => <Sources team={team} evidence={refs} />} />
    <section className="synthesis-problem is-primary" aria-label="主持人全病例诊断意见">
      <Title level={4}>主持人全病例诊断意见</Title>
      <Paragraph><Text strong>病例整体认识：</Text>{opinion.case_formulation}</Paragraph>
      <Paragraph className="synthesis-statement">{opinion.diagnosis}</Paragraph>
      <Title level={5}>综合诊断依据</Title><Paragraph>{opinion.reasoning}</Paragraph>
      <Title level={5}>主要鉴别及取舍</Title><Paragraph>{opinion.differential_reasoning}</Paragraph>
      {opinion.limitations?.length > 0 && <Paragraph><Text strong>关键未决：</Text>{opinion.limitations.join('；')}</Paragraph>}
      <details><summary>查看整体诊断的证据与保留的专科判断</summary><Sources team={team} refs={opinion.source_refs} evidence={opinion.evidence_refs} /></details>
    </section>
    {team.stop_kind === 'budget' && <Alert type="warning" showIcon title="已达到讨论轮数上限，未决事项保留" />}
    {team.stop_kind === 'no_progress' && <Alert type="warning" showIcon title="本轮已无可推进的讨论路径，未决事项保留" />}
    {stopReason && <Paragraph className="report-stop-reason"><Text strong>结束原因：</Text>{stopReason}</Paragraph>}
    {discussionRounds != null && <Paragraph>已完成讨论：{discussionRounds} 轮</Paragraph>}
    <Tabs activeKey={activeTab} onChange={setActiveTab} className="synthesis-tabs" items={[{ key: 'clinical', label: '临床结论', children: clinical }, { key: 'boundaries', label: '本轮判断边界', children: boundaries }, { key: 'disagreements', label: '专科分歧与待核实矛盾', children: disagreements }, { key: 'questions', label: '原始问题与讨论议程', children: questions }, { key: 'next', label: '证据需求及满足状态', children: next }, { key: 'review', label: '判断采纳与依据', children: reviews }].map(item => ({ ...item, forceRender: true, children: <section><Title className="print-section-title" level={4}>{item.label}</Title>{item.children}</section> }))} />
  </Card>
}
