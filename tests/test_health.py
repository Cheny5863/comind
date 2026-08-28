"""check_runtime 部署自检测试（旧 node 场景复现：install.sh 静默失败 → 前端误报未配置）。"""
import subprocess
from pathlib import Path

import chat_service


def _fake_bin(script: str) -> str:
    """生成一个可执行假二进制（脚本内容由调用方给）。"""
    p = Path(f"/tmp/fake-{abs(hash(script))}")
    p.write_text(script, encoding="utf-8")
    p.chmod(0o755)
    return str(p)


def test_check_runtime_ok_when_pi_works(monkeypatch):
    """pi --version 正常 → ok=True。"""
    fake = _fake_bin("#!/bin/bash\necho '0.99.9'\n")
    monkeypatch.setattr(chat_service, "PI_LAUNCH", [fake])
    r = chat_service.check_runtime()
    assert r["ok"] is True
    assert "0.99.9" in r["pi"]


def test_check_runtime_catches_syntax_error_node_too_old(monkeypatch):
    """node 18 跑 pi 崩溃（SyntaxError: Unexpected token 'with'）→ ok=False + 明确错误含 node 版本提示。"""
    fake = _fake_bin("#!/bin/bash\necho \"node:internal/modules/esm/loader:1234 SyntaxError: Unexpected token 'with'\" >&2\nexit 1\n")
    monkeypatch.setattr(chat_service, "PI_LAUNCH", [fake])

    def fake_node(*args, **kwargs):
        out = subprocess.CompletedProcess(args, 0)
        if args[0][0] == "node":
            out.stdout = "v18.19.1\n"
            out.stderr = ""
        else:
            out.stdout = ""
            out.stderr = "node:internal/modules/esm/loader:1234 SyntaxError: Unexpected token 'with'\n"
            out.returncode = 1
        return out

    monkeypatch.setattr(subprocess, "run", fake_node)
    r = chat_service.check_runtime()
    assert r["ok"] is False
    assert "node v18.19.1" in r["error"]
    assert "20.10" in r["error"]


def test_check_runtime_missing_pi(monkeypatch):
    """pi 二进制不存在 → ok=False。"""
    monkeypatch.setattr(chat_service, "PI_LAUNCH", [])
    r = chat_service.check_runtime()
    assert r["ok"] is False
    assert "未找到 pi" in r["error"]


def test_api_health_route():
    """backend /api/health 可访问且返回结构完整。"""
    from fastapi.testclient import TestClient
    import backend
    client = TestClient(backend.app)
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    for key in ("ok", "pi", "node", "error"):
        assert key in body
