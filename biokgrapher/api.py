"""Facade over the store + scoring used by the web app (returns CUIs/names, lock-guarded)."""

from __future__ import annotations

import re
import threading
import time
from collections import Counter
from collections.abc import Iterable, Sequence
from pathlib import Path

from biokgrapher.config import Config
from biokgrapher.scoring import score_concepts
from biokgrapher.store import Store

_PMID_RE = re.compile(r"\d+")


class BioKG:
    def __init__(self, cfg: Config, *, allow_cross_thread: bool = True) -> None:
        self.cfg = cfg
        self.store = Store(cfg.store.db_path, commit_every=cfg.store.commit_every,
                           allow_cross_thread=allow_cross_thread)
        self._lock = threading.Lock()
        self._global: tuple[dict[int, int], int] | None = None
        self._global_total: int = 0

    def close(self) -> None:
        self.store.close()

    # -- scoring --------------------------------------------------------------
    def global_frequencies(self) -> tuple[dict[int, int], int]:
        if self._global is None:
            with self._lock:
                if self._global is None:  # another thread may have loaded it
                    freqs, n = self.store.global_frequencies()
                    self._global_total = sum(freqs.values())  # total postings, once
                    self._global = (freqs, n)
        return self._global

    def local_counter(self, *, pmids: Sequence[int] | None = None,
                      mesh_ui: str | None = None) -> tuple[Counter, int, int]:
        """Local frequencies ``(concept_id->df, n_matched, n_input)``; MeSH uses a lazy aggregate."""
        if mesh_ui is not None:
            with self._lock:
                agg = self.store.get_mesh_aggregate(mesh_ui)
                if agg is None:
                    pmid_list = self.store.pmids_for_mesh(mesh_ui)
                    counter, n_matched = self.store.concepts_for_pmids(pmid_list)
                    self.store.put_mesh_aggregate(mesh_ui, counter, n_matched)
                    return counter, n_matched, len(pmid_list)
                counter, n_matched = agg
                n_input = self.store.mesh_pmid_count(mesh_ui)
            return counter, n_matched, n_input
        with self._lock:
            counter, n_matched = self.store.concepts_for_pmids(pmids or [])
        return counter, n_matched, len(pmids or [])

    def _ranked(self, local: Counter, *, alpha: float, beta: float,
                top_k: int) -> list[tuple[str, float]]:
        global_df, _ = self.global_frequencies()
        ranked_ids = score_concepts(local, global_df, alpha=alpha, beta=beta, top_k=top_k,
                                    exclude_subset=self.cfg.scoring.exclude_subset,
                                    global_total=self._global_total)
        out = []
        for cid, score in ranked_ids:
            cui = self.store.cuis.to_cui(cid)
            if cui:
                out.append((cui, score))
        return out

    def score(self, pmids: Iterable[int], *, alpha: float, beta: float,
              top_k: int) -> tuple[list[tuple[str, float]], int, int]:
        """Return ``(ranked [(cui, score)], n_matched_docs, n_local_concepts)``."""
        local, n_matched, _ = self.local_counter(pmids=list(pmids))
        return self._ranked(local, alpha=alpha, beta=beta, top_k=top_k), n_matched, len(local)

    # -- hierarchy (treemap / sunburst / icicle) ------------------------------
    def hierarchy(self, scores: dict[str, float], terminology: str) -> list[dict]:
        """MRHIER path-to-root forest nodes for scored CUIs in one terminology (§4.5)."""
        cuis = list(scores)
        if not cuis:
            return []
        with self._lock:
            rows = self.store.hier_paths(cuis, terminology)
        if not rows:
            return []
        paths = [(ptr.split(".") if ptr else []) + [aui] for aui, ptr in rows]
        all_auis = {aui for path in paths for aui in path}
        with self._lock:
            aui2cui = self.store.aui_to_cui_map(list(all_auis))
            ref_cuis = list(set(aui2cui.values()))
            names = self.store.names_for(ref_cuis)
            defs = self.store.definitions_for(ref_cuis, self.cfg.umls.definition_sources)

        nodes: list[dict] = [{"id": "ROOT", "parent": "", "name": terminology,
                              "score": 0.0, "explanation": ""}]
        aui_to_node: dict[str, str] = {}
        counter = 0
        for path in paths:
            parent_id = "ROOT"
            for aui in path:
                node_id = aui_to_node.get(aui)
                if node_id is None:
                    node_id = str(counter)
                    counter += 1
                    aui_to_node[aui] = node_id
                    cui = aui2cui.get(aui, "")
                    nodes.append({
                        "id": node_id,
                        "parent": parent_id,
                        "name": names.get(cui) or cui or aui,
                        "score": float(scores.get(cui, 0.0)),
                        "explanation": defs.get(cui, ""),
                    })
                parent_id = node_id
        return nodes

    # -- node-edge knowledge graph (MRREL triples) ----------------------------
    def graph(self, scores: dict[str, float], *, max_edges: int
              ) -> tuple[list[dict], list[dict], bool]:
        cuis = list(scores)
        if not cuis:
            return [], [], False
        with self._lock:
            edges, truncated = self.store.triples_for(cuis, max_edges)
            node_cuis = {c for e in edges for c in (e[0], e[2])}
            names = self.store.names_for(list(node_cuis))
        graph_nodes = [{"cui": c, "name": names.get(c, c), "score": float(scores.get(c, 0.0))}
                       for c in node_cuis]
        graph_edges = [{"source": c1, "relation": rel, "target": c2} for c1, rel, c2 in edges]
        return graph_nodes, graph_edges, truncated

    # -- full graph payload (shared by the web API and precompute) ------------
    def graph_payload(self, *, pmids: Sequence[int] | None = None, mesh_ui: str | None = None,
                      terminology: str, alpha: float, beta: float, top_k: int,
                      max_depth: int, max_edges: int) -> dict | None:
        """Score a source (PMID set or MeSH descriptor) and assemble the full response, or None."""
        t0 = time.perf_counter()
        local, n_matched, n_input = self.local_counter(pmids=pmids, mesh_ui=mesh_ui)
        ranked = self._ranked(local, alpha=alpha, beta=beta, top_k=top_k)
        if not ranked:
            return None
        return self._assemble(ranked, n_matched, len(local), n_input, terminology=terminology,
                              alpha=alpha, beta=beta, top_k=top_k, max_depth=max_depth,
                              max_edges=max_edges, t0=t0)

    def graph_payload_for_preset(self, name: str, **params) -> dict | None:
        """Resolve a preset (``*.txt`` file or MeSH term) and build its graph payload."""
        kind, ref = self._preset_ref(name)
        if kind == "file":
            return self.graph_payload(pmids=ref, **params)
        return self.graph_payload(mesh_ui=ref, **params)

    def _assemble(self, ranked, n_matched, n_concepts, n_input, *, terminology, alpha, beta,
                  top_k, max_depth, max_edges, t0) -> dict:
        scores = dict(ranked)
        warnings: list[str] = []
        unknown = n_input - n_matched
        if unknown > 0:
            warnings.append(f"{unknown:,} of {n_input:,} PMIDs are not in the index.")
        hierarchy = self.hierarchy(scores, terminology)
        if not hierarchy:
            warnings.append(f"No '{terminology}' hierarchy for the top concepts — "
                            "try SNOMEDCT_US or MSH.")
        graph_nodes, graph_edges, truncated = self.graph(scores, max_edges=max_edges)
        if not graph_edges:
            warnings.append("No semantic relations among the top concepts for the graph view.")
        return {
            "hierarchy": hierarchy, "graph_nodes": graph_nodes, "graph_edges": graph_edges,
            "meta": {
                "n_pmids_input": n_input, "n_pmids_matched": n_matched,
                "n_concepts_local": n_concepts, "n_scored": len(ranked),
                "n_hier_nodes": len(hierarchy), "n_graph_nodes": len(graph_nodes),
                "n_graph_edges": len(graph_edges), "edges_truncated": truncated,
                "terminology": terminology, "alpha": alpha, "beta": beta, "top_k": top_k,
                "max_depth": max_depth, "elapsed_ms": int((time.perf_counter() - t0) * 1000),
                "warnings": warnings,
            },
        }

    @staticmethod
    def cache_key(source: str, terminology: str, alpha: float, beta: float,
                  top_k: int, max_depth: int, max_edges: int) -> str:
        return f"{source}|{terminology}|{alpha}|{beta}|{top_k}|{max_depth}|{max_edges}"

    def get_cached(self, key: str) -> dict | None:
        with self._lock:
            return self.store.get_preset_graph(key)

    def put_cached(self, key: str, payload: dict) -> None:
        with self._lock:
            self.store.put_preset_graph(key, payload)

    # -- concept trends over time ---------------------------------------------
    def trends(self, *, pmids: Sequence[int] | None = None, mesh_ui: str | None = None,
               recent_years: int = 3, historical_years: int = 5, top_n: int = 25,
               min_docs: int = 1, as_of_year: int | None = None) -> dict:
        """Trending / declining concepts: rate-of-change of recent vs historical prevalence."""
        with self._lock:
            if pmids is None and mesh_ui is not None:
                pmids = self.store.pmids_for_mesh(mesh_ui)
            pmid_list = list(pmids or [])
            as_of = as_of_year or self.store.max_year()
            if as_of is None:
                return {"trending": [], "declining": [], "as_of": None}
            recent_from = as_of - recent_years + 1
            hist_from = recent_from - historical_years
            rc, rn, hc, hn = self.store.concepts_by_period(pmid_list, recent_from, hist_from)

        rates: dict[int, float] = {}
        for cid in set(rc) | set(hc):
            if rc.get(cid, 0) + hc.get(cid, 0) < min_docs:
                continue
            rf = rc.get(cid, 0) / rn if rn else 0.0
            hf = hc.get(cid, 0) / hn if hn else 0.0
            if hf > 0:
                rates[cid] = (rf - hf) / hf if rf > 0 else -1.0
            elif rf > 0:
                rates[cid] = rf  # newly appearing concept
        up = sorted(((c, r) for c, r in rates.items() if r > 0), key=lambda x: -x[1])[:top_n]
        down = sorted(((c, r) for c, r in rates.items() if r < 0), key=lambda x: x[1])[:top_n]

        cui_for = {c: self.store.cuis.to_cui(c) for c, _ in up + down}
        with self._lock:
            names = self.store.names_for([cui_for[c] for c, _ in up + down if cui_for[c]])

        def fmt(pairs):
            return [{"cui": cui_for[c], "name": names.get(cui_for[c], cui_for[c]),
                     "rate": round(r, 4)} for c, r in pairs if cui_for[c]]

        return {"trending": fmt(up), "declining": fmt(down), "as_of": as_of,
                "recent_from": recent_from, "hist_from": hist_from,
                "n_recent_docs": rn, "n_hist_docs": hn}

    # -- single-concept lookups (lazy tooltips) -------------------------------
    def concept(self, cui: str) -> dict[str, str]:
        with self._lock:
            name = self.store.names_for([cui]).get(cui, cui)
            definition = self.store.definitions_for([cui], self.cfg.umls.definition_sources).get(cui, "")
        return {"cui": cui, "name": name, "definition": definition}

    # -- presets / PMID parsing ----------------------------------------------
    def list_presets(self) -> list[str]:
        """File presets (``*.txt``) plus configured MeSH presets that exist in the index."""
        folder = Path(self.cfg.server.preset_folder)
        files = sorted(p.stem for p in folder.glob("*.txt")) if folder.is_dir() else []
        with self._lock:
            mesh = [m for m in self.cfg.server.mesh_presets if self.store.resolve_mesh_ui(m)]
        return files + [m for m in mesh if m not in files]

    def _preset_ref(self, name: str) -> tuple[str, object]:
        """Resolve a preset to ``("file", pmids)`` or ``("mesh", ui)``; file wins."""
        path = Path(self.cfg.server.preset_folder) / f"{name}.txt"
        if path.is_file():
            return "file", self.parse_pmids(path.read_text(encoding="utf-8", errors="ignore"))
        with self._lock:
            ui = self.store.resolve_mesh_ui(name)
        if ui:
            return "mesh", ui
        raise FileNotFoundError(f"unknown preset: {name}")

    def pmids_for_source(self, name: str) -> list[int]:
        """Resolve a preset name to its PMID list (file preset, else MeSH descriptor)."""
        kind, ref = self._preset_ref(name)
        if kind == "file":
            return ref  # type: ignore[return-value]
        with self._lock:
            return self.store.pmids_for_mesh(ref)[: self.cfg.server.max_pmids]  # type: ignore[arg-type]

    def precompute(self, *, terminologies: Sequence[str] | None = None) -> dict[str, int]:
        """Build and cache the full graph for every preset × terminology at default params."""
        sc = self.cfg.scoring
        terms = list(terminologies or self.cfg.umls.terminologies)
        built: dict[str, int] = {}
        for name in self.list_presets():
            try:
                kind, ref = self._preset_ref(name)
            except FileNotFoundError:
                continue
            if kind == "file":
                local, n_matched, n_input = self.local_counter(pmids=ref)  # type: ignore[arg-type]
            else:
                local, n_matched, n_input = self.local_counter(mesh_ui=ref)  # type: ignore[arg-type]
            ranked = self._ranked(local, alpha=sc.alpha, beta=sc.beta, top_k=sc.top_k)
            if not ranked:
                continue
            for term in terms:
                payload = self._assemble(
                    ranked, n_matched, len(local), n_input, terminology=term,
                    alpha=sc.alpha, beta=sc.beta, top_k=sc.top_k, max_depth=sc.max_depth,
                    max_edges=self.cfg.server.max_edges, t0=time.perf_counter())
                key = self.cache_key(f"preset:{name}", term, sc.alpha, sc.beta,
                                     sc.top_k, sc.max_depth, self.cfg.server.max_edges)
                self.put_cached(key, payload)
            built[name] = len(terms)
        return built

    def parse_pmids(self, text: str, *, limit: int | None = None) -> list[int]:
        seen: set[int] = set()
        out: list[int] = []
        cap = limit or self.cfg.server.max_pmids
        for match in _PMID_RE.finditer(text):
            pmid = int(match.group())
            if pmid not in seen:
                seen.add(pmid)
                out.append(pmid)
                if len(out) >= cap:
                    break
        return out
