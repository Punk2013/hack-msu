"""Run synthetic text trials through real masking, restoration and an HTTP gateway.

No LLM: the independent upstream receiver echoes only the text it received.
Outputs contain synthetic originals and are intended for local inspection.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import socket
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

import httpx
import uvicorn

from pii_gateway.core.types import Context, PiiGatewayError
from pii_gateway.detect import detect
from pii_gateway.gateway.app import Settings, create_app
from pii_gateway.pseudo import mask, restore
from pii_gateway.sandbox.leaks import Canary, LeakScanner
from pii_gateway.sandbox.upstream_spy import UpstreamSpy


@contextmanager
def gateway(settings):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(create_app(settings), log_level="error"))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started and thread.is_alive() and time.monotonic() < deadline:
                time.sleep(.01)
            if not server.started:
                raise RuntimeError("local gateway failed to start")
            yield f"http://127.0.0.1:{port}/v1/chat/completions"
        finally:
            server.should_exit = True
            thread.join(timeout=5)


def trial(case, text, mode, project, output, ui_url):
    ctx = Context(project, mode)
    row = {"file": case["file"], "mode": mode, "original": text,
           "known_limits": case.get("known_limits", False), "error": None,
           "annotated_fields": len(case["sensitive"])}
    started = time.perf_counter()
    try:
        spans = detect(text, ctx)
        row["spans"] = [{"type": str(s.type), "text": s.raw_text} for s in spans]
        row["masked"] = mask(text, spans, ctx).text
        restored = restore(row["masked"], ctx)
        row.update(restored=restored.text, restore_status=str(restored.status),
                   exact=restored.text == text, stable=mask(text, detect(text, ctx), ctx).text
                   == row["masked"])
    except PiiGatewayError as exc:
        row["error"] = str(exc)
    row["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 2)
    row["remaining_values"] = [v for v in case["sensitive"] if v in row.get("masked", "")]
    seen = {}

    def echo(body):
        seen["text"] = body["messages"][-1]["content"]
        return {"choices": [{"index": 0, "finish_reason": "stop", "message": {
            "role": "assistant", "content": seen["text"]}}]}

    canaries = [Canary(f"field-{i}", v) for i, v in enumerate(case["sensitive"])]
    if not canaries:
        # The no-PII control has no annotated secrets; this sentinel is absent by design.
        canaries = [Canary("absent-control", "SYNTHETIC-ABSENT-CONTROL-e105")]
    scanner = LeakScanner(canaries)
    with UpstreamSpy(scanner, responder=echo) as spy:
        settings = Settings(upstream_base_url=spy.base_url, project_id=project, mode=mode,
                            local_token="synthetic-trial-token", workspace=output)
        with gateway(settings) as url:
            response = httpx.post(url, headers={"Authorization": "Bearer synthetic-trial-token"},
                                  json={"model": "synthetic-echo", "messages": [
                                      {"role": "user", "content": text}]}, trust_env=False)
        row["http_status"] = response.status_code
        row["upstream"] = spy.report()
        row["upstream_text"] = seen.get("text")
        row["wire_matches_masked"] = seen.get("text") == row.get("masked")
        row["http_restored_exact"] = response.status_code == 200 and (
            response.json()["choices"][0]["message"]["content"] == text)
    if ui_url:
        with httpx.Client(base_url=ui_url, trust_env=False, timeout=30) as client:
            ui_ctx = {"project_id": project + "-ui", "mode": mode}
            response = client.post("/api/process", json={**ui_ctx, "text": text})
            row["ui_process_status"] = response.status_code
            if response.status_code == 200:
                row["ui_masked"] = response.json()["masked_text"]
                response = client.post("/api/restore", json={**ui_ctx, "text": row["ui_masked"]})
                row["ui_restore_status"] = response.status_code
                row["ui_restored_exact"] = response.status_code == 200 and (
                    response.json()["restored_text"] == text)
    if case.get("expect_refusal"):
        row["passed"] = bool(row["error"]) and row["http_status"] == 500 and (
            row["upstream"]["requests"] == 0)
    else:
        row["passed"] = not row["error"] and row.get("exact") and row.get("stable") and (
            row.get("restore_status") == "complete" and row["wire_matches_masked"] and
            row["http_restored_exact"] and row["upstream"]["requests"] == 1) and (
            not row["remaining_values"] or row["known_limits"])
        if ui_url:
            row["passed"] = row["passed"] and row.get("ui_restored_exact", False)
    return row


def save_report(output, rows, metadata):
    (output / "results.json").write_text(
        json.dumps({"metadata": metadata, "results": rows}, ensure_ascii=False, indent=2))
    blocks = []
    for row in rows:
        if row["remaining_values"]:
            verdict = f"Неполное обезличивание: осталось {len(row['remaining_values'])} полей"
        elif row["http_status"] != 200:
            verdict = f"Запрос отклонён: HTTP {row['http_status']}; upstream: " + str(
                row["upstream"]["requests"])
        elif row.get("annotated_fields") == 0:
            verdict = "Контроль без размеченных ПДн; точное восстановление: " + str(
                row.get("exact"))
        else:
            verdict = "Размеченные значения скрыты; восстановление: " + str(row.get("exact"))
        details = {k: v for k, v in row.items() if k not in (
            "original", "masked", "restored", "upstream_text", "ui_masked")}
        sections = "".join(f"<section><h3>{title}</h3><pre>"
                           f"{html.escape(row.get(key) or '—')}</pre></section>"
                           for title, key in (("Исходный текст", "original"),
                                              ("Подмена", "masked"),
                                              ("Восстановление", "restored")))
        blocks.append(f"<article><h2>{html.escape(row['file'])} · {row['mode']}</h2>"
                      + f"<p><strong>{html.escape(verdict)}</strong></p>"
                      + f"<div class='texts'>{sections}</div>"
                      + "<details><summary>Что принял HTTP upstream</summary><pre>"
                      + html.escape(row.get("upstream_text") or "—") + "</pre></details>"
                      + "<details><summary>Проверки и метаданные</summary><pre>"
                      + html.escape(json.dumps(details, ensure_ascii=False, indent=2))
                      + "</pre></details></article>")
    page = ("<!doctype html><meta charset='utf-8'><title>Локальные прогоны PII Gateway</title>"
            "<style>body{font:16px system-ui;max-width:1100px;margin:32px auto;background:#f3f5f8;"
            "color:#17212f}article{background:white;padding:24px;margin:24px 0;border-radius:12px}"
            "pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#eef1f5;padding:16px}"
            "h3{margin-bottom:8px}.texts{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));"
            "gap:12px}details{margin:12px 0}@media(max-width:850px){.texts{display:block}}"
            "</style><h1>Фактические локальные прогоны</h1>"
            "<p>Только синтетика. Настоящие detect/mask/restore и HTTP gateway. "
            "Вместо LLM — локальный echo receiver. COMPLETE не доказывает отсутствие утечек. "
            "passed означает совпадение с ожиданием: в 05_limits намеренно ожидается leak.</p>"
            + "".join(blocks))
    (output / "report.html").write_text(page)
    header = ("# Локальные тексты: фактический прогон\n\n"
              "Только синтетика; HTTP upstream — локальный echo, не LLM.\n\n"
              "| Текст | Режим | Roundtrip | Повтор стабилен | "
              "Исходных значений осталось | HTTP |\n"
              "| --- | --- | --- | --- | ---: | ---: |\n")
    table = "".join(f"| {r['file']} | {r['mode']} | {r.get('exact', 'отказ')} | "
                    f"{r.get('stable', '—')} | {len(r['remaining_values'])} | "
                    f"{r['http_status']} |\n" for r in rows)
    (output / "summary.md").write_text(header + table)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", type=Path,
                        default=Path(__file__).parent / "fixtures/local_texts")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--ui-url", help="Optional showcase #42 API to probe in addition")
    args = parser.parse_args()
    manifest = json.loads((args.fixtures / "manifest.json").read_text())
    if manifest.get("synthetic") is not True:
        raise ValueError("only explicitly synthetic fixtures are allowed")
    args.out.mkdir(parents=True, exist_ok=False)
    os.environ["PII_VAULT_DIR"] = str(args.out.resolve() / "vault")
    stamp = uuid.uuid4().hex[:10]
    rows = []
    for case in manifest["cases"]:
        text = (args.fixtures / case["file"]).read_text()
        for mode in ("realistic", "marker"):
            project = f"trial-{stamp}-{mode}-{Path(case['file']).stem}"
            row = trial(case, text, mode, project, args.out.resolve(), args.ui_url)
            rows.append(row)
            print(json.dumps({k: row.get(k) for k in (
                "file", "mode", "exact", "stable", "remaining_values", "http_status", "passed")},
                             ensure_ascii=False), flush=True)
    save_report(args.out, rows, {"synthetic": True, "provider": "local-echo",
                               "ui_url": args.ui_url, "run_id": stamp})
    print(f"Report: {args.out.resolve() / 'report.html'}", flush=True)
    return int(any(not row["passed"] for row in rows))


if __name__ == "__main__":
    raise SystemExit(main())
