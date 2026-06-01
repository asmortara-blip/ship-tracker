"""processing/world_graph_metrics.py — network-centrality + resilience metrics.

Pure-function graph analytics for the "world graph" feature: ports, lanes,
canals and companies modelled as a weighted undirected network. The operator
question this answers is *structural*: which nodes are the load-bearing
chokepoints, and how fragmented does the world become when one is removed?

Four metrics, each a stand-alone function over a graph given as:

  * ``nodes`` — a list of node ids (strings).
  * ``edges`` — a list of ``(u, v, weight)`` tuples. Treated as UNDIRECTED.
                ``weight`` is optional per-edge: a bare ``(u, v)`` is taken as
                weight 1.0. Self-loops (``u == v``) are dropped. Parallel
                edges (same unordered pair appearing twice) are merged by
                SUMMING their weights.

The functions:

  1. ``degree_centrality``      — normalised weighted degree. Cheap "how many
                                  lanes touch this port" signal.
  2. ``eigenvector_centrality`` — power iteration on the adjacency matrix.
                                  "Importance by association": a node is central
                                  if its neighbours are central. Handles
                                  disconnected graphs + non-convergence
                                  gracefully (see CAVEATS below).
  3. ``betweenness_centrality`` — Brandes' algorithm (BFS-based, treats the
                                  graph as UNWEIGHTED for path counting). The
                                  systemic-chokepoint signal: how much
                                  shortest-path flow routes *through* a node.
  4. ``resilience_after_removal`` — remove a set of nodes, then report how
                                  fragmented the remainder is (largest-component
                                  fraction + component count). Answers "close
                                  Suez → how broken is the world?".

All deterministic — no RNG anywhere. Node ordering in every returned dict
follows the input ``nodes`` order (de-duplicated, first occurrence wins).

Design notes
------------
* No new dependencies. Pure stdlib + numpy (the platform ethos). networkx is
  deliberately NOT used.
* Functions, not a class hierarchy — each is independently importable and
  testable, matching the rest of ``processing/``.
* Sized for a few hundred nodes / few thousand edges. Brandes is O(V·E);
  eigenvector iteration is O(max_iter · E)-ish via a dense matmul on V×V.

CAVEATS (numerical)
-------------------
* **Eigenvector centrality on a disconnected graph** converges to the dominant
  eigenvector of whichever component has the largest spectral radius; nodes in
  every *other* component are driven toward 0. This is the textbook failure
  mode of eigenvector centrality and is mathematically correct, not a bug —
  but it means you should not compare eigenvector scores *across* components.
  For cross-component importance prefer degree or betweenness. We surface this
  by returning a well-defined (L2-normalised, non-negative) vector regardless,
  and by never raising.
* **Bipartite graphs** (a STAR, a PATH, any 2-colourable lane network) have a
  symmetric spectrum (``lambda_min == -lambda_max``), on which *plain* power
  iteration oscillates forever between the +/- eigenvectors and never
  converges. We defeat this with a spectral shift — iterating on
  ``A + shift*I`` (``shift`` = max row-sum of ``|A|``) — which adds a constant
  to every eigenvalue while leaving the eigenVECTORS unchanged, so iteration
  converges to the true Perron vector. The returned scores are therefore the
  genuine eigenvector centrality, not a truncated oscillation.
* **Non-convergence within ``max_iter``** (rare, e.g. a near-degenerate
  spectral gap): we cap at ``max_iter`` and return the best iterate reached —
  still a unit non-negative vector. Degree/betweenness are unaffected.
* **Empty / single-node graphs**: every function returns sensible, documented
  values (see each docstring) rather than dividing by zero.
"""
from __future__ import annotations

from collections import deque
from typing import Iterable, Optional, Sequence

import numpy as np


__all__ = [
    "degree_centrality",
    "eigenvector_centrality",
    "betweenness_centrality",
    "resilience_after_removal",
]


# ---------------------------------------------------------------------------
# Internal: canonicalise the graph
# ---------------------------------------------------------------------------


def _clean_nodes(nodes: Iterable[str]) -> list[str]:
    """De-duplicate node ids preserving first-occurrence order.

    Node ids are coerced to ``str`` so callers can pass ints/locodes
    interchangeably. Returns a list (the canonical node order used by every
    function and every returned dict).
    """
    seen: set[str] = set()
    out: list[str] = []
    for n in nodes:
        s = str(n)
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _clean_edges(
    edges: Iterable[Sequence],
    valid: set[str],
) -> dict[tuple[str, str], float]:
    """Normalise an edge iterable into a merged undirected weight map.

    * Each edge may be ``(u, v)`` (weight defaults to 1.0) or ``(u, v, w)``.
    * Self-loops (``u == v``) are dropped.
    * Endpoints not present in ``valid`` are dropped (defensive: an edge can't
      reference a node the caller didn't declare).
    * Parallel edges (same unordered pair) are merged by SUMMING weights.
    * Non-finite or non-positive weights are coerced: a missing/NaN weight
      becomes 1.0; a finite weight is used as-is (including negatives, which
      the caller is responsible for — we don't editorialise sign here, but
      see the per-function notes).

    The returned keys are *ordered* tuples ``(min, max)`` so the unordered
    pair {u, v} maps to a single canonical key.
    """
    merged: dict[tuple[str, str], float] = {}
    for e in edges:
        if e is None:
            continue
        # Unpack defensively: accept (u, v) or (u, v, w) (extra items ignored).
        try:
            u = str(e[0])
            v = str(e[1])
        except (TypeError, IndexError, KeyError):
            continue
        w: float = 1.0
        if len(e) >= 3:
            try:
                wf = float(e[2])
                w = wf if np.isfinite(wf) else 1.0
            except (TypeError, ValueError):
                w = 1.0
        if u == v:  # drop self-loops
            continue
        if u not in valid or v not in valid:  # drop dangling endpoints
            continue
        key = (u, v) if u <= v else (v, u)
        merged[key] = merged.get(key, 0.0) + w
    return merged


def _adjacency_lists(
    node_index: dict[str, int],
    edge_weights: dict[tuple[str, str], float],
) -> list[list[int]]:
    """Build integer-indexed undirected adjacency lists (unweighted neighbours).

    Used by the BFS-based routines (betweenness, connectivity). Neighbour
    lists are sorted for determinism.
    """
    adj: list[list[int]] = [[] for _ in range(len(node_index))]
    for (u, v) in edge_weights:
        iu = node_index[u]
        iv = node_index[v]
        adj[iu].append(iv)
        adj[iv].append(iu)
    for lst in adj:
        lst.sort()
    return adj


# ---------------------------------------------------------------------------
# 1. Degree centrality
# ---------------------------------------------------------------------------


def degree_centrality(
    nodes: Iterable[str],
    edges: Iterable[Sequence],
    *,
    weighted: bool = False,
) -> dict[str, float]:
    """Normalised degree centrality.

    Unweighted (default): the classic Freeman degree centrality — a node's
    degree (number of distinct neighbours) divided by ``n - 1`` (the maximum
    possible degree in a simple graph). Result is in ``[0, 1]``.

    Weighted (``weighted=True``): sum of incident edge weights, normalised by
    the largest weighted degree in the graph so the most-connected node scores
    1.0. (There is no universal max for weighted degree, so we normalise by the
    observed max; if all weights are 0 every score is 0.)

    Edge cases:
      * empty graph        → ``{}``
      * single node        → ``{node: 0.0}`` (no neighbours possible)
      * isolated nodes     → ``0.0``
    """
    nodelist = _clean_nodes(nodes)
    n = len(nodelist)
    if n == 0:
        return {}
    valid = set(nodelist)
    ew = _clean_edges(edges, valid)

    if n == 1:
        return {nodelist[0]: 0.0}

    if not weighted:
        deg: dict[str, int] = {nd: 0 for nd in nodelist}
        for (u, v) in ew:
            deg[u] += 1
            deg[v] += 1
        norm = float(n - 1)
        return {nd: deg[nd] / norm for nd in nodelist}

    # Weighted variant.
    wdeg: dict[str, float] = {nd: 0.0 for nd in nodelist}
    for (u, v), w in ew.items():
        wdeg[u] += w
        wdeg[v] += w
    max_wdeg = max(wdeg.values()) if wdeg else 0.0
    if max_wdeg <= 0.0:
        return {nd: 0.0 for nd in nodelist}
    return {nd: wdeg[nd] / max_wdeg for nd in nodelist}


# ---------------------------------------------------------------------------
# 2. Eigenvector centrality (power iteration)
# ---------------------------------------------------------------------------


def eigenvector_centrality(
    nodes: Iterable[str],
    edges: Iterable[Sequence],
    *,
    max_iter: int = 100,
    tol: float = 1e-6,
    weighted: bool = True,
) -> dict[str, float]:
    """Eigenvector centrality via power iteration on the adjacency matrix.

    A node is important if it is connected to important nodes. Formally this is
    the dominant (Perron) eigenvector of the adjacency matrix ``A``; we obtain
    it by repeatedly applying ``x <- A x`` and renormalising.

    Parameters
    ----------
    weighted:
        If True (default), ``A`` carries the merged edge weights; if False, the
        binary adjacency matrix is used.
    max_iter, tol:
        Power iteration stops when the L1 change between successive (unit)
        iterates drops below ``tol``, or after ``max_iter`` steps.

    Returns a dict ``{node: score}`` with the vector L2-normalised and made
    non-negative (the Perron vector of a non-negative matrix can be chosen
    non-negative; we enforce the sign so a flipped iterate never produces
    negative scores). Scores are comparable *within* a connected component.

    Edge cases / numerical behaviour (see module CAVEATS):
      * empty graph              → ``{}``
      * single node              → ``{node: 0.0}`` (degenerate; no self-loop)
      * no edges at all          → all ``0.0`` (A is the zero matrix)
      * disconnected graph       → converges to the dominant component;
                                   other components tend to ``0.0``
      * non-convergence          → best iterate after ``max_iter`` returned;
                                   never raises.
    """
    nodelist = _clean_nodes(nodes)
    n = len(nodelist)
    if n == 0:
        return {}
    if n == 1:
        return {nodelist[0]: 0.0}

    node_index = {nd: i for i, nd in enumerate(nodelist)}
    ew = _clean_edges(edges, set(nodelist))

    if not ew:
        # Zero matrix — every node equally "central" at 0. Returning zeros is
        # the honest answer (an all-isolated graph has no eigenvector signal).
        return {nd: 0.0 for nd in nodelist}

    A = np.zeros((n, n), dtype=float)
    for (u, v), w in ew.items():
        val = w if weighted else 1.0
        iu = node_index[u]
        iv = node_index[v]
        A[iu, iv] = val
        A[iv, iu] = val

    # --- spectral shift to defeat bipartite oscillation -------------------
    # A bipartite graph (e.g. a STAR, a PATH, any 2-colourable lane network)
    # has a symmetric spectrum: its most-negative eigenvalue equals -lambda_max
    # in magnitude. Plain power iteration then OSCILLATES between the +/-
    # eigenvectors forever and never converges. Iterating on (A + shift*I)
    # instead adds ``shift`` to every eigenvalue — leaving the eigenVECTORS
    # untouched — so the dominant eigenvalue becomes uniquely lambda_max+shift
    # and iteration converges to the true Perron vector. We pick shift =
    # max row-sum of |A|, which upper-bounds the spectral radius (Gershgorin),
    # hence strictly exceeds |lambda_min| and guarantees a positive, dominant
    # shifted eigenvalue for any graph. Deterministic; no effect on the answer.
    shift = float(np.abs(A).sum(axis=1).max())
    if shift <= 0.0:  # defensive; ew is non-empty so this shouldn't trigger
        shift = 1.0
    M = A + shift * np.eye(n, dtype=float)

    # Start from the uniform positive vector (deterministic, and a safe start
    # for a non-negative matrix — it has non-zero overlap with the Perron
    # vector whenever the graph has any edge).
    x = np.full(n, 1.0 / np.sqrt(n), dtype=float)

    last = x
    for _ in range(max(1, int(max_iter))):
        # ``einsum`` rather than ``M @ x``: on macOS Accelerate BLAS, matmul
        # raises spurious divide/overflow/invalid RuntimeWarnings on perfectly
        # finite inputs (numpy/numpy#27282). einsum is the same math, equally
        # pure-numpy, and warning-free — so importers (app/worker) don't get
        # console noise outside the pytest filter. Verified identical results.
        x_new = np.einsum("ij,j->i", M, x)
        norm = np.linalg.norm(x_new)
        if norm <= 0.0 or not np.isfinite(norm):
            # Numerical collapse (shouldn't happen for a non-empty A from a
            # uniform start, but stay graceful): keep the last good iterate.
            x_new = last
            break
        x_new = x_new / norm
        if np.sum(np.abs(x_new - last)) < float(tol):
            last = x_new
            break
        last = x_new
        x = x_new   # feed the iterate back in — without this the loop just
                    # recomputes M @ x_uniform and "converges" after one step

    vec = last
    # Enforce non-negative orientation: if the iterate landed on the negated
    # eigenvector (possible for some sign conventions), flip it. We pick the
    # sign that makes the largest-magnitude component positive.
    if vec[np.argmax(np.abs(vec))] < 0:
        vec = -vec
    # Clip tiny negatives from round-off, then renormalise to a unit vector.
    vec = np.clip(vec, 0.0, None)
    vnorm = np.linalg.norm(vec)
    if vnorm > 0.0:
        vec = vec / vnorm

    return {nd: float(vec[node_index[nd]]) for nd in nodelist}


# ---------------------------------------------------------------------------
# 3. Betweenness centrality (Brandes' algorithm, unweighted/BFS)
# ---------------------------------------------------------------------------


def betweenness_centrality(
    nodes: Iterable[str],
    edges: Iterable[Sequence],
    *,
    normalized: bool = True,
) -> dict[str, float]:
    """Betweenness centrality via Brandes' algorithm (BFS, unweighted).

    Betweenness of a node ``w`` is the sum over all source/target pairs
    ``(s, t)`` of the fraction of shortest ``s→t`` paths that pass *through*
    ``w``. It is the canonical "systemic chokepoint" signal: remove a
    high-betweenness node and a large share of the network's shortest-path flow
    has to reroute (or can't).

    Implementation is Brandes (2001): for each source ``s`` run a BFS to get
    shortest-path counts ``sigma`` and the BFS layering, then accumulate
    dependencies in reverse BFS order. Complexity O(V·E) — the reason we run
    BFS (the graph is treated as UNWEIGHTED for path counting) rather than
    Dijkstra. Edge weights are ignored here by design; use weighted Dijkstra
    only if lane *cost* matters more than hop topology.

    Normalisation (``normalized=True``, default): divide raw scores by
    ``(n-1)(n-2)`` for the undirected case — i.e. ``2 / ((n-1)(n-2))`` applied
    to the standard undirected raw score (we halve because each undirected pair
    is otherwise counted from both endpoints). After normalisation a perfect
    chokepoint (the centre of a star) scores 1.0 and pure leaves score 0.0.

    Edge cases:
      * empty graph                  → ``{}``
      * single node / two nodes      → all ``0.0`` (no node lies *between* a
                                       pair when ``n < 3``)
      * disconnected graph           → handled naturally; pairs in different
                                       components contribute nothing.
    """
    nodelist = _clean_nodes(nodes)
    n = len(nodelist)
    if n == 0:
        return {}
    if n <= 2:
        return {nd: 0.0 for nd in nodelist}

    node_index = {nd: i for i, nd in enumerate(nodelist)}
    ew = _clean_edges(edges, set(nodelist))
    adj = _adjacency_lists(node_index, ew)

    betweenness = np.zeros(n, dtype=float)

    for s in range(n):
        # --- single-source shortest-path BFS bookkeeping (Brandes) ---
        stack: list[int] = []                 # nodes in order of non-decreasing dist
        predecessors: list[list[int]] = [[] for _ in range(n)]
        sigma = np.zeros(n, dtype=float)      # # shortest paths s -> v
        dist = np.full(n, -1, dtype=np.int64)  # BFS distance, -1 = unvisited
        sigma[s] = 1.0
        dist[s] = 0
        queue: deque[int] = deque([s])

        while queue:
            v = queue.popleft()
            stack.append(v)
            dv = dist[v]
            sv = sigma[v]
            for w in adj[v]:
                # First time we reach w: set its distance, enqueue it.
                if dist[w] < 0:
                    dist[w] = dv + 1
                    queue.append(w)
                # w found on a shortest path *via* v?
                if dist[w] == dv + 1:
                    sigma[w] += sv
                    predecessors[w].append(v)

        # --- back-propagate dependencies in reverse BFS order ---
        delta = np.zeros(n, dtype=float)
        while stack:
            w = stack.pop()
            sw = sigma[w]
            if sw > 0.0:
                coeff = (1.0 + delta[w]) / sw
                for v in predecessors[w]:
                    delta[v] += sigma[v] * coeff
            if w != s:
                betweenness[w] += delta[w]

    # Each undirected shortest path is counted once from each endpoint, so the
    # raw accumulation double-counts; halve it.
    betweenness *= 0.5

    if normalized:
        # Max possible undirected betweenness is (n-1)(n-2)/2 (the star centre).
        scale = (n - 1) * (n - 2) / 2.0
        if scale > 0.0:
            betweenness = betweenness / scale

    return {nd: float(betweenness[node_index[nd]]) for nd in nodelist}


# ---------------------------------------------------------------------------
# 4. Resilience after node removal
# ---------------------------------------------------------------------------


def _connected_components(
    n: int,
    adj: list[list[int]],
    alive: np.ndarray,
) -> list[int]:
    """Return component sizes (descending) over the alive nodes only.

    ``alive`` is a boolean mask of length ``n``; dead nodes are skipped and
    contribute nothing. BFS flood-fill (union-find would be equivalent; BFS
    reuses the adjacency lists we already built).
    """
    visited = np.zeros(n, dtype=bool)
    sizes: list[int] = []
    for start in range(n):
        if not alive[start] or visited[start]:
            continue
        size = 0
        queue: deque[int] = deque([start])
        visited[start] = True
        while queue:
            v = queue.popleft()
            size += 1
            for w in adj[v]:
                if alive[w] and not visited[w]:
                    visited[w] = True
                    queue.append(w)
        sizes.append(size)
    sizes.sort(reverse=True)
    return sizes


def resilience_after_removal(
    nodes: Iterable[str],
    edges: Iterable[Sequence],
    remove: Iterable[str] | str,
) -> dict:
    """Fragmentation report after removing one or more nodes.

    Removes the node(s) in ``remove`` (a single id or an iterable of ids) and
    measures how shattered the surviving graph is. This is the "close Suez →
    how fragmented is the world?" lever: high-betweenness nodes, when removed,
    typically collapse the largest-component fraction and spike the component
    count.

    Returns a dict with:
      * ``n_nodes_before``            — node count before removal (de-duped).
      * ``n_removed``                 — how many of ``remove`` were actually
                                        present and removed.
      * ``n_nodes_after``             — surviving node count.
      * ``n_components``              — number of connected components among
                                        survivors (isolated survivors each
                                        count as a component of size 1).
      * ``largest_component_size``    — node count of the biggest survivor
                                        component (0 if none survive).
      * ``largest_component_fraction``— ``largest_component_size /
                                        n_nodes_after`` in ``[0, 1]``; the
                                        single headline number for "how much of
                                        the world is still connected". 0.0 when
                                        nothing survives.
      * ``component_sizes``           — all component sizes, descending.
      * ``removed``                   — the sorted list of ids actually removed.

    Edge cases:
      * empty graph                  → everything 0 / empty.
      * remove everything            → ``n_nodes_after == 0``,
                                        ``largest_component_fraction == 0.0``.
      * ids in ``remove`` not in the
        graph                        → silently ignored (counted only if
                                        present).
    """
    nodelist = _clean_nodes(nodes)
    n = len(nodelist)
    node_index = {nd: i for i, nd in enumerate(nodelist)}

    # Normalise ``remove`` to a set of present node ids.
    if isinstance(remove, str):
        remove_iter: Iterable[str] = [remove]
    elif remove is None:
        remove_iter = []
    else:
        remove_iter = remove
    remove_set = {str(r) for r in remove_iter}
    removed_present = sorted(remove_set & set(nodelist))

    if n == 0:
        return {
            "n_nodes_before": 0,
            "n_removed": 0,
            "n_nodes_after": 0,
            "n_components": 0,
            "largest_component_size": 0,
            "largest_component_fraction": 0.0,
            "component_sizes": [],
            "removed": [],
        }

    ew = _clean_edges(edges, set(nodelist))
    adj = _adjacency_lists(node_index, ew)

    alive = np.ones(n, dtype=bool)
    for r in removed_present:
        alive[node_index[r]] = False

    n_after = int(alive.sum())
    sizes = _connected_components(n, adj, alive)
    largest = sizes[0] if sizes else 0
    frac = (largest / n_after) if n_after > 0 else 0.0

    return {
        "n_nodes_before": n,
        "n_removed": len(removed_present),
        "n_nodes_after": n_after,
        "n_components": len(sizes),
        "largest_component_size": largest,
        "largest_component_fraction": float(frac),
        "component_sizes": sizes,
        "removed": removed_present,
    }


# ---------------------------------------------------------------------------
# Self-verification on known graphs
# ---------------------------------------------------------------------------


def _approx(a: float, b: float, eps: float = 1e-9) -> bool:
    return abs(a - b) <= eps


def _self_verify() -> int:
    """Run sanity checks on graphs with known closed-form centralities.

    Returns the number of FAILED checks (0 == all good). Prints PASS/FAIL per
    check so the module can be run directly:  ``python -m
    processing.world_graph_metrics``.
    """
    failures = 0

    def check(label: str, ok: bool, detail: str = "") -> None:
        nonlocal failures
        status = "PASS" if ok else "FAIL"
        if not ok:
            failures += 1
        suffix = f"  [{detail}]" if detail else ""
        print(f"  [{status}] {label}{suffix}")

    # ===================================================================
    # STAR graph: centre C connected to L1..L5. n = 6.
    #   - C has max degree (1.0), leaves all equal (1/(n-1)).
    #   - C has betweenness 1.0 (every leaf-leaf path goes through C),
    #     leaves 0.0.
    #   - C has the max eigenvector centrality.
    # ===================================================================
    print("STAR graph (centre + 5 leaves):")
    star_nodes = ["C", "L1", "L2", "L3", "L4", "L5"]
    star_edges = [("C", f"L{i}", 1.0) for i in range(1, 6)]

    deg = degree_centrality(star_nodes, star_edges)
    check("centre degree == 1.0", _approx(deg["C"], 1.0), f"C={deg['C']:.4f}")
    check(
        "leaves degree == 1/(n-1) == 0.2",
        all(_approx(deg[f"L{i}"], 0.2) for i in range(1, 6)),
        f"L1={deg['L1']:.4f}",
    )
    check(
        "centre degree is strictly the max",
        deg["C"] > max(deg[f"L{i}"] for i in range(1, 6)),
    )

    btw = betweenness_centrality(star_nodes, star_edges)
    check("centre betweenness == 1.0", _approx(btw["C"], 1.0), f"C={btw['C']:.6f}")
    check(
        "all leaves betweenness == 0.0",
        all(_approx(btw[f"L{i}"], 0.0) for i in range(1, 6)),
        f"max_leaf={max(btw[f'L{i}'] for i in range(1, 6)):.6f}",
    )

    eig = eigenvector_centrality(star_nodes, star_edges)
    check(
        "centre eigenvector is strictly the max",
        eig["C"] > max(eig[f"L{i}"] for i in range(1, 6)),
        f"C={eig['C']:.4f}, leaf={eig['L1']:.4f}",
    )
    check(
        "all leaves share one eigenvector value (symmetry)",
        len({round(eig[f'L{i}'], 9) for i in range(1, 6)}) == 1,
    )
    # Closed form for a star: centre/leaf eigenvector ratio == sqrt(n-1).
    if eig["L1"] > 0:
        ratio = eig["C"] / eig["L1"]
        check(
            "centre/leaf eigenvector ratio == sqrt(5)",
            _approx(ratio, np.sqrt(5.0), eps=1e-4),
            f"ratio={ratio:.4f}, sqrt5={np.sqrt(5.0):.4f}",
        )

    # ===================================================================
    # PATH graph: P0 - P1 - P2 - P3 - P4 (5 nodes in a line).
    #   - The middle node P2 has the highest betweenness.
    #   - Symmetric endpoints P0/P4 have betweenness 0.
    # ===================================================================
    print("PATH graph (P0-P1-P2-P3-P4):")
    path_nodes = ["P0", "P1", "P2", "P3", "P4"]
    path_edges = [
        ("P0", "P1", 1.0),
        ("P1", "P2", 1.0),
        ("P2", "P3", 1.0),
        ("P3", "P4", 1.0),
    ]

    pbtw = betweenness_centrality(path_nodes, path_edges)
    middle = pbtw["P2"]
    others = [pbtw["P0"], pbtw["P1"], pbtw["P3"], pbtw["P4"]]
    check(
        "middle node P2 has strictly highest betweenness",
        all(middle > o for o in others),
        f"P2={middle:.4f}, P1={pbtw['P1']:.4f}",
    )
    check(
        "endpoints P0/P4 betweenness == 0.0",
        _approx(pbtw["P0"], 0.0) and _approx(pbtw["P4"], 0.0),
    )
    check(
        "betweenness symmetric (P1 == P3, P0 == P4)",
        _approx(pbtw["P1"], pbtw["P3"]) and _approx(pbtw["P0"], pbtw["P4"]),
    )
    # Closed form (normalised, n=5): P2 = 4/6 ≈ 0.6667, P1 = P3 = 3/6 = 0.5.
    check(
        "P2 normalised betweenness == 4/6",
        _approx(middle, 4.0 / 6.0, eps=1e-9),
        f"P2={middle:.6f}",
    )
    check(
        "P1 normalised betweenness == 3/6",
        _approx(pbtw["P1"], 3.0 / 6.0, eps=1e-9),
        f"P1={pbtw['P1']:.6f}",
    )

    pdeg = degree_centrality(path_nodes, path_edges)
    check(
        "path interior degree (0.5) > endpoint degree (0.25)",
        _approx(pdeg["P2"], 0.5) and _approx(pdeg["P0"], 0.25),
        f"P2={pdeg['P2']:.4f}, P0={pdeg['P0']:.4f}",
    )

    # ===================================================================
    # RESILIENCE: removing a star's centre → N isolated leaf components.
    # ===================================================================
    print("RESILIENCE (remove star centre):")
    res = resilience_after_removal(star_nodes, star_edges, "C")
    check(
        "after removing centre, n_components == 5 (all leaves isolated)",
        res["n_components"] == 5,
        f"n_components={res['n_components']}",
    )
    check(
        "largest component fraction == 1/5 (each leaf alone)",
        _approx(res["largest_component_fraction"], 0.2),
        f"frac={res['largest_component_fraction']:.4f}",
    )
    check("n_nodes_after == 5", res["n_nodes_after"] == 5)
    check("n_removed == 1", res["n_removed"] == 1)

    # Intact star: one component spanning everything.
    res_intact = resilience_after_removal(star_nodes, star_edges, [])
    check(
        "intact star is a single component",
        res_intact["n_components"] == 1
        and _approx(res_intact["largest_component_fraction"], 1.0),
        f"n_components={res_intact['n_components']}",
    )

    # PATH: removing the middle splits into exactly 2 components.
    res_path = resilience_after_removal(path_nodes, path_edges, "P2")
    check(
        "removing path middle → 2 components",
        res_path["n_components"] == 2,
        f"n_components={res_path['n_components']}, sizes={res_path['component_sizes']}",
    )

    # ===================================================================
    # DEGENERATE inputs: must not raise, must return documented shapes.
    # ===================================================================
    print("DEGENERATE inputs:")
    check("empty degree == {}", degree_centrality([], []) == {})
    check("empty eigenvector == {}", eigenvector_centrality([], []) == {})
    check("empty betweenness == {}", betweenness_centrality([], []) == {})
    check(
        "single-node degree == {n: 0.0}",
        degree_centrality(["A"], []) == {"A": 0.0},
    )
    check(
        "single-node betweenness == {n: 0.0}",
        betweenness_centrality(["A"], []) == {"A": 0.0},
    )
    # Self-loop must be dropped (A-A ignored; A-B counts).
    sl_deg = degree_centrality(["A", "B"], [("A", "A", 1.0), ("A", "B", 1.0)])
    check(
        "self-loops dropped (A degree counts only A-B)",
        _approx(sl_deg["A"], 1.0) and _approx(sl_deg["B"], 1.0),
        f"A={sl_deg['A']:.4f}",
    )
    # Disconnected graph: two separate edges, 4 nodes, removing nothing → 2 comp.
    disc = resilience_after_removal(
        ["A", "B", "C", "D"], [("A", "B"), ("C", "D")], []
    )
    check(
        "disconnected graph reports 2 components",
        disc["n_components"] == 2,
        f"n_components={disc['n_components']}",
    )
    # Eigenvector on disconnected graph: no raise, finite, non-negative.
    disc_eig = eigenvector_centrality(
        ["A", "B", "C", "D"], [("A", "B"), ("C", "D")]
    )
    check(
        "eigenvector on disconnected graph is finite & non-negative",
        all(np.isfinite(v) and v >= 0.0 for v in disc_eig.values()),
    )

    print()
    if failures == 0:
        print("ALL CHECKS PASSED")
    else:
        print(f"{failures} CHECK(S) FAILED")
    return failures


if __name__ == "__main__":
    import sys

    sys.exit(1 if _self_verify() else 0)
