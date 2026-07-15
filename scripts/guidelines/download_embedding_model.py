#!/usr/bin/env python3
"""Download the official BAAI BGE-M3 runtime files from ModelScope."""

from argparse import ArgumentParser
from pathlib import Path


FILES = [
    "config.json",
    "1_Pooling/config.json",
    "config_sentence_transformers.json",
    "modules.json",
    "sentence_bert_config.json",
    "sentencepiece.bpe.model",
    "special_tokens_map.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "pytorch_model.bin",
]


def main() -> int:
    parser = ArgumentParser()
    root = Path(__file__).resolve().parents[2]
    parser.add_argument("--output", default=str(root / "data/guidelines/models/bge-m3"))
    args = parser.parse_args()
    try:
        from modelscope import snapshot_download
    except ImportError as exc:
        raise RuntimeError(
            "Install the optional downloader: pip install -e '.[guideline-model-download]'"
        ) from exc
    path = snapshot_download(
        "BAAI/bge-m3",
        local_dir=args.output,
        allow_file_pattern=FILES,
        max_workers=8,
    )
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
