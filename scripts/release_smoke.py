"""Run the release checks, including an isolated end-to-end sample pipeline."""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECKPOINT = ROOT / "reports/generated/checkpoints/20260905T1712Z-final/checkpoint.json"


def _run(*args: str) -> None:
    subprocess.run([sys.executable, *args], cwd=ROOT, check=True)


def main() -> None:
    _run("-m", "ruff", "format", "--check", "src", "tests", "scripts")
    _run("-m", "ruff", "check", "src", "tests", "scripts")
    _run("-m", "pytest")

    with tempfile.TemporaryDirectory(prefix="orderflow-mm-release-") as temporary:
        temporary_root = Path(temporary)
        raw_root = temporary_root / "raw"
        bars_root = temporary_root / "bars"
        validation_report = temporary_root / "validation.json"
        _run(
            "-m",
            "orderflow_mm",
            "generate-sample",
            "--output",
            str(raw_root),
            "--seconds",
            "180",
        )
        _run("-m", "orderflow_mm", "qa", "--input", str(raw_root))
        _run(
            "-m",
            "orderflow_mm",
            "build-bars",
            "--input",
            str(raw_root),
            "--output",
            str(bars_root),
        )
        _run("-m", "orderflow_mm", "inspect-bars", "--input", str(bars_root))
        _run(
            "-m",
            "orderflow_mm",
            "validate-checkpoint",
            "--checkpoint",
            str(CHECKPOINT),
            "--report",
            str(validation_report),
        )

    print("Release smoke test passed: static checks, tests, sample pipeline, final validation.")


if __name__ == "__main__":
    main()
