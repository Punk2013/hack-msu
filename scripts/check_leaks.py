#!/usr/bin/env python3
"""Scan gateway request dumps for values from the synthetic demo cards.

    scripts/check_leaks.py <run-dir>/dumps [demo/clients]

Exit codes: 0 — no card value found, 1 — leak found, 2 — nothing to scan.
Covered: every "Поле: значение" line of each card plus surname case forms.
Only the listed values are checked: zero matches is not proof of no leaks at all.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

FIELD = re.compile(r"^(?:ФИО|Email|Телефон|Паспорт|Клиент|Менеджер): (.+?)\.?$", re.MULTILINE)
SURNAME_ENDINGS = ("", "а", "у", "ым", "е", "ой", "ой")


def card_values(cards: Path) -> set[str]:
    values: set[str] = set()
    for card in sorted(cards.glob("*.txt")):
        for value in FIELD.findall(card.read_text(encoding="utf-8")):
            values.add(value.strip())
            words = value.split()
            if len(words) == 3 and all(w[:1].isupper() for w in words):  # ФИО
                stem = words[-1].removesuffix("а")
                values.update(stem + ending for ending in SURNAME_ENDINGS)
    return values


def main(argv: list[str]) -> int:
    if not 1 <= len(argv) <= 2:
        print(__doc__.strip().splitlines()[2], file=sys.stderr)
        return 2
    dumps = Path(argv[0])
    default_cards = Path(__file__).resolve().parents[1] / "demo" / "clients"
    cards = Path(argv[1]) if len(argv) == 2 else default_cards
    requests = sorted(dumps.glob("*-request.json")) if dumps.is_dir() else []
    values = card_values(cards)
    if not requests or not values:
        print(f"нечего проверять: запросов {len(requests)}, значений {len(values)}",
              file=sys.stderr)
        return 2
    leaks = {}
    for value in sorted(values):
        pattern = re.compile(rf"(?<!\w){re.escape(value)}(?!\w)")
        hits = sum(len(pattern.findall(r.read_text(encoding="utf-8"))) for r in requests)
        if hits:
            leaks[value] = hits
    print(f"запросов: {len(requests)}, проверено значений: {len(values)}")
    if leaks:
        for value, hits in leaks.items():
            print(f"УТЕЧКА: {value!r} — {hits} раз")
        return 1
    print("утечек не найдено среди проверенных значений")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
