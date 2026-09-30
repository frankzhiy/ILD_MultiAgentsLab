"""Small, invented text cases shared by input and agent checks; no local run dependency."""
from copy import deepcopy
from pathlib import Path

from src.agents.common.specialty_input import _ALLOWED_USES, _FILE_MODELS
from src.schemas.specialty_agent_input import EvidenceRole, SpecialtyCaseInput


def case_input(specialty='pulmonology', *, vascular=False):
    from test_pathology_agent import case_input as pathology_case
    case = pathology_case().model_dump(mode='json')
    template = case['segments'][0]['units'][0]
    entries = [
        ('seg_001_gu_001', 'demographics', ['shared_context'], [('prop_001', '成年患者', 'finding')]),
        ('seg_002_gu_001', 'present_illness', ['pulmonology', 'rheumatology'], [('prop_001', '慢性咳嗽近期发热加重，ANA阳性待核实', 'finding')]),
        ('seg_003_gu_003', 'present_illness', ['pulmonology', 'thoracic_radiology'], [
            ('prop_006', '胸部CT示双肺间质增粗纹理走形杂乱', 'finding'),
            ('prop_008', '右中下叶条片状实性高密度影', 'finding'),
            ('prop_009', '左舌段条片状实性高密度影', 'finding'),
            ('prop_010', 'CT报告考虑肺部感染', 'diagnosis_assertion'),
            ('prop_011', '给予抗感染治疗', 'treatment'),
            ('prop_012', '治疗后症状无明显改善', 'outcome'),
            ('prop_013', '入院诊断待分类间质性肺病', 'diagnosis_assertion')]),
        ('seg_004_gu_001', 'imaging_findings', ['thoracic_radiology'], [
            ('prop_001', '胸部CT报告：双肺间质异常', 'finding'),
            ('prop_002', '右中下叶感染性病灶', 'finding'),
            ('prop_003', '左舌段局限性不张', 'finding')]),
        ('seg_005_gu_001', 'exposure_history', ['pulmonology'], [('prop_001', '无明确粉尘暴露记录', 'finding')]),
    ]
    if vascular:
        entries[2:4] = [
            ('seg_003_gu_001', 'imaging_findings', ['thoracic_radiology'], [('prop_001', '超声心动图：右房扩大，肺功能下降，下肢超声未见血栓', 'finding')]),
            ('seg_004_gu_003', 'imaging_findings', ['thoracic_radiology'], [('prop_001', 'CTPA未见明确中央型肺栓塞直接征象', 'finding'), ('prop_003', '双肺间质纤维化', 'finding'), ('prop_004', '肺气肿', 'finding')]),
        ]
    segments=[]
    for idx, (unit_id, source_type, targets, props) in enumerate(entries, 1):
        segment_id=unit_id.rsplit('_gu_', 1)[0]
        blocks=[dict(evidence_id=f'{unit_id}_ev_{n:03d}', text=text) for n, (_,text,_) in enumerate(props,1)]
        text='；'.join(b['text'] for b in blocks)
        unit=deepcopy(template)
        unit['graph_unit'].update(graph_unit_id=unit_id, segment_id=segment_id, text=text,
            source_type=source_type, mdt_specialty=targets, end_char=len(text), segment_end_char=len(text))
        for field in ('primary_frame','clinical_propositions','proposition_validation','local_graph'):
            unit[field]['graph_unit_id']=unit_id
        unit['clinical_propositions']['evidence_blocks']=blocks
        unit['clinical_propositions']['propositions']=[dict(proposition_id=prop_id, proposition_type=kind,
            concept_text=label, status='present', certainty='high', rationale='合成报告的明确文字',
            evidence=dict(evidence_ids=[block['evidence_id']], quote=label)) for (prop_id,label,kind),block in zip(props,blocks)]
        unit['local_graph'].update(segment_id=segment_id, evidence_blocks=blocks,
            nodes=[dict(node_id=f'{unit_id}::{prop_id}', node_type='proposition', semantic_type='finding',
                label=label, status='present', certainty='high', evidence={'evidence_ids':[block['evidence_id']], 'quote':label})
                for (prop_id,label,_),block in zip(props,blocks)])
        role=EvidenceRole.SHARED_CONTEXT if 'shared_context' in targets else EvidenceRole.OWNED if specialty in targets else EvidenceRole.REFERENCE_ONLY
        unit.update(segment_index=idx, unit_index=1, evidence_role=role, may_support_diagnostic_claim=True, allowed_uses=_ALLOWED_USES[role])
        segment=deepcopy(case['segments'][0]['segment'])
        segment.update(segment_id=segment_id, text=text, contained_source_types=[source_type], end_char=len(text))
        segments.append(dict(segment_index=idx, segment=segment, units=[unit]))
    case.update(case_id='synthetic-case', target_specialty=specialty, source_run_dir='synthetic', segments=segments)
    roles=[s['units'][0]['evidence_role'] for s in segments]
    case['summary'].update(segment_count=len(segments), unit_count=len(segments), owned_unit_count=roles.count(EvidenceRole.OWNED), shared_context_unit_count=roles.count(EvidenceRole.SHARED_CONTEXT), reference_only_unit_count=roles.count(EvidenceRole.REFERENCE_ONLY), available_locator_count=len(segments))
    return SpecialtyCaseInput.model_validate(case)


def write_run(root: Path, *, vascular=False):
    case=case_input(vascular=vascular)
    root.mkdir(parents=True, exist_ok=True)
    for suffix, model in _FILE_MODELS.items():
        if suffix == 'discourse_segments':
            data={'segments':[s.segment.model_dump(mode='json') for s in case.segments]}
        else:
            field={'graph_units':'graph_unit', 'primary_frames':'primary_frame', 'local_graphs':'local_graph'}.get(suffix,suffix)
            data={'segments':[dict(segment_id=s.segment.segment_id, **{'graph_units' if suffix=='graph_units' else 'units':[getattr(u,field).model_dump(mode='json') for u in s.units]}) for s in case.segments]}
            if suffix=='local_graphs':data['summary']=dict(segment_count=5,unit_count=5,built_graph_count=5,blocked_graph_count=0,node_count=0,edge_count=0)
            if suffix=='proposition_validation':data.update(is_graph_ready=True,summary=dict(segment_count=5,unit_count=5,graph_ready_unit_count=5,error_count=0,warning_count=0,info_count=0))
        (root/f'{case.case_id}_{suffix}.json').write_text(model.model_validate(data).model_dump_json())
    (root/f'{case.case_id}_input.txt').write_text('\n'.join(s.segment.text for s in case.segments))
    return root


def consultation(internal, trace):
    from src.agents.common.initial_output import SpecialtyInitialConsultResult, SpecialtyInitialOutput
    from test_initial_output import output_payload
    return SpecialtyInitialConsultResult(internal_state=internal,
        formal_output=SpecialtyInitialOutput.model_validate(output_payload()), trace=trace)
