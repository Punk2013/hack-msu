from pathlib import Path

from pii_gateway.core.types import PiiType
from pii_gateway.vault.storage import Vault


def test_vault_get_stats_empty(tmp_path: Path):
    db_path = tmp_path / "empty.db"
    with Vault("empty_proj", db_path=db_path) as vault:
        stats = vault.get_stats()
        assert stats["project_id"] == "empty_proj"
        assert stats["total_entities"] == 0
        assert stats["ambiguous_entities"] == 0
        assert stats["entities_by_type"] == {}
        assert stats["total_surfaces"] == 0
        assert stats["total_markers"] == 0
        assert stats["total_pairs"] == 0
        assert stats["total_keys"] == 0
        assert stats["total_audit_events"] == 0
        assert stats["db_size_bytes"] > 0


def test_vault_get_stats_populated(tmp_path: Path):
    db_path = tmp_path / "pop.db"
    with Vault("pop_proj", db_path=db_path) as vault:
        vault.add_entity("ent_1", PiiType.PERSON, is_ambiguous=False)
        vault.add_entity("ent_2", PiiType.PERSON, is_ambiguous=True)
        vault.add_entity("ent_3", PiiType.EMAIL, is_ambiguous=False)

        vault.add_marker("[PERSON_1]", "ent_1", PiiType.PERSON)
        vault.add_marker("[PERSON_2]", "ent_2", PiiType.PERSON)
        vault.add_marker("[EMAIL_1]", "ent_3", PiiType.EMAIL)

        vault.add_pair("[PERSON_1]", "Иван Иванов", "ent_1")
        vault.add_pair("[EMAIL_1]", "ivan@example.com", "ent_3")

        vault.add_surface("surf_1", "ent_1", "original", "Иван Иванов")
        vault.add_surface("surf_2", "ent_1", "original", "Ивану Иванову")

        vault.add_key("PERSON|masc|Name:иван", "ent_1")

        stats = vault.get_stats()
        assert stats["total_entities"] == 3
        assert stats["ambiguous_entities"] == 1
        assert stats["entities_by_type"] == {
            "PERSON": 2,
            "EMAIL": 1,
        }
        assert stats["total_markers"] == 3
        assert stats["total_pairs"] == 2
        assert stats["total_surfaces"] == 2
        assert stats["total_keys"] == 1


def test_vault_list_entities(tmp_path: Path):
    db_path = tmp_path / "list.db"
    with Vault("list_proj", db_path=db_path) as vault:
        vault.add_entity("ent_10", PiiType.PHONE, is_ambiguous=False)
        vault.add_marker("[PHONE_1]", "ent_10", PiiType.PHONE)
        vault.add_pair("[PHONE_1]", "+7 900 123-45-67", "ent_10")
        vault.add_surface("s1", "ent_10", "original", "+7 900 123-45-67")

        entities = vault.list_entities()
        assert len(entities) == 1
        ent = entities[0]
        assert ent["entity_id"] == "ent_10"
        assert ent["pii_type"] == "PHONE"
        assert ent["is_ambiguous"] is False
        assert ent["marker"] == "[PHONE_1]"
        assert ent["surface_count"] == 1
        assert ent["pair_count"] == 1


def test_vault_purge_cleans_all_tables(tmp_path: Path):
    db_path = tmp_path / "purge.db"
    with Vault("purge_proj", db_path=db_path) as vault:
        vault.add_entity("ent_1", PiiType.PERSON)
        vault.add_marker("[PERSON_1]", "ent_1", PiiType.PERSON)
        vault.add_pair("[PERSON_1]", "Иван Иванов", "ent_1")
        vault.add_surface("s1", "ent_1", "original", "Иван Иванов")
        vault.add_key("key_1", "ent_1")
        # Add an ambiguous key with entity_id NULL:
        conn = vault._get_connection()
        with conn:
            conn.execute("INSERT INTO entity_keys (key, entity_id) VALUES ('amb_key', NULL)")
            conn.execute("INSERT INTO audit (ts, event, entity_id) VALUES (1.0, 'test', 'ent_1')")

        stats_before = vault.get_stats()
        assert stats_before["total_entities"] == 1
        assert stats_before["total_keys"] == 2
        assert stats_before["total_audit_events"] == 1

        vault.purge()

        stats_after = vault.get_stats()
        assert stats_after["total_entities"] == 0
        assert stats_after["total_markers"] == 0
        assert stats_after["total_pairs"] == 0
        assert stats_after["total_surfaces"] == 0
        assert stats_after["total_keys"] == 0
        assert stats_after["total_audit_events"] == 0
