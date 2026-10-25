"""Regression coverage for PR53's local journal and complete request history."""

import json
import os
import stat

import pytest

from pii_gateway.gateway.journal import Journal
from pii_gateway.journal_report import build


def test_existing_journal_is_private_before_first_write(tmp_path):
    path = tmp_path / "journal.jsonl"
    path.write_text("")
    path.chmod(0o644)
    journal = Journal(path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    journal.write({"request_in": "synthetic"})
    assert json.loads(path.read_text())["request_in"] == "synthetic"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_permissions_are_repaired_again_before_each_write(tmp_path):
    path = tmp_path / "journal.jsonl"
    journal = Journal(path)
    path.chmod(0o644)
    journal.write({"request_in": "synthetic"})
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


@pytest.mark.parametrize("link_kind", ["symlink", "hardlink"])
@pytest.mark.parametrize("after_init", [False, True])
def test_linked_file_is_rejected_without_touching_target(tmp_path, link_kind, after_init):
    path, target = tmp_path / "journal.jsonl", tmp_path / "other.txt"
    target.write_text("keep synthetic contents")
    target.chmod(0o644)
    if after_init:
        journal = Journal(path)
        path.unlink()
    if link_kind == "symlink":
        path.symlink_to(target)
    else:
        os.link(target, path)
    with pytest.raises(OSError):
        if after_init:
            journal.write({"request_in": "must not be written"})
        else:
            Journal(path)
    assert target.read_text() == "keep synthetic contents"
    assert stat.S_IMODE(target.stat().st_mode) == 0o644


def test_fifo_is_rejected_without_waiting_for_reader(tmp_path):
    path = tmp_path / "journal.jsonl"
    os.mkfifo(path)
    with pytest.raises(OSError):
        Journal(path)


@pytest.mark.parametrize("histories", [
    [["FIRST_REQUEST"], ["SECOND_REQUEST"]],
    [["OLD_A", "OLD_B"], ["COMPACTED_REQUEST"]],
    [["SAME_REQUEST"], ["SAME_REQUEST"]],
    [["FIRST_REQUEST"], ["FIRST_REQUEST", "ADDED_REQUEST"]],
])
def test_each_exchange_contains_its_entire_request(tmp_path, histories):
    path = tmp_path / "journal.jsonl"
    records = []
    for history in histories:
        messages = [{"role": "user", "content": value} for value in history]
        records.append({"ts": 0, "request_in": messages, "request_out": messages,
                        "model_said": {}, "agent_got": {}})
    path.write_text("\n".join(json.dumps(record) for record in records))
    sections = build(path).split("<h2>")[1:]
    assert len(sections) == len(histories)
    for section, history in zip(sections, histories, strict=True):
        for value in history:
            assert section.count(value) == 2  # agent and upstream columns
