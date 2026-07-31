#!/usr/bin/env python3
"""Tiny offline holdout eval for the recommender.

Holds out N top/liked artists from the taste profile, rebuilds the
fingerprint + candidate pool from the remainder, and measures whether
held-out artists appear in the candidate pool (recovery hit-rate).

In stub mode, candidates are a fixed indie pool that does not include
the stub's own top artists — so this primarily validates the *scoring*
machinery and prints diagnostic ranks. For a meaningful recovery rate,
run against a live profile where related-artists can surface held-out
neighbors of remaining seeds.

Usage:
  SPOTIPY_CLIENT_ID= python bin/eval_holdout.py
  python bin/eval_holdout.py          # uses .env (live if configured)
"""

from __future__ import annotations

import copy
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from recommender import (  # noqa: E402
    analyze_taste,
    clear_caches,
    get_candidate_pool,
    get_related_edges,
    recommend,
)
from spotify_client import Artist, TasteProfile, fetch_profile, is_stub_mode  # noqa: E402


def _strip_artists(p: TasteProfile, holdout_ids: set[str]) -> TasteProfile:
    """Return a shallow-copied profile with holdout artists removed from tops."""
    q = copy.deepcopy(p)

    def filt_artists(xs: list[Artist]) -> list[Artist]:
        return [a for a in xs if a.id not in holdout_ids]

    def filt_tracks(xs):
        return [t for t in xs if not (set(t.artist_ids) & holdout_ids)]

    q.top_artists_short = filt_artists(q.top_artists_short)
    q.top_artists_medium = filt_artists(q.top_artists_medium)
    q.top_artists_long = filt_artists(q.top_artists_long)
    q.liked_tracks = filt_tracks(q.liked_tracks)
    q.top_tracks_short = filt_tracks(q.top_tracks_short)
    q.top_tracks_medium = filt_tracks(q.top_tracks_medium)
    q.top_tracks_long = filt_tracks(q.top_tracks_long)
    q.recent = filt_tracks(q.recent)
    # Drop holdout artists from playlist artist lists (tracks stay — ok).
    for pl in q.playlists:
        pl.artist_ids = [a for a in pl.artist_ids if a not in holdout_ids]
    # Use a distinct user_id so candidate-pool cache doesn't collide.
    q.user_id = f"{p.user_id}__holdout"
    return q


def main() -> int:
    n_holdout = int(os.environ.get("HOLDOUT_N", "5"))
    clear_caches()
    force = is_stub_mode()
    p = fetch_profile(force=force)

    # Prefer short-term top artists as holdouts (strongest signal).
    pool = list(p.top_artists_short)
    if len(pool) < n_holdout:
        pool = pool + [a for a in p.top_artists_medium if a.id not in {x.id for x in pool}]
    holdouts = pool[:n_holdout]
    if not holdouts:
        print("FAIL: no artists to hold out")
        return 1

    holdout_ids = {a.id for a in holdouts}
    holdout_names = {a.id: a.name for a in holdouts}
    print(f"mode: {'stub' if is_stub_mode() else 'live'}")
    print(f"holding out ({len(holdouts)}):",
          [a.name for a in holdouts])

    q = _strip_artists(p, holdout_ids)
    clear_caches()
    fp = analyze_taste(q)
    candidates = get_candidate_pool(q, fp)
    cand_ids = {a.id for a in candidates}
    edges = get_related_edges(q)

    recovered = [hid for hid in holdout_ids if hid in cand_ids]
    # Also check whether any held-out id appears as a related-edge target
    # (shouldn't, since we removed them from seeds — diagnostic only).
    recs = recommend(q, n_each=8)
    rec_ids = {r.artist_id for r in recs.genre_recs + recs.graph_recs}

    hit_rate = len(recovered) / len(holdout_ids)
    print(f"candidate_pool size: {len(candidates)}")
    print(f"related_edges: {len(edges)}")
    print(f"holdout recovered in candidate pool: "
          f"{len(recovered)}/{len(holdout_ids)} ({hit_rate:.0%})")
    if recovered:
        print("  recovered:", [holdout_names[i] for i in recovered])
    missing = holdout_ids - set(recovered)
    if missing:
        print("  missing:", [holdout_names[i] for i in missing])

    print("genre_recs:", [r.artist_name for r in recs.genre_recs])
    print("graph_recs:", [r.artist_name for r in recs.graph_recs])
    print(f"holdouts in final recs: {len(holdout_ids & rec_ids)}/{len(holdout_ids)}")

    # Stub pool is intentionally disjoint from stub library — hit-rate ~0
    # is expected. Exit 0 as long as the pipeline ran and produced recs.
    if not recs.genre_recs and not recs.graph_recs:
        print("FAIL: empty recommendations after holdout")
        return 1
    print("OK: holdout eval completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
