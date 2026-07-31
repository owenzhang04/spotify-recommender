#!/usr/bin/env python3
"""Check whether live Spotify data is usable for recommendations.

Walks through:
  1. Credentials / token presence
  2. Force-refresh of the taste profile (if token exists)
  3. Data-quality report (genres, popularity, playlist tracks)

Usage (from project root, with .env configured):
  python bin/check_live_data.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from recommender import analyze_taste, clear_caches, recommend  # noqa: E402
from spotify_client import (  # noqa: E402
    TOKEN_PATH,
    fetch_profile,
    is_stub_mode,
    last_refresh_error,
    profile_data_quality,
)


def main() -> int:
    print("=== Spotify Rec live-data check ===")
    if is_stub_mode():
        print("FAIL: stub mode active (SPOTIPY_CLIENT_ID empty or STUB).")
        print("  Fill .env with Client ID/Secret, then re-run.")
        return 1

    if not TOKEN_PATH.exists():
        print("FAIL: no OAuth token at data/token.json.")
        print("  1. Add your email under Spotify Dashboard → User Management")
        print("  2. python app.py  → open http://127.0.0.1:8000 → Connect Spotify")
        print("  3. Re-run this script")
        return 1

    print("token: present")
    clear_caches()
    try:
        profile = fetch_profile(force=True)
    except Exception as e:
        print(f"FAIL: fetch_profile raised: {e}")
        return 1

    err = last_refresh_error()
    if err:
        print(f"WARN: last_refresh_error={err} (may be serving stale cache)")

    quality = profile_data_quality(profile)
    print("profile:", profile.display_name, f"({profile.user_id})")
    print("data_quality:")
    print(json.dumps(quality, indent=2))

    fp = analyze_taste(profile)
    print("top_genres:", [g for g, _ in fp.top_genres[:8]] or "(none)")

    ok = True
    if quality["dev_mode_limited"] or not quality["genres_ok"]:
        print("FAIL: genres/popularity look stripped (dev-mode / User Management).")
        print("  Open https://developer.spotify.com/dashboard → your app →")
        print("  User Management → add your Spotify email → Refresh in the UI.")
        ok = False
    if not quality["playlists_ok"]:
        print("WARN: no playlist tracks — graph signal will be weak"
              " (liked/top-track cliques still help).")
        # Not a hard fail; liked + top tracks can still seed the graph.

    if ok or quality["usable_for_recs"]:
        recs = recommend(profile, n_each=8)
        overlap = {r.artist_id for r in recs.genre_recs} & {
            r.artist_id for r in recs.graph_recs
        }
        print(f"genre_recs ({len(recs.genre_recs)}):",
              [r.artist_name for r in recs.genre_recs])
        print(f"graph_recs ({len(recs.graph_recs)}):",
              [r.artist_name for r in recs.graph_recs])
        print("lists_overlap:", sorted(overlap) or "(none)")
        if not recs.genre_recs and not recs.graph_recs:
            print("FAIL: both recommendation lists empty")
            return 1

    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
