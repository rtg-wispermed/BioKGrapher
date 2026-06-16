"""MedCAT (v2) NER+NEL wrapper (§4.2); imported lazily so the package works without medcat."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Protocol, runtime_checkable

from biokgrapher.config import AnnotateConfig
from biokgrapher.parse import Record


@runtime_checkable
class AnnotatorLike(Protocol):
    def annotate(self, records: Iterable[Record]) -> dict[int, set[str]]:
        ...


def _codes(entities, tui_filter: frozenset[str]) -> set[str]:
    """Extract the set of concept codes from a v2 ``Entities`` result (``None`` for empty text)."""
    if entities is None:
        return set()
    items = entities.entities if hasattr(entities, "entities") else entities.get("entities", {})
    values = items.values() if isinstance(items, dict) else items
    out: set[str] = set()
    for ent in values:
        cui = ent.get("cui") if isinstance(ent, dict) else getattr(ent, "cui", None)
        if not cui:
            continue
        if tui_filter:
            tids = ent.get("type_ids") if isinstance(ent, dict) else getattr(ent, "type_ids", None)
            if not (set(tids or ()) & tui_filter):
                continue
        out.add(str(cui))
    return out


def _disable_meta_cat(cat) -> None:
    """Drop MetaCAT addons from the pipeline so annotation skips the Status transformer."""
    try:
        cat._pipeline._addons.clear()
    except Exception:  # pragma: no cover - pipeline layout differs
        pass
    try:
        cat.config.components.addons = []
    except Exception:  # pragma: no cover
        pass


class Annotator:
    """Wraps a loaded MedCAT v2 ``CAT`` model pack (converts legacy v1 packs on load)."""

    def __init__(self, cfg: AnnotateConfig, *, checkpoint_dir: str | Path | None = None) -> None:
        from medcat.cat import CAT  # lazy: only needed for real builds

        self.cfg = cfg
        self.checkpoint_dir = str(checkpoint_dir) if checkpoint_dir else None
        self.cat = CAT.load_model_pack(str(cfg.model_pack))
        if not cfg.meta_cat:
            _disable_meta_cat(self.cat)
        if cfg.spell_check is not None:
            try:
                self.cat.config.general.spell_check = cfg.spell_check
            except AttributeError:  # pragma: no cover
                pass
        if cfg.cui_filter:
            try:
                self.cat.config.components.linking.filters.cuis = set(cfg.cui_filter)
            except AttributeError:  # pragma: no cover - config layout differs
                pass

    def annotate(self, records: Iterable[Record]) -> dict[int, set[str]]:
        data = [(str(r.pmid), r.text()) for r in records if r.has_abstract]
        if not data:
            return {}
        results = self.cat.get_entities_multi_texts(
            data,
            only_cui=False,
            n_process=self.cfg.effective_n_process,
            batch_size_chars=self.cfg.batch_size_chars,
        )
        return {int(pmid): _codes(ents, self.cfg.tui_filter) for pmid, ents in results}


def load_annotator(cfg: AnnotateConfig, *, checkpoint_dir: str | Path | None = None) -> Annotator:
    if not Path(cfg.model_pack).exists():
        raise FileNotFoundError(
            f"MedCAT model pack not found: {cfg.model_pack}. Download a public pack "
            "(see README) or set [annotate].model_pack in biokgrapher.toml."
        )
    return Annotator(cfg, checkpoint_dir=checkpoint_dir)
