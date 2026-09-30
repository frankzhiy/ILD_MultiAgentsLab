"""Temporary synthetic runs replace developer-specific historical output folders."""
import pytest
from synthetic_case import write_run


@pytest.fixture(autouse=True)
def synthetic_run_paths(request, tmp_path, monkeypatch):
    module=request.module
    names=[name for name in ('RUN_DIR', 'RUN_0714', 'RUN_0715') if hasattr(module,name)]
    if not names:
        return
    for name in names:
        path=write_run(tmp_path/name, vascular=name=='RUN_0715')
        monkeypatch.setattr(module,name,path)
    if hasattr(module,'CASE_ID'):
        monkeypatch.setattr(module,'CASE_ID','synthetic-case')
    if hasattr(module,'runner'):
        monkeypatch.setattr(module.runner,'RUNS_DIR',tmp_path)
