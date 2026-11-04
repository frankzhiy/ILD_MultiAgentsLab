"""Referral instructions are separate from patient evidence."""

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

ConsultationTemplate = Literal["refine_diagnosis", "assess_ild", "review_diagnosis", "unspecified"]

TEMPLATES = {
    "refine_diagnosis": ("进一步明确诊断", "患者已基本按ILD转诊；比较具体疾病、综合征和病因，形成证据支持的具体诊断或有限鉴别。有明确反证时复核ILD存在性。"),
    "assess_ild": ("判断是否属于ILD", "先比较ILD与其他肺病解释；若支持ILD，继续推进具体疾病、综合征和病因。"),
    "review_diagnosis": ("复核已有诊断", "提取原文明示的具体诊断，比较维持、修正或替代的依据。未记录具体诊断时明确说明，不能从病例文件名补出。"),
    "unspecified": ("会诊背景未指定", "依据原文明示的转诊问题及患者证据形成具体诊断或有限鉴别；不能预设患者已确诊ILD。"),
}


class ConsultationContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    template: ConsultationTemplate = "unspecified"
    question: str = Field(default="", max_length=1000)

    def prompt_context(self) -> dict:
        label, task = TEMPLATES[self.template]
        return {"template": self.template, "label": label, "task": task,
                "additional_question": self.question,
                "target": "本系统面向间质性肺疾病（ILD）诊断。充分利用现有输入形成最支持的ILD相关诊断及必要的有限鉴别，优先推进具体疾病、综合征和病因；确定程度单独表达。整体替代解释须有患者依据并覆盖主要间质病变；近期恶化和重要并存病变分别评价其原因与风险。",
                "evidence_boundary": "此处是转诊背景和任务指令，不增加患者事实。现有输入就是本次任务的全部患者资料；允许基于事实作诊断推断，不得补造征象、病史或检查结果，不以资料不足或等待补充检查代替本次结论。"}


def consultation_prompt(context: ConsultationContext) -> str:
    import json
    return "会诊任务（与患者证据分开）：\n" + json.dumps(context.prompt_context(), ensure_ascii=False)
