# ILD MultiAgents Lab

Research-oriented multi-agent framework for interstitial lung disease diagnostic reasoning.

The first implemented component is a clinical discourse + graph unit extraction agent:

```text
free-text clinical narrative
  -> clinical discourse segmentation
  -> discourse unit labeling with contained source types
  -> graph unit extraction (one clinical event nucleus per unit)
  -> primary frame selection (one organization template per graph unit)
  -> deterministic evidence-block generation with stable evidence IDs
  -> evidence-grounded clinical proposition and modifier extraction
  -> deterministic clinical proposition validation
  -> HTML research report for manual inspection
```

This is not an application scaffold. It is designed for reproducible experiments, schema evolution,
trace inspection, and future multi-agent diagnostic reasoning.

## Manual LLM Run

Set the APIYI credential:

```bash
export APIYI_API_KEY="..."
```

Set `provider`, model, base URL, API key environment variable, and timeout in
`configs/agents/semantic_graphing/agent.yaml`.

Semantic graphing uses `deepseek-v4-flash` through APIYI's OpenAI-compatible endpoint. Thinking is
explicitly disabled for every stage through `request_options`.

```yaml
provider: apiyi
model: deepseek-v4-flash
base_url: https://api.apiyi.com/v1
api_key_env: APIYI_API_KEY
request_options:
  thinking:
    type: disabled
```

The same config also controls `max_concurrency`, `max_attempts`, per-stage token limits, and
clinical-proposition chunk size.

The current manual script runs clinical discourse segmentation, graph-unit extraction,
primary-frame selection, and clinical-proposition extraction.

```bash
python3 scripts/run/run_semantic_graph_agent.py \
  --input data/raw_cases/01.txt \
  --case-id case_01
```

Or run it without `--input` and select a `.txt` file under `data/raw_cases/` from the CLI:

```bash
python3 scripts/run/run_semantic_graph_agent.py
```

The script writes a timestamped run folder under `outputs/runs/`, including:

- `discourse_segments.json`
- `graph_units.json`
- `primary_frames.json`
- `clinical_propositions.json`
- `proposition_validation.json`
- `report.html`
- `trace.json`
- `timing.json`
- `task_cache/` with successful per-segment and per-unit results for interrupted-run recovery

Each graph unit in `clinical_propositions.json` contains ordered evidence blocks with globally unique
IDs. Propositions, modifiers, and attributions reference those blocks and retain an exact quote,
allowing downstream doctor agents to discuss structured claims while citing the original text.

Resume an interrupted run without repeating completed tasks:

```bash
python3 scripts/run/run_semantic_graph_agent.py \
  --input data/raw_cases/01.txt \
  --resume-run outputs/runs/<interrupted-run-directory>
```

Resume is rejected when the stage models, config, or prompt contents differ from the interrupted
run, preventing mixed-version outputs.

## Downstream Multi-Agent LLM

The downstream multi-agent system has an independent LLM configuration at
`configs/agents/multi_agent_system/llm.yaml`. It does not reuse or change the semantic-graphing
agent configuration.

Set the APIYI credential before running the future multi-agent system:

```bash
export APIYI_API_KEY="..."
```

The default downstream model is `deepseek-v4-flash` through APIYI's OpenAI-compatible endpoint.
Thinking is explicitly disabled through `request_options`. The APIYI client is provider-specific,
not DeepSeek-specific: GPT, Claude, Qwen, and other models exposed through APIYI's compatible
endpoint can use the same client by changing `model` and any model-specific `request_options`.

## Rheumatology ILD Consultation Agent

The rheumatology agent follows the same staged consultation pattern as the pulmonology agent:
three initial-assessment stages (case reconstruction, autoimmune assessment, consult formulation)
and three discussion stages (evidence mapping, state update, chair response). It records the
rheumatic-disease working formulation separately from the ILD attribution assessment and does not
issue a final MDT diagnosis or treatment plan.

Run it after semantic-graph outputs are available:

```bash
python3 scripts/run/run_rheumatology_agent.py
```

The CLI writes rheumatology input, structured result, stage trace, and HTML report beside the
selected semantic-graph run. Configuration and guideline guardrails are in
`configs/agents/rheumatology/agent.yaml`.
