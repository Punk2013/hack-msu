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
    "Артём": {
        "nomn": "Артём", "gent": "Артёма", "datv": "Артёму",
        "accs": "Артёма", "ablt": "Артёмом", "loct": "Артёме",
    },
    "Илья": {
        "nomn": "Илья", "gent": "Ильи", "datv": "Илье",
        "accs": "Илью", "ablt": "Ильёй", "loct": "Илье",
    },
    "Максим": {
        "nomn": "Максим", "gent": "Максима", "datv": "Максиму",
        "accs": "Максима", "ablt": "Максимом", "loct": "Максиме",
    },
    "Николай": {
        "nomn": "Николай", "gent": "Николая", "datv": "Николаю",
        "accs": "Николая", "ablt": "Николаем", "loct": "Николае",
    },
    "Павел": {
        "nomn": "Павел", "gent": "Павла", "datv": "Павлу",
        "accs": "Павла", "ablt": "Павлом", "loct": "Павле",
    },
    "Роман": {
        "nomn": "Роман", "gent": "Романа", "datv": "Роману",
        "accs": "Романа", "ablt": "Романом", "loct": "Романе",
    },
    "Виктор": {
        "nomn": "Виктор", "gent": "Виктора", "datv": "Виктору",
        "accs": "Виктора", "ablt": "Виктором", "loct": "Викторе",
    },
    "Евгений": {
        "nomn": "Евгений", "gent": "Евгения", "datv": "Евгению",
        "accs": "Евгения", "ablt": "Евгением", "loct": "Евгении",
    },
    "Олег": {
        "nomn": "Олег", "gent": "Олега", "datv": "Олегу",
        "accs": "Олега", "ablt": "Олегом", "loct": "Олеге",
    },
    "Игорь": {
        "nomn": "Игорь", "gent": "Игоря", "datv": "Игорю",
        "accs": "Игоря", "ablt": "Игорем", "loct": "Игоре",
    },
    "Василий": {
        "nomn": "Василий", "gent": "Василия", "datv": "Василию",
        "accs": "Василия", "ablt": "Василием", "loct": "Василии",
    },
    "Константин": {
        "nomn": "Константин", "gent": "Константина", "datv": "Константину",
        "accs": "Константина", "ablt": "Константином", "loct": "Константине",
    },
    "Владимир": {
        "nomn": "Владимир", "gent": "Владимира", "datv": "Владимиру",
        "accs": "Владимира", "ablt": "Владимиром", "loct": "Владимире",
    },
    "Юрий": {
        "nomn": "Юрий", "gent": "Юрия", "datv": "Юрию",
        "accs": "Юрия", "ablt": "Юрием", "loct": "Юрии",
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
    "Ирина": {
        "nomn": "Ирина", "gent": "Ирины", "datv": "Ирине",
        "accs": "Ирину", "ablt": "Ириной", "loct": "Ирине",
    },
    "Светлана": {
        "nomn": "Светлана", "gent": "Светланы", "datv": "Светлане",
        "accs": "Светлану", "ablt": "Светланой", "loct": "Светлане",
    },
    "Екатерина": {
        "nomn": "Екатерина", "gent": "Екатерины", "datv": "Екатерине",
        "accs": "Екатерину", "ablt": "Екатериной", "loct": "Екатерине",
    },
    "Юлия": {
        "nomn": "Юлия", "gent": "Юлии", "datv": "Юлии",
        "accs": "Юлию", "ablt": "Юлией", "loct": "Юлии",
    },
    "Алина": {
        "nomn": "Алина", "gent": "Алины", "datv": "Алине",
        "accs": "Алину", "ablt": "Алиной", "loct": "Алине",
    },
    "Ксения": {
        "nomn": "Ксения", "gent": "Ксении", "datv": "Ксении",
        "accs": "Ксению", "ablt": "Ксенией", "loct": "Ксении",
    },
    "Дарья": {
        "nomn": "Дарья", "gent": "Дарьи", "datv": "Дарье",
        "accs": "Дарью", "ablt": "Дарьей", "loct": "Дарье",
    },
    "Марина": {
        "nomn": "Марина", "gent": "Марины", "datv": "Марине",
        "accs": "Марину", "ablt": "Мариной", "loct": "Марине",
    },
    "Виктория": {
        "nomn": "Виктория", "gent": "Виктории", "datv": "Виктории",
        "accs": "Викторию", "ablt": "Викторией", "loct": "Виктории",
    },
    "Полина": {
        "nomn": "Полина", "gent": "Полины", "datv": "Полине",
        "accs": "Полину", "ablt": "Полиной", "loct": "Полине",
    },
    "Алёна": {
        "nomn": "Алёна", "gent": "Алёны", "datv": "Алёне",
        "accs": "Алёну", "ablt": "Алёной", "loct": "Алёне",
    },
    "Вера": {
        "nomn": "Вера", "gent": "Веры", "datv": "Вере",
        "accs": "Веру", "ablt": "Верой", "loct": "Вере",
    },
    "Надежда": {
        "nomn": "Надежда", "gent": "Надежды", "datv": "Надежде",
        "accs": "Надежду", "ablt": "Надеждой", "loct": "Надежде",
    },
    "Любовь": {
        "nomn": "Любовь", "gent": "Любови", "datv": "Любови",
        "accs": "Любовь", "ablt": "Любовью", "loct": "Любови",
    },
}

MASCULINE_PSEUDO_POOL = [
    ("Пётр", "Смирнов"),
    ("Алексей", "Кузнецов"),
    ("Дмитрий", "Васильев"),
    ("Михаил", "Попов"),
    ("Сергей", "Новиков"),
    ("Андрей", "Морозов"),
    ("Артём", "Волков"),
    ("Илья", "Алексеев"),
    ("Максим", "Лебедев"),
    ("Николай", "Семёнов"),
    ("Павел", "Егоров"),
    ("Роман", "Павлов"),
    ("Виктор", "Козлов"),
    ("Евгений", "Степанов"),
    ("Олег", "Николаев"),
    ("Игорь", "Орлов"),
    ("Василий", "Андреев"),
    ("Константин", "Макаров"),
    ("Владимир", "Никитин"),
    ("Юрий", "Захаров"),
]

FEMININE_PSEUDO_POOL = [
    ("Елена", "Смирнова"),
    ("Ольга", "Кузнецова"),
    ("Татьяна", "Васильева"),
    ("Мария", "Попова"),
    ("Наталья", "Новикова"),
    ("Ирина", "Морозова"),
    ("Светлана", "Волкова"),
    ("Екатерина", "Алексеева"),
    ("Анна", "Лебедева"),
    ("Юлия", "Семёнова"),
    ("Алина", "Егорова"),
    ("Ксения", "Павлова"),
    ("Дарья", "Козлова"),
    ("Марина", "Степанова"),
    ("Виктория", "Николаева"),
    ("Полина", "Орлова"),
    ("Алёна", "Андреева"),
    ("Вера", "Макарова"),
    ("Надежда", "Никитина"),
    ("Любовь", "Захарова"),
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
        elif base_surname.endswith("цкий"):
            stem = base_surname[:-4]
            return {
                "nomn": base_surname,
                "gent": stem + "цкого",
                "datv": stem + "цкому",
                "accs": stem + "цкого",
                "ablt": stem + "цким",
                "loct": stem + "цком",
            }
        return {case: base_surname for case in CASES}
    else:
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
    """Heuristic gender detection based on token endings."""
    if name_token in FEMININE_FIRST_NAMES:
        return "fem"
    if name_token in MASCULINE_FIRST_NAMES:
        return "masc"
    if name_token.endswith(("а", "я", "ия")):
        return "fem"
    if name_token.endswith(("ова", "ева", "ина", "ына", "ская", "ая", "овой", "евой", "иной")):
        return "fem"
    if name_token.endswith(("ов", "ев", "ин", "ын", "ский", "овым", "евым", "иным", "ову", "еву")):
        return "masc"
    return "masc"


def lemmatize_patronymic(token: str) -> tuple[str, str, str]:
    """Derive base patronymic, gender, and case from inflected token.
    Returns: (base_patronymic, gender, case)
    """
    # Masculine:
    if token.endswith(("ович", "евич", "ич")):
        return (token, "masc", "nomn")
    if token.endswith(("овича", "евича", "ича")):
        return (token[:-1], "masc", "gent")
    if token.endswith(("овичу", "евичу", "ичу")):
        return (token[:-1], "masc", "datv")
    if token.endswith(("овичем", "евичем", "ичем")):
        return (token[:-2], "masc", "ablt")
    if token.endswith(("овиче", "евиче", "иче")):
        return (token[:-1], "masc", "loct")

    # Feminine:
    if token.endswith(("овна", "евна", "ична", "инична")):
        return (token, "fem", "nomn")
    if token.endswith(("овны", "евны", "ичны", "иничны")):
        return (token[:-1] + "а", "fem", "gent")
    if token.endswith(("овне", "евне", "ичне", "иничне")):
        return (token[:-1] + "а", "fem", "datv")
    if token.endswith(("овну", "евну", "ичну", "иничну")):
        return (token[:-1] + "а", "fem", "accs")
    if token.endswith(("овной", "евной", "ичной", "иничной")):
        return (token[:-2] + "а", "fem", "ablt")

    return (token, "masc", "nomn")


def decline_patronymic(base_patronymic: str, gender: str) -> dict[str, str]:
    """Return dictionary of 6 grammatical case forms for base_patronymic."""
    if gender == "masc":
        stem = base_patronymic
        return {
            "nomn": stem,
            "gent": stem + "а",
            "datv": stem + "у",
            "accs": stem + "а",
            "ablt": stem + "ем",
            "loct": stem + "е",
        }
    else:
        stem = base_patronymic[:-1]  # drop 'а'
        return {
            "nomn": base_patronymic,
            "gent": stem + "ы",
            "datv": stem + "е",
            "accs": stem + "у",
            "ablt": stem + "ой",
            "loct": stem + "е",
        }


def parse_and_decline_full_name(raw_name: str) -> tuple[dict[str, str], str, str]:
    """Parse name tokens, derive base lemmas, detect gender and return full declension table.

    Returns: (forms_dict, gender, matched_case)
    """
    parts = raw_name.strip().split()
    if len(parts) == 1:
        return ({case: raw_name for case in CASES}, detect_gender(raw_name), "nomn")

    if len(parts) == 3:
        patr_idx = -1
        patr_base = ""
        patr_gender = "masc"
        matched_case = "nomn"
        patr_suffixes = (
            "ович", "евич", "ич", "овича", "евича", "ича", "овичу", "евичу", "ичу",
            "овичем", "евичем", "ичем", "овиче", "евиче", "иче",
            "овна", "евна", "ична", "инична", "овны", "евны", "ичны", "иничны",
            "овне", "евне", "ичне", "иничне", "овну", "евну", "ичну", "иничну",
            "овной", "евной", "ичной", "иничной"
        )
        for i, part in enumerate(parts):
            if any(part.endswith(end) for end in patr_suffixes):
                patr_base, patr_gender, matched_case = lemmatize_patronymic(part)
                patr_idx = i
                break

        if patr_idx != -1:
            gender = patr_gender
            patr_forms = decline_patronymic(patr_base, gender)
            other_indices = [j for j in range(3) if j != patr_idx]
            fn_token, sn_token = parts[other_indices[0]], parts[other_indices[1]]
            p1_info = FIRST_NAME_LOOKUP.get(fn_token)
            p2_info = FIRST_NAME_LOOKUP.get(sn_token)
            if p2_info is not None and p1_info is None:
                fn_token, sn_token = sn_token, fn_token
                fn_lemma = p2_info[0]
            elif p1_info is not None:
                fn_lemma = p1_info[0]
            else:
                fn_lemma = fn_token

            sn_lemma = lemmatize_surname(sn_token, gender)
            fn_dict = MASCULINE_FIRST_NAMES if gender == "masc" else FEMININE_FIRST_NAMES
            fn_forms = fn_dict.get(fn_lemma, {c: fn_lemma for c in CASES})
            sn_forms = decline_surname(sn_lemma, gender)

            if patr_idx == 1:
                forms = {
                    case: f"{fn_forms[case]} {patr_forms[case]} {sn_forms[case]}"
                    for case in CASES
                }
            elif patr_idx == 2:
                forms = {
                    case: f"{sn_forms[case]} {fn_forms[case]} {patr_forms[case]}"
                    for case in CASES
                }
            else:
                forms = {
                    case: f"{fn_forms[case]} {patr_forms[case]} {sn_forms[case]}"
                    for case in CASES
                }
            return forms, gender, matched_case

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
    used_pseudos: set[str] | None = None,
) -> RealisticPair | None:
    """Generate a declined pair of original and pseudonym with bijection
    verification and collision checking.
    """
    orig_forms, gender, _ = parse_and_decline_full_name(raw_name)
    used = used_pseudos or set()

    if target_pseudo_override:
        candidates = [target_pseudo_override]
    else:
        pool = MASCULINE_PSEUDO_POOL if gender == "masc" else FEMININE_PSEUDO_POOL
        start_idx = int(hashlib.sha256(entity_id.encode("utf-8")).hexdigest(), 16) % len(pool)
        candidates = [pool[(start_idx + i) % len(pool)] for i in range(len(pool))]

    p_fn_dict = MASCULINE_FIRST_NAMES if gender == "masc" else FEMININE_FIRST_NAMES

    for p_fn, p_sn in candidates:
        p_fn_forms = p_fn_dict.get(p_fn, {c: p_fn for c in CASES})
        p_sn_forms = decline_surname(p_sn, gender)
        pseudo_forms = {case: f"{p_fn_forms[case]} {p_sn_forms[case]}" for case in CASES}

        # Check collision with already allocated pseudonyms in project
        if any(form_val in used for form_val in pseudo_forms.values()):
            continue
        # Also do not collide with any original forms of this entity
        if any(form_val in set(orig_forms.values()) for form_val in pseudo_forms.values()):
            continue

        orig_unique_count = len(set(orig_forms.values()))
        pseudo_unique_count = len(set(pseudo_forms.values()))

        # Equivalence relation bijection check across cases
        is_bijective = (orig_unique_count == pseudo_unique_count)
        if is_bijective:
            for c1 in CASES:
                for c2 in CASES:
                    if (orig_forms[c1] == orig_forms[c2]) != (pseudo_forms[c1] == pseudo_forms[c2]):
                        is_bijective = False
                        break
                if not is_bijective:
                    break

        if not target_pseudo_override and not is_bijective:
            continue

        return RealisticPair(
            original_forms=orig_forms,
            pseudo_forms=pseudo_forms,
            gender=gender,
            is_bijective=is_bijective,
        )

    # All candidates in pool collided or were non-bijective
    return None
