from __future__ import annotations

import io
import zipfile
from pathlib import Path

import httpx


def deploy_store(
    store_name: str,
    target_path: Path | str,
    base_url: str = "http://localhost:8000",
    api_key: str | None = None,
    *,
    overwrite: bool = False,
) -> list[Path]:
    """Download a DocVault store export and extract it to *target_path*.

    Parameters
    ----------
    store_name:
        The store's name.
    target_path:
        Local directory where the store's files will be written.
        The directory is created if it does not already exist.
    base_url:
        Base URL of the running DocVault server (no trailing slash needed).
    api_key:
        Optional API key sent in the ``X-API-Key`` header.  Required when
        the server is configured with ``auth_mode = "api_key"``.
    overwrite:
        When ``False`` (default) existing files are skipped.  Set to
        ``True`` to replace them unconditionally.

    Returns
    -------
    list[Path]
        Paths of all files written to *target_path*.

    Raises
    ------
    httpx.HTTPStatusError
        If the server returns a non-2xx response (e.g. 404 store not found).
    ValueError
        If a zip entry would escape *target_path* (zip-slip protection).

    Examples
    --------
    Basic usage::

        from docvault.tools import deploy_store
        from pathlib import Path

        files = deploy_store("microservice", target_path=Path("/srv/config"))
        print(f"Deployed {len(files)} file(s)")

    With API key::

        files = deploy_store(
            "microservice",
            target_path="/srv/config",
            base_url="https://vault.internal",
            api_key="sk-...",
        )
    """
    target = Path(target_path).expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)

    headers: dict[str, str] = {}
    if api_key:
        headers["X-API-Key"] = api_key

    url = f"{base_url.rstrip('/')}/api/v1/stores/{store_name}/export"
    response = httpx.get(url, headers=headers, follow_redirects=True)
    response.raise_for_status()

    extracted: list[Path] = []
    with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
        for entry in zf.namelist():
            if entry == "_store.json":
                continue
            dest = (target / entry).resolve()
            if not str(dest).startswith(str(target)):
                raise ValueError(
                    f"Zip entry {entry!r} would escape the target directory — "
                    "archive may be malicious"
                )
            if not overwrite and dest.exists():
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(zf.read(entry))
            extracted.append(dest)

    return extracted
