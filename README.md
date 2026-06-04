# ILD MultiAgents Lab

Research-oriented multi-agent framework for interstitial lung disease diagnostic reasoning.

The first implemented component is a modality-aware semantic graph construction agent:

```text
free-text clinical narrative
  -> document/segment type classification
  -> modality-specific graph construction schema activation
  -> local semantic subgraph construction
  -> HTML research report for manual inspection
```

This is not an application scaffold. It is designed for reproducible experiments, schema evolution,
trace inspection, and future multi-agent diagnostic reasoning.

## Manual LLM Run

Set your ChatAnywhere credentials:

```bash
export CHATANYWHERE_API_KEY="..."
export CHATANYWHERE_BASE_URL="https://api.chatanywhere.tech/v1"
export CHATANYWHERE_MODEL="gpt-5.5"
```

The current manual script runs Step 2 only: source-aware text segmentation and classification.

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

- `classification.json`
- `classification_report.html`
- `trace.json`
- `timing.json`
