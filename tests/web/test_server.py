"""Tests for the interactive presentation web server, harness, and dynamic cases."""

from fastapi.testclient import TestClient

from pii_gateway.web.server import app

client = TestClient(app)


def test_api_health():
    res = client.get("/api/health")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "healthy"
    assert "PERSON" in data["supported_types"]


def test_api_cases_and_dynamic_generation():
    # 1. Fetch cases
    res = client.get("/api/cases")
    assert res.status_code == 200
    cases = res.json()
    assert len(cases) >= 2

    # 2. Generate random case dynamically
    res_gen = client.post("/api/cases/generate")
    assert res_gen.status_code == 200
    gen_data = res_gen.json()
    assert "ФИО:" in gen_data["content"]
    assert "Телефон:" in gen_data["content"]
    assert "Email:" in gen_data["content"]
    assert "Паспорт:" in gen_data["content"]


def test_api_harness_run_all_layers():
    text = (
        "Карточка клиента (синтетические данные)\n\n"
        "ФИО: Иван Петрович Иванов\n"
        "Email: ivan.ivanov@example.test\n"
        "Телефон: +7 900 000-00-01\n"
        "Паспорт: 00 01 000001\n\n"
        "Статус: договор продлён до 01.12.2026. Ивану Петровичу направить уведомление."
    )
    res = client.post(
        "/api/harness/run",
        json={
            "text": text,
            "mode": "realistic",
            "project_id": "test-harness",
            "llm_prompt": "Составь официальное уведомление клиенту.",
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert data["fail_closed_triggered"] is False
    assert len(data["layers"]) == 7

    # Check Layer names
    layer_ids = [layer["id"] for layer in data["layers"]]
    assert "layer-1-input" in layer_ids
    assert "layer-2-detect" in layer_ids
    assert "layer-3-vault" in layer_ids
    assert "layer-4-mask" in layer_ids
    assert "layer-5-upstream" in layer_ids
    assert "layer-6-restore" in layer_ids
    assert "layer-7-verify" in layer_ids

    # Layer 4 Canary check
    l4 = next(layer for layer in data["layers"] if layer["id"] == "layer-4-mask")
    assert l4["canary_check"]["verdict"] == "CLEAN (0 LEAKS)"

    # Layer 7 Roundtrip verification
    l7 = next(layer for layer in data["layers"] if layer["id"] == "layer-7-verify")
    assert l7["is_perfect_roundtrip"] is True


def test_api_harness_fail_closed_injection():
    text = "Карточка клиента: Иван Петрович Иванов"
    res = client.post(
        "/api/harness/run",
        json={
            "text": text,
            "mode": "realistic",
            "project_id": "test-harness-fail",
            "fail_closed_injection": True,
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert data["fail_closed_triggered"] is True
    assert "FAIL-CLOSED" in data["security_verdict"]


def test_api_process_and_restore():
    text = (
        "Клиент: Иван Петрович Иванов, тел +7 900 000-00-01, "
        "email: ivan.ivanov@test.ru. Направить Ивану Петровичу."
    )
    # 1. Process (detect + mask)
    res_proc = client.post(
        "/api/process",
        json={"text": text, "mode": "realistic", "project_id": "test-web-proc"},
    )
    assert res_proc.status_code == 200
    data_proc = res_proc.json()
    assert len(data_proc["spans"]) >= 3
    assert data_proc["masked_text"] != text
    assert data_proc["total_latency_ms"] >= 0

    # 2. Restore
    res_rest = client.post(
        "/api/restore",
        json={
            "text": data_proc["masked_text"],
            "mode": "realistic",
            "project_id": "test-web-proc",
        },
    )
    assert res_rest.status_code == 200
    data_rest = res_rest.json()
    assert data_rest["status"] == "complete"
    assert data_rest["restored_text"] == text


def test_api_vault_endpoints():
    res = client.get("/api/vault?project_id=test-web-proc")
    assert res.status_code == 200
    data = res.json()
    assert "stats" in data
    assert "entities" in data

    # Purge
    res_purge = client.post("/api/vault/purge?project_id=test-web-proc")
    assert res_purge.status_code == 200
    assert res_purge.json()["success"] is True


def test_index_page():
    res = client.get("/")
    assert res.status_code == 200
    assert "PII" in res.text


def test_api_presentation_run():
    text = (
        "[ПРОМПТ ДЛЯ АВТОНОМНОГО АГЕНТА: ОБРАБОТКА ОБРАЩЕНИЯ И ПОДГОТОВКА ОТВЕТА КЛИЕНТУ]\n"
        "Инструкция: Сформируй ответное официальное письмо клиенту.\n\n"
        "[ВХОДНОЙ ДОКУМЕНТ: КАРТОЧКА КЛИЕНТА]\n"
        "ФИО: Соколов Алексей Дмитриевич\n"
        "Email: alex.sokolov@corporate.org\n"
        "Телефон: +7 916 123-45-67\n"
        "Паспорт: 45 10 987654\n\n"
        "Статус: Заявка одобрена. Алексею Дмитриевичу нужно предоставить оригиналы справок."
    )
    res = client.post(
        "/api/presentation/run",
        json={
            "text": text,
            "mode": "realistic",
            "project_id": "test-presentation-flow",
        },
    )
    assert res.status_code == 200
    data = res.json()

    # Step 1
    assert "step1" in data
    assert data["step1"]["char_count"] == len(text)

    # Step 2
    assert "step2" in data
    assert data["step2"]["spans_count"] >= 4
    assert "PERSON" in data["step2"]["types_detected"]
    assert "mark class=\"pii-tag pii-person\"" in data["step2"]["highlighted_html"]

    # Step 3
    assert "step3" in data
    assert data["step3"]["canary_leak"] is False
    assert len(data["step3"]["entities_table"]) >= 4

    # Step 4
    assert "step4" in data
    assert "Здравствуйте" in data["step4"]["agent_output_text"]
    assert len(data["step4"]["pseudonyms_used"]) >= 1

    # Step 5
    assert "step5" in data
    assert data["step5"]["status"] == "complete"
    assert data["step5"]["fidelity_pct"] == 100.0
    assert "Соколов Алексей Дмитриевич" in data["step5"]["restored_text"]
    assert len(data["step5"]["transformations"]) >= 1
