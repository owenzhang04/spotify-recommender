"""Regression tests for refresh, cache, and recommend-path fixes.

Stdlib unittest — no pytest required:

  SPOTIPY_CLIENT_ID= python -m unittest tests.test_rec_fixes -v

Also works with pytest if installed:

  SPOTIPY_CLIENT_ID= python -m pytest tests/test_rec_fixes.py -q
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Force stub mode before importing project modules (dotenv won't override).
os.environ["SPOTIPY_CLIENT_ID"] = ""


class TestRefreshErrorCleared(unittest.TestCase):
    def test_successful_fetch_clears_refresh_error(self) -> None:
        import spotify_client as sc
        from spotify_client import fetch_profile, last_refresh_error

        sc._last_refresh_error["value"] = "simulated prior failure"
        self.assertIsNotNone(last_refresh_error())

        profile = fetch_profile(force=True, persist=False)
        self.assertEqual(profile.user_id, "stub_user")
        self.assertIsNone(last_refresh_error())


class TestStubDoesNotOverwriteCache(unittest.TestCase):
    def test_persist_false_leaves_existing_cache(self) -> None:
        import spotify_client as sc
        from recommender import clear_caches, recommend
        from spotify_client import fetch_profile

        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "spotify.json"
            sentinel = {"marker": "do-not-overwrite", "user_id": "live_user"}
            cache_path.write_text(json.dumps(sentinel), encoding="utf-8")
            before = cache_path.read_text(encoding="utf-8")

            with patch.object(sc, "SPOTIFY_CACHE_PATH", cache_path):
                clear_caches()
                profile = fetch_profile(force=True, persist=False)
                self.assertEqual(profile.user_id, "stub_user")
                recs = recommend(profile, n_each=8)
                self.assertEqual(len(recs.genre_recs), 8)
                self.assertEqual(cache_path.read_text(encoding="utf-8"), before)


class TestRefreshRedirect(unittest.TestCase):
    def test_refresh_form_post_redirects_home(self) -> None:
        from fastapi.testclient import TestClient

        import app as app_module

        client = TestClient(app_module.app)
        # Browser-like Accept (no application/json primary).
        resp = client.post(
            "/refresh",
            headers={"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"},
            follow_redirects=False,
        )
        self.assertIn(resp.status_code, (302, 303))
        self.assertEqual(resp.headers.get("location"), "/")

    def test_refresh_json_when_requested(self) -> None:
        from fastapi.testclient import TestClient

        import app as app_module

        client = TestClient(app_module.app)
        resp = client.post(
            "/refresh?format=json",
            headers={"Accept": "application/json"},
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body.get("ok"))
        self.assertIn("n_genre_recs", body)


class TestCrossListNoOverlap(unittest.TestCase):
    def test_stub_recommend_no_artist_overlap(self) -> None:
        from recommender import clear_caches, recommend
        from spotify_client import fetch_profile

        clear_caches()
        profile = fetch_profile(force=True, persist=False)
        recs = recommend(profile, n_each=8)
        overlap = {r.artist_id for r in recs.genre_recs} & {
            r.artist_id for r in recs.graph_recs
        }
        self.assertFalse(overlap, f"unexpected overlap: {sorted(overlap)}")


class TestPickTrackClientInitFailure(unittest.TestCase):
    def test_pick_track_survives_spotify_init_failure(self) -> None:
        from recommender import Artist, TasteProfile, clear_caches, pick_track_for_artist

        clear_caches()
        artist = Artist(
            id="live_artist_1",
            name="Init Fail Artist",
            genres=["indie"],
            popularity=60,
        )
        profile = TasteProfile(
            user_id="u",
            display_name="u",
            liked_tracks=[],
            playlists=[],
            top_artists_short=[],
            top_artists_medium=[],
            top_artists_long=[],
            top_tracks_short=[],
            top_tracks_medium=[],
            top_tracks_long=[],
            recent=[],
        )

        with patch("recommender.is_stub_mode", return_value=False), \
             patch("recommender._live_calls_paused", return_value=False), \
             patch("recommender._spotify_call"), \
             patch("recommender._spotify", side_effect=RuntimeError("client init failed")):
            track = pick_track_for_artist(artist, profile)

        self.assertEqual(track.artists, [artist.name])
        self.assertTrue(track.uri.startswith("spotify:artist:"))


class TestDevModeLimitedAligned(unittest.TestCase):
    def test_helper_matches_profile_data_quality(self) -> None:
        import app as app_module
        from spotify_client import Artist, TasteProfile, profile_data_quality

        limited = TasteProfile(
            user_id="u",
            display_name="u",
            liked_tracks=[],
            playlists=[],
            top_artists_short=[
                Artist(id="a1", name="A", genres=[], popularity=50),
            ],
            top_artists_medium=[],
            top_artists_long=[],
            top_tracks_short=[],
            top_tracks_medium=[],
            top_tracks_long=[],
            recent=[],
        )
        with patch("app.is_stub_mode", return_value=False):
            self.assertEqual(
                app_module._dev_mode_limited(limited),
                profile_data_quality(limited)["dev_mode_limited"],
            )
            self.assertTrue(app_module._dev_mode_limited(limited))


if __name__ == "__main__":
    unittest.main()
