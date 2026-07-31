# Spotify Recommender

A local web app that recommends music based on your Spotify listening
profile. Pulls Liked Songs (most recent 20), your playlists, top artists,
top tracks, and recently played; surfaces two parallel lists of recs:
one based on genre overlap, one based on a co-occurrence graph run
through Personalized PageRank.

## Quick start (stub mode — no Spotify account needed)

```bash
cd ~/Projects/spotify-rec
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
SPOTIPY_CLIENT_ID= python app.py
```

Then visit http://127.0.0.1:8765 or whichever port uvicorn reports.
You'll see fake data modeled after someone who likes indie / dream-pop / shoegaze.
Use the `STUB MODE` badge to confirm you're in stub mode.

## Quick start (live mode — your real Spotify data)

### One-time setup (≈5 minutes)

1. Visit https://developer.spotify.com/dashboard
2. Click **Create app**
   - Name: anything you like (`Spotify Rec (local)`)
   - Description: one sentence is fine
   - Redirect URI: **`http://127.0.0.1:8000/callback`** (must be explicit
     loopback — Spotify rejects `localhost`)
   - APIs: check **Web API** only
3. Save. Copy **Client ID** and **Client Secret** from the app settings.
4. **Important:** Open your app → **User Management** → enter your own
   Spotify email → **Add user**. Without this the API returns stripped
   payloads (empty genres, no popularity) because the app is in dev mode.
   The UI shows a warning banner if this is missing.

### Configure

```bash
cp .env.example .env
# Edit .env and paste in your Client ID and Client Secret.
```

### Run

```bash
python app.py
```

Visit http://127.0.0.1:8000. First time:
- Click **Connect Spotify** → log in → approve
- The app stores a refresh token at `data/token.json` and pulls your data

Press **↻ Refresh** any time to re-pull and recompute (busts the cache).

## What the recommendations are

### List 1 — Genre sampling

Your top 5 genres by weighted histogram across short/medium/long-term
top artists. We pull a candidate artist pool and rank them by genre
overlap with your taste axes, breaking ties by popularity.

### List 2 — Artist co-occurrence graph (PageRank)

Build an **artist–artist** graph where edges = "co-appear in the same
playlist / liked / top-tracks set" (Liked Songs weighted higher). Bridge
candidate artists in via related-artist and soft genre edges, then run
Personalized PageRank seeded from your top + liked artists.
New artists that sit near your listening neighborhood float to the top.

Both algorithms share a candidate pool. Scores are lightly hybridized
(`genre` / `relatedness` / `graph`) and the two lists are cross-deduped
so the same artist doesn't appear in both columns.

## Project layout

```
spotify-rec/
├── app.py              # FastAPI server
├── spotify_client.py   # Live + stub data fetching + data-quality helpers
├── recommender.py      # Taste analysis + genre + artist PageRank
├── bin/
│   ├── smoke_test.py       # stub pipeline smoke test
│   ├── check_live_data.py  # live User Management / playlist check
│   └── eval_holdout.py     # tiny offline holdout eval
├── templates/
│   ├── index.html      # main page
│   └── auth.html       # OAuth landing page
├── static/
│   └── style.css       # Spotify-ish dark theme
├── data/               # runtime: token.json, spotify.json (cache)
├── .env                # your secrets (gitignored)
└── requirements.txt
```

## Caching

- `data/spotify.json` — raw taste profile. TTL 24h by default; bust with
  the **↻ Refresh** button or `POST /refresh`.
- `data/token.json` — Spotify refresh token. Never expires unless revoked.
- `data/recs.json` — currently unused (recommender runs on each request;
  can cache if it gets slow).

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/` | Main page (recs + taste summary) |
| `GET` | `/login` | Redirect to Spotify OAuth (live mode only) |
| `GET` | `/callback` | OAuth callback |
| `POST` | `/refresh` | Force-rebuild cache + recompute recs |
| `GET` | `/healthz` | Sanity check (returns stub_mode + token status) |

## Limitations

- **No audio features.** Spotify's public audio-features and audio-analysis
  endpoints were retired in late 2024. We can't analyze the *sound* of your
  tracks (BPM, key, energy, etc.), only the metadata. Recs are therefore
  more about *who* you listen to than *what those tracks sound like*.
- **No explanations beyond a sentence.** LLM-generated explanations are
  explicitly out of scope for v1.
- **No "Save to playlist".** Spotify Web API requires write scope and an
  approval flow we haven't built.
- **No automatic refresh.** Cache invalidates by TTL or manual button click.
- **No mobile layout.** Tested at desktop widths only.

## Tech

- Python 3.14
- FastAPI + uvicorn
- spotipy 2.x (Spotify Web API wrapper)
- networkx (graph algorithms, including Personalized PageRank)
- Jinja2 (templates)
- python-dotenv (env config)

## Tests / diagnostics

```bash
# Stub smoke test (no Spotify needed):
SPOTIPY_CLIENT_ID= python bin/smoke_test.py

# Regression unit tests (stdlib unittest; no pytest required):
SPOTIPY_CLIENT_ID= python -m unittest tests.test_rec_fixes -v

# Offline holdout eval (pipeline sanity; stub hit-rate is expected ~0):
SPOTIPY_CLIENT_ID= python bin/eval_holdout.py

# Live data quality (requires .env + OAuth token + User Management):
python bin/check_live_data.py

# Server health check:
python app.py &  # in another shell
curl http://127.0.0.1:8000/healthz
# → ok, stub_mode, has_token, data_quality{genres_ok, playlists_ok, ...}
```