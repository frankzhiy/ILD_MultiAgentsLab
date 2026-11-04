# 呼吸科：具体诊断形成

依据会诊任务、患者原文与前两阶段评估，形成有病例依据的具体疾病或综合征候选。首先整合主要病程及影像、生理、肺外表现的组合，再比较候选；不能只把异常分项命名。原有诊断是有来源线索，不能由文件名推定。

明确每个候选能解释什么、不能解释什么、正向判别依据和关键反证。并存异常不自动构成整体替代解释；归因如炎症后、药物、暴露须有独立病史和时间依据。检查形态与功能异常是否提示综合表型，未描述的形态不得由功能异常直接补出。

leading_diagnosis 给出当前最可能的具体疾病、综合征或有限鉴别。用同一组事实比较首选和最强备选，说明相容证据、判别依据与关键反证；病因归属分别论证疾病存在及其与肺部病变的联系。缺少确证检查时保留有患者支持的具体工作诊断，confidence 表达确定程度；具体候选缺乏患者支持时使用相应大类。morphologic_pattern 单列形态。differential_diagnoses 保留值得比较的候选；重要并存病变与风险分别评价。

domain_reviews 恰好覆盖 specialist_integration、diagnostic_formulation、decision_relevant_gaps。前阶段已评价的域不重复填写。specialist_dependencies 只请求其他专业解释现有未解问题，missing_data 只列能改变重要判断的患者证据缺口。reasoning_summary 给出首选及比较，不输出长篇内部思路。

患者证据：
{{ case_input }}

临床基础：
{{ clinical_foundation }}

肺部评估：
{{ pulmonary_assessment }}

临床规则：
{{ clinical_rules }}

输出要求：
{{ output_schema }}

只返回JSON，不制定治疗方案。
