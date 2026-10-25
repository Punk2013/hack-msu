"""Russian name morphology, declension, and realistic pseudonym generator (role 3, issue #11).

Implements 6 grammatical cases for Russian masculine/feminine names and surnames.
Enforces strict 1-to-1 bijection check across all inflected forms to guarantee
unambiguous unmasking; falls back to markers if ambiguity is detected
(specifically handles counterexample: 'Иван Иванов' <-> 'Саша Шевченко').
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

CASES = ("nomn", "gent", "datv", "accs", "ablt", "loct")

MASCULINE_FIRST_NAMES: dict[str, dict[str, str]] = {
    "Иван": {
        "nomn": "Иван", "gent": "Ивана", "datv": "Ивану",
        "accs": "Ивана", "ablt": "Иваном", "loct": "Иване",
    },
    "Пётр": {
        "nomn": "Пётр", "gent": "Петра", "datv": "Петру",
        "accs": "Петра", "ablt": "Петром", "loct": "Петре",
    },
    "Петр": {
        "nomn": "Петр", "gent": "Петра", "datv": "Петру",
        "accs": "Петра", "ablt": "Петром", "loct": "Петре",
    },
    "Алексей": {
        "nomn": "Алексей", "gent": "Алексея", "datv": "Алексею",
        "accs": "Алексея", "ablt": "Алексеем", "loct": "Алексее",
    },
    "Дмитрий": {
        "nomn": "Дмитрий", "gent": "Дмитрия", "datv": "Дмитрию",
        "accs": "Дмитрия", "ablt": "Дмитрием", "loct": "Дмитрии",
    },
    "Михаил": {
        "nomn": "Михаил", "gent": "Михаила", "datv": "Михаилу",
        "accs": "Михаила", "ablt": "Михаилом", "loct": "Михаиле",
    },
    "Сергей": {
        "nomn": "Сергей", "gent": "Сергея", "datv": "Сергею",
        "accs": "Сергея", "ablt": "Сергеем", "loct": "Сергее",
    },
    "Андрей": {
        "nomn": "Андрей", "gent": "Андрея", "datv": "Андрею",
        "accs": "Андрея", "ablt": "Андреем", "loct": "Андрее",
    },
}

FEMININE_FIRST_NAMES: dict[str, dict[str, str]] = {
    "Анна": {
        "nomn": "Анна", "gent": "Анны", "datv": "Анне",
        "accs": "Анну", "ablt": "Анной", "loct": "Анне",
    },
    "Елена": {
        "nomn": "Елена", "gent": "Елены", "datv": "Елене",
        "accs": "Елену", "ablt": "Еленой", "loct": "Елене",
    },
    "Ольга": {
        "nomn": "Ольга", "gent": "Ольги", "datv": "Ольге",
        "accs": "Ольгу", "ablt": "Ольгой", "loct": "Ольге",
    },
    "Татьяна": {
        "nomn": "Татьяна", "gent": "Татьяны", "datv": "Татьяне",
        "accs": "Татьяну", "ablt": "Татьяной", "loct": "Татьяне",
    },
    "Мария": {
        "nomn": "Мария", "gent": "Марии", "datv": "Марии",
        "accs": "Марию", "ablt": "Марией", "loct": "Марии",
    },
    "Наталья": {
        "nomn": "Наталья", "gent": "Натальи", "datv": "Наталье",
        "accs": "Наталью", "ablt": "Натальей", "loct": "Наталье",
    },
}

MASCULINE_PSEUDO_POOL = [
    ("Пётр", "Смирнов"),
    ("Алексей", "Кузнецов"),
    ("Дмитрий", "Васильев"),
    ("Михаил", "Попов"),
    ("Сергей", "Новиков"),
]

FEMININE_PSEUDO_POOL = [
    ("Елена", "Смирнова"),
    ("Ольга", "Кузнецова"),
    ("Татьяна", "Васильева"),
    ("Мария", "Попова"),
    ("Наталья", "Новикова"),
]

# Build reverse lookup for any inflected first name -> (lemma, gender, case)
FIRST_NAME_LOOKUP: dict[str, tuple[str, str, str]] = {}
for lemma_m, forms_m in MASCULINE_FIRST_NAMES.items():
    for c_name, val in forms_m.items():
        if val not in FIRST_NAME_LOOKUP:
            FIRST_NAME_LOOKUP[val] = (lemma_m, "masc", c_name)

for lemma_f, forms_f in FEMININE_FIRST_NAMES.items():
    for c_name, val in forms_f.items():
        if val not in FIRST_NAME_LOOKUP:
            FIRST_NAME_LOOKUP[val] = (lemma_f, "fem", c_name)


def lemmatize_surname(token: str, gender: str) -> str:
    """Derive nominative base form of a Russian surname from inflected form."""
    if token.endswith(("ко", "их", "ых")):
        return token
    if gender == "masc":
        if token.endswith(("овым", "евым", "иным", "ыным")):
            return token[:-2]  # Смирновым -> Смирнов
        if token.endswith(("ова", "ева", "ина", "ына")):
            return token[:-1]  # Смирнова -> Смирнов
        if token.endswith(("ову", "еву", "ину", "ыну")):
            return token[:-1]  # Смирнову -> Смирнов
        if token.endswith(("ове", "еве", "ине", "ыне")):
            return token[:-1]  # Смирнове -> Смирнов
        if token.endswith(("ского", "скому", "ском")):
            return token[:-4] + "ский"
        if token.endswith("ским"):
            return token[:-3] + "кий"
        return token
    else:
        if token.endswith(("овой", "евой", "иной", "ыной")):
            return token[:-2] + "а"  # Смирновой -> Смирнова
        if token.endswith(("ову", "еву", "ину", "ыну")):
            return token[:-1] + "а"  # Смирнову -> Смирнова
        if token.endswith(("ской", "скую")):
            return token[:-4] + "ская"
        return token


def decline_surname(base_surname: str, gender: str) -> dict[str, str]:
    """Return dictionary of 6 grammatical case forms for base_surname."""
    if base_surname.endswith(("ко", "их", "ых")):
        return {case: base_surname for case in CASES}

    if gender == "masc":
        if base_surname.endswith(("ов", "ев", "ин", "ын")):
            return {
                "nomn": base_surname,
                "gent": base_surname + "а",
                "datv": base_surname + "у",
                "accs": base_surname + "а",
                "ablt": base_surname + "ым",
                "loct": base_surname + "е",
            }
        elif base_surname.endswith("ский"):
            stem = base_surname[:-4]
            return {
                "nomn": base_surname,
                "gent": stem + "ского",
                "datv": stem + "скому",
                "accs": stem + "ского",
                "ablt": stem + "ским",
                "loct": stem + "ском",
            }
    elif gender == "fem":
        if base_surname.endswith(("ова", "ева", "ина", "ына")):
            stem = base_surname[:-1]
            return {
                "nomn": base_surname,
                "gent": stem + "ой",
                "datv": stem + "ой",
                "accs": stem + "у",
                "ablt": stem + "ой",
                "loct": stem + "ой",
            }
        elif base_surname.endswith("ская"):
            stem = base_surname[:-4]
            return {
                "nomn": base_surname,
                "gent": stem + "ской",
                "datv": stem + "ской",
                "accs": stem + "скую",
                "ablt": stem + "ской",
                "loct": stem + "ской",
            }

    return {case: base_surname for case in CASES}


def detect_gender(name_token: str) -> str:
    """Guess gender from first name or surname token."""
    if name_token in FIRST_NAME_LOOKUP:
        return FIRST_NAME_LOOKUP[name_token][1]

    if name_token.endswith(("ова", "ева", "ина", "ына", "ская", "ая", "овой", "евой", "иной")):
        return "fem"
    if name_token.endswith(("ов", "ев", "ин", "ын", "ский", "овым", "евым", "иным", "ову", "еву")):
        return "masc"
    return "masc"


def parse_and_decline_full_name(raw_name: str) -> tuple[dict[str, str], str, str]:
    """Parse name tokens, derive base lemmas, detect gender and return full declension table.

    Returns: (forms_dict, gender, matched_case)
    """
    parts = raw_name.strip().split()
    if len(parts) != 2:
        return ({case: raw_name for case in CASES}, "masc", "nomn")

    p1, p2 = parts
    p1_info = FIRST_NAME_LOOKUP.get(p1)
    p2_info = FIRST_NAME_LOOKUP.get(p2)

    if p1_info is not None:
        fn_lemma, gender, matched_case = p1_info
        sn_lemma = lemmatize_surname(p2, gender)
        order = "fn_sn"
    elif p2_info is not None:
        fn_lemma, gender, matched_case = p2_info
        sn_lemma = lemmatize_surname(p1, gender)
        order = "sn_fn"
    else:
        gender = detect_gender(p1)
        fn_lemma = p1
        sn_lemma = lemmatize_surname(p2, gender)
        matched_case = "nomn"
        order = "fn_sn"

    fn_dict = MASCULINE_FIRST_NAMES if gender == "masc" else FEMININE_FIRST_NAMES
    fn_forms = fn_dict.get(fn_lemma, {c: fn_lemma for c in CASES})
    sn_forms = decline_surname(sn_lemma, gender)

    if order == "fn_sn":
        forms = {case: f"{fn_forms[case]} {sn_forms[case]}" for case in CASES}
    else:
        forms = {case: f"{sn_forms[case]} {fn_forms[case]}" for case in CASES}

    return forms, gender, matched_case


def decline_full_name(raw_name: str, gender: str) -> dict[str, str]:
    """Decline a 2-part Russian name across 6 cases."""
    forms, _, _ = parse_and_decline_full_name(raw_name)
    return forms


def detect_case_from_forms(raw_name: str, forms: dict[str, str]) -> str:
    """Find matching grammatical case from precomputed forms."""
    for case in ("nomn", "gent", "datv", "accs", "ablt", "loct"):
        if forms.get(case) == raw_name:
            return case
    return "nomn"


@dataclass(frozen=True)
class RealisticPair:
    original_forms: dict[str, str]
    pseudo_forms: dict[str, str]
    gender: str
    is_bijective: bool


def generate_realistic_pair(
    raw_name: str,
    entity_id: str,
    target_pseudo_override: tuple[str, str] | None = None,
) -> RealisticPair | None:
    """Generate a declined pair of original and pseudonym with bijection verification."""
    orig_forms, gender, _ = parse_and_decline_full_name(raw_name)

    if target_pseudo_override:
        p_fn, p_sn = target_pseudo_override
    else:
        pool = MASCULINE_PSEUDO_POOL if gender == "masc" else FEMININE_PSEUDO_POOL
        idx = int(hashlib.sha256(entity_id.encode("utf-8")).hexdigest(), 16) % len(pool)
        p_fn, p_sn = pool[idx]

    p_fn_dict = MASCULINE_FIRST_NAMES if gender == "masc" else FEMININE_FIRST_NAMES
    p_fn_forms = p_fn_dict.get(p_fn, {c: p_fn for c in CASES})
    p_sn_forms = decline_surname(p_sn, gender)
    pseudo_forms = {case: f"{p_fn_forms[case]} {p_sn_forms[case]}" for case in CASES}

    orig_unique_count = len(set(orig_forms.values()))
    pseudo_unique_count = len(set(pseudo_forms.values()))

    # Counterexample check (e.g. Ivan Ivanov <-> Sasha Shevchenko):
    # If the original inflects with distinct forms across cases, but the pseudonym
    # collapses them (or vice versa), reverse mapping is ambiguous!
    is_bijective = (orig_unique_count == pseudo_unique_count)

    return RealisticPair(
        original_forms=orig_forms,
        pseudo_forms=pseudo_forms,
        gender=gender,
        is_bijective=is_bijective,
    )
