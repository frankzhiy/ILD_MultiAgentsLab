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

Set credentials for the providers you use:

```bash
export DEEPSEEK_API_KEY="..."
export CHATANYWHERE_API_KEY="..."
```

Set `provider`, model, base URL, API key environment variable, and timeout in
`configs/agents/semantic_graphing/agent.yaml`.

The default provider is DeepSeek using the official `deepseek-v4-pro` model. Set `thinking` to
`enabled` or `disabled` to control DeepSeek reasoning for every stage. To switch back to
ChatAnywhere, set `provider: chatanywhere`, its base URL and API key environment variable, and the
models available from ChatAnywhere.

```yaml
provider: chatanywhere
model: gpt-4.1-mini
classification_model: gpt-4.1-mini
graph_unit_model: gpt-4.1-mini
primary_frame_model: gpt-4.1-nano
clinical_proposition_model: gpt-4.1-mini
base_url: https://api.chatanywhere.tech/v1
api_key_env: CHATANYWHERE_API_KEY
```

The same config also controls stage-specific models, `max_concurrency`, `max_attempts`, per-stage
token limits, and clinical-proposition chunk size.

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
