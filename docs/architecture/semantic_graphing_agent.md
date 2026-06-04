# Semantic Graphing Agent Architecture

The first agent is a modality-aware ILD semantic graph construction agent.

## Research Objective

Transform unstructured clinical narratives into an evidence-grounded, patient-specific semantic
graph for downstream diagnostic reasoning agents.

## Workflow

```text
free-text input
  -> source-aware segmentation and classification
  -> modality graph construction schema selection
  -> local semantic subgraph construction
  -> machine JSON and human-readable HTML report
```

The current runnable script is intentionally stopped at Step 2 so the classification result can be
manually inspected before graph construction is enabled.

## Core Design Choice

The agent has three schema layers:

1. Document type schema: identifies whether text is history, HRCT, PFT, labs, pathology,
   exposure/medication, or clinician assessment.
2. Concept schema: constrains node types, edge types, and required attributes.
3. Graph construction schema: defines the graph center and construction logic for each modality.

Examples:

- HRCT: pattern-centered graph.
- History: timeline-risk-factor graph.
- Pulmonary function: physiology-trend graph.
- Laboratory: evidence-to-phenotype graph.
- Pathology: morphology-pattern graph.
- Exposure/medication: causal-exposure graph.
- Clinician assessment: assessment-provenance graph.

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

- `classification.json`
- `classification_report.html`
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
