"""
app.py — FastAPI server for Spotify Recommender.

Endpoints:
  GET  /           — render the rec page (or auth prompt if not connected)
  GET  /login      — redirect to Spotify OAuth (only used in live mode)
  GET  /callback   — OAuth callback, exchanges code for token
  POST /refresh    — bust cache and rebuild recs
  GET  /healthz    — sanity check
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from recommender import clear_caches, recommend
from spotify_client import (
    SpotifyClient,
    TOKEN_PATH,
    fetch_profile,
    is_stub_mode,
    last_refresh_error,
    load_cached_profile,
    profile_data_quality,
)

# Load .env from the project root (next to app.py) OR from ~/.env.
ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")              # project-local first
load_dotenv(Path.home() / ".env")        # fall back to user-level

app = FastAPI(title="Spotify Recommender", version="0.1.0")

# Static + templates
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
templates = Jinja2Templates(directory=str(ROOT / "templates"))


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────

def _has_live_token() -> bool:
    return TOKEN_PATH.exists() and not is_stub_mode()


def _spotify_client() -> SpotifyClient:
    return SpotifyClient(
        client_id=os.environ["SPOTIPY_CLIENT_ID"],
        client_secret=os.environ["SPOTIPY_CLIENT_SECRET"],
        redirect_uri=os.environ["SPOTIPY_REDIRECT_URI"],
    )


def _recs_to_context(recs) -> dict:
    """Flatten Recommendations into a JSON-serializable dict for the template."""
    return {
        "taste": {
            "top_genres": recs.taste.top_genres[:8],
            "n_liked": recs.taste.n_liked,
            "n_playlists": recs.taste.n_playlists,
            "n_tracks_total": recs.taste.n_tracks_total,
        },
        "genre_recs": [
            {
                "track_name": r.track_name,
                "artist_name": r.artist_name,
                "uri": r.uri,
                "reason": r.reason,
                "web_url": _spotify_web_url(r.uri),
            }
            for r in recs.genre_recs
        ],
        "graph_recs": [
            {
                "track_name": r.track_name,
                "artist_name": r.artist_name,
                "uri": r.uri,
                "reason": r.reason,
                "web_url": _spotify_web_url(r.uri),
            }
            for r in recs.graph_recs
        ],
    }


def _spotify_web_url(uri: str) -> str:
    """Convert spotify:track:xxx → https://open.spotify.com/track/xxx.
    Stub URIs (starting with rec_) still get a sensible URL."""
    if not uri.startswith("spotify:"):
        return "#"
    parts = uri.split(":")
    if len(parts) != 3:
        return "#"
    return f"https://open.spotify.com/{parts[1]}/{parts[2]}"


def _dev_mode_limited(profile) -> bool:
    """Same detector for `/` and `/healthz` (live mode only)."""
    if profile is None or is_stub_mode():
        return False
    return bool(profile_data_quality(profile).get("dev_mode_limited"))


def _wants_json(request: Request) -> bool:
    """True for API clients; false for normal browser form posts."""
    if request.query_params.get("format", "").lower() == "json":
        return True
    accept = request.headers.get("accept", "")
    for part in accept.split(","):
        media = part.split(";")[0].strip().lower()
        if media == "application/json":
            return True
    return False


# ─────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────

@app.get("/healthz")
def healthz() -> dict:
    profile = load_cached_profile(force=True)
    quality = profile_data_quality(profile) if profile else None
    err = last_refresh_error()
    return {
        "ok": True,
        "stub_mode": is_stub_mode(),
        "has_token": _has_live_token(),
        "has_cached_profile": profile is not None,
        "dev_mode_limited": _dev_mode_limited(profile),
        "last_refresh_error": err,
        "data_quality": quality,
    }


@app.get("/me/diag")
def me_diag() -> dict:
    """Diagnostic: dump the raw /me and one /artists response so we can
    see exactly what fields Spotify is returning (vs. stripping)."""
    if is_stub_mode():
        return {"stub_mode": True}
    if not _has_live_token():
        return {"error": "no_token", "message": "Visit /login first"}
    client = _spotify_client()
    import requests as _rq
    tok = client.sp.auth_manager.get_access_token(as_dict=False)
    headers = {"Authorization": f"Bearer {tok}"}
    me_r = _rq.get("https://api.spotify.com/v1/me", headers=headers)
    artist_r = _rq.get("https://api.spotify.com/v1/artists/3TVXtAsR1Inumwj472S9r4",
                       headers=headers)
    top_a_r = _rq.get(
        "https://api.spotify.com/v1/me/top/artists?limit=1&time_range=short_term",
        headers=headers)
    return {
        "me_status": me_r.status_code,
        "me": me_r.json(),
        "artist_status": artist_r.status_code,
        "artist": (artist_r.json() if artist_r.status_code == 200
                   else artist_r.text[:200]),
        "top_artists_status": top_a_r.status_code,
        "top_artist_keys": (list(top_a_r.json().get("items", [{}])[0].keys())
                            if top_a_r.status_code == 200 else None),
    }


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    # Live mode without token → show auth prompt.
    if not is_stub_mode() and not _has_live_token():
        return templates.TemplateResponse(request, "auth.html", {
            "redirect_uri": os.environ.get("SPOTIPY_REDIRECT_URI", ""),
        })

    # Use cached profile if it's still fresh; only hit Spotify on /refresh.
    cached = load_cached_profile()
    if cached is None:
        profile = fetch_profile()
    else:
        profile = cached
    recs = recommend(profile, n_each=8)
    ctx = _recs_to_context(recs)
    ctx["display_name"] = profile.display_name
    ctx["stub_mode"] = is_stub_mode()
    # Same detector as /healthz (genres stripped + flat popularity).
    ctx["dev_mode_limited"] = _dev_mode_limited(profile)
    err = last_refresh_error()
    ctx["refresh_error"] = err
    return templates.TemplateResponse(request, "index.html", ctx)


@app.get("/login")
async def login() -> RedirectResponse:
    if is_stub_mode():
        raise HTTPException(400, "Stub mode: no login needed.")
    client = _spotify_client()
    auth_url = client.get_auth_url()
    return RedirectResponse(auth_url)


@app.get("/callback")
async def callback(code: str) -> RedirectResponse:
    if is_stub_mode():
        raise HTTPException(400, "Stub mode: no callback expected.")
    client = _spotify_client()
    client.exchange_code(code)
    return RedirectResponse("/")


@app.post("/refresh")
async def refresh(request: Request):
    """Force-rebuild the taste profile from Spotify (busts cache).

    Browser form posts redirect back to `/` so the page reloads with
    fresh recs. API clients can keep JSON via Accept: application/json
    or ?format=json.
    """
    clear_caches()
    profile = fetch_profile(force=True)
    if not _wants_json(request):
        return RedirectResponse("/", status_code=303)
    recs = recommend(profile, n_each=8)
    quality = profile_data_quality(profile)
    return {
        "ok": True,
        "n_genre_recs": len(recs.genre_recs),
        "n_graph_recs": len(recs.graph_recs),
        "refresh_error": last_refresh_error(),
        "data_quality": quality,
        "lists_overlap": sorted(
            {r.artist_id for r in recs.genre_recs}
            & {r.artist_id for r in recs.graph_recs}
        ),
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=True)