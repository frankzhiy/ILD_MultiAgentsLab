你负责把一个 discourse segment 切成 graph units，并在确定边界时选择 primary frame。

graph unit 是一个 clinical event nucleus 的连续原文证据块，不是 finding/node：
- 一次诊疗接触中的检查、结果、判断、治疗、即时反应和转归保持为一个 unit。
- 两次诊疗接触之间，围绕同一症状主线的起病、演变、诱因、阴性症状、自我应对和主观反应保持为一个 unit。
- 时间、诱因、症状属性、患者态度/依从是修饰语，必须并入所修饰事件，不能单独成块。
- 人口学与主诉可分开；一般情况、查体、独立报告、独立医生判断在事件核切换时分开。
- source_type 变化不是切分依据，专科路由变化也不是切分依据。

每个 unit：
- text 必须是当前 segment 的连续逐字子串；按顺序完整覆盖全部非空白字符，不重叠、不遗漏、不改写。
- source_type 取主导叙事角色：demographics, chief_complaint, present_illness,
  past_medical_history, exposure_history, family_history, medication_history,
  general_condition, physical_exam, imaging_findings, laboratory_findings,
  ctd_related_findings, bronchoscopy_findings, pulmonary_function_findings,
  pathology_findings, treatment, clinician_assessment, other。
- mdt_specialty 至少一个，可多选：pulmonology, thoracic_radiology, pathology,
  rheumatology, shared_context。
- thoracic_radiology 只用于胸部 CT/HRCT/CTPA/胸片文字；肺功能、超声心动图、下肢血管超声及其他非胸部影像不属于胸部影像科。
- CTPA 文字结论由影像科解释，相关低氧/肺栓塞临床问题同时属于呼吸科。
- ANA/ENA/肌炎抗体及关节、雷诺、皮疹、肌肉表现属于风湿科；BAL、肺功能、暴露、氧合和呼吸病程属于呼吸科；人口学/主诉可用 shared_context。
- status：present, absent, possible, historical, planned, performed, not_performed, unknown。
- certainty：high, moderate, low, unknown。

primary frame 必填：
{{ primary_frame_catalog }}

primary_frame_rationale 用一句话说明事件核；只有怀疑混入多个事件核时填写 boundary_warning。
graph_unit_id、segment_id、offset、metadata 和 notes 由程序生成，不要输出。

只返回 JSON：
{"graph_units":[{"text":"逐字连续原文","source_type":"present_illness","mdt_specialty":["pulmonology"],"temporal_anchor":null,"clinical_context":"简短上下文","primary_frame":"symptom_episode","primary_frame_rationale":"事件核理由","boundary_warning":null,"status":"present","certainty":"high","rationale":"边界及类型理由"}]}

当前 segment：
- unit_type: {{ unit_type }}
- contained_source_types: {{ contained_source_types }}
- clinical_frame: {{ clinical_frame }}

segment 原文：
{{ segment_text }}
