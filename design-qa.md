**Comparison target**

- Source design: `/var/folders/0y/x03bsc751k38q3k4l0894g380000gn/T/codex-clipboard-e32a0f02-84b3-42ee-a081-ddde50ded029.png`
- Implementation: in-app Browser capture of `http://127.0.0.1:5173/runs/20260716_222505_81-IPF_step2_step3/routing`
- Viewport/state: desktop, completed run `81-IPF`, full matrix and filtered-unit states checked.

**Findings**

- No actionable P0/P1/P2 findings. The redundant four-card overview and specialty selector are removed. The compact summary shows unique units, shared background once, specialty-routed units, and locator coverage.
- The matrix now uses one row per graph unit and checkmarks for each responsible specialty. Shared-background units check all four specialties; multi-specialty units check their assigned specialties.

**Required fidelity surfaces**

- Fonts and typography: existing application typography and hierarchy are preserved.
- Spacing and layout rhythm: the former oversized card region is replaced by one compact summary strip.
- Colors and visual tokens: existing panel, border, success, and warning tokens are preserved.
- Image quality and asset fidelity: no raster or illustrative assets are used in this data workspace.
- Copy and content: removed `共享 / 协作` and role labels; copy now describes unique evidence distribution.

**Interaction checks**

- Search for `seg_001_gu_004` reduced the table to its matching row.
- Clicking that unit opened the evidence inspector with the matching source text and identifiers.

**Implementation checklist**

- [x] Normalize multi-specialty units to `owned` for every assigned specialty.
- [x] Remove collaborative role/count from the input schema, summaries, prompts, and reports.
- [x] Expose unique-unit routing data from the workbench API.
- [x] Render the checkmark matrix and compact overview.

**Follow-up polish**

- P3: column-header counts can be added later if clinicians request per-specialty workload totals.

final result: passed
