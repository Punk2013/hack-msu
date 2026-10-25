"""Run: .venv/bin/python tests/detect/evaluate.py [corpus.jsonl]. No input text in output."""

import hashlib
import json
import platform
import statistics
import sys
from pathlib import Path
from time import perf_counter

from pii_gateway.core.types import Context, EntitySpan, PiiType, check_spans
from pii_gateway.detect import detect


def evaluate(path: Path) -> dict:
    payload = path.read_bytes()
    rows = [json.loads(line) for line in payload.decode().splitlines()]
    kinds = (PiiType.PERSON, PiiType.EMAIL, PiiType.PHONE, PiiType.PASSPORT_RU)
    counts = {kind: {"tp": 0, "fp": 0, "fn": 0} for kind in kinds}
    mismatches = []
    cold = perf_counter()
    detect("Иван Иванов", Context("benchmark-warmup"))
    cold_ms = (perf_counter() - cold) * 1000
    durations = []
    for row in rows:
        text = row["text"]
        expected = [EntitySpan(**s, detector="annotation") for s in row["expected"]]
        check_spans(text, expected)
        start = perf_counter()
        result = detect(text, Context("benchmark-synthetic"))
        durations.append((perf_counter() - start) * 1000)
        check_spans(text, result)
        want = {(s.start, s.end, s.type) for s in expected}
        got = {(s.start, s.end, s.type) for s in result}
        if want != got:
            mismatches.append(row["id"])
        for kind in kinds:
            counts[kind]["tp"] += sum(s[2] == kind for s in want & got)
            counts[kind]["fp"] += sum(s[2] == kind for s in got - want)
            counts[kind]["fn"] += sum(s[2] == kind for s in want - got)
    for values in counts.values():
        tp, fp, fn = values["tp"], values["fp"], values["fn"]
        values["precision"] = tp / (tp + fp) if tp + fp else None
        values["recall"] = tp / (tp + fn) if tp + fn else None
    return {
        "corpus": path.name,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "records": len(rows),
        "metric": "exact (start, end, type), micro per type",
        "python": platform.python_version(),
        "machine": platform.machine(),
        "model": "natasha==1.6.0 / NewsNERTagger",
        "counts": counts,
        "mismatch_case_ids": mismatches,
        "cold_init_and_inference_ms": round(cold_ms, 2),
        "warm_median_ms": round(statistics.median(durations), 2),
        "warm_max_ms": round(max(durations), 2),
        "limitation": "Small development corpus, not a held-out or privacy guarantee",
    }


if __name__ == "__main__":
    path = (
        Path(sys.argv[1])
        if len(sys.argv) > 1
        else (Path(__file__).parent / "fixtures/synthetic_v1.jsonl")
    )
    print(json.dumps(evaluate(path), ensure_ascii=False, indent=2))
