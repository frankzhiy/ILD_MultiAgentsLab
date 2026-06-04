# Modality Graph Schemas

The modality schemas live in:

```text
configs/agents/semantic_graphing/modality_schemas/
```

Each schema defines:

- `graph_construction_strategy`
- `graph_center`
- `primary_questions`
- allowed node and edge types
- required node and edge attributes
- missingness checks
- construction rules
- diagnostic targets

These files are intentionally configuration-first so that schema changes can be tracked as research
variables during experiments.

