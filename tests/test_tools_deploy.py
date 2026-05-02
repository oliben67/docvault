from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import httpx
import pytest

from docvault.tools.deploy import deploy_template


def _make_zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _ok_response(url: str, zip_bytes: bytes) -> httpx.Response:
    req = httpx.Request("GET", url)
    return httpx.Response(
        200,
        content=zip_bytes,
        headers={"content-type": "application/zip"},
        request=req,
    )


def _err_response(url: str, status: int) -> httpx.Response:
    req = httpx.Request("GET", url)
    return httpx.Response(status, request=req)


def test_deploy_extracts_files(tmp_path: Path, monkeypatch):
    zip_bytes = _make_zip(
        {
            "_template.json": json.dumps(
                {"name": "svc", "id": "svc/v1.0.0/abc"}
            ).encode(),
            "config/app.json": json.dumps({"host": "localhost"}).encode(),
            "config/database.json": json.dumps({"dsn": "postgres://"}).encode(),
        }
    )
    monkeypatch.setattr(httpx, "get", lambda url, **kw: _ok_response(url, zip_bytes))

    files = deploy_template("svc", target_path=tmp_path / "out")

    assert len(files) == 2
    assert (tmp_path / "out" / "config" / "app.json").exists()
    assert (
        json.loads((tmp_path / "out" / "config" / "app.json").read_text())["host"]
        == "localhost"
    )
    assert not (tmp_path / "out" / "_template.json").exists()


def test_deploy_skips_existing_by_default(tmp_path: Path, monkeypatch):
    existing = tmp_path / "out" / "config" / "app.json"
    existing.parent.mkdir(parents=True)
    existing.write_text('{"host": "original"}')

    zip_bytes = _make_zip({"config/app.json": json.dumps({"host": "new"}).encode()})
    monkeypatch.setattr(httpx, "get", lambda url, **kw: _ok_response(url, zip_bytes))

    files = deploy_template("svc", target_path=tmp_path / "out")
    assert files == []
    assert json.loads(existing.read_text())["host"] == "original"  # unchanged


def test_deploy_overwrite(tmp_path: Path, monkeypatch):
    existing = tmp_path / "out" / "config" / "app.json"
    existing.parent.mkdir(parents=True)
    existing.write_text('{"host": "original"}')

    zip_bytes = _make_zip({"config/app.json": json.dumps({"host": "updated"}).encode()})
    monkeypatch.setattr(httpx, "get", lambda url, **kw: _ok_response(url, zip_bytes))

    files = deploy_template("svc", target_path=tmp_path / "out", overwrite=True)
    assert len(files) == 1
    assert json.loads(existing.read_text())["host"] == "updated"


def test_deploy_raises_on_http_error(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda url, **kw: _err_response(url, 404))
    with pytest.raises(httpx.HTTPStatusError):
        deploy_template("missing", target_path=tmp_path / "out")


def test_deploy_zip_slip_protection(tmp_path: Path, monkeypatch):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../../etc/passwd", "malicious")
    zip_bytes = buf.getvalue()

    monkeypatch.setattr(httpx, "get", lambda url, **kw: _ok_response(url, zip_bytes))

    with pytest.raises(ValueError, match="escape"):
        deploy_template("evil", target_path=tmp_path / "out")


def test_deploy_accepts_full_template_id(tmp_path: Path, monkeypatch):
    zip_bytes = _make_zip({"item.json": b'{"x": 1}'})
    captured_url: list[str] = []

    def fake_get(url: str, **kw):
        captured_url.append(url)
        return _ok_response(url, zip_bytes)

    monkeypatch.setattr(httpx, "get", fake_get)

    deploy_template("myapp/v1.0.0/deadbeef", target_path=tmp_path / "out")
    # Only the name prefix should appear in the URL
    assert captured_url[0].endswith("/templates/myapp/export")
