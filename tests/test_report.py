import json

from pii_gateway.core.types import Context, EntitySpan, PiiType
from pii_gateway.pseudo import mask
from pii_gateway.report import build


def test_report_shows_fake_and_real_side_by_side(tmp_path, monkeypatch):
    monkeypatch.setenv("PII_VAULT_DIR", str(tmp_path / "vault"))
    text = "ФИО: Иван Петрович Иванов"
    span = EntitySpan(5, 25, PiiType.PERSON, "Иван Петрович Иванов", detector="test")
    masked = mask(text, [span], Context("r")).text
    dumps = tmp_path / "dumps"
    dumps.mkdir()
    body = {"messages": [{"role": "tool", "content": masked}]}
    (dumps / "1-request.json").write_text(json.dumps(body, ensure_ascii=False))
    page = build(dumps, "r")
    assert "<mark class=real>Иван</mark>" in page
    assert "Иванов" in page and masked.split()[-1] in page
