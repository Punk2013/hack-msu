import json
from pathlib import Path

from pii_gateway.core.types import PiiType
from pii_gateway.vault.cli import main
from pii_gateway.vault.storage import Vault


def test_cli_list_projects(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setenv("PII_VAULT_DIR", str(tmp_path))
    # Initially empty
    code = main(["list-projects"])
    assert code == 0
    assert "No vault databases found" in capsys.readouterr().out

    # Create two projects
    with Vault("alpha"):
        pass
    with Vault("beta"):
        pass

    code = main(["list-projects"])
    assert code == 0
    out = capsys.readouterr().out
    assert "alpha" in out
    assert "beta" in out

    # JSON mode
    code = main(["--json", "list-projects"])
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    assert len(data) == 2
    assert {d["project_id"] for d in data} == {"alpha", "beta"}


def test_cli_stats_and_inspect(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setenv("PII_VAULT_DIR", str(tmp_path))
    with Vault("my_project") as vault:
        vault.add_entity("ent_abc", PiiType.EMAIL)
        vault.add_marker("[EMAIL_1]", "ent_abc", PiiType.EMAIL)
        vault.add_pair("[EMAIL_1]", "test@example.com", "ent_abc")

    # stats human
    code = main(["stats", "my_project"])
    assert code == 0
    out = capsys.readouterr().out
    assert "Vault Statistics: my_project" in out
    assert "Total Entities:     1" in out
    assert "EMAIL          : 1" in out

    # stats json
    code = main(["--json", "stats", "my_project"])
    assert code == 0
    stats = json.loads(capsys.readouterr().out)
    assert stats["project_id"] == "my_project"
    assert stats["total_entities"] == 1
    assert stats["entities_by_type"] == {"EMAIL": 1}

    # inspect human
    code = main(["inspect", "my_project"])
    assert code == 0
    inspect_out = capsys.readouterr().out
    assert "ent_abc" in inspect_out
    assert "[EMAIL_1]" in inspect_out

    # inspect json
    code = main(["--json", "inspect", "my_project"])
    assert code == 0
    entities = json.loads(capsys.readouterr().out)
    assert len(entities) == 1
    assert entities[0]["entity_id"] == "ent_abc"
    assert entities[0]["marker"] == "[EMAIL_1]"


def test_cli_purge_with_force(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setenv("PII_VAULT_DIR", str(tmp_path))
    with Vault("target_project") as vault:
        vault.add_entity("ent_1", PiiType.PERSON)

    # Purge force
    code = main(["purge", "target_project", "--force"])
    assert code == 0
    assert "successfully purged" in capsys.readouterr().out

    with Vault("target_project") as vault:
        assert vault.get_stats()["total_entities"] == 0
