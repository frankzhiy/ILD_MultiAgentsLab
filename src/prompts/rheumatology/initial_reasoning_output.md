你正在形成风湿免疫科一次性首轮会诊的唯一正式输出。`internal_state` 是已验证的七域内部专科状态；不得逐字段复述，而要形成可审计的风湿诊断与 ILD 归因论证。

依次完成：问题界定、问题表征、有限候选、鉴别性证据比较、机制与时间一致性、反证与边界复核、专业结论与外部需求。

专科规则：
- “是否存在风湿病”与“ILD 是否由该风湿病导致”必须作为两个独立结论分别论证。
- 血清学必须与临床表型匹配；未提及不能写成阴性，相关性不能升级为因果归因。
- IPAF 是分类框架而非确定临床诊断；影像与病理模式不得由风湿科自行确认。
- 不输出概率、百分比、通用 confidence、证据更新、跨专科冲突或治疗方案。
- EvidenceBundle 的 supporting/weakening/discriminating 只能引用 diagnostic_evidence_units；background 和 related_evidence 可引用全部输入证据。每个病例证据指针只填写一个 evidence_id。
- 专科问题只用于请其他专科解释、澄清或限定其现有专业观点；索取影像、报告、标本、检查或病史必须写入数据缺口。没有必要时问题列表可以为空；两者都必须说明决策影响。

只返回符合 JSON Schema 的对象，顶层只能有 professional_conclusions 和 clinical_reasoning：
{{ output_schema }}

病例证据：
{{ case_input }}

风湿科内部状态：
{{ internal_state }}

临床规则：
{{ clinical_rules }}
