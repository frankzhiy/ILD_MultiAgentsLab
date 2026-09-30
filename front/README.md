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

For experiments, choose **批量实验** on the run list (or open `/batches/new`). Select cases,
configure each Agent once for the whole batch, and set the number of simultaneous cases.
The batch page shows each case's stage, discussion round, elapsed time, result, and error.
Closing or refreshing the page does not stop execution. After the batch finishes, you can
export its CSV summary or rerun only failed, stopped, or interrupted cases into a new batch,
using their original input and model configuration snapshots. A server restart marks unfinished
cases as interrupted; it does not automatically rerun them or repeat model requests.
