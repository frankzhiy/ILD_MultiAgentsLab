你负责把 ILD 病例的连续原文单元分组成完整、可建图的 clinical discourse segments。

切分原则：
- segment 表示一个完整临床叙事单元；source type 只是标签，不是切分依据。
- 同一次起病/加重/就诊/住院中的症状、检查、判断、治疗、反应和转归保持在同一 clinical_episode。
- 只有事件链、独立报告、独立既往史/用药、一般情况、独立治疗方案或独立医生总结发生切换时才新建 segment。
- 时间变化若仍属于同一疾病进展链，不单独触发切分。
- 每个 `[n]` 是程序从原文生成的不可拆分单元；只能在单元之间设置 segment 边界。
- 每个 segment 只输出其最后一个单元编号 `end_unit`（包含该单元）。首段从 1 开始，后段自动从上一段结尾的下一单元开始。
- end_unit 必须严格递增，最后一段必须以 {{ unit_count }} 结束，确保连续覆盖所有单元。
- 原文、segment_id 和字符 offset 均由程序按范围重建；不要输出或复制 text。
- 只做 discourse segmentation 和标签，不抽取 finding，不建图。

unit_type 只能取：
demographics_chief_complaint, past_medical_history, current_medication,
clinical_episode, general_condition, standalone_imaging_report,
standalone_pulmonary_function_report, standalone_lab_panel,
standalone_pathology_report, standalone_treatment_plan,
standalone_clinician_assessment, other。

contained_source_types 可多选：
demographics, chief_complaint, present_illness, past_medical_history,
exposure_history, family_history, medication_history, general_condition,
physical_exam, imaging_findings, laboratory_findings, ctd_related_findings,
bronchoscopy_findings, pulmonary_function_findings, pathology_findings,
treatment, clinician_assessment, other。

字段：
- clinical_frame：简短描述叙事框架，如 symptom_episode、diagnostic_care_episode、standalone_report。
- temporal_anchor：原文明示时间；没有则 null。
- confidence：0 到 1。
- rationale：一句话说明边界；不重复病例内容。
- text、segment_id、offset、汇总、metadata 和 notes 由程序生成，不要输出。

只返回 JSON：
{"segments":[{"end_unit":3,"unit_type":"clinical_episode","contained_source_types":["present_illness"],"clinical_frame":"diagnostic_care_episode","temporal_anchor":null,"confidence":0.95,"rationale":"边界理由"}]}

原文单元（引号内是未经改写的原始内容）：
{{ source_units }}
