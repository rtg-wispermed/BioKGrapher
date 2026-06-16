"""Build / update orchestration: download -> parse -> annotate -> index (resumable per file)."""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path

from tqdm import tqdm

from biokgrapher import download
from biokgrapher.annotate import AnnotatorLike, load_annotator
from biokgrapher.codec import unpack_ids
from biokgrapher.config import Config
from biokgrapher.parse import iter_deletions, iter_records
from biokgrapher.store import Store

BASELINE = "baseline"
UPDATEFILES = "updatefiles"


def process_file(store: Store, annotator: AnnotatorLike, path: str | Path,
                 name: str, kind: str, *, index_mesh: bool = True,
                 code2cui: dict[str, str] | None = None) -> tuple[int, int]:
    """Annotate and index one MEDLINE file atomically; return ``(n_records, n_deletes)``."""
    records = list(iter_records(str(path)))
    cui_map = annotator.annotate(records)
    deletions = list(iter_deletions(str(path))) if kind == UPDATEFILES else []

    with store.transaction():
        if kind == BASELINE:
            _index_fresh(store, records, cui_map, index_mesh, code2cui)  # bulk, insert-only
        else:
            _index_updates(store, records, cui_map, deletions, index_mesh, code2cui)
        store.mark_processed(name, kind, len(records), len(deletions))
    return len(records), len(deletions)


def _to_ids(store, codes, code2cui) -> set[int]:
    """Intern concept codes -> ids, mapping source codes (e.g. SNOMED) to CUIs if needed."""
    if code2cui is None:
        return {store.cuis.intern(c) for c in codes}
    return {store.cuis.intern(cui) for c in codes if (cui := code2cui.get(c))}


def _index_fresh(store, records, cui_map, index_mesh, code2cui) -> None:
    """Fast path for the baseline: no per-doc lookup/diff, one bulk insert per file."""
    docs, mesh_terms, mesh_pairs, years = [], [], [], []
    for rec in records:
        if index_mesh and rec.mesh:
            for ui, name in rec.mesh:
                mesh_terms.append((ui, name))
                mesh_pairs.append((ui, rec.pmid))
        ids = _to_ids(store, cui_map.get(rec.pmid, ()), code2cui)
        if ids:
            docs.append((rec.pmid, rec.version, ids))
            if rec.year is not None:
                years.append((rec.pmid, rec.year))
    store.add_documents(docs)
    store.add_years_bulk(years)
    if index_mesh:
        store.add_mesh_bulk(mesh_terms, mesh_pairs)


def _index_updates(store, records, cui_map, deletions, index_mesh, code2cui) -> None:
    """Update path: upsert/delete with frequency bookkeeping (revisions, withdrawals)."""
    for rec in records:
        if index_mesh:
            store.index_mesh(rec.pmid, rec.mesh)
        ids = _to_ids(store, cui_map.get(rec.pmid, ()), code2cui)
        if ids:
            store.upsert_pmid(rec.pmid, rec.version, ids)
            store.set_year(rec.pmid, rec.year)
        else:
            store.delete_pmid(rec.pmid)  # no-op unless a revision lost its concepts
            store.delete_year(rec.pmid)
    for pmid in deletions:
        store.delete_pmid(pmid)
        store.delete_year(pmid)
        if index_mesh:
            store.delete_mesh(pmid)


def _files_for(cfg: Config, store: Store, kind: str, *, skip_download: bool,
               limit: int | None, client, shard: tuple[int, int] | None) -> list[tuple[str, Path]]:
    dest = cfg.download.raw_dir / kind
    if skip_download:
        names = sorted(p.name for p in dest.glob("*.xml.gz")) if dest.is_dir() else []
        if shard is not None:
            i, k = shard
            names = [n for idx, n in enumerate(names) if idx % k == i]
        names = [n for n in names if not store.is_processed(n)]
        if limit is not None:
            names = names[:limit]
        return [(n, dest / n) for n in names]

    synced = download.sync(
        kind, dest, client, cfg.download.base_url,
        skip_names=store.processed_names(), limit=limit,
        max_concurrency=cfg.download.max_concurrency, shard=shard,
    )
    out: list[tuple[str, Path]] = []
    for rf, path, md5 in synced:
        store.mark_downloaded(rf.name, kind, md5)
        out.append((rf.name, path))
    return out


def _load_code2cui(cfg: Config, store: Store) -> dict[str, str] | None:
    """Source-code -> CUI map for SNOMED-style models (None if not needed)."""
    if cfg.annotate.map_codes != "snomed":
        return None
    mapping = store.load_code2cui()
    if mapping:
        return mapping
    # shard workers may not have ingested UMLS — build directly from MRCONSO
    from biokgrapher import umls
    mapping = {}
    for r in umls._iter_rows(cfg.umls.mrconso):
        if len(r) > 11 and "SNOMED" in r[11] and len(r) > 9 and r[9]:
            mapping.setdefault(r[9], r[0])
    return mapping


def _run_kind(cfg: Config, store: Store, annotator: AnnotatorLike, kind: str, *,
              skip_download: bool, limit: int | None, client,
              shard: tuple[int, int] | None = None,
              code2cui: dict[str, str] | None = None) -> dict[str, int]:
    files = _files_for(cfg, store, kind, skip_download=skip_download, limit=limit,
                       client=client, shard=shard)
    n_rec = n_del = 0
    for name, path in tqdm(files, desc=f"index {kind}", unit=" file"):
        if store.is_processed(name):
            continue
        if not Path(path).is_file():
            continue
        if kind == BASELINE and store.get_meta("baseline_year") is None:
            store.set_meta("baseline_year", _year_of(name))
        rec, dele = process_file(store, annotator, path, name, kind,
                                 index_mesh=cfg.store.index_mesh, code2cui=code2cui)
        n_rec += rec
        n_del += dele
    return {"files": len(files), "records": n_rec, "deletes": n_del}


def _year_of(name: str) -> str:
    # "pubmed26n0001.xml.gz" -> "26"
    digits = name.removeprefix("pubmed")
    return digits[: digits.index("n")] if "n" in digits else "?"


def shard_cfg(cfg: Config, shard: tuple[int, int]) -> Config:
    """Return a config whose DB/checkpoint paths are unique to this shard."""
    i, k = shard
    db = cfg.store.db_path
    sdb = db.parent / f"{db.stem}.shard{i}of{k}{db.suffix}"
    sck = cfg.store.checkpoint_dir / f"shard{i}of{k}"
    return replace(cfg, store=replace(cfg.store, db_path=sdb, checkpoint_dir=sck))


def run_build(cfg: Config, *, limit: int | None = None, include_updates: bool = True,
              skip_download: bool = False, annotator: AnnotatorLike | None = None,
              shard: tuple[int, int] | None = None) -> dict:
    """Full build: baseline (+ optional updatefiles); ``shard=(i,k)`` writes a shard DB to merge later."""
    if shard is not None:
        cfg = shard_cfg(cfg, shard)
        include_updates = False
    ann = annotator or load_annotator(cfg.annotate, checkpoint_dir=cfg.store.checkpoint_dir)
    client = None if skip_download else download.make_client()
    try:
        with Store(cfg.store.db_path, commit_every=cfg.store.commit_every) as store:
            if store.get_meta("umls_ingested") != "1" and shard is None:
                tqdm.write("warning: UMLS not ingested yet — run `biokgrapher ingest-umls` "
                           "for hierarchy/definitions/graph views.")
            store.set_meta("model_pack", str(cfg.annotate.model_pack))
            code2cui = _load_code2cui(cfg, store)
            result = {BASELINE: _run_kind(cfg, store, ann, BASELINE, skip_download=skip_download,
                                          limit=limit, client=client, shard=shard, code2cui=code2cui)}
            if include_updates:
                result[UPDATEFILES] = _run_kind(cfg, store, ann, UPDATEFILES, skip_download=skip_download,
                                                limit=limit, client=client, code2cui=code2cui)
            return result
    finally:
        if client is not None:
            client.close()


def run_update(cfg: Config, *, limit: int | None = None, skip_download: bool = False,
               annotator: AnnotatorLike | None = None) -> dict:
    """Pull new updatefiles and apply upserts + deletions."""
    ann = annotator or load_annotator(cfg.annotate, checkpoint_dir=cfg.store.checkpoint_dir)
    client = None if skip_download else download.make_client()
    try:
        with Store(cfg.store.db_path, commit_every=cfg.store.commit_every) as store:
            res = _run_kind(cfg, store, ann, UPDATEFILES, skip_download=skip_download,
                            limit=limit, client=client, code2cui=_load_code2cui(cfg, store))
            store.clear_mesh_aggregates()  # corpus changed -> recompute lazily
            return {UPDATEFILES: res}
    finally:
        if client is not None:
            client.close()


def merge_shards(cfg: Config, shard_paths: list[str | Path], *, batch: int = 20_000) -> dict:
    """Additively merge shard DBs into the main index (re-mapping concept ids)."""
    merged = {"shards": 0, "pmids": 0}
    with Store(cfg.store.db_path, commit_every=cfg.store.commit_every) as main:
        for sp in tqdm(shard_paths, desc="merge shards", unit=" shard"):
            shard = sqlite3.connect(str(sp))
            try:
                # remap shard concept ids -> main concept ids once
                id_map = {sid: main.cuis.intern(cui)
                          for sid, cui in shard.execute("SELECT id, cui FROM concept")}
                with main.transaction():
                    docs = []
                    for pmid, version, blob in shard.execute(
                            "SELECT pmid, version, concepts FROM pmid_concepts"):
                        ids = {id_map[i] for i in unpack_ids(blob)}
                        docs.append((pmid, version, ids))
                        merged["pmids"] += 1
                        if len(docs) >= batch:
                            main.add_documents(docs)
                            docs = []
                    main.add_documents(docs)
                    main.add_mesh_bulk(
                        list(shard.execute("SELECT ui, name FROM mesh_term")),
                        list(shard.execute("SELECT ui, pmid FROM mesh_pmid")))
                    main.conn.executemany(
                        "INSERT OR IGNORE INTO pmid_year(pmid, year) VALUES (?, ?)",
                        list(shard.execute("SELECT pmid, year FROM pmid_year")))
                    main.conn.executemany(
                        "INSERT OR IGNORE INTO file_manifest"
                        "(name, kind, md5, downloaded_at, processed_at, n_records, n_deletes) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        list(shard.execute("SELECT name, kind, md5, downloaded_at, "
                                          "processed_at, n_records, n_deletes FROM file_manifest")))
            finally:
                shard.close()
            merged["shards"] += 1
        main.set_meta("baseline_year", main.get_meta("baseline_year") or "?")
    return merged
