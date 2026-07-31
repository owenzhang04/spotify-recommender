"""
recommender.py - turn a TasteProfile into recommendations.

Two algorithms, run side-by-side:
  1. Genre sampling: pick from your top genres, dedupe against your library.
  2. Artist co-occurrence graph + Personalized PageRank: build an artist
     graph from your playlists and liked songs, bridge candidates via
     related-artist / genre soft edges, then rank undiscovered artists.

Both algorithms take a TasteProfile + a candidate Artist pool and return
a list of Track recommendations. Scores are hybridized so each list has
a primary signal plus light secondary signals, then cross-deduped.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Callable

import networkx as nx

from spotify_client import Artist, TasteProfile, Track, is_stub_mode


# ─────────────────────────────────────────────────────────────────────
# Taste analysis: derive a "taste fingerprint" from a profile.
# ─────────────────────────────────────────────────────────────────────

@dataclass
class TasteFingerprint:
    """A condensed view of the user's taste."""
    top_genres: list[tuple[str, float]]       # (genre, weight)
    top_artists: list[tuple[str, float]]      # (artist_id, weight)
    n_liked: int
    n_playlists: int
    n_tracks_total: int


def analyze_taste(p: TasteProfile) -> TasteFingerprint:
    """Build the taste fingerprint."""
    genre_weights: Counter = Counter()
    artist_weights: Counter = Counter()

    for w, group in [
        (3.0, p.top_artists_short),
        (2.0, p.top_artists_medium),
        (1.0, p.top_artists_long),
    ]:
        for a in group:
            artist_weights[a.id] += w
            for g in a.genres:
                genre_weights[g] += w

    for t in p.liked_tracks:
        for aid in t.artist_ids:
            artist_weights[aid] += 2.0

    return TasteFingerprint(
        top_genres=genre_weights.most_common(20),
        top_artists=artist_weights.most_common(30),
        n_liked=len(p.liked_tracks),
        n_playlists=len(p.playlists),
        n_tracks_total=len(p.all_known_track_ids()),
    )


# ─────────────────────────────────────────────────────────────────────
# Candidate artist pool - pluggable, stub or live.
# ─────────────────────────────────────────────────────────────────────

CandidateProvider = Callable[[TasteProfile, TasteFingerprint], list[Artist]]

# Per-process caches. Spotify's dev-mode 403s and rate limits make
# per-artist lookups expensive, so we cache results for the life of
# this server process. Cleared on /refresh via clear_caches().
_candidate_pool_cache: dict[str, list[Artist]] = {}
_related_edges_cache: dict[str, list[tuple[str, str, float]]] = {}
_track_lookup_cache: dict[str, Track] = {}


def clear_caches() -> None:
    """Drop process-local recommender caches (call on force refresh)."""
    _candidate_pool_cache.clear()
    _related_edges_cache.clear()
    _track_lookup_cache.clear()
    _live_call_log.clear()


def _cache_key(p: TasteProfile) -> str:
    return p.user_id or "anonymous"


def get_related_edges(p: TasteProfile) -> list[tuple[str, str, float]]:
    """Seed→candidate relatedness edges recorded while building the pool."""
    return list(_related_edges_cache.get(_cache_key(p), []))


def _stub_candidate_pool(
    p: TasteProfile, fp: TasteFingerprint
) -> tuple[list[Artist], list[tuple[str, str, float]]]:
    """Fake candidate artists plausibly similar to a stub profile."""
    artists = [
        Artist(id="rec_1", name="Alvvays", genres=["indie pop", "dream pop", "shoegaze"], popularity=70),
        Artist(id="rec_2", name="Real Estate", genres=["indie rock", "jangle pop", "dream pop"], popularity=66),
        Artist(id="rec_3", name="Warpaint", genres=["indie rock", "dream pop", "art rock"], popularity=63),
        Artist(id="rec_4", name="Widowspeak", genres=["dream pop", "indie rock", "singer-songwriter"], popularity=55),
        Artist(id="rec_5", name="HAIM", genres=["indie pop", "folk pop"], popularity=72),
        Artist(id="rec_6", name="Mazzy Star", genres=["dream pop", "shoegaze", "slowcore"], popularity=68),
        Artist(id="rec_7", name="Cigarettes After Sex", genres=["dream pop", "shoegaze", "ambient pop"], popularity=80),
        Artist(id="rec_8", name="Men I Trust", genres=["dream pop", "indie pop", "bedroom pop"], popularity=73),
        Artist(id="rec_9", name="FKA twigs", genres=["art pop", "electronic", "experimental pop"], popularity=76),
        Artist(id="rec_10", name="Lorde", genres=["art pop", "indie pop", "electropop"], popularity=78),
        Artist(id="rec_11", name="Grimes", genres=["art pop", "electropop", "experimental pop"], popularity=75),
        Artist(id="rec_12", name="Caroline Polachek", genres=["art pop", "indie pop", "experimental pop"], popularity=70),
        Artist(id="rec_13", name="Soccer Mommy", genres=["indie rock", "singer-songwriter", "lo-fi"], popularity=68),
        Artist(id="rec_14", name="Sade", genres=["soul", "smooth jazz", "adult contemporary"], popularity=80),
        Artist(id="rec_15", name="Maz", genres=["indie pop", "singer-songwriter"], popularity=58),
        Artist(id="rec_16", name="The Japanese House", genres=["indie pop", "dream pop", "electropop"], popularity=67),
        Artist(id="rec_17", name="Angel Olsen", genres=["indie rock", "singer-songwriter", "art pop"], popularity=64),
        Artist(id="rec_18", name="Cocteau Twins", genres=["dream pop", "ethereal wave", "shoegaze"], popularity=69),
        Artist(id="rec_19", name="Tame Impala", genres=["psychedelic pop", "indie pop", "neo-psychedelia"], popularity=84),
        Artist(id="rec_20", name="Yves Tumor", genres=["art pop", "experimental", "electronic"], popularity=62),
    ]
    # Soft relatedness: connect each candidate to seed artists that share genres.
    seeds = p.top_artists_short[:8]
    edges: list[tuple[str, str, float]] = []
    for a in artists:
        for seed in seeds:
            overlap = len(set(a.genres) & set(seed.genres))
            if overlap:
                edges.append((seed.id, a.id, float(overlap)))
    return artists, edges


def get_candidate_pool(p: TasteProfile, fp: TasteFingerprint) -> list[Artist]:
    """Return the candidate artist pool. Stub or live depending on env.

    Also records seed→candidate relatedness edges used by the graph ranker.
    """
    key = _cache_key(p)
    if key in _candidate_pool_cache:
        return _candidate_pool_cache[key]

    if is_stub_mode():
        pool, edges = _stub_candidate_pool(p, fp)
    else:
        pool, edges = _live_candidate_pool(p, fp, _spotify())

    _candidate_pool_cache[key] = pool
    _related_edges_cache[key] = edges
    return pool


def _spotify():
    """Lazy import of spotipy client configured from env. Reuses the
    same scope string that spotify_client.SpotifyClient uses, so they
    can't drift apart."""
    import os
    from pathlib import Path

    import spotipy
    from spotipy.oauth2 import SpotifyOAuth

    from spotify_client import SpotifyClient
    token_path = Path(__file__).parent / "data" / "token.json"
    return spotipy.Spotify(auth_manager=SpotifyOAuth(
        client_id=os.environ["SPOTIPY_CLIENT_ID"],
        client_secret=os.environ["SPOTIPY_CLIENT_SECRET"],
        redirect_uri=os.environ["SPOTIPY_REDIRECT_URI"],
        scope=SpotifyClient.SCOPES,
        cache_path=str(token_path),
    ))


def _live_candidate_pool(
    p: TasteProfile, fp: TasteFingerprint, sp
) -> tuple[list[Artist], list[tuple[str, str, float]]]:
    """Build a candidate artist pool + relatedness edges.

    Sources:
    1. Related artists for top seed artists (best signal)
    2. Genre search
    3. Free-text search by top artist names
    4. Keyword search on top genres
    """
    known = {aid for aid, _ in fp.top_artists} | {
        aid for t in p.liked_tracks for aid in t.artist_ids
    }
    seen: dict[str, Artist] = {}
    edges: list[tuple[str, str, float]] = []

    def _add(artists: list[Artist], seed_id: str | None = None,
             weight: float = 1.0) -> None:
        for a in artists:
            if a.id in known:
                continue
            if a.id not in seen:
                seen[a.id] = a
            if seed_id is not None:
                edges.append((seed_id, a.id, weight))

    seed_ids = [aid for aid, _ in fp.top_artists[:8]]
    for sid in seed_ids:
        try:
            data = sp.artist_related_artists(sid)
            related = [
                Artist(
                    id=a["id"], name=a["name"],
                    genres=a.get("genres", []),
                    popularity=a.get("popularity", 50),
                )
                for a in data.get("artists", [])
            ]
            _add(related, seed_id=sid, weight=1.5)
        except Exception:
            continue

    for genre, _ in fp.top_genres[:5]:
        try:
            results = sp.search(q=f'genre:"{genre}"', type="artist",
                                limit=20)["artists"]["items"]
            for a in results:
                if genre in (a.get("genres") or []):
                    _add([Artist(
                        id=a["id"], name=a["name"],
                        genres=a.get("genres", []),
                        popularity=a.get("popularity", 50),
                    )])
        except Exception:
            continue

    name_sources: list[Artist] = (
        p.top_artists_short + p.top_artists_medium + p.top_artists_long
    )
    seen_names: set[str] = set()
    for ta in name_sources:
        if ta.name in seen_names or len(seen_names) >= 8:
            continue
        seen_names.add(ta.name)
        try:
            r = sp.search(q=ta.name, type="artist", limit=10)
            for a in r["artists"]["items"]:
                if a["id"] == ta.id or a["id"] in known:
                    continue
                _add([Artist(
                    id=a["id"], name=a["name"],
                    genres=a.get("genres", []),
                    popularity=a.get("popularity", 50),
                )], seed_id=ta.id, weight=0.4)
        except Exception:
            continue

    keywords: list[str] = [g for g, _ in fp.top_genres[:3]]
    for kw in keywords:
        try:
            r = sp.search(q=kw, type="artist", limit=10)
            for a in r["artists"]["items"]:
                if a["id"] in known:
                    continue
                _add([Artist(
                    id=a["id"], name=a["name"],
                    genres=a.get("genres", []),
                    popularity=a.get("popularity", 50),
                )])
        except Exception:
            continue

    return list(seen.values()), edges


# ─────────────────────────────────────────────────────────────────────
# Track lookup - given an artist, return their top track.
# ─────────────────────────────────────────────────────────────────────

@dataclass
class RecommendedTrack:
    track_name: str
    artist_name: str
    artist_id: str
    uri: str
    reason: str        # human-readable explanation
    source: str        # "genre" or "graph"


def pick_track_for_artist(artist: Artist, p: TasteProfile,
                          provider: Callable[[str], list[Track]] | None = None
                          ) -> Track:
    """Pick one representative track for the artist."""
    if artist.id in _track_lookup_cache:
        return _track_lookup_cache[artist.id]

    if is_stub_mode():
        out = Track(
            id=f"rec_track_{artist.id}",
            name=f"A track by {artist.name}",
            artists=[artist.name],
            artist_ids=[artist.id],
            album=f"Album by {artist.name}",
            popularity=artist.popularity,
            uri=f"spotify:track:rec_{artist.id}",
            duration_ms=210_000,
        )
        _track_lookup_cache[artist.id] = out
        return out
    if provider is not None:
        tracks = provider(artist.id)
    elif _live_calls_paused():
        tracks = []
    else:
        tracks = []
        sp = None
        try:
            _spotify_call()
            sp = _spotify()
            data = sp.artist_top_tracks(artist.id, country="US")
            tracks = [
                Track(
                    id=t["id"], name=t["name"],
                    artists=[a["name"] for a in t["artists"]],
                    artist_ids=[a["id"] for a in t["artists"]],
                    album=t["album"]["name"],
                    popularity=t.get("popularity", 50),
                    uri=t["uri"],
                    duration_ms=t["duration_ms"],
                )
                for t in data.get("tracks", [])[:3]
            ]
        except Exception as e:
            _register_live_failure(e)
            tracks = []

        if not tracks and not _live_calls_paused():
            try:
                _spotify_call()
                if sp is None:
                    sp = _spotify()
                r = sp.search(q=artist.name, type="track", limit=5)
                for t in r["tracks"]["items"][:3]:
                    tracks.append(Track(
                        id=t["id"], name=t["name"],
                        artists=[a["name"] for a in t["artists"]],
                        artist_ids=[a["id"] for a in t["artists"]],
                        album=t["album"]["name"],
                        popularity=t.get("popularity", 50),
                        uri=t["uri"],
                        duration_ms=t["duration_ms"],
                    ))
            except Exception as e:
                _register_live_failure(e)
    if not tracks:
        out = Track(
            id=artist.id, name="Artist on Spotify",
            artists=[artist.name], artist_ids=[artist.id],
            album="", popularity=artist.popularity,
            uri=f"spotify:artist:{artist.id}", duration_ms=0,
        )
        _track_lookup_cache[artist.id] = out
        return out
    out = tracks[0]
    _track_lookup_cache[artist.id] = out
    return out


# ──────────────────────────────────────────────────────────────────
# Live-mode throttle
# ──────────────────────────────────────────────────────────────────
_live_call_log: list[float] = []
_LIVE_WINDOW_S = 30.0
_LIVE_MAX_FAILS = 3


def _spotify_call() -> None:
    import time as _t
    _t.sleep(0.2)


def _register_live_failure(exc: Exception) -> None:
    import time as _t
    now = _t.time()
    _live_call_log.append(now)
    while _live_call_log and (now - _live_call_log[0]) > _LIVE_WINDOW_S:
        _live_call_log.pop(0)


def _live_calls_paused() -> bool:
    import time as _t
    now = _t.time()
    while _live_call_log and (now - _live_call_log[0]) > _LIVE_WINDOW_S:
        _live_call_log.pop(0)
    return len(_live_call_log) >= _LIVE_MAX_FAILS


# ─────────────────────────────────────────────────────────────────────
# Scoring helpers
# ─────────────────────────────────────────────────────────────────────

def _normalize(scores: dict[str, float]) -> dict[str, float]:
    if not scores:
        return {}
    lo = min(scores.values())
    hi = max(scores.values())
    if hi <= lo:
        return {k: 1.0 for k in scores}
    return {k: (v - lo) / (hi - lo) for k, v in scores.items()}


def _genre_raw_scores(
    fp: TasteFingerprint, candidates: list[Artist]
) -> dict[str, float]:
    """Raw genre-overlap scores keyed by artist id."""
    top_genres = {g for g, _ in fp.top_genres[:8]}
    known_artists = {aid for aid, _ in fp.top_artists}
    scores: dict[str, float] = {}

    if top_genres:
        for a in candidates:
            if a.id in known_artists:
                continue
            overlap = len(set(a.genres) & top_genres)
            if overlap == 0:
                continue
            scores[a.id] = overlap * 10 + a.popularity * 0.1
        return scores

    all_pool_genres: Counter[str] = Counter()
    for a in candidates:
        if a.id not in known_artists:
            all_pool_genres.update(a.genres)
    if all_pool_genres:
        for a in candidates:
            if a.id in known_artists or not a.genres:
                continue
            scores[a.id] = sum(all_pool_genres[g] for g in a.genres) + a.popularity * 0.5
        return scores

    for a in candidates:
        if a.id in known_artists:
            continue
        scores[a.id] = float(a.popularity)
    return scores


def _relatedness_scores(
    candidates: list[Artist], edges: list[tuple[str, str, float]]
) -> dict[str, float]:
    by_cand: Counter[str] = Counter()
    cand_ids = {a.id for a in candidates}
    for _seed, cand, w in edges:
        if cand in cand_ids:
            by_cand[cand] += w
    return dict(by_cand)


def _playlist_artist_ids(pl, p: TasteProfile) -> list[str]:
    """Artists in a playlist; fall back to resolving track ids from profile."""
    if pl.artist_ids:
        return list(pl.artist_ids)
    track_map: dict[str, list[str]] = {}
    for t in (
        p.liked_tracks + p.top_tracks_short + p.top_tracks_medium
        + p.top_tracks_long + p.recent
    ):
        track_map[t.id] = t.artist_ids
    out: list[str] = []
    seen: set[str] = set()
    for tid in pl.track_ids:
        for aid in track_map.get(tid, []):
            if aid not in seen:
                seen.add(aid)
                out.append(aid)
    return out


# Full playlist cliques are O(n²); cap keeps normal playlists intact.
_MAX_CLIQUE_ARTISTS = 50

# Treat PageRank mass below this as "no graph signal" (float noise / isolates).
_GRAPH_PR_EPS = 1e-12


def build_artist_cooccurrence_graph(p: TasteProfile) -> nx.Graph:
    """Nodes = artists. Edges = co-appear in the same playlist / liked set."""
    g = nx.Graph()

    def add_clique(artist_ids: list[str], weight: float) -> None:
        # Cap dense sets so huge playlists don't explode edge count.
        aids = list(dict.fromkeys(artist_ids))[:_MAX_CLIQUE_ARTISTS]
        for i, a in enumerate(aids):
            g.add_node(a, kind="artist")
            for b in aids[i + 1:]:
                if g.has_edge(a, b):
                    g[a][b]["weight"] += weight
                else:
                    g.add_edge(a, b, weight=weight, kind="cooccur")

    for pl in p.playlists:
        artists = _playlist_artist_ids(pl, p)
        if len(artists) >= 2:
            add_clique(artists, 1.0)

    liked_artists = [aid for t in p.liked_tracks for aid in t.artist_ids]
    if len(liked_artists) >= 2:
        add_clique(liked_artists, 3.0)

    # Soft cliques from top tracks (helps when playlists are empty).
    for group, w in [
        (p.top_tracks_short, 2.0),
        (p.top_tracks_medium, 1.0),
    ]:
        aids = [aid for t in group for aid in t.artist_ids]
        if len(aids) >= 2:
            add_clique(aids, w)

    return g


def _bridge_candidates(
    g: nx.Graph,
    p: TasteProfile,
    fp: TasteFingerprint,
    candidates: list[Artist],
    edges: list[tuple[str, str, float]],
) -> None:
    """Attach candidate artists into the user's co-listening graph.

    Priority:
      1. Explicit related-artist / search edges from the candidate pool
      2. Soft genre bridges to seed artists
    """
    seed_artists = list(p.top_artists_short[:8])
    # Ensure seeds exist as nodes even if they weren't in a playlist clique.
    for aid, _ in fp.top_artists[:15]:
        g.add_node(aid, kind="artist")

    for seed_id, cand_id, w in edges:
        g.add_node(seed_id, kind="artist")
        g.add_node(cand_id, kind="artist")
        if g.has_edge(seed_id, cand_id):
            g[seed_id][cand_id]["weight"] += w
        else:
            g.add_edge(seed_id, cand_id, weight=w, kind="related")

    bridged = {cand for _, cand, _ in edges}
    for a in candidates:
        g.add_node(a.id, kind="artist")
        if a.id in bridged or not a.genres:
            continue
        for seed in seed_artists:
            overlap = len(set(a.genres) & set(seed.genres))
            if overlap == 0:
                continue
            w = 0.25 * overlap
            if g.has_edge(seed.id, a.id):
                g[seed.id][a.id]["weight"] += w
            else:
                g.add_edge(seed.id, a.id, weight=w, kind="genre-bridge")


def _graph_raw_scores(
    p: TasteProfile,
    fp: TasteFingerprint,
    candidates: list[Artist],
    edges: list[tuple[str, str, float]],
) -> dict[str, float]:
    """Personalized PageRank mass on candidate artist nodes."""
    known_artists = {aid for aid, _ in fp.top_artists}
    new_artists = [a for a in candidates if a.id not in known_artists]
    if not new_artists:
        return {}

    g = build_artist_cooccurrence_graph(p)
    _bridge_candidates(g, p, fp, new_artists, edges)

    # Personalization: top + liked artists that exist in the graph.
    seed_ids = [aid for aid, _ in fp.top_artists[:10]]
    for t in p.liked_tracks[:10]:
        seed_ids.extend(t.artist_ids)
    seed_ids = [s for s in dict.fromkeys(seed_ids) if s in g]
    if not seed_ids:
        # Empty graph / no seeds — fall back to relatedness only.
        return _relatedness_scores(new_artists, edges)

    personalization = {node: 1.0 for node in seed_ids}
    total = sum(personalization.values())
    personalization = {k: v / total for k, v in personalization.items()}

    try:
        pr = nx.pagerank(
            g, alpha=0.85, personalization=personalization,
            weight="weight", max_iter=200,
        )
    except nx.PowerIterationFailedConvergence:
        pr = nx.pagerank(
            g, alpha=0.85, personalization=personalization, max_iter=200,
        )

    scores = {a.id: float(pr.get(a.id, 0.0)) for a in new_artists}
    # If PageRank couldn't distinguish (all ~equal), fold in relatedness.
    vals = list(scores.values())
    if vals and (max(vals) - min(vals)) < 1e-12:
        rel = _relatedness_scores(new_artists, edges)
        for aid in scores:
            scores[aid] = rel.get(aid, 0.0)
    return scores


# ─────────────────────────────────────────────────────────────────────
# Algorithm 1: genre sampling (hybrid-aware)
# ─────────────────────────────────────────────────────────────────────

def recommend_by_genre(p: TasteProfile, fp: TasteFingerprint,
                       candidates: list[Artist] | None = None,
                       n: int = 8,
                       exclude: set[str] | None = None,
                       hybrid: dict[str, float] | None = None,
                       raw_scores: dict[str, float] | None = None,
                       include: set[str] | None = None,
                       ) -> list[RecommendedTrack]:
    """Pick artists whose genre tags overlap your top genres."""
    candidates = candidates if candidates is not None else get_candidate_pool(p, fp)
    top_genres = {g for g, _ in fp.top_genres[:8]}
    known_artists = {aid for aid, _ in fp.top_artists}
    exclude = exclude or set()

    raw = raw_scores if raw_scores is not None else _genre_raw_scores(fp, candidates)
    pool = include if include is not None else set(raw)
    ranked_ids = sorted(
        (aid for aid in pool
         if aid not in exclude and aid not in known_artists),
        key=lambda aid: -(hybrid or raw).get(aid, raw.get(aid, 0.0)),
    )
    by_id = {a.id: a for a in candidates}
    out: list[RecommendedTrack] = []
    for aid in ranked_ids:
        if len(out) >= n:
            break
        a = by_id.get(aid)
        if a is None:
            continue
        if top_genres and set(a.genres) & top_genres:
            reason = (
                f"matches your top genres "
                f"({', '.join(sorted(set(a.genres) & top_genres))})"
            )
        elif a.genres:
            reason = f"related to your top artists ({', '.join(a.genres[:3])})"
        else:
            reason = "popular in your scene"
        track = pick_track_for_artist(a, p)
        out.append(RecommendedTrack(
            track_name=track.name,
            artist_name=a.name,
            artist_id=a.id,
            uri=track.uri,
            reason=reason,
            source="genre",
        ))
    return out


# ─────────────────────────────────────────────────────────────────────
# Algorithm 2: artist co-occurrence + Personalized PageRank
# ─────────────────────────────────────────────────────────────────────

# Back-compat alias used by older docs / imports.
build_cooccurrence_graph = build_artist_cooccurrence_graph


def _has_graph_mass(
    aid: str,
    graph_raw: dict[str, float],
    related_raw: dict[str, float],
) -> bool:
    return (
        graph_raw.get(aid, 0.0) > _GRAPH_PR_EPS
        or related_raw.get(aid, 0.0) > 0.0
    )


def _graph_reason(
    aid: str,
    graph_raw: dict[str, float],
    related_raw: dict[str, float],
) -> str:
    if graph_raw.get(aid, 0.0) > _GRAPH_PR_EPS:
        return "shares listeners with artists you love"
    if related_raw.get(aid, 0.0) > 0.0:
        return "related to artists you love"
    return "related to artists you love"


def recommend_by_graph(p: TasteProfile, fp: TasteFingerprint,
                       candidates: list[Artist] | None = None,
                       n: int = 8,
                       exclude: set[str] | None = None,
                       hybrid: dict[str, float] | None = None,
                       raw_scores: dict[str, float] | None = None,
                       related_scores: dict[str, float] | None = None,
                       include: set[str] | None = None,
                       ) -> list[RecommendedTrack]:
    """Personalized PageRank over an artist co-occurrence graph."""
    candidates = candidates if candidates is not None else get_candidate_pool(p, fp)
    known_artists = {aid for aid, _ in fp.top_artists}
    exclude = exclude or set()

    if raw_scores is None or related_scores is None:
        edges = get_related_edges(p)
        if related_scores is None:
            related_scores = _relatedness_scores(candidates, edges)
        if raw_scores is None:
            raw_scores = _graph_raw_scores(p, fp, candidates, edges)
    raw = raw_scores
    related = related_scores

    def _ok(aid: str) -> bool:
        return (
            aid not in exclude
            and aid not in known_artists
            and (include is None or aid in include)
        )

    score_of = hybrid or raw
    pool = include if include is not None else set(raw)
    with_mass = [aid for aid in pool if _ok(aid) and _has_graph_mass(aid, raw, related)]
    ranked_ids = sorted(with_mass, key=lambda aid: -score_of.get(aid, 0.0))
    # Only surface zero-mass graph candidates once real graph/related pool is exhausted.
    if len(ranked_ids) < n:
        zeros = [
            aid for aid in pool
            if _ok(aid) and not _has_graph_mass(aid, raw, related)
        ]
        ranked_ids.extend(
            sorted(zeros, key=lambda aid: -score_of.get(aid, 0.0))
        )

    by_id = {a.id: a for a in candidates}
    out: list[RecommendedTrack] = []
    for aid in ranked_ids:
        if len(out) >= n:
            break
        a = by_id.get(aid)
        if a is None:
            continue
        track = pick_track_for_artist(a, p)
        out.append(RecommendedTrack(
            track_name=track.name,
            artist_name=a.name,
            artist_id=a.id,
            uri=track.uri,
            reason=_graph_reason(aid, raw, related),
            source="graph",
        ))
    return out


# ─────────────────────────────────────────────────────────────────────
# Top-level: hybrid scores + cross-list diversity
# ─────────────────────────────────────────────────────────────────────

@dataclass
class Recommendations:
    taste: TasteFingerprint
    genre_recs: list[RecommendedTrack]
    graph_recs: list[RecommendedTrack]
    # Optional debug / eval payload
    component_scores: dict[str, dict[str, float]] = field(default_factory=dict)


def _dedupe(recs: list[RecommendedTrack]) -> list[RecommendedTrack]:
    out: list[RecommendedTrack] = []
    seen: set[str] = set()
    for r in recs:
        if r.artist_id in seen:
            continue
        seen.add(r.artist_id)
        out.append(r)
    return out


def _hybrid_maps(
    genre_raw: dict[str, float],
    related_raw: dict[str, float],
    graph_raw: dict[str, float],
    candidate_ids: set[str],
) -> tuple[dict[str, float], dict[str, float]]:
    """Build genre-weighted and graph-weighted hybrid scores."""
    g_n = _normalize({k: genre_raw.get(k, 0.0) for k in candidate_ids})
    r_n = _normalize({k: related_raw.get(k, 0.0) for k in candidate_ids})
    p_n = _normalize({k: graph_raw.get(k, 0.0) for k in candidate_ids})

    genre_hybrid = {
        k: 0.70 * g_n.get(k, 0.0) + 0.20 * r_n.get(k, 0.0) + 0.10 * p_n.get(k, 0.0)
        for k in candidate_ids
    }
    graph_hybrid = {
        k: 0.10 * g_n.get(k, 0.0) + 0.30 * r_n.get(k, 0.0) + 0.60 * p_n.get(k, 0.0)
        for k in candidate_ids
    }
    return genre_hybrid, graph_hybrid


def _assign_cross_list(
    candidate_ids: list[str],
    genre_hybrid: dict[str, float],
    graph_hybrid: dict[str, float],
    genre_raw: dict[str, float],
    graph_raw: dict[str, float],
    related_raw: dict[str, float],
    n_each: int,
) -> tuple[set[str], set[str]]:
    """Assign each artist to at most one list by hybrid advantage.

    Prefer the list where genre_hybrid - graph_hybrid is more favorable for
    that list, while keeping the graph column honest (real PR/related mass
    first). Top up short lists from leftovers so neither side goes empty
    when the pool allows.
    """
    ranked = sorted(
        candidate_ids,
        key=lambda aid: -max(
            genre_hybrid.get(aid, 0.0), graph_hybrid.get(aid, 0.0)
        ),
    )
    genre_ids: list[str] = []
    graph_ids: list[str] = []
    deferred: list[str] = []

    for aid in ranked:
        gh = genre_hybrid.get(aid, 0.0)
        ph = graph_hybrid.get(aid, 0.0)
        has_genre = genre_raw.get(aid, 0.0) > 0.0
        has_graph = _has_graph_mass(aid, graph_raw, related_raw)
        prefer_genre = (gh - ph) > 0.0

        if prefer_genre:
            if len(genre_ids) < n_each:
                genre_ids.append(aid)
            elif has_graph and len(graph_ids) < n_each:
                graph_ids.append(aid)
            else:
                deferred.append(aid)
        elif has_graph:
            if len(graph_ids) < n_each:
                graph_ids.append(aid)
            elif has_genre and len(genre_ids) < n_each:
                genre_ids.append(aid)
            else:
                deferred.append(aid)
        elif has_genre and len(genre_ids) < n_each:
            # Graph-preferring but no graph mass — keep genre list honest.
            genre_ids.append(aid)
        else:
            deferred.append(aid)

    for aid in deferred:
        if len(genre_ids) >= n_each and len(graph_ids) >= n_each:
            break
        gh = genre_hybrid.get(aid, 0.0)
        ph = graph_hybrid.get(aid, 0.0)
        has_genre = genre_raw.get(aid, 0.0) > 0.0
        has_graph = _has_graph_mass(aid, graph_raw, related_raw)
        need_genre = n_each - len(genre_ids)
        need_graph = n_each - len(graph_ids)

        if need_genre > 0 and need_graph > 0:
            if has_graph and (ph > gh or not has_genre):
                graph_ids.append(aid)
            elif has_genre:
                genre_ids.append(aid)
            elif has_graph:
                graph_ids.append(aid)
            elif gh >= ph:
                genre_ids.append(aid)
            else:
                graph_ids.append(aid)
        elif need_graph > 0:
            graph_ids.append(aid)
        elif need_genre > 0:
            genre_ids.append(aid)

    return set(genre_ids), set(graph_ids)


def recommend(p: TasteProfile, n_each: int = 8) -> Recommendations:
    fp = analyze_taste(p)
    candidates = get_candidate_pool(p, fp)
    edges = get_related_edges(p)

    # Score once per recommend() — list builders reuse these dicts.
    genre_raw = _genre_raw_scores(fp, candidates)
    related_raw = _relatedness_scores(candidates, edges)
    graph_raw = _graph_raw_scores(p, fp, candidates, edges)
    known_artists = {aid for aid, _ in fp.top_artists}
    cand_ids = {a.id for a in candidates}
    genre_hybrid, graph_hybrid = _hybrid_maps(
        genre_raw, related_raw, graph_raw, cand_ids
    )

    assignable = [aid for aid in cand_ids if aid not in known_artists]
    genre_set, graph_set = _assign_cross_list(
        assignable, genre_hybrid, graph_hybrid,
        genre_raw, graph_raw, related_raw, n_each,
    )

    genre_recs = _dedupe(recommend_by_genre(
        p, fp, candidates, n=n_each,
        hybrid=genre_hybrid, raw_scores=genre_raw, include=genre_set,
    ))
    graph_recs = _dedupe(recommend_by_graph(
        p, fp, candidates, n=n_each,
        hybrid=graph_hybrid, raw_scores=graph_raw,
        related_scores=related_raw, include=graph_set,
    ))

    # Top up short lists without recomputing PageRank.
    taken = {r.artist_id for r in genre_recs} | {r.artist_id for r in graph_recs}
    if len(genre_recs) < n_each:
        extra = recommend_by_genre(
            p, fp, candidates, n=n_each - len(genre_recs),
            exclude=taken, hybrid=genre_hybrid, raw_scores=genre_raw,
        )
        genre_recs = _dedupe(genre_recs + extra)[:n_each]
        taken = {r.artist_id for r in genre_recs} | {r.artist_id for r in graph_recs}
    if len(graph_recs) < n_each:
        extra = recommend_by_graph(
            p, fp, candidates, n=n_each - len(graph_recs),
            exclude=taken, hybrid=graph_hybrid, raw_scores=graph_raw,
            related_scores=related_raw,
        )
        graph_recs = _dedupe(graph_recs + extra)[:n_each]

    return Recommendations(
        taste=fp,
        genre_recs=genre_recs,
        graph_recs=graph_recs,
        component_scores={
            "genre": genre_raw,
            "related": related_raw,
            "graph": graph_raw,
        },
    )
