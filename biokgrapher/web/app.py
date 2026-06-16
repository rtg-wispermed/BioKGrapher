"""FastAPI app: serves the single-page explorer and the JSON graph/trends API."""

from __future__ import annotations

import os
import uuid
from collections import OrderedDict
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from biokgrapher.api import BioKG
from biokgrapher.config import Config, load_config

STATIC_DIR = Path(__file__).parent / "static"
_UPLOADS: "OrderedDict[str, list[int]]" = OrderedDict()
_UPLOAD_CAP = 64
_RESULT_CACHE: "OrderedDict[tuple, dict]" = OrderedDict()
_RESULT_CAP = 32


class GraphRequest(BaseModel):
    preset: Optional[str] = None
    pmids: Optional[list[str]] = None
    pmid_token: Optional[str] = None
    terminology: str = "SNOMEDCT_US"
    alpha: float = Field(0.5, ge=0, le=10)
    beta: float = Field(0.5, ge=0, le=10)
    top_k: int = Field(2500, ge=10, le=20000)
    max_depth: int = Field(5, ge=1, le=12)
    max_edges: int = Field(4000, ge=50, le=20000)


class TrendsRequest(BaseModel):
    preset: Optional[str] = None
    pmids: Optional[list[str]] = None
    pmid_token: Optional[str] = None
    recent_years: int = Field(3, ge=1, le=50)
    historical_years: int = Field(5, ge=1, le=100)
    top_n: int = Field(20, ge=5, le=100)


def _resolve_pmids(kg: BioKG, req: GraphRequest) -> list[int]:
    if req.pmid_token:
        pmids = _UPLOADS.get(req.pmid_token)
        if pmids is None:
            raise HTTPException(422, "Upload token expired — please re-upload the PMID file.")
        return pmids
    if req.pmids:
        return kg.parse_pmids(" ".join(req.pmids))
    if req.preset:
        try:
            return kg.pmids_for_source(req.preset)
        except FileNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
    raise HTTPException(422, "Provide a preset, a PMID list, or an upload token.")


def create_app(cfg: Config | None = None) -> FastAPI:
    cfg = cfg or load_config(os.environ.get("BIOKGRAPHER_CONFIG", "biokgrapher.toml"))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        kg = BioKG(cfg)
        kg.global_frequencies()  # warm the cache once
        app.state.kg = kg
        try:
            yield
        finally:
            kg.close()

    app = FastAPI(title="BioKGrapher", lifespan=lifespan)
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/health")
    def health() -> dict:
        _, n = app.state.kg.global_frequencies()
        return {"status": "ok", "global_docs": n}

    @app.get("/api/presets")
    def presets() -> dict:
        return {
            "presets": app.state.kg.list_presets(),
            "terminologies": list(cfg.umls.terminologies),
            "defaults": {
                "alpha": cfg.scoring.alpha, "beta": cfg.scoring.beta,
                "top_k": cfg.scoring.top_k, "max_depth": cfg.scoring.max_depth,
                "max_edges": cfg.server.max_edges,
            },
        }

    @app.post("/api/upload")
    async def upload(file: UploadFile) -> dict:
        raw = (await file.read()).decode("utf-8", errors="ignore")
        pmids = app.state.kg.parse_pmids(raw)
        if not pmids:
            raise HTTPException(422, "No PMIDs found in the uploaded file.")
        token = uuid.uuid4().hex
        _UPLOADS[token] = pmids
        while len(_UPLOADS) > _UPLOAD_CAP:
            _UPLOADS.popitem(last=False)
        return {"pmid_token": token, "n_pmids": len(pmids),
                "truncated": len(pmids) >= cfg.server.max_pmids,
                "sample": [str(p) for p in pmids[:5]]}

    @app.get("/api/concept/{cui}")
    def concept(cui: str) -> dict:
        return app.state.kg.concept(cui)

    @app.post("/api/trends")
    def trends(req: TrendsRequest) -> dict:
        kg: BioKG = app.state.kg
        params = dict(recent_years=req.recent_years, historical_years=req.historical_years,
                      top_n=req.top_n)
        if req.preset:
            try:
                kind, ref = kg._preset_ref(req.preset)
            except FileNotFoundError as exc:
                raise HTTPException(404, str(exc)) from exc
            if kind == "mesh":
                return kg.trends(mesh_ui=ref, **params)
            return kg.trends(pmids=ref, **params)
        pmids = _resolve_pmids(kg, req)
        return kg.trends(pmids=pmids, **params)

    @app.post("/api/graph")
    def graph(req: GraphRequest) -> dict:
        kg: BioKG = app.state.kg
        if req.terminology not in cfg.umls.terminologies:
            raise HTTPException(422, f"Unknown terminology: {req.terminology}")
        params = dict(terminology=req.terminology, alpha=req.alpha, beta=req.beta,
                      top_k=req.top_k, max_depth=req.max_depth, max_edges=req.max_edges)

        # Named presets use the persistent precomputed cache + MeSH aggregate fast path.
        if req.preset:
            key = kg.cache_key(f"preset:{req.preset}", req.terminology, req.alpha,
                               req.beta, req.top_k, req.max_depth, req.max_edges)
            cached = kg.get_cached(key)
            if cached is not None:
                return cached
            try:
                payload = kg.graph_payload_for_preset(req.preset, **params)
            except FileNotFoundError as exc:
                raise HTTPException(404, str(exc)) from exc
            payload = _require(payload)
            kg.put_cached(key, payload)
            return payload

        # ad-hoc PMIDs / uploads use an in-memory cache.
        pmids = _resolve_pmids(kg, req)
        if not pmids:
            raise HTTPException(422, "No PMIDs resolved from the request.")
        mem_key = (req.pmid_token or hash(tuple(pmids)), req.terminology, req.alpha,
                   req.beta, req.top_k, req.max_depth, req.max_edges)
        if mem_key in _RESULT_CACHE:
            _RESULT_CACHE.move_to_end(mem_key)
            return _RESULT_CACHE[mem_key]
        payload = _require(kg.graph_payload(pmids=pmids, **params))
        _RESULT_CACHE[mem_key] = payload
        while len(_RESULT_CACHE) > _RESULT_CAP:
            _RESULT_CACHE.popitem(last=False)
        return payload

    return app


def _require(payload: dict | None) -> dict:
    if payload is None:
        raise HTTPException(422, "None of those PMIDs are in the index (or scoring produced nothing).")
    return payload
