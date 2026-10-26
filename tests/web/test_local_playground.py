"""Real pipeline roundtrip, presentation deck, and access boundaries of the local inspector."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pii_gateway.core.types import Context, PiiType
from pii_gateway.pseudo import restore
from pii_gateway.vault import Vault
from pii_gateway.web import server
from pii_gateway.web.server import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PII_VAULT_DIR", str(tmp_path / "vault"))
    with TestClient(create_app(), base_url="http://127.0.0.1:8790") as test_client:
        yield test_client


def auth(client):
    page = client.get("/")
    token = re.search(r'name="pii-session" content="([^"]+)"', page.text)[1]
    return {"Authorization": f"Bearer {token}", "Origin": "http://127.0.0.1:8790"}


@pytest.mark.parametrize("mode", ["marker", "realistic"])
def test_real_roundtrip_and_edited_answer(client, mode):
    headers = auth(client)
    text = "Email: synthetic.demo@example.test"
    response = client.post("/api/process", headers=headers, json={"text": text, "mode": mode})
    assert response.status_code == 200
    data = response.json()
    assert data["scope"] == "local_transform_only"
    assert data["exact"] and data["status"] == "complete"
    assert data["restored_text"] == text
    assert "synthetic.demo@example.test" not in data["masked_text"]
    assert len(data["spans"]) == 1
    answer = "Напоминание. " + data["masked_text"]
    result = client.post("/api/restore", headers=headers, json={"text": answer, "mode": mode})
    assert result.status_code == 200
    assert result.json()["restored_text"] == "Напоминание. " + text
    assert result.json()["unresolved"] == []


@pytest.mark.parametrize(
    "path", ["/api/process", "/api/restore", "/api/presentation/run", "/api/vault/purge"]
)
def test_no_session_token_means_no_access(client, path):
    response = client.post(path, json={"text": "synthetic@example.test"})
    assert response.status_code == 401
    assert "access-control-allow-origin" not in response.headers


@pytest.mark.parametrize("origin", ["https://untrusted.example", "null", "http://127.0.0.1:8791"])
def test_foreign_origin_rejected_even_with_valid_token(client, origin):
    headers = {**auth(client), "Origin": origin}
    response = client.post(
        "/api/process", headers=headers, json={"text": "synthetic@example.test"}
    )
    assert response.status_code == 403
    assert "access-control-allow-origin" not in response.headers
    preflight = client.options(
        "/api/process",
        headers={"Origin": origin, "Access-Control-Request-Method": "POST"},
    )
    assert preflight.status_code == 403


def test_rebinding_host_and_cross_site_html_rejected(client):
    assert client.get("/", headers={"Host": "untrusted.example"}).status_code == 400
    assert client.get("/", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403


def test_arbitrary_project_and_vault_routes_unavailable(client):
    with Vault("other_project") as vault:
        vault.add_entity("other_entity", PiiType.EMAIL)
        vault.add_pair("[EMAIL_999]", "other.synthetic@example.test", "other_entity")
    headers = auth(client)
    response = client.post(
        "/api/process",
        headers=headers,
        json={"text": "synthetic@example.test", "project_id": "other_project"},
    )
    assert response.status_code == 422
    assert client.get("/api/vault?project_id=other_project", headers=headers).status_code == 404
    assert (
        client.post(
            "/api/vault/purge", headers=headers, json={"project_id": "other_project"}
        ).status_code
        == 404
    )
    # Existing records of other projects remain untouched and unreadable through this API.
    result = client.post(
        "/api/restore", headers=headers, json={"text": "[EMAIL_999]", "mode": "marker"}
    ).json()
    assert result["status"] == "partial"
    assert result["restored_text"] == "[EMAIL_999]"
    assert restore("[EMAIL_999]", Context("other_project")).text == "other.synthetic@example.test"


def test_reserved_marker_is_failure_without_fake_success(client):
    response = client.post(
        "/api/process",
        headers=auth(client),
        json={"text": "[PERSON_1] synthetic@example.test"},
    )
    assert response.status_code == 500
    assert "masked_text" not in response.json()
    assert "synthetic@example.test" not in response.text
    assert "complete" not in response.text


def test_detector_failure_does_not_echo_input_or_exception(client, monkeypatch):
    def fail(*args):
        raise RuntimeError("do-not-log.synthetic@example.test")

    monkeypatch.setattr(server, "detect", fail)
    response = client.post(
        "/api/process", headers=auth(client), json={"text": "synthetic@example.test"}
    )
    assert response.status_code == 500
    assert "synthetic" not in response.text


@pytest.mark.parametrize(
    "body",
    [
        {"text": ""},
        {"text": "x" * 100001},
        {"text": 123},
        {"text": "sensitive", "mode": "unknown"},
    ],
)
def test_invalid_input_diagnostics_do_not_repeat_input(client, body):
    response = client.post("/api/process", headers=auth(client), json=body)
    assert response.status_code == 422
    assert "sensitive" not in response.text
    assert len(response.text) < 300


def test_no_caching_external_assets_or_browser_storage(client):
    page = client.get("/")
    assert page.headers["cache-control"] == "no-store"
    assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
    assert "https://" not in page.text
    for path in ("/static/app.js", "/static/styles.css"):
        response = client.get(path)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"


def test_app_sessions_cannot_read_each_others_markers(client):
    one = client.post(
        "/api/process",
        headers=auth(client),
        json={"text": "first.synthetic@example.test", "mode": "marker"},
    ).json()
    with TestClient(create_app(), base_url="http://127.0.0.1:8790") as other:
        result = other.post(
            "/api/restore",
            headers=auth(other),
            json={"text": one["masked_text"], "mode": "marker"},
        ).json()
        assert result["status"] == "partial"
        assert "first.synthetic@example.test" not in result["restored_text"]
        assert (
            other.post(
                "/api/process", headers=auth(client), json={"text": "hello"}
            ).status_code
            == 401
        )


def test_cli_uses_temporary_vault_and_restores_environment(tmp_path, monkeypatch):
    from pii_gateway.web import __main__ as cli

    original = tmp_path / "existing-vault"
    original.mkdir()
    sentinel = original / "keep.txt"
    sentinel.write_text("existing synthetic data")
    monkeypatch.setenv("PII_VAULT_DIR", str(original))
    monkeypatch.setattr("sys.argv", ["pii_gateway.web", "--port", "18791"])
    observed = []

    def run(app, **kwargs):
        import os

        directory = Path(os.environ["PII_VAULT_DIR"])
        observed.append(directory)
        assert directory != original and directory.is_dir()
        if os.name != "nt":
            assert directory.stat().st_mode & 0o077 == 0
        assert kwargs["host"] == "127.0.0.1" and kwargs["access_log"] is False
        with TestClient(app, base_url="http://127.0.0.1:18791") as test_client:
            page = test_client.get("/")
            token = re.search(r'name="pii-session" content="([^"]+)"', page.text)[1]
            assert (
                test_client.post(
                    "/api/process",
                    headers={"Authorization": f"Bearer {token}"},
                    json={"text": "synthetic@example.test"},
                ).status_code
                == 200
            )
        assert list(directory.glob("*.db"))

    monkeypatch.setattr(cli.uvicorn, "run", run)
    cli.main()
    import os

    assert os.environ["PII_VAULT_DIR"] == str(original)
    assert sentinel.read_text() == "existing synthetic data"
    assert not observed[0].exists()


def test_presentation_run_all_5_steps(client):
    headers = auth(client)
    text = (
        "[ПРОМПТ ДЛЯ АВТОНОМНОГО АГЕНТА: ОБРАБОТКА ОБРАЩЕНИЯ И ПОДГОТОВКА ОТВЕТА]\n"
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
        headers=headers,
        json={"text": text, "mode": "realistic"},
    )
    assert res.status_code == 200
    data = res.json()

    # Step 1
    assert data["step1"]["char_count"] == len(text)

    # Step 2
    assert data["step2"]["spans_count"] >= 4
    assert "PERSON" in data["step2"]["types_detected"]

    # Step 3
    assert len(data["step3"]["entities_table"]) >= 4

    # Step 4
    assert "Здравствуйте" in data["step4"]["agent_output_text"]
    assert len(data["step4"]["pseudonyms_used"]) >= 1

    # Step 5
    assert data["step5"]["status"] == "complete"
    assert data["step5"]["is_complete"] is True
    assert "Соколов Алексей Дмитриевич" in data["step5"]["restored_text"]


def test_presentation_run_reserved_marker_fails_closed(client):
    headers = auth(client)
    res = client.post(
        "/api/presentation/run",
        headers=headers,
        json={"text": "[PERSON_1] sensitive@example.test", "mode": "realistic"},
    )
    assert res.status_code == 500
    assert "step2" not in res.json()
