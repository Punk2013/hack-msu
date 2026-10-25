"""Side-by-side report for the demo: what the provider saw vs what the user has.

Reads gateway dumps (PII_DUMP_DIR, synthetic data only) and restores each
outbound message with the project vault, highlighting every replacement.

    python -m pii_gateway.report --dumps <dir> --project demo --out report.html
"""

from __future__ import annotations

import argparse
import difflib
import html
import json
import re
from pathlib import Path

from pii_gateway.core.types import Context
from pii_gateway.pseudo import restore

TOKENS = re.compile(r"\w+|\W")


def _diff(masked: str, real: str) -> tuple[str, str, int]:
    a, b = TOKENS.findall(masked), TOKENS.findall(real)
    left, right, changes = [], [], 0
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        x, y = html.escape("".join(a[i1:i2])), html.escape("".join(b[j1:j2]))
        if op == "equal":
            left.append(x)
            right.append(y)
        else:
            changes += 1
            left.append(f"<mark class=fake>{x}</mark>")
            right.append(f"<mark class=real>{y}</mark>")
    return "".join(left), "".join(right), changes


def _texts(message: dict) -> list[str]:
    content = message.get("content")
    if isinstance(content, str):
        out = [content]
    elif isinstance(content, list):
        out = [p.get("text", "") for p in content if isinstance(p, dict)]
    else:
        out = []
    for call in message.get("tool_calls") or []:
        out.append(f"{call['function']['name']}({call['function']['arguments']})")
    return [t for t in out if t]


def build(dumps: Path, project: str) -> str:
    ctx = Context(project)
    requests = sorted(dumps.glob("*-request.json"))
    if not requests:
        raise SystemExit(f"no request dumps in {dumps}")
    last = json.loads(requests[-1].read_text(encoding="utf-8"))  # full history of the run
    rows, total = [], 0
    for message in last["messages"]:
        for text in _texts(message):
            left, right, changes = _diff(text, restore(text, ctx).text)
            total += changes
            rows.append(
                f"<tr><td class=role>{html.escape(message['role'])}</td>"
                f"<td><pre>{left}</pre></td><td><pre>{right}</pre></td></tr>"
            )
    return f"""<!doctype html><html lang=ru><head><meta charset=utf-8>
<meta name=viewport content="width=device-width, initial-scale=1"><title>PII Gateway</title>
<style>
:root{{--bg:#fff;--fg:#1d1d1f;--muted:#6e6e73;--line:#e5e5ea;--fake:#ffe8b3;--real:#c9f2d0}}
@media (prefers-color-scheme:dark){{:root{{--bg:#111;--fg:#f2f2f7;--muted:#a1a1a6;
--line:#2c2c2e;--fake:#5c4300;--real:#0f4d1d}}}}
body{{margin:0;padding:16px;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui}}
h1{{font-size:20px;margin:0 0 4px}} p{{color:var(--muted);margin:0 0 16px}}
table{{width:100%;border-collapse:collapse;table-layout:fixed}}
th,td{{border-top:1px solid var(--line);padding:8px;vertical-align:top;text-align:left}}
th.role,td.role{{width:80px;color:var(--muted)}}
pre{{margin:0;white-space:pre-wrap;word-break:break-word;font:13px/1.45 ui-monospace,monospace}}
mark{{color:inherit;border-radius:3px;padding:0 2px}} .fake{{background:var(--fake)}}
.real{{background:var(--real)}}
</style></head><body>
<h1>Что увидел провайдер и что осталось у пользователя</h1>
<p>Запросов через gateway: {len(requests)}. Изменённых фрагментов в итоговой истории: {total}.
Слева — исходящий запрос (дамп gateway), справа — восстановление по локальному vault.</p>
<table><tr><th class=role>роль</th><th>провайдер видел</th><th>у пользователя</th></tr>
{''.join(rows)}</table></body></html>"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dumps", type=Path, required=True)
    parser.add_argument("--project", default="default")
    parser.add_argument("--out", type=Path, default=Path("report.html"))
    args = parser.parse_args(argv)
    args.out.write_text(build(args.dumps, args.project), encoding="utf-8")
    print(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
