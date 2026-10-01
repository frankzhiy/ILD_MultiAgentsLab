从给定 graph unit 抽取全部可独立引用的 clinical propositions，只表达原文明示信息，不补常识、因果或诊断。

按“原文定位→作用范围→命题拆分→属性归属→整体复核”生成：
1. 顺序读取 evidence blocks，先定位原文陈述及 evidence_ids。遇并列省略，先选包含共享否定、时间、来源或谓词的完整并列组为 quote，再拆命题；组内每条复用该 quote，不逐项缩短或重写。非并列选最小充分连续片段。
2. 判断否定、可能性、时间、主体和谓词各自的作用范围。并列项可共享这些词，但不能仅凭逗号扩展范围；转折、新谓词、主体或时间变化需重新判断。同一概念在不同时间的阴阳性陈述分开保留。
3. 从选定证据拆出独立命题，按原文顺序输出。concept_text 与 quote 两者不要求逐字相同；并列结构可补全共享前/后缀，但仅用于概念表达，不要把语义展开或规范化后的 `concept_text` 直接复制为 quote。
   “否认发热、盗汗及咯血”：三条阴性命题共享原句，不引用原文不存在的“否认盗汗”。“超声提示左房、右室增大”：两条所见共享原句。
4. 从证据确定类型、状态、信度和属性；diagnosis_assertion 保留判断来源，不改为患者确定患病。“不详/未见检查单”用 information_availability，不解释为正常或未实施。
5. 提交前复核全部 proposition、modifier、attribution 引用：quote 在所属连续 blocks 合并原文中逐字存在，且实际支持状态和范围。孤立“盗汗”不能证明否认，应带共享否定词。不删、合并独立命题以通过校验。重试按错误类别检查整份输出；quote 错误触发全部引用复核，从原文重选片段，不只修首个报错。

modifier 归属：
- 只输出具体命题的局部属性，填写 modifier_type、value_text 和 evidence。
- 整个事件的时间、起病、触发、场景保留在 graph unit，不输出独立 event modifier；已写入 concept_text 的语义不再作为 modifier。
- 属性 quote 也复制原文，不补共享词或换算单位。

attribution：
- attribution 表示当前 graph unit 原文明示的陈述来源，不是 proposition 的语义主体。
- 当前 evidence blocks 明示患者、医生或报告等陈述来源时，填写其辖内每条命题的 attribution；来源仅在上级 segment 或其他 graph unit 出现时，必须输出 null。
- 症状、暴露和既往史不得仅因其主体是患者而填写 patient attribution。
- 先定位来源原文 quote，再从中复制 actor_text，不分开编写；actor_text 必须逐字包含在 attribution quote 中。
- 非 null 填 attribution_type、"actor_text" 和 evidence；“外院医生考虑甲病或乙病”：两条 possible 命题均填 clinician、actor_text="外院医生"，引用“外院医生考虑”。

证据：
- 只引用给定 evidence_id，不创建 evidence blocks；IDs 属于连续 blocks 且按原文顺序。
- modifier 至少与所属 proposition 共享一个 evidence_id；相同片段重复出现时，按当前事件和时间选择位置。
枚举：{{ clinical_proposition_catalog }}

程序填写 graph_unit_id、primary_frame、命题及属性 ID；rationale、notes、metadata 不输出。

只返回 propositions，字段结构：
{"propositions":[{"proposition_type":"finding","concept_text":"完整命题","status":"present","certainty":"high","attribution":null,"modifiers":[{"modifier_type":"severity","value_text":"原文属性","evidence":{"evidence_ids":["{{ graph_unit_id }}_ev_001"],"quote":"逐字原文"}}],"evidence":{"evidence_ids":["{{ graph_unit_id }}_ev_001"],"quote":"最小充分逐字原文"}}]}

graph_unit_id: {{ graph_unit_id }}
primary_frame: {{ primary_frame }}
evidence blocks（按顺序拼接即完整 graph unit 原文）：
{{ evidence_blocks }}
