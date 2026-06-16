"""Mirror the PubMed baseline + updatefiles from NCBI over HTTPS (md5-verified, resumable)."""

from __future__ import annotations

import hashlib
import os
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import httpx
from tqdm import tqdm

_FILE_RE = re.compile(r'href="(pubmed\d+n\d+\.xml\.gz)"')
_MD5_RE = re.compile(r"MD5\([^)]*\)\s*=\s*([0-9a-fA-F]{32})")
_USER_AGENT = "BioKGrapher/1.0 (+https://doi.org/10.1016/j.csbj.2024.10.017)"


@dataclass(frozen=True)
class RemoteFile:
    name: str
    kind: str  # "baseline" | "updatefiles"
    url: str
    md5_url: str


def make_client(timeout: float = 120.0) -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": _USER_AGENT},
        timeout=timeout,
        follow_redirects=True,
    )


def list_remote(kind: str, client: httpx.Client, base_url: str) -> list[RemoteFile]:
    """List the ``.xml.gz`` files available under ``base_url/kind/``."""
    dir_url = f"{base_url}/{kind}/"
    resp = client.get(dir_url)
    resp.raise_for_status()
    names = sorted(set(_FILE_RE.findall(resp.text)))
    return [RemoteFile(n, kind, dir_url + n, dir_url + n + ".md5") for n in names]


def remote_md5(rf: RemoteFile, client: httpx.Client) -> str | None:
    try:
        resp = client.get(rf.md5_url)
        resp.raise_for_status()
    except httpx.HTTPError:
        return None
    m = _MD5_RE.search(resp.text)
    return m.group(1).lower() if m else None


def _md5_of(path: Path) -> str:
    h = hashlib.md5()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _remote_size(rf: RemoteFile, client: httpx.Client) -> int | None:
    try:
        resp = client.head(rf.url)
        resp.raise_for_status()
        return int(resp.headers.get("Content-Length", 0)) or None
    except (httpx.HTTPError, ValueError):
        return None


def ensure_local(rf: RemoteFile, dest_dir: Path, client: httpx.Client,
                 *, retries: int = 3) -> tuple[Path, str | None]:
    """Ensure ``rf`` exists locally (resume + md5-verify); return ``(path, md5)``."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    final = dest_dir / rf.name
    part = dest_dir / (rf.name + ".part")
    expected = remote_md5(rf, client)

    if final.exists():
        size = _remote_size(rf, client)
        if size is None or final.stat().st_size == size:
            return final, expected

    for attempt in range(1, retries + 1):
        resume_from = part.stat().st_size if part.exists() else 0
        headers = {"Range": f"bytes={resume_from}-"} if resume_from else {}
        try:
            with client.stream("GET", rf.url, headers=headers) as resp:
                if resume_from and resp.status_code == 200:  # server ignored Range
                    resume_from = 0
                    part.unlink(missing_ok=True)
                resp.raise_for_status()
                mode = "ab" if resume_from else "wb"
                with part.open(mode) as fh:
                    for chunk in resp.iter_bytes(1 << 20):
                        fh.write(chunk)
        except httpx.HTTPError:
            if attempt == retries:
                raise
            continue

        if expected and _md5_of(part) != expected:
            part.unlink(missing_ok=True)
            if attempt == retries:
                raise OSError(f"md5 mismatch for {rf.name} after {retries} attempts")
            continue
        os.replace(part, final)
        return final, expected

    raise OSError(f"failed to download {rf.name}")  # pragma: no cover


def sync(kind: str, dest_dir: Path, client: httpx.Client, base_url: str, *,
         skip_names: set[str] | None = None, limit: int | None = None,
         max_concurrency: int = 4, shard: tuple[int, int] | None = None
         ) -> list[tuple[RemoteFile, Path, str | None]]:
    """Download not-yet-processed files for ``kind``; ``shard=(i,k)`` fetches a disjoint slice."""
    skip = skip_names or set()
    remote = list_remote(kind, client, base_url)  # sorted by name
    if shard is not None:
        i, k = shard
        remote = [rf for idx, rf in enumerate(remote) if idx % k == i]
    remote = [rf for rf in remote if rf.name not in skip]
    if limit is not None:
        remote = remote[:limit]
    results: list[tuple[RemoteFile, Path, str | None]] = [None] * len(remote)  # type: ignore

    def work(idx_rf: tuple[int, RemoteFile]) -> None:
        idx, rf = idx_rf
        path, md5 = ensure_local(rf, dest_dir, client)
        results[idx] = (rf, path, md5)

    with ThreadPoolExecutor(max_workers=max_concurrency) as pool:
        list(tqdm(pool.map(work, enumerate(remote)), total=len(remote),
                  desc=f"download {kind}", unit=" file"))
    return [r for r in results if r is not None]
