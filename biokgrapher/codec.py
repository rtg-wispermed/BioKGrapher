"""Pack/unpack concept-id blobs and intern CUI strings to dense integer ids."""

from __future__ import annotations

import sqlite3
import sys
from array import array
from collections.abc import Iterable

_TYPECODE = "I"  # unsigned int, >= 32 bits on every supported platform
_BIG_ENDIAN = sys.byteorder == "big"


def pack_ids(ids: Iterable[int]) -> bytes:
    """Pack concept ids into a little-endian ``uint32`` blob (sorted, de-duplicated)."""
    arr = array(_TYPECODE, sorted(set(ids)))
    if _BIG_ENDIAN:  # pragma: no cover - normalise so DBs are portable
        arr.byteswap()
    return arr.tobytes()


def unpack_ids(blob: bytes) -> list[int]:
    """Inverse of :func:`pack_ids`."""
    if not blob:
        return []
    arr = array(_TYPECODE)
    arr.frombytes(blob)
    if _BIG_ENDIAN:  # pragma: no cover
        arr.byteswap()
    return arr.tolist()


class CuiInterner:
    """Maps CUI strings to dense integer ids via the ``concept`` table (cached in memory)."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._cui_to_id: dict[str, int] = {}
        self._id_to_cui: dict[int, str] = {}

    def load(self) -> None:
        """Warm the cache from the table (call once after opening the DB)."""
        for cid, cui in self._conn.execute("SELECT id, cui FROM concept"):
            self._cui_to_id[cui] = cid
            self._id_to_cui[cid] = cui

    def intern(self, cui: str) -> int:
        """Return the id for ``cui``, creating a new row if needed."""
        cid = self._cui_to_id.get(cui)
        if cid is not None:
            return cid
        cur = self._conn.execute(
            "INSERT OR IGNORE INTO concept(cui) VALUES (?)", (cui,)
        )
        if cur.lastrowid and cur.rowcount:
            cid = int(cur.lastrowid)
        else:  # raced/pre-existing — read it back
            cid = int(self._conn.execute(
                "SELECT id FROM concept WHERE cui = ?", (cui,)
            ).fetchone()[0])
        self._cui_to_id[cui] = cid
        self._id_to_cui[cid] = cui
        return cid

    def to_cui(self, cid: int) -> str | None:
        cui = self._id_to_cui.get(cid)
        if cui is None:
            row = self._conn.execute(
                "SELECT cui FROM concept WHERE id = ?", (cid,)
            ).fetchone()
            if row:
                cui = row[0]
                self._id_to_cui[cid] = cui
                self._cui_to_id[cui] = cid
        return cui
