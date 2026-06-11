# ILD MultiAgents Lab

Research-oriented multi-agent framework for interstitial lung disease diagnostic reasoning.

The first implemented component is a clinical discourse + graph unit extraction agent:

```text
free-text clinical narrative
  -> clinical discourse segmentation
  -> discourse unit labeling with contained source types
  -> graph unit extraction (one clinical event nucleus per unit)
  -> primary frame selection (one organization template per graph unit)
  -> evidence-grounded clinical proposition and modifier extraction
  -> deterministic clinical proposition validation
  -> HTML research report for manual inspection
```

This is not an application scaffold. It is designed for reproducible experiments, schema evolution,
trace inspection, and future multi-agent diagnostic reasoning.

## Manual LLM Run

Set your ChatAnywhere credentials:

```bash
export CHATANYWHERE_API_KEY="..."
```

Set the model, ChatAnywhere base URL, and timeout in
`configs/agents/semantic_graphing/agent.yaml`.

The semantic graph stages use separate models by default:

- `gpt-4.1-mini` for discourse segmentation, graph-unit extraction, and clinical propositions
- `gpt-4.1-nano` only for the closed-set primary-frame selection task

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

Resume an interrupted run without repeating completed tasks:

```bash
python3 scripts/run/run_semantic_graph_agent.py \
  --input data/raw_cases/01.txt \
  --resume-run outputs/runs/<interrupted-run-directory>
```

Resume is rejected when the stage models, config, or prompt contents differ from the interrupted
run, preventing mixed-version outputs.
