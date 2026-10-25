"""Tests for the interactive presentation web server and API."""

from fastapi.testclient import TestClient

from pii_gateway.web.server import app

client = TestClient(app)


def test_api_health():
    res = client.get("/api/health")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "healthy"
    assert "PERSON" in data["supported_types"]


def test_api_demo_cases():
    res = client.get("/api/demo/cases")
    assert res.status_code == 200
    cases = res.json()
    assert len(cases) >= 2
    ids = [c["id"] for c in cases]
    assert "client1" in ids or "loan_agreement" in ids


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


def test_api_pipeline():
    text = "Карточка: Анна Сергеевна Смирнова, телефон +7 900 000-00-02."
    res = client.post(
        "/api/pipeline",
        json={"text": text, "mode": "realistic", "project_id": "test-web-pipe"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["is_perfect_roundtrip"] is True
    assert data["spans_count"] >= 2
    assert "latencies" in data
    assert "vault" in data


def test_api_benchmarks():
    res = client.get("/api/benchmarks")
    assert res.status_code == 200
    data = res.json()
    assert data["roundtrip_accuracy_pct"] == 100.0
    assert data["leaks_detected"] == 0
    assert data["canary_verdict"] == "CLEAN"


def test_api_vault_endpoints():
    res = client.get("/api/vault?project_id=test-web-pipe")
    assert res.status_code == 200
    data = res.json()
    assert "stats" in data
    assert "entities" in data

    # Purge
    res_purge = client.post("/api/vault/purge?project_id=test-web-pipe")
    assert res_purge.status_code == 200
    assert res_purge.json()["success"] is True


def test_index_page():
    res = client.get("/")
    assert res.status_code == 200
    assert "PII GATEWAY" in res.text
    assert "nav-tabs" in res.text
