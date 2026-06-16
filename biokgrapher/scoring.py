"""KL-divergence weighting and frequency re-ranking of concepts (§4.3-4.4)."""

from __future__ import annotations

import math
from collections.abc import Mapping

_LOW = 1.0  # background low constant for concepts absent outside the subset (§4.3)


def kl_divergence(
    local: Mapping[int, int],
    global_df: Mapping[int, int],
    *,
    exclude_subset: bool = True,
    global_total: int | None = None,
) -> dict[int, float]:
    """Per-concept KLD ``p·log(p/P)`` over subset concepts; ``P`` excludes the subset (eq. 1)."""
    n = sum(local.values())
    if n <= 0:
        return {}
    big_n = global_total if global_total is not None else sum(global_df.values())
    n_bg = big_n - n
    use_excl = exclude_subset and n_bg > 0
    out: dict[int, float] = {}
    for cid, f in local.items():
        if f <= 0:
            continue
        big_f = global_df.get(cid, 0)
        if use_excl:
            f_bg = big_f - f
            big_p = (f_bg if f_bg > 0 else _LOW) / n_bg
        else:
            if big_f <= 0:
                continue
            big_p = big_f / big_n
        p = f / n
        if big_p > 0:
            out[cid] = p * math.log(p / big_p)
    return out


def min_max_normalize(values: Mapping[int, float]) -> dict[int, float]:
    """Scale values into [0, 1]. A degenerate (all-equal) set maps to 1.0."""
    if not values:
        return {}
    lo = min(values.values())
    hi = max(values.values())
    if hi == lo:
        return {k: 1.0 for k in values}
    span = hi - lo
    return {k: (v - lo) / span for k, v in values.items()}


def score_concepts(
    local: Mapping[int, int],
    global_df: Mapping[int, int],
    *,
    alpha: float = 0.5,
    beta: float = 0.5,
    top_k: int = 2500,
    exclude_subset: bool = True,
    global_total: int | None = None,
) -> list[tuple[int, float]]:
    """Rank concepts by ``S = alpha*norm(KLD) + beta*norm(LF)``; top_k pairs, best first (§4.4)."""
    kld = kl_divergence(local, global_df, exclude_subset=exclude_subset, global_total=global_total)
    if not kld:
        return []
    norm_kld = min_max_normalize(kld)
    norm_lf = min_max_normalize({cid: float(local[cid]) for cid in kld})
    scored = {cid: alpha * norm_kld[cid] + beta * norm_lf[cid] for cid in kld}
    ranked = sorted(scored.items(), key=lambda kv: kv[1], reverse=True)
    return ranked[:top_k] if top_k and top_k > 0 else ranked
