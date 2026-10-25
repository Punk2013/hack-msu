from pii_gateway.demo_agent import Workspace


def test_workspace_tools_stay_inside(tmp_path):
    ws = Workspace(tmp_path)
    assert ws.run("write_file", {"path": "out/a.txt", "content": "текст"}) == "записано: out/a.txt"
    assert ws.run("read_file", {"path": "out/a.txt"}) == "текст"
    assert "out/a.txt" in ws.run("list_files", {})
    assert ws.run("read_file", {"path": "../x"}).startswith("ошибка")
