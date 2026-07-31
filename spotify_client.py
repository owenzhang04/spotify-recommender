"""
spotify_client.py — Spotify API wrapper.

Two modes:
  - LIVE:    uses spotipy with real OAuth tokens. Requires CLIENT_ID/SECRET.
  - STUB:    returns deterministic fake data so the rest of the app can be
             built and demoed without Spotify credentials. Activated when
             SPOTIPY_CLIENT_ID is empty or equals "STUB".

The interface (TasteProfile dataclass) is the same in both modes so the
recommender doesn't care which one is active.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import spotipy  # for the SpotifyException reference in _playlist

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)
TOKEN_PATH = DATA_DIR / "token.json"
SPOTIFY_CACHE_PATH = DATA_DIR / "spotify.json"


# ─────────────────────────────────────────────────────────────────────
# Data model: what the recommender sees, regardless of source.
# ─────────────────────────────────────────────────────────────────────

@dataclass
class Track:
    id: str
    name: str
    artists: list[str]   # artist names, primary first
    artist_ids: list[str]
    album: str
    popularity: int       # 0..100
    uri: str              # spotify:track:...
    duration_ms: int


@dataclass
class Artist:
    id: str
    name: str
    genres: list[str]
    popularity: int


@dataclass
class Playlist:
    id: str
    name: str
    track_ids: list[str]   # tracks *in* this playlist
    owner: str
    # Artists appearing on those tracks. Needed for the artist–artist
    # co-occurrence graph. Empty on older caches until the next refresh.
    artist_ids: list[str] = field(default_factory=list)


@dataclass
class TasteProfile:
    """Everything the recommender needs from Spotify."""
    user_id: str
    display_name: str
    liked_tracks: list[Track] = field(default_factory=list)
    playlists: list[Playlist] = field(default_factory=list)
    top_artists_short: list[Artist] = field(default_factory=list)
    top_artists_medium: list[Artist] = field(default_factory=list)
    top_artists_long: list[Artist] = field(default_factory=list)
    top_tracks_short: list[Track] = field(default_factory=list)
    top_tracks_medium: list[Track] = field(default_factory=list)
    top_tracks_long: list[Track] = field(default_factory=list)
    recent: list[Track] = field(default_factory=list)

    def all_known_track_ids(self) -> set[str]:
        ids = set()
        for t in self.liked_tracks:
            ids.add(t.id)
        for p in self.playlists:
            ids.update(p.track_ids)
        for t in self.top_tracks_short + self.top_tracks_medium + self.top_tracks_long:
            ids.add(t.id)
        for t in self.recent:
            ids.add(t.id)
        return ids


# ─────────────────────────────────────────────────────────────────────
# Stub mode — deterministic fake data, no network needed.
# ─────────────────────────────────────────────────────────────────────

def _stub_profile() -> TasteProfile:
    """A taste profile that looks plausibly like someone who likes
    indie rock / dream pop / shoegaze. Useful for visual development."""
    # Artists with their genre lists (drawn from real Spotify genre tags).
    artists_data = [
        ("0OdUWJ0sBJDr0H1Y4i4jQ5", "Phoebe Bridgers", ["indie rock", "sadcore", "dream pop", "singer-songwriter"], 78),
        ("3hcs9ucDycAlNVgWP2tLN8", "Big Thief", ["indie rock", "folk", "dream pop"], 71),
        ("5M52tdnflLh3NGcb2Z2ePa", "Radiohead", ["alternative rock", "art rock", "electronic"], 86),
        ("6olE6TJLqED3rqAzbIH5rO", "Sufjan Stevens", ["indie folk", "singer-songwriter", "chamber pop"], 75),
        ("4Cv5TAlqfBhCMp4skypYJl", "Beach House", ["dream pop", "shoegaze", "indie pop"], 72),
        ("4FXo1c5fkooIhlYfL2qR0J", "My Bloody Valentine", ["shoegaze", "noise pop", "dream pop"], 69),
        ("0Odg3xmcG3VeC8c9rr3Sjm", "Slowdive", ["shoegaze", "dream pop", "noise pop"], 67),
        ("3kjuyy7t2egmlRmNoliaYh", "Snail Mail", ["indie rock", "singer-songwriter"], 64),
        ("4zgJ0jnzdqF0XsM5KlOLkS", "Japanese Breakfast", ["indie pop", "dream pop", "art pop"], 73),
        ("3inCnii8wOpN4KOMpELc5W", "Mitski", ["indie rock", "singer-songwriter", "art pop"], 76),
        ("5pDjXkAHJG8BU8tYMxHNcy", "Maggie Rogers", ["indie pop", "folk pop", "singer-songwriter"], 70),
        ("3nJDe68D0osrLRbNdm1fvD", "Father John Misty", ["indie rock", "singer-songwriter", "chamber pop"], 74),
        ("5lAme0wclfVmhdRltLh9lE", "Fleet Foxes", ["indie folk", "chamber folk", "singer-songwriter"], 68),
        ("0XNa1vTidXlvJ1g2W89Mnm", "Adrianne Lenker", ["indie folk", "singer-songwriter", "freak folk"], 65),
        ("6hbxwhd87TxXoeS4qyfN0w", "Weyes Blood", ["dream pop", "chamber pop", "art pop"], 66),
        ("2WZ2TdfdzUzUOZc4vhsFX8", "Daughter", ["indie rock", "singer-songwriter", "dream pop"], 67),
        ("3vbKDsBS70Q9sXcDWiOFVh", "Bon Iver", ["indie folk", "singer-songwriter", "chamber pop"], 81),
        ("1kdP3kWKaL0wLjOCAx7AQ2", "Courtney Barnett", ["indie rock", "singer-songwriter"], 70),
        ("6P61wLlTZA7YmeoUkqJCAw", "Julia Jacklin", ["indie rock", "singer-songwriter", "sadcore"], 60),
        ("5NGO30tJxNlK8Y75YAdJTK", "Big Thief", ["indie rock", "folk", "dream pop"], 71),  # alias? skip
    ]
    seen_ids = set()
    artists: list[Artist] = []
    for aid, name, genres, pop in artists_data:
        if aid in seen_ids:
            continue
        seen_ids.add(aid)
        artists.append(Artist(id=aid, name=name, genres=genres, popularity=pop))

    def mk_track(i: int, name: str, artist_idxs: list[int], album: str,
                 pop: int, dur_ms: int = 200_000) -> Track:
        a = [artists[k] for k in artist_idxs]
        return Track(
            id=f"stub_track_{i:03d}",
            name=name,
            artists=[x.name for x in a],
            artist_ids=[x.id for x in a],
            album=album,
            popularity=pop,
            uri=f"spotify:track:stub_track_{i:03d}",
            duration_ms=dur_ms,
        )

    tracks: list[Track] = [
        mk_track(1,  "Motion Sickness",        [9], "Puberty 2", 76),
        mk_track(2,  "Kyoto",                  [9], "Be the Cowboy", 75),
        mk_track(3,  "I Know the End",         [9], "Be the Cowboy", 78),
        mk_track(4,  "Not",                    [10], "Surrender", 70),
        mk_track(5,  "Alaska",                 [10], "Surrender", 71),
        mk_track(6,  "Myth",                   [2], "In Rainbows", 80),
        mk_track(7,  "Weird Fishes / Arpeggi", [2], "In Rainbows", 79),
        mk_track(8,  "No Surprises",           [2], "OK Computer", 85),
        mk_track(9,  "Fourth of July",         [3], "Carrie & Lowell", 74),
        mk_track(10, "Mystery of Love",        [3], "Call Me by Your Name", 73),
        mk_track(11, "Space Song",             [4], "Depression Cherry", 78),
        mk_track(12, "Myth",                   [4], "Depression Cherry", 73),
        mk_track(13, "Only Shallow",           [5], "Loveless", 70),
        mk_track(14, "When You Sleep",         [6], "Slowdive (EP)", 72),
        mk_track(15, "Pristine",               [7], "Lush", 68),
        mk_track(16, "Everybody Wants to Love You", [8], "Psychopomp", 71),
        mk_track(17, "Beaches",                [8], "Lush", 66),
        mk_track(18, "Towing the Line",        [11], "God's Favorite Customer", 73),
        mk_track(19, "Hollywood Forever",      [12], "Helplessness Blues", 70),
        mk_track(20, "anything",               [13], "songs", 64),
    ]

    # Playlists with overlapping tracks (so co-occurrence edges exist).
    pl1_tracks = [t.id for t in tracks[0:8]] + [t.id for t in tracks[10:14]]
    pl2_tracks = [t.id for t in tracks[2:12]] + [t.id for t in tracks[14:18]]
    pl3_tracks = [t.id for t in tracks[5:18]]
    pl4_tracks = [t.id for t in tracks[8:18]] + [t.id for t in tracks[0:3]]

    def _artists_for(tids: list[str]) -> list[str]:
        by_id = {t.id: t for t in tracks}
        out: list[str] = []
        seen: set[str] = set()
        for tid in tids:
            t = by_id.get(tid)
            if not t:
                continue
            for aid in t.artist_ids:
                if aid not in seen:
                    seen.add(aid)
                    out.append(aid)
        return out

    playlists = [
        Playlist(id="pl_stub_1", name="indie sad hours", track_ids=pl1_tracks,
                 owner="you", artist_ids=_artists_for(pl1_tracks)),
        Playlist(id="pl_stub_2", name="rainy day indie", track_ids=pl2_tracks,
                 owner="you", artist_ids=_artists_for(pl2_tracks)),
        Playlist(id="pl_stub_3", name="focus deep work", track_ids=pl3_tracks,
                 owner="you", artist_ids=_artists_for(pl3_tracks)),
        Playlist(id="pl_stub_4", name="late night drive", track_ids=pl4_tracks,
                 owner="you", artist_ids=_artists_for(pl4_tracks)),
    ]

    # Liked = most recent 20.
    liked = list(tracks[:20])
    recent = list(reversed(tracks[:15]))
    top_tracks_short = tracks[0:10]
    top_tracks_medium = tracks[2:14]
    top_tracks_long = tracks[4:18]

    return TasteProfile(
        user_id="stub_user",
        display_name="Stub Mode User",
        liked_tracks=liked,
        playlists=playlists,
        top_artists_short=artists[0:10],
        top_artists_medium=artists[1:11],
        top_artists_long=artists[3:18],
        top_tracks_short=top_tracks_short,
        top_tracks_medium=top_tracks_medium,
        top_tracks_long=top_tracks_long,
        recent=recent,
    )


# ─────────────────────────────────────────────────────────────────────
# Live mode — uses spotipy. Token refresh handled transparently.
# ─────────────────────────────────────────────────────────────────────

class SpotifyClient:
    """Wraps spotipy. Reads/writes token cache at data/token.json."""

    SCOPES = ("user-library-read user-top-read playlist-read-private "
              "playlist-read-collaborative user-read-recently-played "
              "user-read-email")

    def __init__(self, client_id: str, client_secret: str,
                 redirect_uri: str) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self._sp = None

    @property
    def sp(self):
        if self._sp is None:
            import spotipy
            from spotipy.oauth2 import SpotifyOAuth
            self._sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
                client_id=self.client_id,
                client_secret=self.client_secret,
                redirect_uri=self.redirect_uri,
                scope=self.SCOPES,
                cache_path=str(TOKEN_PATH),
                open_browser=True,
            ))
        return self._sp

    def get_auth_url(self) -> str:
        from spotipy.oauth2 import SpotifyOAuth
        return SpotifyOAuth(
            client_id=self.client_id,
            client_secret=self.client_secret,
            redirect_uri=self.redirect_uri,
            scope=self.SCOPES,
            cache_path=str(TOKEN_PATH),
        ).get_authorize_url()

    def exchange_code(self, code: str) -> None:
        from spotipy.oauth2 import SpotifyOAuth
        SpotifyOAuth(
            client_id=self.client_id,
            client_secret=self.client_secret,
            redirect_uri=self.redirect_uri,
            scope=self.SCOPES,
            cache_path=str(TOKEN_PATH),
        ).get_access_token(code)

    def fetch_profile(self) -> TasteProfile:
        sp = self.sp
        me = sp.current_user()
        # Liked: just the most recent 20 (no need to paginate beyond).
        liked_raw = sp.current_user_saved_tracks(limit=20)["items"]
        playlists_raw = sp.current_user_playlists(limit=50)["items"]
        top_a_s = sp.current_user_top_artists(limit=20, time_range="short_term")["items"]
        top_a_m = sp.current_user_top_artists(limit=20, time_range="medium_term")["items"]
        top_a_l = sp.current_user_top_artists(limit=20, time_range="long_term")["items"]
        top_t_s = sp.current_user_top_tracks(limit=20, time_range="short_term")["items"]
        top_t_m = sp.current_user_top_tracks(limit=20, time_range="medium_term")["items"]
        top_t_l = sp.current_user_top_tracks(limit=20, time_range="long_term")["items"]
        recent_raw = sp.current_user_recently_played(limit=50)["items"]

        def _track(t: dict) -> Track:
            return Track(
                id=t["id"],
                name=t["name"],
                artists=[a["name"] for a in t["artists"]],
                artist_ids=[a["id"] for a in t["artists"]],
                album=t["album"]["name"],
                popularity=t.get("popularity", 50),
                uri=t["uri"],
                duration_ms=t["duration_ms"],
            )

        def _artist(a: dict) -> Artist:
            return Artist(
                id=a["id"], name=a["name"], genres=a.get("genres", []),
                popularity=a.get("popularity", 50),
            )

        def _playlist(p: dict) -> Playlist | None:
            """Fetch full tracks for a playlist. Returns None if the user
            doesn't have access (e.g. followed-but-not-owned public playlists
            return 403 even with playlist-read-private scope)."""
            try:
                track_ids: list[str] = []
                artist_ids: list[str] = []
                seen_artists: set[str] = set()
                # Request artist ids alongside track ids so the co-occurrence
                # graph can be built at the artist level without a second pass.
                # Avoid over-restrictive field filters — spotipy pagination via
                # sp.next() is fragile when `next` is projected away.
                results = sp.playlist_items(
                    p["id"],
                    limit=100,
                    fields="items(track(id,type,artists(id))),next",
                    additional_types=("track",),
                )
                while True:
                    for it in results.get("items") or []:
                        t = it.get("track") or {}
                        if not t or t.get("type") == "episode" or not t.get("id"):
                            continue
                        track_ids.append(t["id"])
                        for a in t.get("artists") or []:
                            aid = a.get("id")
                            if aid and aid not in seen_artists:
                                seen_artists.add(aid)
                                artist_ids.append(aid)
                    if not results.get("next"):
                        break
                    results = sp.next(results)
                return Playlist(
                    id=p["id"], name=p["name"], track_ids=track_ids,
                    owner=(p.get("owner", {}) or {}).get("display_name")
                            or (p.get("owner", {}) or {}).get("id", "unknown"),
                    artist_ids=artist_ids,
                )
            except spotipy.exceptions.SpotifyException as e:
                # 401/403/404 → user can't read this playlist's items.
                # It's still useful as a metadata record, but with no tracks
                # it doesn't help PageRank. Skip it.
                if e.http_status in (401, 403, 404):
                    return None
                raise

        return TasteProfile(
            user_id=me["id"], display_name=me.get("display_name") or me["id"],
            liked_tracks=[_track(t["track"]) for t in liked_raw],
            playlists=[pl for pl in (_playlist(p) for p in playlists_raw) if pl is not None],
            top_artists_short=[_artist(a) for a in top_a_s],
            top_artists_medium=[_artist(a) for a in top_a_m],
            top_artists_long=[_artist(a) for a in top_a_l],
            top_tracks_short=[_track(t) for t in top_t_s],
            top_tracks_medium=[_track(t) for t in top_t_m],
            top_tracks_long=[_track(t) for t in top_t_l],
            recent=[_track(it["track"]) for it in recent_raw],
        )


# ─────────────────────────────────────────────────────────────────────
# Public API for app.py
# ─────────────────────────────────────────────────────────────────────

def is_stub_mode() -> bool:
    """Stub mode is on when credentials are missing or set to 'STUB'."""
    cid = os.environ.get("SPOTIPY_CLIENT_ID", "").strip()
    return cid == "" or cid.upper() == "STUB"


def load_cached_profile(force: bool = False) -> TasteProfile | None:
    if not SPOTIFY_CACHE_PATH.exists():
        return None
    age_hours = (time.time() - SPOTIFY_CACHE_PATH.stat().st_mtime) / 3600
    if not force and age_hours > float(os.environ.get("CACHE_TTL_HOURS", "24")):
        return None
    data = json.loads(SPOTIFY_CACHE_PATH.read_text())
    return _deserialize_profile(data)


# Process-local record of the most recent refresh error (if any). Used
# by /healthz to surface when Spotify is rate-limiting without
# surfacing internal exceptions in the UI.
_last_refresh_error: dict[str, str | None] = {"value": None}


def last_refresh_error() -> str | None:
    return _last_refresh_error["value"]


def save_cached_profile(p: TasteProfile) -> None:
    SPOTIFY_CACHE_PATH.write_text(json.dumps(_serialize_profile(p), indent=2))


def fetch_profile(force: bool = False) -> TasteProfile:
    """Get the user's taste profile. Cached unless force=True or no cache."""
    if not force:
        cached = load_cached_profile()
        if cached is not None:
            return cached

    if is_stub_mode():
        profile = _stub_profile()
        save_cached_profile(profile)
        return profile

    client = SpotifyClient(
        client_id=os.environ["SPOTIPY_CLIENT_ID"],
        client_secret=os.environ["SPOTIPY_CLIENT_SECRET"],
        redirect_uri=os.environ["SPOTIPY_REDIRECT_URI"],
    )
    # Fetch profile; if Spotify is rate-limiting or auth has lapsed, fall
    # back to whatever's on disk (even if past TTL) rather than 500ing.
    try:
        profile = client.fetch_profile()
        save_cached_profile(profile)
        return profile
    except Exception as e:
        cached = load_cached_profile(force=True)
        if cached is None:
            raise
        # Note: still "fresh" from the user's view; they can refresh again
        # later once Spotify comes back online.
        _last_refresh_error["value"] = str(e)[:200]
        return cached


# ─────────────────────────────────────────────────────────────────────
# (De)serialization for the cache file
# ─────────────────────────────────────────────────────────────────────

def _serialize_profile(p: TasteProfile) -> dict[str, Any]:
    return {
        "user_id": p.user_id,
        "display_name": p.display_name,
        "liked_tracks": [t.__dict__ for t in p.liked_tracks],
        "playlists": [pl.__dict__ for pl in p.playlists],
        "top_artists_short": [a.__dict__ for a in p.top_artists_short],
        "top_artists_medium": [a.__dict__ for a in p.top_artists_medium],
        "top_artists_long": [a.__dict__ for a in p.top_artists_long],
        "top_tracks_short": [t.__dict__ for t in p.top_tracks_short],
        "top_tracks_medium": [t.__dict__ for t in p.top_tracks_medium],
        "top_tracks_long": [t.__dict__ for t in p.top_tracks_long],
        "recent": [t.__dict__ for t in p.recent],
    }


def _playlist_from_dict(pl: dict[str, Any]) -> Playlist:
    """Tolerant playlist loader — older caches omit artist_ids."""
    return Playlist(
        id=pl["id"],
        name=pl["name"],
        track_ids=list(pl.get("track_ids") or []),
        owner=pl.get("owner") or "unknown",
        artist_ids=list(pl.get("artist_ids") or []),
    )


def _deserialize_profile(d: dict[str, Any]) -> TasteProfile:
    return TasteProfile(
        user_id=d["user_id"],
        display_name=d["display_name"],
        liked_tracks=[Track(**t) for t in d.get("liked_tracks", [])],
        playlists=[_playlist_from_dict(pl) for pl in d.get("playlists", [])],
        top_artists_short=[Artist(**a) for a in d.get("top_artists_short", [])],
        top_artists_medium=[Artist(**a) for a in d.get("top_artists_medium", [])],
        top_artists_long=[Artist(**a) for a in d.get("top_artists_long", [])],
        top_tracks_short=[Track(**t) for t in d.get("top_tracks_short", [])],
        top_tracks_medium=[Track(**t) for t in d.get("top_tracks_medium", [])],
        top_tracks_long=[Track(**t) for t in d.get("top_tracks_long", [])],
        recent=[Track(**t) for t in d.get("recent", [])],
    )


def profile_data_quality(p: TasteProfile) -> dict[str, Any]:
    """Summarize whether a live pull has usable recommender signal.

    Used by /healthz and bin/check_live_data.py to tell whether Spotify
    User Management / playlist access is working.
    """
    n_artists = (
        len(p.top_artists_short) + len(p.top_artists_medium) + len(p.top_artists_long)
    )
    artists_with_genres = sum(
        1 for a in (p.top_artists_short + p.top_artists_medium + p.top_artists_long)
        if a.genres
    )
    popularities = [
        a.popularity for a in (p.top_artists_short + p.top_artists_medium + p.top_artists_long)
    ]
    unique_pops = sorted(set(popularities))
    playlists_with_tracks = sum(1 for pl in p.playlists if pl.track_ids)
    playlists_with_artists = sum(1 for pl in p.playlists if pl.artist_ids)
    n_playlist_tracks = sum(len(pl.track_ids) for pl in p.playlists)
    # Dev-mode stripping typically yields empty genres + every popularity == 50.
    genres_ok = artists_with_genres > 0
    popularity_ok = len(unique_pops) > 1 or (unique_pops != [50] and bool(popularities))
    playlists_ok = playlists_with_tracks > 0
    return {
        "n_liked": len(p.liked_tracks),
        "n_playlists": len(p.playlists),
        "playlists_with_tracks": playlists_with_tracks,
        "playlists_with_artists": playlists_with_artists,
        "n_playlist_tracks": n_playlist_tracks,
        "n_top_artists": n_artists,
        "artists_with_genres": artists_with_genres,
        "unique_popularities": unique_pops[:8],
        "genres_ok": genres_ok,
        "popularity_ok": popularity_ok,
        "playlists_ok": playlists_ok,
        "usable_for_recs": genres_ok or playlists_ok,
        "dev_mode_limited": (not genres_ok) and (not popularity_ok),
    }