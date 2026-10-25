"""Run from PR #37 checkout: PYTHONPATH=src pytest <this-file>. Synthetic data only."""
import importlib.util
from pathlib import Path

from pii_gateway.vault.cli import main


def test_stats_does_not_create_a_nonexistent_vault(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PII_VAULT_DIR", str(tmp_path))
    code = main(["--json", "stats", "missing"])
    capsys.readouterr()
    assert not (tmp_path / "missing.db").exists()
    assert code != 0


def test_report_does_not_claim_perfect_accuracy_when_measurement_failed(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "restoration_benchmark", Path("bench/bench_restoration.py")
    )
    benchmark = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(benchmark)
    row = {
        "accuracy_pct": 50.0, "perfect_roundtrips": 1, "total_cases": 2,
        "mask_ms_per_1k": 1.0, "restore_ms_per_1k": 1.0,
        "bytes_per_entity": 1.0, "total_entities": 1, "db_size_bytes": 1,
    }
    output = tmp_path / "report.md"
    benchmark.generate_report({"marker": row, "realistic": row}, output)
    assert "достигнута 100% точность" not in output.read_text()
