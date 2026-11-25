from gamerec import env_check
from gamerec.env_check import Check, _ver, all_required_ok


def test_version_compare():
    assert _ver("v0.2.0") > _ver("0.1.0")
    assert _ver("v0.1.0") == _ver("0.1.0")
    assert _ver("v1.10.0") > _ver("1.9.3")


def test_required_vs_optional():
    checks = [Check("a", "A", True, ""), Check("npcap", "N", False, "", required=False)]
    assert all_required_ok(checks)
    assert not all_required_ok(checks + [Check("admin", "Ad", False, "")])


def test_run_checks_keys():
    keys = [c.key for c in env_check.run_checks()]
    assert keys == ["os", "arch", "admin", "npcap"]


def test_check_update(monkeypatch):
    import json

    payload = {"tag_name": "v9.0.0", "html_url": "https://example/rel",
               "assets": [{"name": "GameRec.exe", "browser_download_url": "https://example/GameRec.exe"}]}
    monkeypatch.setattr(env_check, "_http_get", lambda url, timeout=8: json.dumps(payload).encode())
    info = env_check.check_update()
    assert info == {"version": "v9.0.0", "url": "https://example/rel", "exe_url": "https://example/GameRec.exe"}
    payload["tag_name"] = "v0.0.1"
    assert env_check.check_update() is None


def test_latest_npcap_installer(monkeypatch):
    html = '<a href="npcap-1.79.exe">x</a><a href="npcap-1.80.exe">y</a><a href="npcap-1.8.exe">z</a>'
    monkeypatch.setattr(env_check, "_http_get", lambda url, timeout=15: html.encode())
    assert env_check.latest_npcap_installer() == "npcap-1.80.exe"
