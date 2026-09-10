from pathlib import Path

import pytest

from orderflow_mm.bars import build_closed_hour_dataset
from orderflow_mm.quality import inspect_dataset
from orderflow_mm.sample import generate_sample_data


def test_sample_data_passes_structural_qa_and_builds_bars(tmp_path: Path) -> None:
    raw_root = tmp_path / "raw"
    result = generate_sample_data(raw_root, seconds=30)

    assert result["book_ticker_rows"] == 30
    assert result["trade_rows"] == 30
    report = inspect_dataset(raw_root)
    assert all(stream.passed for stream in report.values())
    assert report["book_ticker"].duplicate_event_ids == 0
    assert report["trade"].duplicate_event_ids == 0

    outputs = build_closed_hour_dataset(raw_root, tmp_path / "bars")
    assert len(outputs) == 1
    assert outputs[0].exists()


def test_sample_data_refuses_to_append_duplicate_fixture(tmp_path: Path) -> None:
    raw_root = tmp_path / "raw"
    generate_sample_data(raw_root, seconds=20)
    with pytest.raises(FileExistsError, match="already contains"):
        generate_sample_data(raw_root, seconds=20)


def test_sample_data_requires_feature_warmup_window(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="at least 20"):
        generate_sample_data(tmp_path / "raw", seconds=19)
