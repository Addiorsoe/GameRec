import json

from gamerec import sysinfo


def test_scan_has_facts():
    d = sysinfo.scan()
    assert {"os", "cpu", "ram", "gpus", "monitors", "disks", "virtualization"} <= set(d)
    assert d["ram"]["total_gb"] > 0 and d["cpu"]["threads"] >= 1
    json.dumps(d)  # must be serialisable as-is
    assert len(sysinfo.summary_lines(d)) == 6


def test_scans_only_once(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(sysinfo, "scan", lambda: calls.append(1) or {"ram": {"total_gb": 8}})
    p = tmp_path / "system_info.json"
    assert sysinfo.load_or_scan(path=p) == {"ram": {"total_gb": 8}}
    sysinfo.load_or_scan(path=p)
    assert len(calls) == 1                      # second call reads the saved file
    sysinfo.load_or_scan(force=True, path=p)    # "重新掃描" button
    assert len(calls) == 2
    p.write_text("{broken", encoding="utf-8")   # corrupted file -> rescan instead of crashing
    sysinfo.load_or_scan(path=p)
    assert len(calls) == 3


def test_virt_text():
    assert sysinfo._virt_text({"firmware_enabled": True}) == "已開啟"
    assert sysinfo._virt_text({"firmware_enabled": False}) == "未開啟"
    assert "無法判斷" in sysinfo._virt_text({"firmware_enabled": False, "vbs_enabled": True})
    assert sysinfo._virt_text({}) == "無法判斷"
