# ILD MDT Research Workbench

Use the existing `multiAgent` conda environment. From the repository root:

```bash
conda activate multiAgent
python -m pip install "fastapi>=0.116,<1" "uvicorn[standard]>=0.35,<1" "python-multipart>=0.0.20,<1"
pnpm --dir front install
python -m src.workbench
```

The UI runs at `http://127.0.0.1:5173`; FastAPI runs at `http://127.0.0.1:8000`.
The workbench reads historical artifacts from `outputs/runs/` and writes new run metadata,
runtime configuration snapshots, event history, and outputs under `outputs/`.
