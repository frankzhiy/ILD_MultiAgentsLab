from __future__ import annotations

import argparse
import signal
import subprocess
import sys
import time
import webbrowser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    parser = argparse.ArgumentParser(description="Start the ILD MDT research workbench.")
    parser.add_argument("--api-port", type=int, default=8000)
    parser.add_argument("--web-port", type=int, default=5173)
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args()
    commands = [
        [
            sys.executable,
            "-m",
            "uvicorn",
            "src.workbench.app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(args.api_port),
            "--reload",
        ],
        [
            "pnpm",
            "--dir",
            "front",
            "dev",
            "--host",
            "127.0.0.1",
            "--port",
            str(args.web_port),
        ],
    ]
    processes = [subprocess.Popen(command, cwd=ROOT) for command in commands]

    def stop(*_: object) -> None:
        for process in processes:
            if process.poll() is None:
                process.terminate()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    if not args.no_open:
        time.sleep(1.2)
        webbrowser.open(f"http://127.0.0.1:{args.web_port}/runs")
    try:
        while all(process.poll() is None for process in processes):
            time.sleep(0.4)
    finally:
        stop()
        for process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
    return next((process.returncode for process in processes if process.returncode), 0)


if __name__ == "__main__":
    raise SystemExit(main())
