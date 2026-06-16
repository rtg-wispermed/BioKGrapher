"""One-time ingestion of UMLS RRF files into SQLite (§4.5).

RRF columns used (0-indexed): MRCONSO 0=CUI 1=LAT 2=TS 4=STT 6=ISPREF 7=AUI 9=SCUI 11=SAB
14=STR; MRDEF 0=CUI 4=SAB 5=DEF; MRHIER 0=CUI 1=AUI 4=SAB 6=PTR; MRREL 0=CUI1 3=REL 4=CUI2
7=RELA 10=SAB 14=SUPPRESS.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from pathlib import Path

from tqdm import tqdm

from biokgrapher.config import Config
from biokgrapher.store import Store

_BATCH = 50_000


def _iter_rows(path: str | Path) -> Iterator[list[str]]:
    """Yield split fields for each line of an RRF file."""
    with open(path, encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            if line:
                yield line.rstrip("\n").split("|")


def _batched(store: Store, table: str, columns: Sequence[str],
             rows: Iterable[Sequence], desc: str) -> int:
    batch: list[Sequence] = []
    total = 0
    bar = tqdm(desc=desc, unit=" rows", unit_scale=True)
    for row in rows:
        batch.append(row)
        if len(batch) >= _BATCH:
            total += store.insert_many(table, columns, batch)
            bar.update(len(batch))
            batch.clear()
    if batch:
        total += store.insert_many(table, columns, batch)
        bar.update(len(batch))
    bar.close()
    store.conn.commit()
    return total


def ingest_mrconso(store: Store, path: str | Path) -> tuple[int, int]:
    """Load AUI->CUI for every atom and a preferred English name per CUI."""
    best_name: dict[str, tuple[int, str]] = {}

    def aui_rows() -> Iterator[tuple[str, str]]:
        for r in _iter_rows(path):
            if len(r) < 15:
                continue
            cui, aui, name = r[0], r[7], r[14]
            yield aui, cui
            if r[1] == "ENG" and name:
                rank = (r[6] == "Y") * 4 + (r[2] == "P") * 2 + (r[4] == "PF")
                cur = best_name.get(cui)
                if cur is None or rank > cur[0]:
                    best_name[cui] = (rank, name)

    n_aui = _batched(store, "umls_aui2cui", ("aui", "cui"), aui_rows(), "MRCONSO aui->cui")
    n_name = _batched(store, "umls_name", ("cui", "name"),
                      ((cui, name) for cui, (_, name) in best_name.items()), "MRCONSO names")
    return n_aui, n_name


def ingest_mrdef(store: Store, path: str | Path, sources: Sequence[str]) -> int:
    keep = set(sources)

    def rows() -> Iterator[tuple[str, str, str]]:
        for r in _iter_rows(path):
            if len(r) >= 6 and (not keep or r[4] in keep):
                yield r[0], r[4], r[5]

    return _batched(store, "umls_def", ("cui", "source", "definition"), rows(), "MRDEF defs")


def ingest_mrhier(store: Store, path: str | Path, terminologies: Sequence[str]) -> int:
    keep = set(terminologies)

    def rows() -> Iterator[tuple[str, str, str, str]]:
        for r in _iter_rows(path):
            if len(r) >= 7 and r[4] in keep:
                yield r[4], r[0], r[1], r[6]  # sab, cui, aui, ptr

    return _batched(store, "umls_hier", ("sab", "cui", "aui", "ptr"), rows(), "MRHIER paths")


def ingest_mrrel(store: Store, path: str | Path, terminologies: Sequence[str]) -> int:
    keep = set(terminologies)

    def rows() -> Iterator[tuple[str, str, str, str, str]]:
        for r in _iter_rows(path):
            if len(r) < 11 or r[10] not in keep:
                continue
            if len(r) > 14 and r[14] not in ("", "N"):  # skip suppressed
                continue
            if r[0] == r[4]:
                continue
            yield r[0], r[3], r[7], r[4], r[10]  # cui1, rel, rela, cui2, sab

    return _batched(store, "umls_rel", ("cui1", "rel", "rela", "cui2", "sab"), rows(), "MRREL triples")


def ingest_code2cui(store: Store, path: str | Path, *, sab_substr: str = "SNOMED",
                    code_col: int = 9) -> int:
    """Map source codes (e.g. SNOMED ``SCUI``) to CUIs from MRCONSO (col 9 -> col 0)."""
    def rows() -> Iterator[tuple[str, str]]:
        for r in _iter_rows(path):
            if len(r) > 11 and sab_substr in r[11] and len(r) > code_col and r[code_col]:
                yield r[code_col], r[0]

    return _batched(store, "code2cui", ("code", "cui"), rows(), "MRCONSO code->cui")


def ingest_all(store: Store, cfg: Config) -> dict[str, int]:
    """Clear and rebuild every UMLS table from the configured RRF files."""
    um = cfg.umls
    for name, p in (("MRCONSO", um.mrconso), ("MRHIER", um.mrhier),
                    ("MRREL", um.mrrel), ("MRDEF", um.mrdef)):
        if not Path(p).is_file():
            raise FileNotFoundError(f"{name} file not found: {p}")

    store.clear_umls()
    n_aui, n_name = ingest_mrconso(store, um.mrconso)
    n_def = ingest_mrdef(store, um.mrdef, um.definition_sources)
    n_hier = ingest_mrhier(store, um.mrhier, um.terminologies)
    n_rel = ingest_mrrel(store, um.mrrel, um.terminologies)
    counts = {"aui2cui": n_aui, "names": n_name, "defs": n_def, "hier": n_hier, "rel": n_rel}
    if cfg.annotate.map_codes == "snomed":
        counts["code2cui"] = ingest_code2cui(store, um.mrconso)
    store.build_umls_indexes()
    store.set_meta("umls_ingested", "1")
    store.conn.commit()
    return counts
