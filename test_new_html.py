"""测试新的HTML报告生成"""
from pathlib import Path
import json
from src.reporting.html_report import render_report
from src.schemas.semantic_graphing import DocumentGraphUnits, DocumentFrameTriage, DocumentClassification

# 加载已有数据
run_dir = Path('outputs/runs/20260605_184303_03_step2_step3_graph_units')
raw_text = (run_dir / 'input.txt').read_text(encoding='utf-8')
timing = json.loads((run_dir / 'timing.json').read_text())

# 加载discourse segments
seg_data = json.loads((run_dir / 'discourse_segments.json').read_text())

# 构造一个简单的result对象
class FakeResult:
    def __init__(self, case_id, seg_data):
        self.case_id = case_id
        self.classification = DocumentClassification.model_validate(seg_data)

result = FakeResult('03_test_new_html', seg_data)

# 加载graph units
gu_data = json.loads((run_dir / 'graph_units.json').read_text())
graph_units = DocumentGraphUnits.model_validate(gu_data)

# 加载frame triage
ft_data = json.loads((run_dir / 'frame_triage.json').read_text())
frame_triage = DocumentFrameTriage.model_validate(ft_data)

# 生成HTML
output = render_report(
    result=result,
    graph_units=graph_units,
    source_filename='03.txt',
    raw_text=raw_text,
    timing=timing,
    output_path=run_dir / 'report_new.html',
    frame_triage=frame_triage
)
print(f'✅ 新HTML报告已生成: {output}')
print(f'📂 文件大小: {output.stat().st_size} 字节')
