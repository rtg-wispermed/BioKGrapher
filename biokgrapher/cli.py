"""``biokgrapher`` command-line interface."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import typer

from biokgrapher.config import load_config

app = typer.Typer(add_completion=False, help="Automated knowledge-graph construction from PubMed.")

ConfigOpt = typer.Option("biokgrapher.toml", "--config", "-c", help="Path to biokgrapher.toml")
LimitOpt = typer.Option(None, "--limit", "-n", help="Process only N files (sample / test).")


@app.command("ingest-umls")
def ingest_umls(config: Path = ConfigOpt) -> None:
    """Parse the UMLS RRF files into SQLite (run once per UMLS release)."""
    from biokgrapher import umls
    from biokgrapher.store import Store

    cfg = load_config(config)
    with Store(cfg.store.db_path, commit_every=cfg.store.commit_every) as store:
        counts = umls.ingest_all(store, cfg)
    typer.echo("UMLS ingested: " + ", ".join(f"{k}={v:,}" for k, v in counts.items()))


def _parse_shard(spec: str) -> tuple[int, int]:
    try:
        i, k = (int(x) for x in spec.split("/"))
    except ValueError as exc:
        raise typer.BadParameter("shard must look like i/k, e.g. 0/8") from exc
    if not (0 <= i < k):
        raise typer.BadParameter("shard must satisfy 0 <= i < k")
    return i, k


@app.command("build")
def build(
    config: Path = ConfigOpt,
    limit: Optional[int] = LimitOpt,
    include_updates: bool = typer.Option(True, help="Also apply updatefiles after baseline."),
    skip_download: bool = typer.Option(False, help="Use files already in raw_dir; don't fetch."),
    shard: Optional[str] = typer.Option(
        None, "--shard", help="Process only shard i/k into a shard DB (e.g. 0/8); merge later."),
) -> None:
    """Download + parse + annotate + index the PubMed baseline (and updatefiles)."""
    from biokgrapher import pipeline

    cfg = load_config(config)
    result = pipeline.run_build(cfg, limit=limit, include_updates=include_updates,
                                skip_download=skip_download,
                                shard=_parse_shard(shard) if shard else None)
    typer.echo(_fmt(result))


@app.command("merge")
def merge(
    shards: list[Path] = typer.Argument(..., help="Shard DB files to merge into the main index."),
    config: Path = ConfigOpt,
) -> None:
    """Additively merge shard DBs (from `build --shard`) into the main index."""
    from biokgrapher import pipeline

    cfg = load_config(config)
    typer.echo(_fmt(pipeline.merge_shards(cfg, shards)))


@app.command("update")
def update(
    config: Path = ConfigOpt,
    limit: Optional[int] = LimitOpt,
    skip_download: bool = typer.Option(False, help="Use files already in raw_dir; don't fetch."),
) -> None:
    """Pull new updatefiles and apply upserts + deletions."""
    from biokgrapher import pipeline

    cfg = load_config(config)
    result = pipeline.run_update(cfg, limit=limit, skip_download=skip_download)
    typer.echo(_fmt(result))


@app.command("precompute")
def precompute(
    config: Path = ConfigOpt,
    terminology: Optional[str] = typer.Option(None, help="Only this terminology (default: all)."),
) -> None:
    """Precompute + cache the full graph for every preset so they load instantly."""
    from biokgrapher.api import BioKG

    cfg = load_config(config)
    kg = BioKG(cfg)
    try:
        built = kg.precompute(terminologies=[terminology] if terminology else None)
    finally:
        kg.close()
    if not built:
        typer.echo("No presets found. Add *.txt files to the preset folder, or set "
                   "[server].mesh_presets and run `biokgrapher build` to populate the MeSH index.")
    else:
        typer.echo("Precomputed graphs for: " + ", ".join(f"{k} (×{v})" for k, v in built.items()))


@app.command("convert-model")
def convert_model(
    out: Path = typer.Argument(..., help="Output folder for the converted v2 model pack."),
    config: Path = ConfigOpt,
) -> None:
    """Convert the configured (legacy v1) MedCAT pack to a v2 pack for fast loading."""
    from medcat.cat import CAT

    cfg = load_config(config)
    out.mkdir(parents=True, exist_ok=True)
    cat = CAT.load_model_pack(str(cfg.annotate.model_pack))
    path = cat.save_model_pack(str(out))
    typer.echo(f"Converted. Set [annotate].model_pack to:\n  {path}")


@app.command("serve")
def serve(config: Path = ConfigOpt) -> None:
    """Launch the FastAPI + Plotly.js knowledge-graph explorer."""
    import uvicorn

    cfg = load_config(config)
    os.environ["BIOKGRAPHER_CONFIG"] = str(Path(config).resolve())
    typer.echo(f"BioKGrapher serving on http://{cfg.server.host}:{cfg.server.port}")
    uvicorn.run("biokgrapher.web.app:create_app", factory=True,
                host=cfg.server.host, port=cfg.server.port)


@app.command("status")
def status(config: Path = ConfigOpt, verify: bool = typer.Option(False, help="Recompute counters.")) -> None:
    """Show index counts and versions."""
    from biokgrapher.store import Store

    cfg = load_config(config)
    if not Path(cfg.store.db_path).exists():
        typer.echo(f"No index yet at {cfg.store.db_path}. Run `biokgrapher build`.")
        raise typer.Exit(code=1)
    with Store(cfg.store.db_path) as store:
        if verify:
            store.recompute_counters()
            typer.echo("counters recomputed from pmid_concepts.")
        typer.echo(_fmt(store.counts()))
        meta = {k: store.get_meta(k) for k in
                ("schema_version", "baseline_year", "umls_ingested", "model_pack")}
        typer.echo(_fmt({k: v for k, v in meta.items() if v is not None}))


def _fmt(d: dict) -> str:
    return "\n".join(f"  {k}: {v}" for k, v in d.items())


if __name__ == "__main__":  # pragma: no cover
    app()
