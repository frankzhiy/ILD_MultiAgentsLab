你负责从一个已确定边界和 primary frame 的 graph unit 中抽取全部可独立引用的 clinical propositions。

规则：
- 只表达原文明示信息；不补医学常识、因果或诊断结论。
- propositions 按证据出现顺序；并列且可独立判断状态的概念分别抽取，共享谓词可在 concept_text 中补全。
- concept_text 是最小但语义完整的命题，quote 是逐字证据，两者不要求逐字相同；不要把语义展开或规范化后的 `concept_text` 直接复制为 quote。
- 阳性、阴性、可能、历史、计划、已执行、未执行、未知必须区分。
- 诊断判断用 diagnosis_assertion，保留原文明示来源；不能改成患者确定患病。
- “不详/未见检查单”用 information_availability，不能解释为正常或未实施。

modifier 归属：
- 修饰整个事件核的时间、起病、触发或场景放 event_modifiers。
- duration、frequency、severity、value、trend、site、dose、route、schedule、response 等局部属性放所属 proposition.modifiers。
- 同一修饰信息只能出现一次；已写入 concept_text 的语义不能再次作为 modifier。

attribution：
- attribution 表示当前 graph unit 原文明示的陈述来源，不是 proposition 的语义主体。
- 只有当前 evidence blocks 明示患者、医生或报告等来源时填写；来源仅在上级 segment 或其他 graph unit 出现时，必须输出 null。
- 症状、暴露和既往史不得仅因其主体是患者而填写 patient attribution。
- actor_text 必须逐字包含在 attribution quote 中。
- attribution 非 null 时结构为：`{"attribution_type":"clinician","actor_text":"逐字主体","evidence":{"evidence_ids":["..."],"quote":"逐字原文"}}`。

证据：
- 只能引用下面程序生成的 evidence_id，不创建或输出 evidence blocks。
- proposition、modifier、attribution 都必须给 evidence_ids 和连续逐字 quote。
- evidence_ids 必须属于连续 blocks 并按原文顺序；modifier 至少与所属 proposition 共享一个 evidence_id。
- quote 是被引用 blocks 合并文本中的连续子串；并列省略可让多个 proposition 共享证据。

枚举：
{{ clinical_proposition_catalog }}

程序生成 graph_unit_id、primary_frame、proposition_id、modifier_id、rationale、notes 和 metadata，不要输出。

只返回 event_modifiers 和 propositions，字段结构：
{"event_modifiers":[{"modifier_type":"time","value_text":"原文值","evidence":{"evidence_ids":["{{ graph_unit_id }}_ev_001"],"quote":"逐字原文"}}],"propositions":[{"proposition_type":"finding","concept_text":"完整命题","status":"present","certainty":"high","attribution":null,"modifiers":[],"evidence":{"evidence_ids":["{{ graph_unit_id }}_ev_001"],"quote":"最小充分逐字原文"}}]}

graph_unit_id: {{ graph_unit_id }}
primary_frame: {{ primary_frame }}
evidence blocks（按顺序拼接即完整 graph unit 原文）：
{{ evidence_blocks }}
