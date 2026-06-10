# ILD MultiAgents Lab

Research-oriented multi-agent framework for interstitial lung disease diagnostic reasoning.

The first implemented component is a clinical discourse + graph unit extraction agent:

```text
free-text clinical narrative
  -> clinical discourse segmentation
  -> discourse unit labeling with contained source types
  -> graph unit extraction (one clinical event nucleus per unit)
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

The current manual script runs clinical discourse segmentation and graph unit extraction.

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
- `discourse_segmentation_report.html`
- `trace.json`
- `timing.json`
