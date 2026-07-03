# Break the Loop — Application Documentation

## What Is It

**Break the Loop** is an AI-powered music discovery web application that deliberately steers users *away* from their existing listening habits. Where every mainstream recommender (Spotify's own Discover Weekly, YouTube suggestions, Last.fm) optimises *toward* your history — surfacing more of what you already like — Break the Loop does the opposite.

It reads your real Spotify listening data (or a built-in demo profile), then instructs a large language model to find tracks you would *not* have reached on your own. The result is a curated batch of five tracks each time you run it, with a brief AI-written explanation of why each pick is a step outside your usual loop.

---

## Core Concept

A conventional recommender answers: *"Given what you like, what else do you like?"*

Break the Loop asks: *"Given what you like, what have you been missing?"*

The LLM is given your taste profile as an explicit **avoid list** — not a seed for more-of-the-same, but a boundary to escape from. The steering prompt instructs the model to find music that is a reachable adjacent step from your taste, not a jarring leap, but genuinely outside the rut.

---

## User Flow

### 1. Home Screen

The app opens on a simulated Spotify-style home screen — dark theme, tile grid layout. Tiles are visible immediately; no login is required to see the app.

- **Demo path (recommended):** A prominent **"▶ Load sample listener (demo)"** button is available without any login. It loads a hardcoded indie-rock listener profile and immediately enables the discovery flow. This is the intended path for evaluators.
- **Spotify path:** A compact **"Connect Spotify"** chip in the top-right opens Spotify's OAuth consent screen. After granting access, the app reads the user's real library and returns to the home screen with live data.

The **"Break the loop"** tile is the only interactive tile. All others (Liked Songs, Discover Weekly, Daily Mix) are decorative placeholders reflecting the Spotify home aesthetic. The Break the Loop tile is enabled once a profile — demo or real — is loaded.

---

### 2. Discovery Page — Three-Step Guided Flow

Clicking the tile navigates to the discovery page. The flow is structured as three sequential steps, each step unlocking the next.

**Step 1 — Load a listener**

- If arriving from the demo button on home, this step is already marked complete with a green ✓.
- If not, the user can load the demo profile here or connect Spotify.
- On completion, shows: `✓ Loaded: Demo Listener` (or the Spotify display name).

**Step 2 — Tell it your vibe**

- Unlocks after Step 1.
- A free-text input box: *"What do you want right now?"*
- Three starter chips pre-fill the box with example intents:
  - `surprise me — not the mainstream`
  - `older gems in a genre I love`
  - `go deep into an artist I half-know`
- Once the user types or selects a vibe, the step collapses to `✓ Vibe set: <their text>` with an edit button.

**Step 3 — Break the loop**

- The **"Break my loop ↗"** button is enabled only when both Step 1 and Step 2 are complete.
- On click: the app calls Claude with the taste profile + intent, receives 8 candidate tracks, runs multiple filter passes, and displays the surviving 5.

---

### 3. Results

Each result row shows:
- **Album artwork** (640 × 640 px from Spotify catalog, or a ♪ placeholder if not resolved)
- **Track title** with a **"▶ Open in Spotify"** search link (no login or Premium required)
- **Artist name**
- **Why this pick** — one sentence from the model tying the recommendation to the user's profile
- **♡ Save to Liked Songs** button — visible only when Spotify is connected and the track resolved to a real catalog ID

A small caption below the results reports any filtering that occurred (e.g. *"2 artists removed — in your taste profile · 1 track skipped — already shown this session"*).

---

### 4. Steer Again

After results appear, four buttons at the bottom allow refinement:

| Button | Effect |
|---|---|
| **more obscure** | Appends `"— make these more obscure"` to the last intent and re-runs |
| **older** | Appends `"— make these older"` and re-runs |
| **go deeper** | Appends `"— go deeper"` and re-runs |
| **↺ Start fresh** | Clears the entire shown-tracks history, resets intent, returns to a clean slate |

Each steer re-calls the LLM and runs all filters. The session-level dedup list grows with every run, so the same track or artist is never repeated within a session. Clicking "Start fresh" wipes that memory and allows artists to reappear.

---

## File Structure

```
Spotify_Discovery_MVP/
├── app.py               — Entire Streamlit UI, state management, LLM call, result rendering
├── spotify_client.py    — All Spotify API interactions (OAuth, profile fetch, search, playlist)
├── secrets_util.py      — Loads API keys from st.secrets or .streamlit/secrets.toml
├── .streamlit/
│   ├── secrets.toml     — API keys (not committed to source control)
│   └── config.toml      — Streamlit layout config (wide mode, theme)
└── requirements.txt     — Python dependencies
```

---

## Technical Architecture

### Overview

Break the Loop is a **single-process, stateless-server Python application** built on Streamlit. There is no separate backend process, no database, and no persistent server-side storage. All application state lives in `st.session_state`, which is scoped to the browser session. Every user gets their own isolated state.

```
Browser (Streamlit frontend)
        │
        ▼
  app.py (Streamlit server — Python)
        │
        ├──▶  Anthropic API   (claude-sonnet-4-6)
        │          └── Steering prompt + taste profile → 8 track candidates (JSON)
        │
        └──▶  Spotify Web API  (via Spotipy)
                   ├── OAuth 2.0 PKCE flow
                   ├── User profile & listening history
                   └── Catalog search + optional playlist write
```

---

### Frontend

**Framework:** [Streamlit](https://streamlit.io) (Python)

Streamlit renders the entire UI from Python. There is no separate HTML/JS frontend. The app uses:

- `st.markdown(..., unsafe_allow_html=True)` — for all custom Spotify-like components (home tiles, playlist rows, header card). CSS is embedded inline in a single `<style>` block injected at startup.
- `st.button`, `st.text_input`, `st.columns`, `st.spinner`, `st.caption`, `st.toast`, `st.warning` — native Streamlit widgets for interactive elements.
- `st.session_state` — the entire application state machine (current page, Spotify token, taste profile, generated picks, shown-track history, demo mode flag).
- `st.query_params` — used to receive the Spotify OAuth callback code from the redirect URI.
- `st.rerun()` — triggers a full page re-render after any state change (button clicks, page navigation, OAuth completion).

**Layout pattern:** The app implements a manual single-page application (SPA) pattern using `st.session_state.page`. The routing block at the bottom of `app.py` calls either `render_home()` or `render_break_the_loop()` on every render cycle, based on the current page state.

---

### Backend / Logic Layer (`app.py`)

All logic runs inside the Streamlit process.

#### State variables (key `st.session_state` keys)

| Key | Type | Purpose |
|---|---|---|
| `page` | `str` | Current page: `"home"` or `"break_the_loop"` |
| `spotify_token` | `dict` | Spotify OAuth token (access + refresh) |
| `taste_profile` | `dict` | Fetched or demo taste profile |
| `demo_mode` | `bool` | Whether demo profile is active |
| `profile_loaded` | `bool` | Step 1 complete flag |
| `intent_val` | `str` | Current discovery intent text |
| `last_picks` | `list[dict]` | Most recent resolved track batch |
| `last_intent` | `str` | Intent used for the last run (base for steer-again) |
| `pick_filter_meta` | `dict` | Filter counters for the last run |
| `shown_tracks` | `set[tuple]` | All `(track, artist)` pairs shown this session |
| `resolve_debug` | `str` | Spotify match count message |
| `session_playlist_id` | `str` | Ephemeral Spotify playlist ID |
| `playing_embed` | `dict` | Which track/playlist is in the sticky player |
| `saved_tracks` | `set[str]` | Track IDs saved to Liked Songs this session |
| `steer_trigger` | `str` | Intent set by steer-again buttons; auto-fires discovery |

#### Filtering pipeline (per run)

Claude returns up to 8 candidates. Before display, they pass through four sequential filters:

```
Claude response (8 picks)
    │
    ▼
1. _dedup_artist_within_batch()  — max 1 track per artist in this batch
    │
    ▼
2. filter_picks_by_exclusions()  — drop any whose "language" matches user's avoid phrases
    │
    ▼
3. _filter_shown_picks()         — drop any track/artist already shown this session
    │
    ▼
4. _filter_profile_artists()     — drop any artist that appears in the taste profile
    │
    ▼
Display first 5 survivors
```

---

### `spotify_client.py`

Encapsulates all Spotify API interactions. The main app imports named functions; the Spotify object (`sp`) is passed in where needed.

| Function | What it does |
|---|---|
| `build_oauth()` | Creates `SpotifyOAuth` with `SessionCacheHandler` (stores token in `st.session_state`, not disk) |
| `complete_auth()` | Exchanges the OAuth `code` query param for an access + refresh token |
| `get_client()` | Returns a `spotipy.Spotify` client if a valid token exists |
| `fetch_taste_profile()` | Calls four Spotify endpoints and assembles the taste summary dict |
| `resolve_picks()` | For each AI pick, searches the Spotify catalog and attaches `spotify_id`, `uri`, `album_art`, `duration_ms` |
| `search_track()` | Primary search: `track:"name" artist:"name"`. Fallback: free-text + artist name match |
| `sync_session_playlist()` | Creates one private playlist per session; replaces tracks on every steer instead of creating a new playlist |
| `teardown_session_playlist()` | Removes the session playlist from the user's library on logout |
| `save_track_to_library()` | Adds a single track to the user's Liked Songs |
| `find_break_loop_playlists()` | Finds legacy playlists from earlier sessions by name marker |
| `remove_break_loop_playlists()` | Bulk-deletes legacy playlists (cleanup utility) |

**Custom cache handler (`SessionCacheHandler`):** Spotipy by default writes tokens to a local `.cache` file. This implementation overrides that with `st.session_state`, which means tokens are browser-session-scoped, never written to disk, and automatically discarded when the session ends.

**Raw HTTP client (`_spotify_request`):** Some Spotify endpoints not covered by Spotipy's Python wrapper (e.g. `PUT /playlists/{id}/items` with exact semantics, `DELETE /playlists/{id}/followers`) are called directly with `requests` using a manually assembled Bearer token header.

---

### Anthropic Claude API

**Model used:** `claude-sonnet-4-6`

**What it is used for:** Generating the discovery batch. Claude is the only component that decides *which tracks to recommend*. It is not used for any other feature.

**How the prompt is structured (`STEER_PROMPT`):**

The prompt is a structured template with six injected sections:

```
[1] CONSTRAINT EXTRACTION        ← tells model to find avoid/no/not/without phrases
[2] {exclusions_block}           ← parsed exclusion list (Python-extracted from intent)
[3] {shown_block}                ← tracks/artists already shown this session
[4] --- discovery engine preamble ---
[5] USER'S TASTE PROFILE         ← {taste} — free-text summary from Spotify data
    TRACKS THEY KNOW             ← {avoid} — top artists + top tracks
    {taste_artist_block}         ← explicit ban list of profile artists + regional guidance
[6] INTENT                       ← {intent} — user's typed or chip-selected vibe
    TASK + ADJACENCY rules
    HARD RULES (7 explicit constraints)
    FINAL SELF-CHECK (3-point verification before responding)
```

**Output format:** Claude is instructed to return only valid JSON — no prose, no markdown fences. The schema is:

```json
{
  "picks": [
    {
      "track": "exact Spotify title",
      "artist": "primary artist name",
      "language": "English | Hindi | Tamil | ...",
      "tag": "new | older | deep | edge",
      "newness": "short phrase describing novelty",
      "reason": "one sentence tying this pick to the user's profile"
    }
  ]
}
```

The `language` field is required for the Python-side language exclusion filter. The `reason` field is displayed inline under each track in the UI.

**Token budget:** `max_tokens=2000`, which comfortably fits 8 detailed picks in JSON.

---

## Spotify OAuth Scopes

| Scope | Why it is required |
|---|---|
| `user-top-read` | Fetch top artists and top tracks (6-month window) for the taste profile |
| `user-library-read` | Fetch saved/liked tracks for library-lean signal |
| `user-read-recently-played` | Fetch recent play history for the taste profile |
| `playlist-read-private` | Read user's playlists (for legacy cleanup check) |
| `playlist-modify-private` | Create and update the ephemeral session playlist |
| `playlist-modify-public` | Fallback write scope (some accounts require this instead of private) |
| `user-library-modify` | Save a track to Liked Songs via the ♡ button |

---

## Privacy and Data Handling

- **No data is persisted.** All Spotify data lives only in `st.session_state` for the duration of the browser session.
- **The Spotify token is never written to disk.** `SessionCacheHandler` keeps it in memory only.
- **The session playlist is ephemeral.** It is deleted from the user's Spotify library when they click "Disconnect Spotify."
- **Nothing is sent to Anthropic except the taste summary and intent.** No user identity, email, or Spotify user ID is included in the Claude prompt.
- **Demo mode requires zero credentials.** Evaluators can run the full discovery flow using the hardcoded demo profile without providing any login.

---

## Setup and Running

### Prerequisites

```
Python 3.11+
Spotify Developer App (create at https://developer.spotify.com/dashboard)
Anthropic API key
```

### Install

```bash
pip install -r requirements.txt
```

### Configure secrets

Create `.streamlit/secrets.toml`:

```toml
ANTHROPIC_API_KEY = "sk-ant-..."
SPOTIFY_CLIENT_ID = "your_client_id"
SPOTIFY_CLIENT_SECRET = "your_client_secret"
SPOTIFY_REDIRECT_URI = "http://127.0.0.1:8501"
```

In the Spotify Developer Dashboard, add `http://127.0.0.1:8501` as an allowed Redirect URI (and your Streamlit Cloud URL when deploying).

### Run

```bash
streamlit run app.py
```

The app opens at `http://127.0.0.1:8501`. For the demo path, no Spotify credentials are needed — click "▶ Load sample listener (demo)" on the home screen.
