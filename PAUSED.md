# Spotify Recommender — status

**Resumed / updated:** 2026-07-30 (see `RESUME.md`).
**Status:** stub pipeline validated. Live mode still needs Spotify User
Management + OAuth (`python bin/check_live_data.py`).

## What's in this project
- `app.py` (222 lines) — FastAPI server, 5 endpoints.
- `spotify_client.py` (426 lines) — Live + stub modes, 24h cache, dev-mode detection.
- `recommender.py` (633 lines) — Taste fingerprint, genre sampling, PageRank.
- `templates/index.html` + `auth.html` + `static/style.css` — Spotify-ish dark UI.
- `data/spotify.json` — Last raw pull (still on disk, 24h TTL means it expired 2026-06-30 16:25 CDT).
- `spec-spotify-rec-v1.md` lives in `~/.openclaw/workspace/` (the source of truth for the spec).

## How to pick this back up
1. Read `RESUME.md` — it lists what's done, what's not, and gotchas.
2. `cd ~/Projects/spotify-rec && source .venv/bin/activate && uvicorn app:app`
3. Open `http://127.0.0.1:8000`. Stub mode is the default; flip to live with
   `SPOTIPY_CLIENT_ID=... uvicorn app:app` using the credentials in `.env`.

## To delete safely (if you decide not to pick it up)
- The `data/spotify.json` cache contains your real Liked Songs. If you want
  to remove them, `rm ~/Projects/spotify-rec/data/spotify.json` before deleting
  the directory.
- The `.venv` is 100+ MB; `rm -rf ~/Projects/spotify-rec` clears everything.
