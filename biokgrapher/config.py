"""Typed configuration loaded from biokgrapher.toml (defaults fill any missing keys)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - 3.10 backport
    import tomli as tomllib  # type: ignore

DEFAULT_TERMINOLOGIES = (
    "SNOMEDCT_US", "NCI", "MSH", "MSHGER", "LNC", "ATC",
    "ICD10", "ICD10CM", "ICD10PCS", "FMA",
)


@dataclass(frozen=True)
class ScoringConfig:
    alpha: float = 0.5
    beta: float = 0.5
    top_k: int = 2500
    max_depth: int = 5
    exclude_subset: bool = True  # compute background freq excluding the subset (paper §4.3)


@dataclass(frozen=True)
class UMLSConfig:
    mrconso: Path = Path("data/umls/MRCONSO.RRF")
    mrhier: Path = Path("data/umls/MRHIER.RRF")
    mrrel: Path = Path("data/umls/MRREL.RRF")
    mrdef: Path = Path("data/umls/MRDEF.RRF")
    terminologies: tuple[str, ...] = DEFAULT_TERMINOLOGIES
    definition_sources: tuple[str, ...] = ("NCI",)


@dataclass(frozen=True)
class AnnotateConfig:
    model_pack: Path = Path("models/mc_modelpack_snomed_int_16_mar_2022_25be3857ba34bdd5.zip")
    n_process: int = 0  # 0 => auto (cpu_count - 1)
    batch_size_chars: int = 5_000_000
    cui_filter: frozenset[str] = frozenset()
    tui_filter: frozenset[str] = frozenset()
    # "none": the model emits UMLS CUIs directly (UMLS-Full pack).
    # "snomed": the model emits SNOMED codes -> mapped to CUIs via MRCONSO (SNOMED pack).
    map_codes: str = "snomed"
    meta_cat: bool = False  # run MetaCAT (Status) meta-annotations
    spell_check: bool | None = None  # None keeps the model default; True/False overrides it

    @property
    def effective_n_process(self) -> int:
        if self.n_process > 0:
            return self.n_process
        return max(1, (os.cpu_count() or 2) - 1)


@dataclass(frozen=True)
class DownloadConfig:
    base_url: str = "https://ftp.ncbi.nlm.nih.gov/pubmed"
    raw_dir: Path = Path("data/pubmed")
    max_concurrency: int = 4


@dataclass(frozen=True)
class StoreConfig:
    db_path: Path = Path("biokg.db")
    commit_every: int = 5000
    checkpoint_dir: Path = Path("data/medcat_ckpt")
    index_mesh: bool = True  # build the MeSH->PMID inverted index for fast presets


@dataclass(frozen=True)
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 6006
    preset_folder: Path = Path("presets")
    max_pmids: int = 500_000
    max_edges: int = 4000
    mesh_presets: tuple[str, ...] = ()  # MeSH terms/UIs offered as predefined presets


@dataclass(frozen=True)
class Config:
    scoring: ScoringConfig = field(default_factory=ScoringConfig)
    umls: UMLSConfig = field(default_factory=UMLSConfig)
    annotate: AnnotateConfig = field(default_factory=AnnotateConfig)
    download: DownloadConfig = field(default_factory=DownloadConfig)
    store: StoreConfig = field(default_factory=StoreConfig)
    server: ServerConfig = field(default_factory=ServerConfig)


# --- loading -----------------------------------------------------------------

def _section(raw: dict[str, Any], name: str) -> dict[str, Any]:
    value = raw.get(name, {})
    if not isinstance(value, dict):
        raise ValueError(f"[{name}] must be a table in the config file")
    return value


def _resolve(base: Path, *parts: Path) -> tuple[Path, ...]:
    """Resolve each path against ``base`` unless it is already absolute."""
    return tuple(p if p.is_absolute() else (base / p) for p in parts)


def load_config(path: str | os.PathLike[str] = "biokgrapher.toml") -> Config:
    """Load a :class:`Config` from a TOML file, falling back to defaults.

    Missing files and missing keys are fine — only the keys present override
    the defaults. Relative paths become absolute relative to the file's folder.
    """
    cfg_path = Path(path)
    if cfg_path.is_file():
        with cfg_path.open("rb") as fh:
            raw = tomllib.load(fh)
        base = cfg_path.resolve().parent
    else:
        raw = {}
        base = Path.cwd()

    sc = _section(raw, "scoring")
    scoring = ScoringConfig(
        alpha=float(sc.get("alpha", 0.5)),
        beta=float(sc.get("beta", 0.5)),
        top_k=int(sc.get("top_k", 2500)),
        max_depth=int(sc.get("max_depth", 5)),
        exclude_subset=bool(sc.get("exclude_subset", True)),
    )

    um = _section(raw, "umls")
    mrconso, mrhier, mrrel, mrdef = _resolve(
        base,
        Path(um.get("mrconso", UMLSConfig.mrconso)),
        Path(um.get("mrhier", UMLSConfig.mrhier)),
        Path(um.get("mrrel", UMLSConfig.mrrel)),
        Path(um.get("mrdef", UMLSConfig.mrdef)),
    )
    umls = UMLSConfig(
        mrconso=mrconso, mrhier=mrhier, mrrel=mrrel, mrdef=mrdef,
        terminologies=tuple(um.get("terminologies", DEFAULT_TERMINOLOGIES)),
        definition_sources=tuple(um.get("definition_sources", ("NCI",))),
    )

    an = _section(raw, "annotate")
    (model_pack,) = _resolve(base, Path(an.get("model_pack", AnnotateConfig.model_pack)))
    annotate = AnnotateConfig(
        model_pack=model_pack,
        n_process=int(an.get("n_process", 0)),
        batch_size_chars=int(an.get("batch_size_chars", 5_000_000)),
        cui_filter=frozenset(an.get("cui_filter", ())),
        tui_filter=frozenset(an.get("tui_filter", ())),
        map_codes=str(an.get("map_codes", "snomed")),
        meta_cat=bool(an.get("meta_cat", False)),
        spell_check=an.get("spell_check", None),
    )

    dl = _section(raw, "download")
    (raw_dir,) = _resolve(base, Path(dl.get("raw_dir", DownloadConfig.raw_dir)))
    download = DownloadConfig(
        base_url=str(dl.get("base_url", DownloadConfig.base_url)).rstrip("/"),
        raw_dir=raw_dir,
        max_concurrency=int(dl.get("max_concurrency", 4)),
    )

    st = _section(raw, "store")
    db_path, checkpoint_dir = _resolve(
        base,
        Path(st.get("db_path", StoreConfig.db_path)),
        Path(st.get("checkpoint_dir", StoreConfig.checkpoint_dir)),
    )
    store = StoreConfig(
        db_path=db_path,
        commit_every=int(st.get("commit_every", 5000)),
        checkpoint_dir=checkpoint_dir,
        index_mesh=bool(st.get("index_mesh", True)),
    )

    sv = _section(raw, "server")
    (preset_folder,) = _resolve(base, Path(sv.get("preset_folder", ServerConfig.preset_folder)))
    server = ServerConfig(
        host=str(sv.get("host", "0.0.0.0")),
        port=int(sv.get("port", 6006)),
        preset_folder=preset_folder,
        max_pmids=int(sv.get("max_pmids", 500_000)),
        max_edges=int(sv.get("max_edges", 4000)),
        mesh_presets=tuple(sv.get("mesh_presets", ())),
    )

    return Config(scoring, umls, annotate, download, store, server)
