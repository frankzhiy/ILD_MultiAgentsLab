# 提示词用途与运行链路

核对日期：2026-10-01。此文档记录实际加载位置及职责，不作为模型提示词加载。当前 `src/prompts` 共 26 个 Markdown 文件，全部存在运行入口；本次整理没有发现可删除的闲置提示词。输出目录内的历史运行快照是实验记录，不属于闲置源文件。

## 组织原则

专科会前提示词统一为：阶段任务 → 输入与证据 → 分析任务 → 专业边界 → 输出要求 → 运行输入。结构一致，专业内容和阶段产物不同。共用协议管理判断方法、字段表达和协作；阶段文件管理具体工作；程序追加的契约管理编号、引用范围和结构。

临床推理先界定问题，再生成、比较和细分有依据的解释，形成当前倾向，最后表达关键边界。深度依据候选疾病自身的分类关系推进；间质病分类和形态模式只在对应问题中使用。疾病名称、形态、信度和限制分别表达。

主持人的任务包括独立形成候选和裁决，不能只把专科意见取并集；会中回答、复核、版本更新和综合态度记录各有用途。资料整理与临床判断也分别维护。

## 实际提示词如何组成

| 环节 | 系统提示词 | 用户提示词及追加部分 | 调用位置 |
| --- | --- | --- | --- |
| 四科会前各阶段 | 各 agent.py 的 SYSTEM_PROMPT + 共用判断协议 | 配置加载的阶段文件 + 本阶段变量 + 有适用检索结果时的指南规则/片段 + 输出契约 | `src/agents/{pulmonology,rheumatology,thoracic_radiology,pathology}/agent.py` 的 `_generate` |
| 正式意见后的证据标注 | 内嵌“证据标注器”，不重新判断疾病 | 固定 claim × evidence 槽位、方向/功能规则及允许范围 | `src/agents/common/initial_output_validation.py` 的 `assign_specialty_initial_evidence` |
| 主持人首次和每轮重新综合 | `src/agents/mdt_chair/expert.py` 内嵌角色 + 共用协议 | 主持人文件、病例/四科/讨论/指南输入，以及当前正式判断审阅编号 | `MDTChairAgent.integrate` → `src/agents/mdt_chair/expert.py` |
| 会中回答、原提问方复核、正式判断更新、综合态度 | `ROLE_BOUNDARIES` 中的对应专业角色及各调用的任务说明 + 共用协议 | 对应会中文件和该步骤限定输入；回答阶段另有本轮检索指南 | `src/agents/mdt_discussion/specialty_agent.py` |
| 语义资料整理 | 各 extractor/selector 内嵌资料处理角色，不注入临床判断协议 | 四份语义提示词与各自原文输入 | `src/agents/semantic_graphing/` |
| 表达润色和含义核对 | 展示提示词的两个独立部分 | 每条已有临床文本及 path | `src/workbench/presentation.py` |
| 重试或局部结构修复 | 原始任务角色，或局部 JSON 修复角色 | 原任务/当前结果/实际校验错误/允许来源编号；局部修复使用 edits | `src/llm/structured.py` |
| 最终报告 | 无模型调用 | 审定团队快照的确定性投影 | `src/agents/mdt_discussion/final_report.py` |

共用文件：

- `src/prompts/common/mdt_judgment_protocol.md`：通过 `judgment_system_prompt` 注入全部临床判断阶段；内容只管理共用方法，不要求各阶段提前完成其他阶段任务。
- `src/prompts/common/clinical_presentation.md`：按 `## 润色` 和 `## 医学含义与口吻核对` 拆成两个任务，标记参与解析。仍用于专科展示及兼容历史产物。含 team_synthesis 的主持人、讨论和报告直接展示审定原文，不走润色。
- `src/agents/common/prompt_contract.py`：输出契约最后追加；Evidence ID、radiology proposition、partitioned evidence、formal claims 等分支沿用原规则，优先于示例和指南。
- `src/guidelines/runtime.py` 的 PROMPT_RULES：外部规则不作患者事实；只用本轮提供的 chunk/quote unit，引用与字符定位由程序回填。

四科 `from_config` 均要求配置指定全部会前文件。直接构造 agent 时可省略正式意见文件，此时该模板为空；这是已有程序调用兼容路径，不是另一份老旧提示词，本次未改变。

## 四科会前文件清单

每个阶段还接收 output_schema。严格 JSON Schema 模式下，该变量是 API 提供 schema 的说明；其他模式为紧凑 schema。临床规则按各 agent 的 `_RULE_KEYS_BY_STAGE` 投影，正式意见输入不是新的患者证据。

| 科室 / 文件 | 主要输入（除 schema） | 产物与用途 |
| --- | --- | --- |
| `src/prompts/pulmonology/initial_foundation.md` | case_input、clinical_rules | 临床表型、时间线、继发病因线索及可评价状态 |
| `src/prompts/pulmonology/initial_pulmonary_assessment.md` | case_input、clinical_foundation、clinical_rules | 严重度、呼吸检查、条件性 BAL/支气管镜、进展及 PPF |
| `src/prompts/pulmonology/initial_diagnostic_formulation.md` | case_input、clinical_foundation、pulmonary_assessment、clinical_rules | 八问整合、具体首选/鉴别、专科依赖、决策缺口 |
| `src/prompts/pulmonology/initial_reasoning_output.md` | case_input、internal_state、clinical_rules | 唯一正式意见及原子 claims，然后进入证据槽位标注 |
| `src/prompts/rheumatology/initial_case_reconstruction.md` | case_input、clinical_rules | 来源、系统表型、肺外受累及时间关系 |
| `src/prompts/rheumatology/initial_autoimmune_assessment.md` | case_input、case_reconstruction、clinical_rules | 血清学、具体风湿疾病、活动性及风险三个 domain |
| `src/prompts/rheumatology/initial_consult_formulation.md` | case_input、case_reconstruction、autoimmune_assessment、clinical_rules | 风湿疾病与肺部归因分别论证、依赖及缺口 |
| `src/prompts/rheumatology/initial_reasoning_output.md` | case_input、internal_state、clinical_rules | 七域评估归纳为唯一正式意见及原子 claims |
| `src/prompts/thoracic_radiology/initial_case_reconstruction.md` | working_input、clinical_rules | 检查归一、报告所见、可评价性、问题驱动任务计划 |
| `src/prompts/thoracic_radiology/initial_consult_formulation.md` | working_input、case_reconstruction、clinical_rules | active 任务逐项评价、核心影像解释及 MDT 影响 |
| `src/prompts/thoracic_radiology/initial_reasoning_output.md` | working_input、internal_state、clinical_rules | 影像专业正式意见及原子 claims |
| `src/prompts/pathology/initial_specimen_reconstruction.md` | case_input、clinical_rules | 来源/材料状态、取材、技术充分性及代表性 |
| `src/prompts/pathology/initial_morphologic_assessment.md` | case_input、specimen_reconstruction、clinical_rules | 五个形态评估 domain，模式、病因线索及辅助检查 |
| `src/prompts/pathology/initial_consult_formulation.md` | case_input、specimen_reconstruction、morphologic_assessment、clinical_rules | 九项核对、病理模式及依赖；材料缺失时记录边界 |
| `src/prompts/pathology/initial_reasoning_output.md` | case_input、internal_state、clinical_rules | 十域评估归纳为正式意见；无材料时给病例相关条件贡献 |

呼吸、风湿、病理重建阶段接收原工作输入，后续阶段使用已有证据投影；影像重建接收病例定向文字和胸部影像候选，后续使用影像证据投影。具体引用范围仍由原投影、契约及验证器执行，本次未扩大输入。

### 保留的完整专业任务

**呼吸科八域**：clinical_phenotype、secondary_causes、pulmonary_severity、respiratory_tests_and_bronchoscopy、specialist_integration、progression、diagnostic_formulation、decision_relevant_gaps。

保留暴露/抗原、吸烟、药物与放疗、CTD、感染/误吸、家族与遗传线索；明确阴性与未知区分；症状、氧合、血气、FVC、DLCO、肺容量及运动能力；质量和合并症混杂；BAL 的明确目标及安全性可评价性；症状/生理/影像进展分别评价；PPF 的适用对象、纤维化基础、时间窗、所需分量、替代解释及单一配置版本/阈值。疾病、形态、严重度及进展保持独立。

**风湿七域**：source_and_evaluability、autoimmune_phenotype、serologic_assessment、rheumatic_disease_formulation、ild_attribution、activity_and_risk、specialist_integration_and_gaps。

保留关节、皮肤、肌肉、血管、腺体、浆膜、肾脏、血液等系统表型；ANA 类型/核型/滴度、ENA、RF、CCP、肌炎抗体、ANCA；抗体与表型匹配；分类、诊断和肺部因果归属分别判断；明确 CTD、重叠、未分化及 IPAF 的区别；活动性与疾病特异肺部风险分开。自身免疫评估恰好覆盖三个指定 domain。

**影像任务制**：source_reconciliation、targeted_pulmonary_vascular、acute_parenchymal_overlay、ild_phenotype、ild_morphologic_pattern、conditional_ipf_hrct、longitudinal_change、actionable_ancillary_findings、other。任务按病例激活，不机械凑七问。

保留报告文字而非真实影像的输入方式；定向原文与影像证据分开；disposition 和 excluded_candidate_ids 限制；HRCT/CT/CTPA/胸片/未知方式；来源等级、所见/印象/建议/可用性分层；可能同次检查和日期未知处理；形态模式的征象、分布、组合；条件性 IPF 四分类；CTPA 中央型直接征象的否定边界；明确比较后才给纵向结论；临床背景只作相关证据；靶向而非通用扫描需求。

**病理十域**：source_and_material_evaluability、specimen_and_sampling、tissue_compartment_and_architecture、primary_histopathologic_pattern、coexisting_pattern_and_acute_overlay、etiologic_and_alternative_clues、ancillary_studies、pathology_formulation、specialist_integration、decision_relevant_gaps。

保留文字报告来源和 direct_slides_reviewed=false；取材方式/部位/时间/大小/多部位/胸膜与小气道/伪影；技术充分性与代表性分开；区室、空间/时间结构、关键形态、主导/共存/急性叠加；UIP、NSIP、BIP、DAD、PPFE、LIP、OP、RB-ILD、AMP 等模式；条件性组织学 UIP 四分类；BIP 与 HP、UIP 与 IPF 区分；AMP/DIP 术语关系及原文保留；染色、免疫组化、分子/克隆性结果；小标本 OP 的取样限制；既往材料追索及新取材的条件价值，不直接裁决 SLB/TBLC 或风险。

病理特殊分支仍由 `_requires_material_plan` 控制：无材料、提过取材但未附报告、可用性不明时，程序跳过形态模型调用并生成不可评价形态状态。会诊形成 primary_pattern=null，正式意见使用材料计划 schema；conditional_contributions 只分析可能结果如何改变病例决定，不作为已见形态或自动活检建议。

## 主持人和会中清单

| 文件 | 输入 | 用途及独立职责 |
| --- | --- | --- |
| `src/prompts/mdt_chair/expert_synthesis.md` | chair_input、case_evidence、discussion_context、guideline_context、output_schema | 每轮专业审阅、独立候选比较、主次/关联、facets、议题/资料/边界台账、自然临床综合 |
| `src/prompts/mdt_discussion/specialty_response.md` | specialty_label、specialty_initial_output、chair_result、task、clinical_rules、guideline_context、output_schema | 仅回答一个任务的剩余问题，原子 claims 逐项证据关系及有价值的新问题 |
| `src/prompts/mdt_discussion/answer_review.md` | review_context、answer、output_schema | 原提问方复核回答/边界，选择接受、澄清、佐证、不兼容或资料需求转换 |
| `src/prompts/mdt_discussion/judgment_update.md` | specialty_label、active_judgments、round_answers、review_context、output_schema | maintain/supplement/qualify/revise/withdraw/create；维护自身正式版本及触发来源 |
| `src/prompts/mdt_discussion/synthesis_acceptance.md` | specialty、specialty_output、team、output_schema | 记录特定版本的接受、边界接受或异议，不改写诊断 |

主持人保留每个有效判断恰好一次审阅、重要调整原作者回应、真实冲突条件、每项原始问题去向、每项资料需求台账、至少两种获取结果对应决定、limitations 与 judgment_boundary 一一对应，以及跨轮 ID/版本/来源一致性。继续议题须有现有材料可推进的未解决点；预算、程序结束、回答接受与共识不混同。

## 语义资料处理清单

这些文件建立事实与引用结构，不承担临床候选裁决，本次不改动。

| 文件 | 实际调用 | 用途 |
| --- | --- | --- |
| `src/prompts/semantic_graphing/document_classification.md` | DocumentClassifier.classify | 连续原文单元 → discourse segments |
| `src/prompts/semantic_graphing/graph_unit_extraction.md` | SegmentGraphUnitExtractor.extract | segment → 原文事件核 graph units、路由及 frame |
| `src/prompts/semantic_graphing/primary_frame_selection.md` | PrimaryFrameSelector.select_unit，经 select_primary_frames | 已有 frame 时直接沿用；缺少 frame 时按 unit 原文选择，兼容既有产物 |
| `src/prompts/semantic_graphing/clinical_proposition_extraction.md` | ClinicalPropositionExtractor.extract_unit | graph unit → 独立原文明示命题、状态/属性/来源及精确引用 |

## 清理结果与验证边界

保留全部 26 个在用文件；没有另建第二套提示词、动态模板框架或报告再诊断环节。清理的是在用临床提示词内的重复方法、重复边界和历史纠偏措辞，必要证据与专业边界仍保留在协议、阶段任务或原输出契约中。

本次修改 21 个临床提示词文件和主持人一处内嵌角色文案。所有模板变量集合、配置、证据投影、模型 schema、状态写入及运行结构保持不变。验证包含本地测试和文件/模板对照，不运行真实病例，也不向 APIYI 发送数据；因此不将本地校验解释为诊断质量已获病例验证。
