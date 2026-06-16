"""SQLite index: per-PMID concept blobs, global frequencies, UMLS lookups, MeSH index (§4.2)."""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from biokgrapher.codec import CuiInterner, pack_ids, unpack_ids

SCHEMA_VERSION = "1"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS concept (
    id  INTEGER PRIMARY KEY,
    cui TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS pmid_concepts (
    pmid     INTEGER PRIMARY KEY,
    version  INTEGER NOT NULL,
    n        INTEGER NOT NULL,
    concepts BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS concept_freq (
    concept_id INTEGER PRIMARY KEY,
    df         INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS corpus_stats (
    id             INTEGER PRIMARY KEY CHECK (id = 1),
    n_pmids        INTEGER NOT NULL DEFAULT 0,
    total_postings INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS file_manifest (
    name          TEXT PRIMARY KEY,
    kind          TEXT NOT NULL,
    md5           TEXT,
    downloaded_at TEXT,
    processed_at  TEXT,
    n_records     INTEGER,
    n_deletes     INTEGER
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

-- Publication year per PMID, for concept trend-over-time analysis.
CREATE TABLE IF NOT EXISTS pmid_year (pmid INTEGER PRIMARY KEY, year INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS ix_pmid_year_year ON pmid_year(year);

-- MeSH inverted index: resolve a descriptor to its PMID set instantly (fast presets).
CREATE TABLE IF NOT EXISTS mesh_term (ui TEXT PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS mesh_pmid (ui TEXT NOT NULL, pmid INTEGER NOT NULL,
    PRIMARY KEY (ui, pmid));
CREATE INDEX IF NOT EXISTS ix_mesh_pmid_pmid ON mesh_pmid(pmid);
CREATE INDEX IF NOT EXISTS ix_mesh_name ON mesh_term(name COLLATE NOCASE);

-- Aggregated concept document-frequency per MeSH descriptor (built lazily / by precompute),
-- so scoring a MeSH term is O(#concepts) instead of scanning all its PMID blobs.
CREATE TABLE IF NOT EXISTS mesh_concept_freq (
    ui TEXT NOT NULL, concept_id INTEGER NOT NULL, df INTEGER NOT NULL,
    PRIMARY KEY (ui, concept_id));
CREATE TABLE IF NOT EXISTS mesh_doc_count (ui TEXT PRIMARY KEY, n INTEGER NOT NULL);

-- Precomputed graph payloads (the whole /api/graph response) keyed by source+params.
CREATE TABLE IF NOT EXISTS preset_graph (key TEXT PRIMARY KEY, payload TEXT, built_at TEXT);

-- Source-code -> CUI map (e.g. SNOMED code -> CUI) for models that emit source codes.
CREATE TABLE IF NOT EXISTS code2cui (code TEXT PRIMARY KEY, cui TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS umls_aui2cui (aui TEXT PRIMARY KEY, cui TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS umls_name    (cui TEXT PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS umls_def     (cui TEXT NOT NULL, source TEXT, definition TEXT);
CREATE TABLE IF NOT EXISTS umls_hier (
    sab TEXT NOT NULL, cui TEXT NOT NULL, aui TEXT NOT NULL, ptr TEXT
);
CREATE TABLE IF NOT EXISTS umls_rel (
    cui1 TEXT NOT NULL, rel TEXT, rela TEXT, cui2 TEXT NOT NULL, sab TEXT
);
"""

UMLS_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS ix_def_cui   ON umls_def(cui);
CREATE INDEX IF NOT EXISTS ix_hier_sab_cui ON umls_hier(sab, cui);
CREATE INDEX IF NOT EXISTS ix_rel_cui1  ON umls_rel(cui1);
CREATE INDEX IF NOT EXISTS ix_rel_cui2  ON umls_rel(cui2);
"""

_PARAM_CHUNK = 900  # stay well under SQLITE_MAX_VARIABLE_NUMBER


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _chunks(seq: Sequence, size: int = _PARAM_CHUNK):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


class Store:
    """Owns the SQLite connection and every query against it."""

    def __init__(self, db_path: str | Path, *, commit_every: int = 5000,
                 allow_cross_thread: bool = False) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.commit_every = commit_every
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=not allow_cross_thread)
        self._configure()
        self._create_schema()
        self.cuis = CuiInterner(self.conn)
        self.cuis.load()
        # in-memory accumulators flushed per file/batch for write throughput
        self._df_delta: Counter[int] = Counter()
        self._d_pmids = 0
        self._d_postings = 0

    # -- lifecycle ------------------------------------------------------------
    def _configure(self) -> None:
        for pragma in (
            "journal_mode=WAL",
            "synchronous=NORMAL",
            "temp_store=MEMORY",
            "cache_size=-524288",       # ~512 MB page cache
            "mmap_size=1073741824",     # 1 GB memory-mapped I/O
            "wal_autocheckpoint=20000",  # fewer checkpoint stalls during bulk load
        ):
            self.conn.execute(f"PRAGMA {pragma}")

    def _create_schema(self) -> None:
        self.conn.executescript(SCHEMA_SQL)
        self.conn.execute("INSERT OR IGNORE INTO corpus_stats(id, n_pmids, total_postings) "
                          "VALUES (1, 0, 0)")
        self.set_meta("schema_version", SCHEMA_VERSION)
        self.conn.commit()

    def close(self) -> None:
        try:
            self.flush_deltas()
            self.conn.commit()
        finally:
            self.conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @contextmanager
    def transaction(self):
        """Run a unit of work atomically, flushing accumulated counter deltas."""
        try:
            yield self.conn
            self.flush_deltas()
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            self._df_delta.clear()
            self._d_pmids = 0
            self._d_postings = 0
            raise

    # -- meta -----------------------------------------------------------------
    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO meta(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )

    def get_meta(self, key: str, default: str | None = None) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else default

    # -- document writes (incremental, counter-exact) -------------------------
    def _stored_ids(self, pmid: int) -> tuple[set[int], int] | None:
        row = self.conn.execute(
            "SELECT version, concepts FROM pmid_concepts WHERE pmid = ?", (pmid,)
        ).fetchone()
        if row is None:
            return None
        return set(unpack_ids(row[1])), int(row[0])

    def upsert_pmid(self, pmid: int, version: int, cui_ids: Iterable[int]) -> None:
        """Insert/revise a PMID, adjusting global frequencies by the delta (stale revisions ignored)."""
        new_ids = set(cui_ids)
        prev = self._stored_ids(pmid)
        if prev is not None:
            old_ids, old_version = prev
            if version < old_version:
                return
        else:
            old_ids = set()
            self._d_pmids += 1
        for cid in new_ids - old_ids:
            self._df_delta[cid] += 1
        for cid in old_ids - new_ids:
            self._df_delta[cid] -= 1
        self._d_postings += len(new_ids) - len(old_ids)
        self.conn.execute(
            "INSERT INTO pmid_concepts(pmid, version, n, concepts) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(pmid) DO UPDATE SET version=excluded.version, "
            "n=excluded.n, concepts=excluded.concepts",
            (pmid, version, len(new_ids), pack_ids(new_ids)),
        )

    def add_documents(self, docs: Iterable[tuple[int, int, set[int]]]) -> None:
        """Insert-only fast path for a fresh build: ``(pmid, version, concept_ids)`` tuples."""
        rows = []
        for pmid, version, ids in docs:
            self._df_delta.update(ids)
            self._d_pmids += 1
            self._d_postings += len(ids)
            rows.append((pmid, version, len(ids), pack_ids(ids)))
        if rows:
            self.conn.executemany(
                "INSERT INTO pmid_concepts(pmid, version, n, concepts) VALUES (?, ?, ?, ?)", rows)

    def add_mesh_bulk(self, terms: Sequence[tuple[str, str]],
                      pmids: Sequence[tuple[str, int]]) -> None:
        """Insert-only MeSH rows for a fresh build (skips the per-doc delete of upserts)."""
        if terms:
            self.conn.executemany(
                "INSERT OR IGNORE INTO mesh_term(ui, name) VALUES (?, ?)", terms)
        if pmids:
            self.conn.executemany(
                "INSERT OR IGNORE INTO mesh_pmid(ui, pmid) VALUES (?, ?)", pmids)

    def delete_pmid(self, pmid: int) -> None:
        prev = self._stored_ids(pmid)
        if prev is None:
            return
        old_ids, _ = prev
        for cid in old_ids:
            self._df_delta[cid] -= 1
        self._d_pmids -= 1
        self._d_postings -= len(old_ids)
        self.conn.execute("DELETE FROM pmid_concepts WHERE pmid = ?", (pmid,))

    # -- publication years (trend analysis) -----------------------------------
    def add_years_bulk(self, rows: Sequence[tuple[int, int]]) -> None:
        if rows:
            self.conn.executemany(
                "INSERT OR REPLACE INTO pmid_year(pmid, year) VALUES (?, ?)", rows)

    def set_year(self, pmid: int, year: int | None) -> None:
        if year is None:
            self.conn.execute("DELETE FROM pmid_year WHERE pmid = ?", (pmid,))
        else:
            self.conn.execute(
                "INSERT INTO pmid_year(pmid, year) VALUES (?, ?) "
                "ON CONFLICT(pmid) DO UPDATE SET year = excluded.year", (pmid, year))

    def delete_year(self, pmid: int) -> None:
        self.conn.execute("DELETE FROM pmid_year WHERE pmid = ?", (pmid,))

    def max_year(self) -> int | None:
        row = self.conn.execute("SELECT MAX(year) FROM pmid_year").fetchone()
        return int(row[0]) if row and row[0] is not None else None

    def concepts_by_period(self, pmids: Iterable[int], recent_from: int, hist_from: int
                           ) -> tuple[Counter[int], int, Counter[int], int]:
        """Split a PMID set into recent (year>=recent_from) and historical windows of concept df."""
        ids = {int(p) for p in pmids}
        if not ids:
            return Counter(), 0, Counter(), 0
        self.conn.execute("CREATE TEMP TABLE IF NOT EXISTS _sel(pmid INTEGER PRIMARY KEY)")
        self.conn.execute("DELETE FROM _sel")
        self.conn.executemany("INSERT OR IGNORE INTO _sel(pmid) VALUES (?)", ((p,) for p in ids))
        recent: Counter[int] = Counter()
        hist: Counter[int] = Counter()
        n_recent = n_hist = 0
        for blob, year in self.conn.execute(
            "SELECT p.concepts, y.year FROM pmid_concepts p "
            "JOIN _sel s ON p.pmid = s.pmid JOIN pmid_year y ON p.pmid = y.pmid"
        ):
            cids = unpack_ids(blob)
            if not cids:
                continue
            if year >= recent_from:
                n_recent += 1
                recent.update(cids)
            elif year >= hist_from:
                n_hist += 1
                hist.update(cids)
        self.conn.execute("DELETE FROM _sel")
        return recent, n_recent, hist, n_hist

    def flush_deltas(self) -> None:
        """Apply accumulated frequency/stat deltas to the persistent tables."""
        if self._df_delta:
            self.conn.executemany(
                "INSERT INTO concept_freq(concept_id, df) VALUES (?, ?) "
                "ON CONFLICT(concept_id) DO UPDATE SET df = df + excluded.df",
                list(self._df_delta.items()),
            )
            self._df_delta.clear()
        if self._d_pmids or self._d_postings:
            self.conn.execute(
                "UPDATE corpus_stats SET n_pmids = n_pmids + ?, "
                "total_postings = total_postings + ? WHERE id = 1",
                (self._d_pmids, self._d_postings),
            )
            self._d_pmids = 0
            self._d_postings = 0

    # -- manifest -------------------------------------------------------------
    def is_processed(self, name: str) -> bool:
        row = self.conn.execute(
            "SELECT processed_at FROM file_manifest WHERE name = ?", (name,)
        ).fetchone()
        return bool(row and row[0])

    def mark_downloaded(self, name: str, kind: str, md5: str | None) -> None:
        self.conn.execute(
            "INSERT INTO file_manifest(name, kind, md5, downloaded_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(name) DO UPDATE SET md5=excluded.md5, downloaded_at=excluded.downloaded_at",
            (name, kind, md5, _now()),
        )
        self.conn.commit()

    def processed_names(self) -> set[str]:
        return {r[0] for r in self.conn.execute(
            "SELECT name FROM file_manifest WHERE processed_at IS NOT NULL")}

    def mark_processed(self, name: str, kind: str, n_records: int, n_deletes: int) -> None:
        self.conn.execute(
            "INSERT INTO file_manifest(name, kind, processed_at, n_records, n_deletes) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(name) DO UPDATE SET "
            "processed_at=excluded.processed_at, n_records=excluded.n_records, "
            "n_deletes=excluded.n_deletes",
            (name, kind, _now(), n_records, n_deletes),
        )

    # -- MeSH inverted index --------------------------------------------------
    def index_mesh(self, pmid: int, mesh: Sequence[tuple[str, str]]) -> None:
        """Replace the MeSH rows for ``pmid`` (idempotent across revisions)."""
        self.conn.execute("DELETE FROM mesh_pmid WHERE pmid = ?", (pmid,))
        if not mesh:
            return
        self.conn.executemany("INSERT OR IGNORE INTO mesh_term(ui, name) VALUES (?, ?)", mesh)
        self.conn.executemany("INSERT OR IGNORE INTO mesh_pmid(ui, pmid) VALUES (?, ?)",
                              [(ui, pmid) for ui, _ in mesh])

    def delete_mesh(self, pmid: int) -> None:
        self.conn.execute("DELETE FROM mesh_pmid WHERE pmid = ?", (pmid,))

    def resolve_mesh_ui(self, key: str) -> str | None:
        """Resolve a descriptor UI or (case-insensitive) name to its UI."""
        row = self.conn.execute(
            "SELECT ui FROM mesh_term WHERE ui = ? OR name = ? COLLATE NOCASE LIMIT 1",
            (key, key)).fetchone()
        return row[0] if row else None

    def pmids_for_mesh(self, key: str) -> list[int]:
        ui = self.resolve_mesh_ui(key)
        if ui is None:
            return []
        return [r[0] for r in self.conn.execute(
            "SELECT pmid FROM mesh_pmid WHERE ui = ?", (ui,))]

    def mesh_pmid_count(self, ui: str) -> int:
        return int(self.conn.execute(
            "SELECT COUNT(*) FROM mesh_pmid WHERE ui = ?", (ui,)).fetchone()[0])

    # -- per-MeSH aggregated concept frequencies (fast scoring) ---------------
    def get_mesh_aggregate(self, ui: str) -> tuple[Counter[int], int] | None:
        """Return ``(concept_id->df Counter, n_docs)`` for a descriptor, or None."""
        row = self.conn.execute("SELECT n FROM mesh_doc_count WHERE ui = ?", (ui,)).fetchone()
        if row is None:
            return None
        counter: Counter[int] = Counter(dict(self.conn.execute(
            "SELECT concept_id, df FROM mesh_concept_freq WHERE ui = ?", (ui,))))
        return counter, int(row[0])

    def put_mesh_aggregate(self, ui: str, counter: Mapping[int, int], n: int) -> None:
        self.conn.execute("DELETE FROM mesh_concept_freq WHERE ui = ?", (ui,))
        self.conn.executemany(
            "INSERT INTO mesh_concept_freq(ui, concept_id, df) VALUES (?, ?, ?)",
            [(ui, cid, df) for cid, df in counter.items()])
        self.conn.execute(
            "INSERT INTO mesh_doc_count(ui, n) VALUES (?, ?) "
            "ON CONFLICT(ui) DO UPDATE SET n = excluded.n", (ui, n))
        self.conn.commit()

    def clear_mesh_aggregates(self) -> None:
        """Invalidate cached aggregates (call after an update changes the corpus)."""
        self.conn.execute("DELETE FROM mesh_concept_freq")
        self.conn.execute("DELETE FROM mesh_doc_count")
        self.conn.commit()

    # -- precomputed graph cache ----------------------------------------------
    def get_preset_graph(self, key: str) -> dict | None:
        row = self.conn.execute(
            "SELECT payload FROM preset_graph WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def put_preset_graph(self, key: str, payload: dict) -> None:
        self.conn.execute(
            "INSERT INTO preset_graph(key, payload, built_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET payload=excluded.payload, built_at=excluded.built_at",
            (key, json.dumps(payload), _now()))
        self.conn.commit()

    # -- scoring inputs (read side) -------------------------------------------
    def global_frequencies(self) -> tuple[dict[int, int], int]:
        """Return ``({concept_id: df}, n_global_docs)`` for scoring's ``P(c)``."""
        freqs = dict(self.conn.execute("SELECT concept_id, df FROM concept_freq WHERE df > 0"))
        n = self.conn.execute("SELECT n_pmids FROM corpus_stats WHERE id = 1").fetchone()
        return freqs, int(n[0] if n else 0)

    def concepts_for_pmids(self, pmids: Iterable[int]) -> tuple[Counter[int], int]:
        """Return ``(local document-frequency Counter, n_matched_docs)`` for a subset."""
        ids = {int(p) for p in pmids}
        if not ids:
            return Counter(), 0
        self.conn.execute("CREATE TEMP TABLE IF NOT EXISTS _sel(pmid INTEGER PRIMARY KEY)")
        self.conn.execute("DELETE FROM _sel")
        self.conn.executemany("INSERT OR IGNORE INTO _sel(pmid) VALUES (?)",
                              ((p,) for p in ids))
        local: Counter[int] = Counter()
        matched = 0
        for (blob,) in self.conn.execute(
            "SELECT p.concepts FROM pmid_concepts p JOIN _sel s ON p.pmid = s.pmid"
        ):
            cids = unpack_ids(blob)
            if cids:
                matched += 1
                local.update(cids)
        self.conn.execute("DELETE FROM _sel")
        return local, matched

    # -- UMLS bulk load (write side, used by umls.py) -------------------------
    def clear_umls(self) -> None:
        for tbl in ("umls_aui2cui", "umls_name", "umls_def", "umls_hier", "umls_rel", "code2cui"):
            self.conn.execute(f"DELETE FROM {tbl}")
        self.conn.commit()

    def load_code2cui(self) -> dict[str, str]:
        return dict(self.conn.execute("SELECT code, cui FROM code2cui"))

    def insert_many(self, table: str, columns: Sequence[str], rows: Iterable[Sequence]) -> int:
        placeholders = ", ".join("?" for _ in columns)
        sql = f"INSERT INTO {table}({', '.join(columns)}) VALUES ({placeholders})"
        if table in ("umls_aui2cui", "umls_name", "code2cui"):  # PK tables: tolerate dups
            sql = sql.replace("INSERT INTO", "INSERT OR IGNORE INTO")
        cur = self.conn.executemany(sql, rows)
        return cur.rowcount

    def build_umls_indexes(self) -> None:
        self.conn.executescript(UMLS_INDEX_SQL)
        self.conn.commit()

    # -- UMLS reads (used by api.py) ------------------------------------------
    def names_for(self, cuis: Sequence[str]) -> dict[str, str]:
        out: dict[str, str] = {}
        for chunk in _chunks(list(cuis)):
            q = f"SELECT cui, name FROM umls_name WHERE cui IN ({','.join('?' * len(chunk))})"
            out.update(self.conn.execute(q, chunk))
        return out

    def definitions_for(self, cuis: Sequence[str], sources: Sequence[str]) -> dict[str, str]:
        out: dict[str, str] = {}
        src_clause = ""
        if sources:
            src_clause = f" AND source IN ({','.join('?' * len(sources))})"
        for chunk in _chunks(list(cuis)):
            params = list(chunk) + list(sources)
            q = (f"SELECT cui, definition FROM umls_def "
                 f"WHERE cui IN ({','.join('?' * len(chunk))}){src_clause}")
            for cui, definition in self.conn.execute(q, params):
                out.setdefault(cui, definition)  # first definition per CUI
        return out

    def hier_paths(self, cuis: Sequence[str], terminology: str) -> list[tuple[str, str]]:
        """Rows ``(aui, ptr)`` from MRHIER whose leaf CUI is in ``cuis`` for one SAB."""
        rows: list[tuple[str, str]] = []
        for chunk in _chunks(list(cuis)):
            q = (f"SELECT aui, ptr FROM umls_hier WHERE sab = ? "
                 f"AND cui IN ({','.join('?' * len(chunk))})")
            rows.extend(self.conn.execute(q, [terminology, *chunk]))
        return rows

    def aui_to_cui_map(self, auis: Sequence[str]) -> dict[str, str]:
        out: dict[str, str] = {}
        uniq = list(set(auis))
        for chunk in _chunks(uniq):
            q = f"SELECT aui, cui FROM umls_aui2cui WHERE aui IN ({','.join('?' * len(chunk))})"
            out.update(self.conn.execute(q, chunk))
        return out

    def triples_for(self, cuis: Sequence[str], max_edges: int) -> tuple[list[tuple[str, str, str]], bool]:
        """MRREL triples ``(cui1, relation, cui2)`` with both endpoints in ``cuis`` (RELA else REL)."""
        allowed = set(cuis)
        if not allowed:
            return [], False
        seen: set[tuple[str, str]] = set()
        edges: list[tuple[str, str, str]] = []
        truncated = False
        for chunk in _chunks(list(allowed)):
            q = (f"SELECT cui1, COALESCE(NULLIF(rela, ''), rel, ''), cui2 FROM umls_rel "
                 f"WHERE cui1 IN ({','.join('?' * len(chunk))})")
            for cui1, relation, cui2 in self.conn.execute(q, chunk):
                if cui1 == cui2 or cui2 not in allowed:
                    continue
                key = (cui1, cui2) if cui1 < cui2 else (cui2, cui1)
                if key in seen:
                    continue
                seen.add(key)
                edges.append((cui1, relation, cui2))
                if len(edges) >= max_edges:
                    return edges, True
        return edges, truncated

    # -- maintenance ----------------------------------------------------------
    def recompute_counters(self) -> None:
        """Rebuild ``concept_freq``/``corpus_stats`` from ``pmid_concepts`` (status --verify)."""
        self._df_delta.clear()  # discard pending deltas; we rebuild from source of truth
        self._d_pmids = self._d_postings = 0
        freq: Counter[int] = Counter()
        n_pmids = 0
        postings = 0
        for (blob,) in self.conn.execute("SELECT concepts FROM pmid_concepts"):
            cids = unpack_ids(blob)
            n_pmids += 1
            postings += len(cids)
            freq.update(cids)
        with self.transaction() as conn:
            conn.execute("DELETE FROM concept_freq")
            conn.executemany("INSERT INTO concept_freq(concept_id, df) VALUES (?, ?)",
                             list(freq.items()))
            conn.execute("UPDATE corpus_stats SET n_pmids = ?, total_postings = ? WHERE id = 1",
                         (n_pmids, postings))

    def counts(self) -> dict[str, int]:
        c = self.conn.execute
        return {
            "concepts": c("SELECT COUNT(*) FROM concept").fetchone()[0],
            "pmids": c("SELECT COUNT(*) FROM pmid_concepts").fetchone()[0],
            "concept_freq_rows": c("SELECT COUNT(*) FROM concept_freq WHERE df > 0").fetchone()[0],
            "files_processed": c("SELECT COUNT(*) FROM file_manifest WHERE processed_at IS NOT NULL").fetchone()[0],
            "umls_names": c("SELECT COUNT(*) FROM umls_name").fetchone()[0],
            "umls_rels": c("SELECT COUNT(*) FROM umls_rel").fetchone()[0],
            "mesh_terms": c("SELECT COUNT(*) FROM mesh_term").fetchone()[0],
            "mesh_postings": c("SELECT COUNT(*) FROM mesh_pmid").fetchone()[0],
            "preset_graphs": c("SELECT COUNT(*) FROM preset_graph").fetchone()[0],
        }
