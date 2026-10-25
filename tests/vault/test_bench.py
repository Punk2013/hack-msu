import importlib.util
from pathlib import Path


def test_report_does_not_claim_perfect_accuracy_when_measurement_failed(tmp_path: Path):
    spec = importlib.util.spec_from_file_location(
        "restoration_benchmark", Path("bench/bench_restoration.py")
    )
    assert spec is not None and spec.loader is not None
    benchmark = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(benchmark)
    row = {
        "accuracy_pct": 50.0,
        "perfect_roundtrips": 1,
        "total_cases": 2,
        "mask_ms_per_1k": 1.0,
        "restore_ms_per_1k": 1.0,
        "bytes_per_entity": 1.0,
        "total_entities": 1,
        "total_pairs": 1,
        "db_size_bytes": 1,
    }
    output = tmp_path / "report.md"
    benchmark.generate_report({"marker": row, "realistic": row}, output)
    report_text = output.read_text(encoding="utf-8")
    assert "достигнута 100% точность" not in report_text
    assert "50.0%" in report_text
