"""HTML report from the gateway journal (PII_JOURNAL).

For every model call it shows how the agent's text was masked on the way to the
model and how the model's answer was restored on the way back.

    python -m pii_gateway.journal_report <journal.jsonl> <report.html>
"""

from __future__ import annotations

import html
import json
import sys
from datetime import datetime
from pathlib import Path

from pii_gateway.report import _diff, _texts

STYLE = """
:root{--bg:#fff;--fg:#1d1d1f;--muted:#6e6e73;--line:#e5e5ea;--card:#f5f5f7;
--fake:#ffe8b3;--real:#c9f2d0;--bad:#ffd2d2}
@media (prefers-color-scheme:dark){:root{--bg:#111;--fg:#f2f2f7;--muted:#a1a1a6;
--line:#2c2c2e;--card:#1c1c1e;--fake:#5c4300;--real:#0f4d1d;--bad:#5c1414}}
body{margin:0;padding:16px;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui}
h1{font-size:20px;margin:0 0 4px}h2{font-size:16px;margin:24px 0 8px}
h3{font-size:13px;margin:12px 0 4px;color:var(--muted);text-transform:uppercase}
p.meta{color:var(--muted);margin:0 0 16px}
.stats{display:flex;gap:12px;flex-wrap:wrap;margin:12px 0}
.stat{background:var(--card);border-radius:8px;padding:8px 12px}
.stat b{display:block;font-size:20px}
table{width:100%;border-collapse:collapse;table-layout:fixed}
th,td{border-top:1px solid var(--line);padding:6px 8px;vertical-align:top;text-align:left}
th.role,td.role{width:84px;color:var(--muted)}
pre{margin:0;white-space:pre-wrap;word-break:break-word;font:13px/1.45 ui-monospace,monospace}
mark{color:inherit;border-radius:3px;padding:0 2px}.fake{background:var(--fake)}
.real{background:var(--real)}.blocked{background:var(--bad);padding:2px 6px;border-radius:4px}
"""


def _rows(pairs: list[tuple[str, str, str]], left_is_real: bool) -> tuple[str, int]:
    out, changes = [], 0
    for role, real, fake in pairs:
        masked, restored, n = _diff(fake, real)
        changes += n
        left, right = (restored, masked) if left_is_real else (masked, restored)
        out.append(f"<tr><td class=role>{html.escape(role)}</td>"
                   f"<td><pre>{left}</pre></td><td><pre>{right}</pre></td></tr>")
    return "".join(out), changes


def _calls(said: dict, got: dict) -> list[tuple[str, str, str]]:
    pairs = []
    if said.get("content") or got.get("content"):
        pairs.append(("текст", got.get("content", ""), said.get("content", "")))
    delivered = list(got.get("tool_calls") or [])
    for call in said.get("tool_calls") or []:
        match = next((c for c in delivered if c["name"] == call["name"]), None)
        if match is not None:
            delivered.remove(match)
            pairs.append((call["name"], f"{match['name']}({match['arguments']})",
                          f"{call['name']}({call['arguments']})"))
        else:
            pairs.append((call["name"], "⛔ заблокировано политикой gateway",
                          f"{call['name']}({call['arguments']})"))
    return pairs


def build(journal: Path) -> str:
    records = [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines()
               if line.strip()]
    sections, seen, masked_total, restored_total = [], 0, 0, 0
    for n, rec in enumerate(records, 1):
        real_in, sent = rec.get("request_in", []), rec.get("request_out", [])
        new = []
        for real_msg, sent_msg in list(zip(real_in, sent, strict=False))[seen:]:
            for real_text, sent_text in zip(_texts(real_msg), _texts(sent_msg), strict=False):
                new.append((real_msg.get("role", ""), real_text, sent_text))
        seen = len(real_in)
        req_rows, req_changes = _rows(new, left_is_real=True)
        resp_rows, resp_changes = _rows(
            _calls(rec.get("model_said", {}), rec.get("agent_got", {})), left_is_real=False
        )
        masked_total += req_changes
        restored_total += resp_changes
        when = datetime.fromtimestamp(rec.get("ts", 0)).strftime("%H:%M:%S")
        sections.append(
            f"<h2>Обмен {n} · {when}</h2>"
            f"<h3>Запрос: у агента → ушло модели ({req_changes} замен)</h3>"
            f"<table><tr><th class=role></th><th>у агента (реальные данные)</th>"
            f"<th>ушло модели</th></tr>{req_rows}</table>"
            f"<h3>Ответ: модель написала → агент получил ({resp_changes} замен)</h3>"
            f"<table><tr><th class=role></th><th>модель написала</th>"
            f"<th>агент получил</th></tr>{resp_rows}</table>"
        )
    started = datetime.fromtimestamp(records[0]["ts"]).strftime("%d.%m.%Y %H:%M") if records else ""
    return f"""<!doctype html><html lang=ru><head><meta charset=utf-8>
<meta name=viewport content="width=device-width, initial-scale=1">
<title>Отчёт PII Gateway</title><style>{STYLE}</style></head><body>
<h1>Отчёт PII Gateway</h1>
<p class=meta>Сессия от {started}. Файл содержит реальные данные — храните его локально.</p>
<div class=stats><div class=stat><b>{len(records)}</b>обращений к модели</div>
<div class=stat><b>{masked_total}</b>замен в запросах</div>
<div class=stat><b>{restored_total}</b>восстановлений в ответах</div></div>
<p class=meta><mark class=real>зелёным</mark> — реальные данные,
<mark class=fake>жёлтым</mark> — что видела модель.</p>
{''.join(sections) or '<p>Обращений к модели не было.</p>'}
</body></html>"""


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    journal, out = Path(argv[0]), Path(argv[1])
    if not journal.exists():
        print(f"журнал не найден: {journal}", file=sys.stderr)
        return 2
    out.write_text(build(journal), encoding="utf-8")
    out.chmod(0o600)
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
