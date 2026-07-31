#!/usr/bin/env python3
"""Stub-mode smoke test for the recommender pipeline.

Usage:
  SPOTIPY_CLIENT_ID= python bin/smoke_test.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Force stub mode regardless of .env
os.environ["SPOTIPY_CLIENT_ID"] = ""

from recommender import analyze_taste, clear_caches, recommend  # noqa: E402
from spotify_client import fetch_profile, profile_data_quality  # noqa: E402


def main() -> int:
    clear_caches()
    # Bypass stale cache from live pulls
    p = fetch_profile(force=True)
    assert p.user_id == "stub_user", p.user_id
    assert any(pl.track_ids for pl in p.playlists), "stub playlists empty"
    assert any(pl.artist_ids for pl in p.playlists), "stub playlist artists empty"

    fp = analyze_taste(p)
    assert fp.top_genres, "taste fingerprint empty"
    quality = profile_data_quality(p)
    assert quality["genres_ok"] and quality["playlists_ok"], quality

    recs = recommend(p, n_each=8)
    assert len(recs.genre_recs) == 8, recs.genre_recs
    assert len(recs.graph_recs) == 8, recs.graph_recs
    overlap = {r.artist_id for r in recs.genre_recs} & {
        r.artist_id for r in recs.graph_recs
    }
    assert not overlap, f"lists should be cross-deduped, got {overlap}"

    print("OK:")
    print(f"  genres: {[g for g, _ in fp.top_genres[:5]]}")
    print(f"  genre_recs ({len(recs.genre_recs)}):",
          [r.artist_name for r in recs.genre_recs])
    print(f"  graph_recs ({len(recs.graph_recs)}):",
          [r.artist_name for r in recs.graph_recs])
    print(f"  overlap: {sorted(overlap)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
