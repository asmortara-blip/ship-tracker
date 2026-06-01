"""engine/world_graph_layout.py — pure-numpy 2-D graph layouts.

Two deterministic, dependency-light layout algorithms for laying out the
unified "world graph" (``processing.world_graph``) as a node-link diagram on a
cartesian plane. Both take a square adjacency MATRIX (numpy, ``n x n``) and
return an ``(n, 2)`` array of x/y coordinates roughly normalised to the unit
box (``max |coord| == 1`` when the layout is non-degenerate).

Why no networkx
---------------
The platform ethos is pure stdlib + numpy (see ``processing/world_graph_metrics``).
networkx is deliberately NOT a dependency. Both layouts are small, vectorised
numpy and run in milliseconds for the few-hundred-node graphs this app builds.

Guarantees (verified by a research agent + the ``__main__`` self-check):
  * **Finite**: never returns NaN/inf, even for disconnected graphs or graphs
    with isolated nodes. Distances are clipped away from zero and the final
    normalisation guards a zero span.
  * **Deterministic**: a fixed ``seed`` gives bit-identical output across runs
    (the only randomness is the tiny jitter that breaks perfect symmetry, and
    it is drawn from a seeded ``default_rng``).
  * **Degenerate sizes**: ``n == 0`` → ``(0, 2)``; ``n == 1`` → ``(1, 2)`` at
    the origin; ``spectral_layout`` falls back to Fruchterman–Reingold for
    ``n < 3`` (the Laplacian has too few non-trivial eigenvectors to embed).

Both functions symmetrise the input (``A = max(A, A.T)``) so a directed
adjacency matrix is treated as undirected — matching how the world graph's
centrality + neighbourhood helpers treat edges.
"""
from __future__ import annotations

import numpy as np


__all__ = [
    "fruchterman_reingold_layout",
    "spectral_layout",
]


def fruchterman_reingold_layout(
    adjacency: np.ndarray,
    *,
    iterations: int = 80,
    seed: int = 0,
    k: float | None = None,
) -> np.ndarray:
    """Force-directed (Fruchterman–Reingold) layout.

    Models nodes as particles that repel each other (Coulomb-like) while edges
    act as springs (Hooke-like), then relaxes the system for ``iterations``
    cooling steps. Produces the familiar "organic" node-link look where densely
    connected clusters pull together and the rest spreads out.

    Parameters
    ----------
    adjacency:
        Square ``(n, n)`` adjacency matrix (weights allowed). Symmetrised
        internally, so directedness is ignored.
    iterations:
        Number of cooling steps. More iterations = more relaxed (and slower);
        80 is a good default for a few hundred nodes.
    seed:
        Seeds the tiny symmetry-breaking jitter so output is deterministic.
    k:
        Optimal edge length. Defaults to ``1/sqrt(n)`` (the standard FR choice),
        which spaces nodes for a unit-area drawing.

    Returns
    -------
    np.ndarray
        ``(n, 2)`` float array of x/y positions, normalised so
        ``max |coord| == 1`` unless the layout collapsed to a point.
        Always finite; never NaN.
    """
    A = np.asarray(adjacency, dtype=float)
    n = A.shape[0]
    if n == 0:
        return np.zeros((0, 2))
    if n == 1:
        return np.zeros((1, 2))
    A = np.maximum(A, A.T)
    rng = np.random.default_rng(seed)
    # Seed on a circle (deterministic) + tiny jitter so coincident nodes don't
    # share a position (a zero pairwise distance would blow up the force term).
    ang = np.linspace(0.0, 2 * np.pi, n, endpoint=False)
    pos = np.column_stack([np.cos(ang), np.sin(ang)]) + rng.standard_normal((n, 2)) * 0.01
    if k is None:
        k = 1.0 / np.sqrt(n)
    t = 0.1
    dt = t / (iterations + 1)
    eye = np.eye(n, dtype=bool)
    for _ in range(iterations):
        delta = pos[:, None, :] - pos[None, :, :]
        dist = np.sqrt((delta ** 2).sum(-1))
        dist[eye] = 1.0
        dist = np.clip(dist, 1e-3, None)
        # Repulsive (k^2/d) minus attractive (d/k * A) force, projected onto the
        # unit displacement vector.
        coef = ((k * k) / dist - (dist / k) * A) / dist
        coef[eye] = 0.0
        disp = (coef[..., None] * delta).sum(axis=1)
        length = np.clip(np.sqrt((disp ** 2).sum(-1)), 1e-3, None)
        # Move each node along its net force, capped by the cooling temperature.
        pos = pos + (disp / length[:, None]) * np.minimum(length, t)[:, None]
        t -= dt
    pos -= pos.mean(0)
    span = np.abs(pos).max()
    return pos / span if span > 0 else pos


def spectral_layout(adjacency: np.ndarray, *, seed: int = 0) -> np.ndarray:
    """Spectral (graph-Laplacian) layout.

    Embeds the graph using the eigenvectors of the Laplacian ``L = D - A``
    associated with its two smallest non-trivial eigenvalues (the Fiedler
    vector + the next). This places strongly-connected nodes near each other
    and tends to "unfold" the graph cleanly — a stabler, fully deterministic
    alternative to the iterative force layout for the structural backbone.

    For ``n < 3`` the Laplacian doesn't have two usable non-trivial
    eigenvectors, so we fall back to :func:`fruchterman_reingold_layout`.

    Parameters
    ----------
    adjacency:
        Square ``(n, n)`` adjacency matrix. Symmetrised internally so the
        Laplacian is real-symmetric and ``eigh`` applies.
    seed:
        Only used for the ``n < 3`` Fruchterman–Reingold fallback.

    Returns
    -------
    np.ndarray
        ``(n, 2)`` float array of x/y positions, normalised so
        ``max |coord| == 1`` unless the layout collapsed to a point.
        Always finite (``eigh`` on a real-symmetric matrix is well-behaved);
        never NaN.
    """
    A = np.asarray(adjacency, dtype=float)
    n = A.shape[0]
    if n < 3:
        return fruchterman_reingold_layout(A, seed=seed)
    A = np.maximum(A, A.T)
    L = np.diag(A.sum(1)) - A
    # Real-symmetric → eigh gives ascending real eigenvalues. Skip the trivial
    # zero eigenvalue (constant eigenvector) and take the next two.
    w, v = np.linalg.eigh(L)
    idx = np.argsort(w)
    coords = v[:, idx[1:3]]
    coords = coords - coords.mean(0)
    span = np.abs(coords).max()
    return coords / span if span > 0 else coords


# ---------------------------------------------------------------------------
# Self-verification: run directly with ``python -m engine.world_graph_layout``.
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # STAR (centre 0 connected to 1..5): a connected, bipartite graph.
    n = 6
    star = np.zeros((n, n))
    for leaf in range(1, n):
        star[0, leaf] = star[leaf, 0] = 1.0

    fr = fruchterman_reingold_layout(star, seed=0)
    sp = spectral_layout(star, seed=0)
    assert fr.shape == (n, 2) and sp.shape == (n, 2), "shape"
    assert np.isfinite(fr).all(), "FR star produced non-finite coords"
    assert np.isfinite(sp).all(), "spectral star produced non-finite coords"

    # Determinism: same seed → identical output.
    assert np.array_equal(fr, fruchterman_reingold_layout(star, seed=0)), "FR not deterministic"
    assert np.array_equal(sp, spectral_layout(star, seed=0)), "spectral not deterministic"

    # DISCONNECTED (two separate edges + one isolated node) → must stay finite.
    disc = np.zeros((5, 5))
    disc[0, 1] = disc[1, 0] = 1.0
    disc[2, 3] = disc[3, 2] = 1.0  # node 4 isolated
    assert np.isfinite(fruchterman_reingold_layout(disc, seed=1)).all(), "FR disconnected NaN"
    assert np.isfinite(spectral_layout(disc, seed=1)).all(), "spectral disconnected NaN"

    # Degenerate sizes.
    assert fruchterman_reingold_layout(np.zeros((0, 0))).shape == (0, 2), "n=0"
    assert fruchterman_reingold_layout(np.zeros((1, 1))).shape == (1, 2), "n=1"
    assert spectral_layout(np.zeros((2, 2))).shape == (2, 2), "n<3 falls back"

    print("ALL CHECKS PASSED — finite, deterministic, NaN-safe on star / disconnected / n in {0,1,2}")
