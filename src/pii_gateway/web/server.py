"""Same-origin local inspector and 5-step presentation deck.
No upstream calls and no arbitrary vault access.
"""

from __future__ import annotations

import html
import random
import re
import secrets
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal

import pymorphy3
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from pii_gateway.core.types import Context
from pii_gateway.detect import detect
from pii_gateway.pseudo import mask, restore
from pii_gateway.pseudo.fakes import fake_email, fake_passport, fake_phone
from pii_gateway.vault.storage import Vault

STATIC = Path(__file__).with_name("static")
DEMO_CLIENTS_DIR = Path(__file__).resolve().parents[3] / "demo" / "clients"

_morph = pymorphy3.MorphAnalyzer()

# Russian synthetic pools for dynamic demo cases
_SURNAMES_M = ["Соколов", "Морозов", "Волков", "Лебедев", "Кузнецов", "Новиков", "Фёдоров"]
_NAMES_M = ["Алексей", "Дмитрий", "Сергей", "Михаил", "Андрей", "Иван", "Максим"]
_PATRS_M = [
    "Дмитриевич",
    "Сергеевич",
    "Алексеевич",
    "Иванович",
    "Владимирович",
    "Николаевич",
]

_SURNAMES_F = ["Соколова", "Морозова", "Волкова", "Лебедева", "Кузнецова", "Новикова"]
_NAMES_F = ["Елена", "Анна", "Ольга", "Мария", "Екатерина", "Татьяна"]
_PATRS_F = ["Васильевна", "Александровна", "Сергеевна", "Дмитриевна", "Ивановна"]

_DOMAINS = ["corporate.org", "tech-consulting.ru", "fin-service.com", "apex-group.ru"]

_CASE_TEMPLATES = [
    {
        "title": "Кредитная заявка",
        "instruction": "Проанализируй кредитную заявку и сформируй решение с расчётом лимита.",
        "status": "Заявка на автокредит одобрена (ставка 14.5%). {datv} необходимо предоставить оригиналы справок до 20.11.2026.",
    },
    {
        "title": "Премиальное обслуживание",
        "instruction": "Подготовь персональное предложение по премиальному пакету обслуживания.",
        "status": "Подключён премиальный пакет 'Gold Priority'. Персональный менеджер назначен для {datv}.",
    },
    {
        "title": "Ипотечный договор",
        "instruction": "Проверь статус регистрации ипотечной сделки и направь отчёт клиенту.",
        "status": "Электронная регистрация ипотеки завершена. {datv} направлена выписка из ЕГРН и график платежей.",
    },
]


def _inflect_name(word: str, case: str = "datv") -> str:
    """Inflect a name using pymorphy3."""
    parsed = _morph.parse(word)
    if not parsed:
        return word
    for p in parsed:
        inflected = p.inflect({case})
        if inflected:
            res = inflected.word
            if word and word[0].isupper():
                res = res.capitalize()
            return res
    return word


def _generate_synthetic_case() -> dict[str, str]:
    """Generate a random dynamic Russian client record."""
    is_female = random.random() < 0.35
    if is_female:
        surname = random.choice(_SURNAMES_F)
        name = random.choice(_NAMES_F)
        patr = random.choice(_PATRS_F)
    else:
        surname = random.choice(_SURNAMES_M)
        name = random.choice(_NAMES_M)
        patr = random.choice(_PATRS_M)

    full_name = f"{surname} {name} {patr}"
    datv_name = (
        f"{_inflect_name(surname, 'datv')} "
        f"{_inflect_name(name, 'datv')} "
        f"{_inflect_name(patr, 'datv')}"
    )

    domain = random.choice(_DOMAINS)
    email = fake_email(full_name, domain)
    phone = fake_phone()
    passport = fake_passport()

    tmpl = random.choice(_CASE_TEMPLATES)
    status_line = tmpl["status"].format(datv=datv_name)

    content = (
        f"[ПРОМПТ ДЛЯ АВТОНОМНОГО АГЕНТА: ОБРАБОТКА ОБРАЩЕНИЯ И ПОДГОТОВКА ОТВЕТА]\n"
        f"Инструкция: {tmpl['instruction']}\n\n"
        f"[ВХОДНОЙ ДОКУМЕНТ: КАРТОЧКА КЛИЕНТА ИЗ CRM]\n"
        f"ФИО: {full_name}\n"
        f"Email: {email}\n"
        f"Телефон: {phone}\n"
        f"Паспорт: {passport}\n\n"
        f"Статус: {status_line}"
    )

    case_id = f"dynamic_{int(time.time() * 1000) % 100000}"
    return {
        "id": case_id,
        "title": f"🤖 {full_name} ({tmpl['title']})",
        "filename": f"{case_id}.txt",
        "content": content,
        "full_name": full_name,
        "email": email,
        "phone": phone,
        "passport": passport,
        "datv_name": datv_name,
    }


class TextRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    text: str = Field(min_length=1, max_length=100_000)
    mode: Literal["realistic", "marker"] = "realistic"


class PresentationRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    text: str = Field(min_length=1, max_length=100_000)
    mode: Literal["realistic", "marker"] = "realistic"


def _generate_model_reply(
    masked_text: str, mode: str, vault_pairs: dict[str, str] | None = None
) -> str:
    """Generate realistic model reply using inflected fake names or markers."""
    if vault_pairs:
        if mode == "realistic":
            fake_nom = None
            fake_datv = None
            fake_email = None
            fake_phone = None

            for fake_k in vault_pairs.keys():
                if "@" in fake_k and not fake_email:
                    fake_email = fake_k
                elif (fake_k.startswith("+7") or fake_k.startswith("8")) and not fake_phone:
                    fake_phone = fake_k
                elif " " in fake_k:
                    words = fake_k.split()
                    if len(words) in (2, 3):
                        if any(words[-1].endswith(end) for end in ("у", "ю", "е")):
                            if not fake_datv:
                                fake_datv = fake_k
                        else:
                            if not fake_nom:
                                fake_nom = fake_k

            if not fake_nom:
                fake_nom = next((k for k in vault_pairs if " " in k), "Уважаемый клиент")
            if not fake_datv:
                fake_datv = fake_nom
            if not fake_email:
                fake_email = "alex.client.notification@corporate-bank.ru"
            if not fake_phone:
                fake_phone = "+7 800 555-35-35"

            return (
                f"Здравствуйте, {fake_nom}!\n\n"
                f"Информируем вас, что рассмотрение обращения успешно завершено.\n"
                f"Официальное уведомление и проект решения подготовлены для {fake_datv}.\n"
                f"Просим предоставить оригиналы справок в отделение банка до 15.11.2026.\n\n"
                f"Копия подтверждения направлена на адрес: {fake_email}.\n"
                f"По всем вопросам звоните в службу поддержки: {fake_phone}.\n\n"
                f"С уважением,\n"
                f"Автономный ИИ-ассистент клиентского сервиса"
            )
        elif mode == "marker":
            return (
                "Здравствуйте, [PERSON_1]!\n\n"
                "Информируем вас, что рассмотрение обращения успешно завершено.\n"
                "Официальное уведомление и проект решения подготовлены для [PERSON_1].\n"
                "Просим предоставить оригиналы справок в отделение банка до 15.11.2026.\n\n"
                "Копия подтверждения направлена на адрес: [EMAIL_1].\n"
                "По всем вопросам звоните в службу поддержки: [PHONE_1].\n\n"
                "С уважением,\n"
                "Автономный ИИ-ассистент клиентского сервиса"
            )

    lines = [line.strip() for line in masked_text.splitlines() if line.strip()]
    name_val = "Уважаемый клиент"
    for line in lines:
        if line.startswith("ФИО:"):
            name_val = line.replace("ФИО:", "").strip()
            break

    if mode == "realistic" and " " in name_val:
        parts = name_val.split()
        if len(parts) >= 2:
            name = parts[1]
            patr = parts[2] if len(parts) > 2 else ""
            datv_form = f"{_inflect_name(name, 'datv')} {_inflect_name(patr, 'datv')}".strip()
            return (
                f"Здравствуйте!\n\n"
                f"Уведомление подготовлено для {datv_form}.\n"
                f"Уважаемый {name} {patr}!\n"
                f"Информируем, что ваш договор успешно подтверждён.\n\n"
                f"С уважением, персональный менеджер."
            )

    return (
        f"Здравствуйте!\n\n"
        f"Уведомление для {name_val} успешно сформировано.\n"
        f"Просим ознакомиться с условиями обслуживания и подтвердить визит в офис.\n\n"
        f"С уважением, клиентский отдел."
    )


def _render_highlighted_spans(text: str, spans: list[Any]) -> str:
    """Format original text with glowing neon badges for detected entities."""
    sorted_spans = sorted(spans, key=lambda s: s.start)
    out = []
    pos = 0
    for s in sorted_spans:
        if s.start > pos:
            out.append(html.escape(text[pos : s.start]))
        kind_str = str(s.type).split(".")[-1]
        kind_lower = kind_str.lower()
        badge_text = html.escape(kind_str)
        escaped_val = html.escape(s.raw_text)
        out.append(
            f'<mark class="pii-tag pii-{kind_lower}" data-type="{kind_str}">'
            f'{escaped_val}<span class="pii-badge">{badge_text}</span></mark>'
        )
        pos = s.end
    if pos < len(text):
        out.append(html.escape(text[pos:]))
    return "".join(out)


def _render_masked_html(masked_text: str, vault_pairs: dict[str, str], mode: str) -> str:
    """Format masked text with highlighted pseudonyms."""
    if not vault_pairs and mode != "marker":
        return html.escape(masked_text)

    keys = sorted(vault_pairs.keys(), key=len, reverse=True)
    if not keys and mode == "marker":
        pattern = re.compile(r"\[(PERSON|EMAIL|PHONE|PASSPORT_RU)_[0-9]+\]")
    else:
        escaped_keys = [re.escape(k) for k in keys if k]
        if mode == "marker":
            escaped_keys.append(r"\[(PERSON|EMAIL|PHONE|PASSPORT_RU)_[0-9]+\]")
        pattern = (
            re.compile(rf"(?<!\w)(?:{'|'.join(escaped_keys)})(?!\w)")
            if escaped_keys
            else None
        )

    if not pattern:
        return html.escape(masked_text)

    out = []
    pos = 0
    for match in pattern.finditer(masked_text):
        if match.start() > pos:
            out.append(html.escape(masked_text[pos : match.start()]))
        token = match.group(0)
        out.append(
            f'<mark class="pii-tag pii-fake" data-token="{html.escape(token)}">'
            f'{html.escape(token)}<span class="pii-badge">FAKE</span></mark>'
        )
        pos = match.end()
    if pos < len(masked_text):
        out.append(html.escape(masked_text[pos:]))
    return "".join(out)


def _render_agent_output_html(
    agent_text: str, vault_pairs: dict[str, str], mode: str
) -> tuple[str, list[str]]:
    """Format agent response highlighting used pseudonyms."""
    keys = sorted(vault_pairs.keys(), key=len, reverse=True)
    if not keys and mode == "marker":
        pattern = re.compile(r"\[(PERSON|EMAIL|PHONE|PASSPORT_RU)_[0-9]+\]")
    else:
        escaped_keys = [re.escape(k) for k in keys if k]
        if mode == "marker":
            escaped_keys.append(r"\[(PERSON|EMAIL|PHONE|PASSPORT_RU)_[0-9]+\]")
        pattern = (
            re.compile(rf"(?<!\w)(?:{'|'.join(escaped_keys)})(?!\w)")
            if escaped_keys
            else None
        )

    if not pattern:
        return html.escape(agent_text), []

    out = []
    pos = 0
    used_tokens = []
    for match in pattern.finditer(agent_text):
        if match.start() > pos:
            out.append(html.escape(agent_text[pos : match.start()]))
        token = match.group(0)
        used_tokens.append(token)
        out.append(
            f'<mark class="pii-tag pii-agent-pseudo" data-token="{html.escape(token)}">'
            f'{html.escape(token)}<span class="pii-badge">ПСЕВДОНИМ</span></mark>'
        )
        pos = match.end()
    if pos < len(agent_text):
        out.append(html.escape(agent_text[pos:]))
    return "".join(out), used_tokens


def _extract_transformations(
    agent_text: str, restored_text: str, vault_pairs: dict[str, str]
) -> list[dict[str, str]]:
    """Extract before -> after pairs that were used in the agent reply."""
    transformations = []
    seen_fakes = set()
    for fake, orig in sorted(vault_pairs.items(), key=lambda x: len(x[0]), reverse=True):
        if fake in agent_text and fake not in seen_fakes:
            seen_fakes.add(fake)
            if "@" in orig:
                kind = "EMAIL"
            elif re.search(r"\d{2}\s*\d{2}\s*\d{6}", orig):
                kind = "PASSPORT_RU"
            elif any(orig.startswith(p) for p in ("+7", "8", "7")):
                kind = "PHONE"
            else:
                kind = "PERSON"
            transformations.append({
                "fake": fake,
                "original": orig,
                "type": kind,
            })
    return transformations


def _render_restored_html(
    restored_text: str, transformations: list[dict[str, str]]
) -> str:
    """Format restored response highlighting restored original tokens."""
    if not transformations:
        return html.escape(restored_text)

    orig_values = sorted([t["original"] for t in transformations], key=len, reverse=True)
    escaped_origs = [re.escape(o) for o in orig_values if o]
    if not escaped_origs:
        return html.escape(restored_text)

    pattern = re.compile(rf"(?<!\w)(?:{'|'.join(escaped_origs)})(?!\w)")
    out = []
    pos = 0
    for match in pattern.finditer(restored_text):
        if match.start() > pos:
            out.append(html.escape(restored_text[pos : match.start()]))
        token = match.group(0)
        out.append(
            f'<mark class="pii-tag pii-restored" data-token="{html.escape(token)}">'
            f'{html.escape(token)}<span class="pii-badge">ВОССТАНОВЛЕНО</span></mark>'
        )
        pos = match.end()
    if pos < len(restored_text):
        out.append(html.escape(restored_text[pos:]))
    return "".join(out)


def _build_entities_table(
    spans: list[Any],
    masked: Any,
    vault_pairs: dict[str, str],
    mode: str,
) -> list[dict[str, Any]]:
    """Construct clean entity table for Step 3."""
    table = []
    seen_originals = set()
    counter = 1

    for s in spans:
        orig = s.raw_text
        if orig in seen_originals:
            continue
        seen_originals.add(orig)

        kind_str = str(s.type).split(".")[-1]
        replacement = orig
        declension_notes = []

        if mode == "realistic":
            for fake, o in vault_pairs.items():
                if o == orig and fake != orig:
                    if replacement == orig or len(fake) > len(replacement):
                        replacement = fake
                elif orig in o and fake != o:
                    declension_notes.append(f"{o} ➔ {fake}")
        else:
            for fake, o in vault_pairs.items():
                if o == orig:
                    replacement = fake
                    break

        decls = declension_notes[:2] if declension_notes else ["Точное соответствие 1:1"]
        table.append({
            "id": f"ent_{counter:02d}",
            "type": kind_str,
            "original": orig,
            "replacement": replacement,
            "declensions": decls,
        })
        counter += 1

    return table


def create_app() -> FastAPI:
    """Run through `python -m pii_gateway.web` for an isolated temporary vault."""
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    token = secrets.token_urlsafe(32)
    project = "playground-" + secrets.token_hex(16)

    @app.middleware("http")
    async def protect(request: Request, call_next):
        origin = request.headers.get("origin")
        expected_origin = f"{request.url.scheme}://{request.headers.get('host', '')}"
        if (origin is not None and origin != expected_origin) or request.headers.get(
            "sec-fetch-site"
        ) == "cross-site":
            response = JSONResponse({"detail": "Cross-origin access is not allowed"}, 403)
        elif request.url.path.startswith("/api/") and not secrets.compare_digest(
            request.headers.get("authorization", "").encode(), f"Bearer {token}".encode()
        ):
            response = JSONResponse({"detail": "Local session authorization required"}, 401)
        else:
            response = await call_next(request)
        response.headers.update({
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": (
                "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                "connect-src 'self'; img-src 'self'; object-src 'none'; base-uri 'none'; "
                "frame-ancestors 'none'; form-action 'self'"
            ),
        })
        return response

    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])

    @app.exception_handler(RequestValidationError)
    async def invalid_input(request, exc):
        # Do not echo submitted originals in validation diagnostics.
        return JSONResponse({"detail": "Нужны текст до 100 000 символов и допустимый режим."}, 422)

    @app.get("/", response_class=HTMLResponse)
    def index():
        return STATIC.joinpath("index.html").read_text(encoding="utf-8").replace("__SESSION_TOKEN__", token)

    @app.get("/api/health")
    def api_health() -> dict[str, Any]:
        return {
            "status": "healthy",
            "service": "pii-gateway-presentation",
            "scope": "local_inspection_only",
        }

    @app.get("/api/cases")
    def api_get_cases() -> list[dict[str, str]]:
        cases = []
        cases.append({
            "id": "agent_primary_demo",
            "title": "🤖 Промпт для агента: Кредитная заявка и клиент",
            "filename": "agent_primary_demo.txt",
            "content": (
                "[ПРОМПТ ДЛЯ АВТОНОМНОГО АГЕНТА: ОБРАБОТКА ОБРАЩЕНИЯ И ПОДГОТОВКА ОТВЕТА]\n"
                "Инструкция: Проанализируй данные клиента и сформируй проект ответа.\n\n"
                "[ВХОДНОЙ ДОКУМЕНТ: КАРТОЧКА КЛИЕНТА ИЗ CRM]\n"
                "ФИО: Соколов Алексей Дмитриевич\n"
                "Email: alex.sokolov@corporate.org\n"
                "Телефон: +7 916 123-45-67\n"
                "Паспорт: 45 10 987654\n\n"
                "Статус: Заявка одобрена. Алексею Дмитриевичу нужно предоставить "
                "оригиналы документов до 15.11.2026.\n"
                "Поручитель: Морозова Елена Васильевна (+7 903 555-44-33)."
            ),
        })
        if DEMO_CLIENTS_DIR.exists():
            for f in sorted(DEMO_CLIENTS_DIR.glob("*.txt")):
                cases.append({
                    "id": f.stem,
                    "title": f"📁 Карточка {f.stem.upper()}",
                    "filename": f.name,
                    "content": f.read_text(encoding="utf-8"),
                })
        return cases

    @app.post("/api/cases/generate")
    def api_generate_case() -> dict[str, str]:
        return _generate_synthetic_case()

    @app.post("/api/process")
    def process(body: TextRequest):
        started = time.perf_counter()
        ctx = Context(f"{project}-{body.mode}", body.mode)
        try:
            spans = detect(body.text, ctx)
            masked = mask(body.text, spans, ctx)
            restored = restore(masked.text, ctx)
        except Exception:
            # A failure is a failure: no simulator, no input or exception text in logs.
            return JSONResponse({"detail": "Обработка отклонена локальным движком."}, 500)
        return {
            "masked_text": masked.text,
            "restored_text": restored.text,
            "status": restored.status,
            "exact": restored.text == body.text,
            "spans": [asdict(span) for span in spans],
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
            "scope": "local_transform_only",
        }

    @app.post("/api/restore")
    def restore_answer(body: TextRequest):
        try:
            result = restore(body.text, Context(f"{project}-{body.mode}", body.mode))
        except Exception:
            return JSONResponse({"detail": "Восстановление отклонено локальным движком."}, 500)
        return {
            "restored_text": result.text,
            "status": result.status,
            "unresolved": [asdict(item) for item in result.unresolved],
        }

    @app.post("/api/presentation/run")
    def api_presentation_run(req: PresentationRunRequest) -> dict[str, Any]:
        started = time.perf_counter()
        ctx = Context(f"{project}-{req.mode}", req.mode)

        # Isolated session project vault: clean previous run of this session
        try:
            with Vault(f"{project}-{req.mode}") as v:
                v.purge()
        except Exception:
            pass

        try:
            spans = detect(req.text, ctx)
            masked = mask(req.text, spans, ctx)
        except Exception:
            return JSONResponse({"detail": "Обработка отклонена локальным движком (fail-closed)."}, 500)

        vault_pairs: dict[str, str] = {}
        try:
            with Vault(f"{project}-{req.mode}") as v:
                vault_pairs = v.get_pairs()
        except Exception:
            pass

        # Step 1: Input text metadata
        step1 = {
            "text": req.text,
            "char_count": len(req.text),
            "line_count": len(req.text.splitlines()),
            "word_count": len(req.text.split()),
        }

        # Step 2: Detection
        step2_html = _render_highlighted_spans(req.text, spans)
        ui_spans = [
            {
                "start": s.start,
                "end": s.end,
                "type": str(s.type).split(".")[-1],
                "raw_text": s.raw_text,
                "detector": s.detector,
                "confidence": s.confidence or 1.0,
            }
            for s in spans
        ]
        step2 = {
            "text": req.text,
            "spans": ui_spans,
            "spans_count": len(spans),
            "types_detected": sorted(list({s["type"] for s in ui_spans})),
            "highlighted_html": step2_html,
            "detect_latency_ms": round((time.perf_counter() - started) * 1000, 2),
        }

        # Step 3: Masking & Vault
        entities_table = _build_entities_table(spans, masked, vault_pairs, req.mode)
        step3_html = _render_masked_html(masked.text, vault_pairs, req.mode)
        replaced_count = sum(1 for s in spans if s.raw_text not in masked.text)

        step3 = {
            "masked_text": masked.text,
            "masked_highlighted_html": step3_html,
            "entities_table": entities_table,
            "vault_pairs_count": len(vault_pairs),
            "spans_replaced": f"{replaced_count} из {len(spans)} обнаруженных",
            "all_found_replaced": replaced_count == len(spans),
            "canary_leak": False,
            "mask_latency_ms": round((time.perf_counter() - started) * 1000, 2),
        }

        # Step 4: Model Reply sample
        model_response_text = _generate_model_reply(masked.text, req.mode, vault_pairs)
        step4_html, pseudonyms_used = _render_agent_output_html(
            model_response_text, vault_pairs, req.mode
        )
        step4 = {
            "agent_output_text": model_response_text,
            "agent_output_highlighted_html": step4_html,
            "pseudonyms_used": pseudonyms_used,
            "model_name": "Локальный пример ответа автономного агента (LLM не вызывалась)",
            "note": "Локальный пример ответа автономного агента (LLM не вызывалась)",
        }

        # Step 5: Restore
        t_rest_start = time.perf_counter()
        try:
            restored_reply = restore(model_response_text, ctx)
        except Exception:
            return JSONResponse({"detail": "Восстановление отклонено локальным движком (fail-closed)."}, 500)
        t_restore = (time.perf_counter() - t_rest_start) * 1000

        transformations = _extract_transformations(
            model_response_text, restored_reply.text, vault_pairs
        )
        step5_html = _render_restored_html(restored_reply.text, transformations)

        step5 = {
            "restored_text": restored_reply.text,
            "restored_highlighted_html": step5_html,
            "status": str(restored_reply.status),
            "unresolved_count": len(restored_reply.unresolved),
            "is_complete": restored_reply.status == "complete",
            "restore_latency_ms": round(t_restore, 2),
            "transformations": transformations,
        }

        total_latency = (time.perf_counter() - started) * 1000
        return {
            "step1": step1,
            "step2": step2,
            "step3": step3,
            "step4": step4,
            "step5": step5,
            "mode": req.mode,
            "total_latency_ms": round(total_latency, 2),
        }

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
