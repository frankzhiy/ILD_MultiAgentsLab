"""Atomic UTF-8 snapshots for readers polling an active run."""

import json
import os
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile


def write_json(path: Path, value) -> None:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    with _atomic_stream(path) as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, default=str)


def write_text(path: Path, value: str) -> None:
    with _atomic_stream(path) as stream:
        stream.write(value)


def read_checkpoint(path: Path, model):
    """An absent, truncated or incompatible checkpoint can be recomputed."""
    try:
        return model.model_validate_json(path.read_bytes())
    except (FileNotFoundError, ValueError):
        return None


@contextmanager
def _atomic_stream(path: Path):
    temporary = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            yield stream
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def verify_protocol_snapshot(root: Path, run_dir: Path, *, allow_stage_rerun: bool = False) -> None:
    """Refuse to silently combine a saved clinical run with changed rules."""
    manifest = run_dir / "protocol_manifest.json"
    if not manifest.exists():
        return  # Existing artifacts remain readable and explicitly retain their old schema.
    data = json.loads(manifest.read_text(encoding="utf-8"))
    from src.utils.config import load_yaml
    run = json.loads((run_dir / ".workbench_run.json").read_text(encoding="utf-8"))
    patient = Path(run["input_path"])
    if sha256(patient.read_bytes()).hexdigest() != data["patient_sha256"]:
        raise ValueError("病例快照已改变，请创建新运行")
    if data.get("consultation") != run.get("consultation"):
        raise ValueError("会诊任务快照已改变，请创建新运行")
    if allow_stage_rerun:
        return  # New stage records current rules; immutable patient input remains mandatory.
    for agent, signature in data.get("agents", {}).items():
        if load_yaml(run["configs"][agent]) != signature["config"]:
            raise ValueError(f"{agent} 运行配置已改变，请创建新运行")
    changed = [relative for section in ("prompts", "guidelines", "knowledge_index")
               for relative, digest in data.get(section, {}).items()
               if not (root / relative).exists() or sha256((root / relative).read_bytes()).hexdigest() != digest]
    for signature in data.get("agents", {}).values():
        changed.extend(_changed_configured_prompts(signature))
    if changed:
        raise ValueError("运行所用提示或指南已改变，请创建新运行，不能混用旧意见：" + ", ".join(changed))


def verify_stage_snapshot(root: Path, run_dir: Path, stage: str) -> None:
    verify_protocol_snapshot(root, run_dir, allow_stage_rerun=True)
    path = run_dir / f"{stage}_stage_rules.json"
    if not path.exists():
        return
    from src.utils.config import load_yaml
    rules = json.loads(path.read_text(encoding="utf-8"))
    run = json.loads((run_dir / ".workbench_run.json").read_text(encoding="utf-8"))
    changed = [agent for agent, config in rules["configs"].items()
               if load_yaml(run["configs"][agent]) != config]
    changed.extend(relative for section in ("prompts", "guidelines", "knowledge_index")
                   for relative, digest in rules.get(section, {}).items()
                   if not (root / relative).exists() or sha256((root / relative).read_bytes()).hexdigest() != digest)
    changed.extend(_changed_configured_prompts(rules.get("chair_signature", {})))
    if changed:
        raise ValueError("本阶段运行期间规则发生改变，请重新运行本阶段：" + ", ".join(changed))


def _changed_configured_prompts(signature: dict) -> list[str]:
    changed = []
    for key, digest in signature.get("prompt_sha256", {}).items():
        path = Path(signature["config"][key])
        if not path.is_file() or sha256(path.read_bytes()).hexdigest() != digest:
            changed.append(str(path))
    return changed
