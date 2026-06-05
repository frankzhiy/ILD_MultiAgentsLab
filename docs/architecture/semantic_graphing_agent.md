# Semantic Graphing Agent Architecture

The first agent is an ILD clinical discourse + graph unit extraction agent.

## Research Objective

Transform unstructured clinical narratives into evidence-grounded, patient-specific graph units
for downstream diagnostic reasoning agents.

## Workflow

```text
free-text input
  -> clinical discourse segmentation
  -> discourse unit labeling with contained source types
  -> graph unit extraction (one clinical event nucleus per unit)
  -> machine JSON and human-readable HTML report
```

The runnable script produces discourse segments and graph units for manual inspection before
downstream node/edge graph construction is enabled.

## Core Design Choice

The pipeline separates three conceptual layers:

1. Segment layer: a complete clinical discourse unit / episode or a standalone report-like unit.
2. Graph unit layer: one clinical event nucleus inside a segment (a single onset / flare / visit /
   exam / treatment), labeled with its own `source_type`. Modifiers such as time anchors, symptom
   attributes, and patient attitude stay inside their event nucleus and never become separate units.
3. Node/edge layer: finding-level decomposition, performed by downstream graph construction (not
   part of this agent).

## Manual Run

```bash
export CHATANYWHERE_API_KEY="..."
export CHATANYWHERE_BASE_URL="https://api.chatanywhere.tech/v1"
export CHATANYWHERE_MODEL="gpt-5.5"

python3 scripts/run/run_semantic_graph_agent.py \
  --input data/raw_cases/01.txt \
  --case-id case_01
```

If `--input` is omitted, the script lists `.txt` files under `data/raw_cases/` and lets the user
choose one interactively in the CLI.

Current outputs:

- `discourse_segments.json`
- `discourse_segmentation_report.html`
- `trace.json`
- `timing.json`

## Structured Output

The agent does not rely only on prompt wording to obtain JSON. Each LLM generation step uses:

1. a target Pydantic schema converted to JSON Schema when the API supports structured output;
2. JSON parsing;
3. Pydantic validation;
4. automatic correction retries with the validation error if the output is invalid.

If the OpenAI-compatible endpoint does not support JSON Schema response format, the code falls back
to JSON object mode while keeping schema validation and retry repair.
