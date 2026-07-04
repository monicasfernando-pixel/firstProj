"""
app.py — "Break the Loop" discovery MVP
---------------------------------------
Ephemeral discovery: reads your Spotify taste, steers Claude AGAINST your usual,
matches tracks in-session — nothing is saved to your library.

Setup (Spotify Developer Dashboard → create app):
  - Redirect URI: http://127.0.0.1:8501  (and your Streamlit Cloud URL when deployed)
  - Secrets: ANTHROPIC_API_KEY, SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET
  - Optional: SPOTIFY_REDIRECT_URI (defaults to http://127.0.0.1:8501)

Run: streamlit run app.py
"""

from __future__ import annotations

import base64
import html
import importlib
import json
import re
import urllib.parse
from pathlib import Path

import streamlit as st
from spotipy.exceptions import SpotifyException

import spotify_client as _spotify_client
importlib.reload(_spotify_client)

from secrets_util import has_spotify_credentials, load_app_secrets
from spotify_client import (
    build_oauth,
    complete_auth,
    fetch_taste_profile,
    find_break_loop_playlists,
    get_client,
    remove_break_loop_playlists,
    resolve_picks,
    save_track_to_library,
    sync_session_playlist,
    teardown_session_playlist,
)

# ── Broken-loop icon (inline SVG — swap body of BREAK_LOOP_SVG with your artwork) ──
# The SVG below is a placeholder circular-arrow-with-break in Spotify green.
# Paste your own SVG between the triple-quotes to replace it.
BREAK_LOOP_SVG = """<svg width="64" height="64" viewBox="0 0 64 64" fill="none" xmlns="http://www.w3.org/2000/svg">
  <!-- Circular loop arc: 320° clockwise, center(32,32), r=22 -->
  <!-- Start top (32,10) → clockwise → end upper-left (18,15) leaving a ~40° gap -->
  <path d="M32 10 A22 22 0 1 1 18 15"
        stroke="#1ed760" stroke-width="4.5" stroke-linecap="round" fill="none"/>
  <!-- Arrowhead at (18,15) pointing in clockwise travel direction -->
  <path d="M26 13 L18 15 L22 7"
        stroke="#1ed760" stroke-width="4.5" stroke-linecap="round" stroke-linejoin="round" fill="none"/>
  <!-- AI sparkle — 4-pointed star upper-right, white so visible on any bg -->
  <path d="M50 10 L51.3 14.2 L55.5 15.5 L51.3 16.8 L50 21 L48.7 16.8 L44.5 15.5 L48.7 14.2 Z"
        fill="#ffffff" opacity="0.92"/>
  <!-- Small accent dot for depth -->
  <circle cx="43" cy="8" r="2" fill="#1ed760" opacity="0.7"/>
</svg>"""

_favicon_path = Path(__file__).parent / "assets" / "BreakloopIcon.png"

# Use the PNG for page_icon if available (PIL required by Streamlit for image icons)
try:
    from PIL import Image as _PILImage
    _page_icon = _PILImage.open(_favicon_path) if _favicon_path.exists() else "↺"
except Exception:
    _page_icon = "↺"

st.set_page_config(
    page_title="Break the Loop",
    page_icon=_page_icon,
    layout="wide",
    initial_sidebar_state="collapsed",
)

# Belt-and-suspenders: also inject <link rel="icon"> so the tab picks it up
# even if PIL wasn't available above.
if _favicon_path.exists():
    _favicon_b64 = base64.b64encode(_favicon_path.read_bytes()).decode()
    st.markdown(
        f'<link rel="icon" type="image/png" href="data:image/png;base64,{_favicon_b64}" />'
        f'<link rel="shortcut icon" type="image/png" href="data:image/png;base64,{_favicon_b64}" />',
        unsafe_allow_html=True,
    )

# ---------------------------------------------------------------- Spotify UI theme
st.markdown(
    """
<style>
  @import url('https://fonts.googleapis.com/css2?family=Circular+Std:wght@400;500;700&display=swap');
  .stApp { background: #121212; }
  .block-container { max-width: none; padding: 1.5rem 2.5rem 110px !important; }
  header[data-testid="stHeader"] { background: transparent; }
  .hero { padding: 1.5rem 0 1rem; }
  .hero h1 { font-size: 3rem; font-weight: 700; margin: 0; letter-spacing: -0.04em; }
  .hero p { color: #b3b3b3; font-size: 1rem; margin: 0.5rem 0 0; max-width: 560px; }
  .chip-row { display: flex; flex-wrap: wrap; gap: 8px; margin: 12px 0; }
  .chip {
    background: rgba(255,255,255,0.08); color: #fff; padding: 6px 12px;
    border-radius: 999px; font-size: 13px;
  }
  .taste-box {
    background: #181818; border-radius: 12px; padding: 16px 18px;
    border: 1px solid rgba(255,255,255,0.06); color: #b3b3b3; font-size: 14px;
    line-height: 1.5; white-space: pre-wrap;
  }
  .playlist-card {
    background: linear-gradient(135deg, #1ed760 0%, #169c46 100%);
    border-radius: 12px; padding: 24px; margin: 1rem 0;
    display: flex; align-items: flex-end; gap: 20px; min-height: 140px;
  }
  .playlist-card .cover {
    width: 132px; height: 132px; background: #121212; border-radius: 8px;
    display: flex; align-items: center; justify-content: center;
    font-size: 48px; box-shadow: 0 8px 24px rgba(0,0,0,0.4); flex-shrink: 0;
  }
  .playlist-card .meta h2 { margin: 0; font-size: 2rem; font-weight: 700; color: #fff; }
  .playlist-card .meta .sub { color: rgba(255,255,255,0.85); font-size: 14px; margin-top: 6px; }
  .spotify-playlist-panel {
    margin: 1rem 0 0;
    border-radius: 8px;
    overflow: hidden;
    background: #121212;
    border: 1px solid rgba(255,255,255,0.06);
  }
  .pl-toolbar {
    display: flex; align-items: center; justify-content: space-between;
    padding: 16px 16px 8px; gap: 12px;
  }
  .pl-toolbar .label {
    color: #b3b3b3; font-size: 12px; font-weight: 600;
    text-transform: uppercase; letter-spacing: 0.06em;
  }
  .pl-row-title {
    display: flex; align-items: center; gap: 12px; min-width: 0;
  }
  .pl-row-art {
    width: 40px; height: 40px; border-radius: 4px; object-fit: cover;
    background: #282828; flex-shrink: 0;
  }
  .pl-row-meta { min-width: 0; }
  .pl-row-track {
    color: #fff; font-size: 15px; font-weight: 500; line-height: 1.3;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .pl-row-artist {
    color: #b3b3b3; font-size: 13px; margin-top: 2px;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .pl-row-reason {
    color: #727272; font-size: 12px; margin-top: 5px; line-height: 1.45;
    white-space: normal;
  }
  .open-spotify-link {
    color: #1ed760; font-size: 12px; font-weight: 600;
    text-decoration: none; white-space: nowrap; opacity: 0.85;
  }
  .open-spotify-link:hover { opacity: 1; text-decoration: underline; }
  .sticky-player {
    position: fixed; bottom: 0; left: 0; right: 0; z-index: 999;
    background: #181818; border-top: 1px solid #282828;
    box-shadow: 0 -4px 24px rgba(0,0,0,0.5);
  }
  .sticky-player-inner {
    max-width: none; margin: 0 auto; padding: 0 2.5rem;
  }
  .sticky-player iframe {
    display: block; border: none; width: 100%; height: 88px;
  }
  div[data-testid="column"] button {
    min-height: 2rem; padding: 0.15rem 0.4rem;
  }
  .connect-banner {
    background: #181818; border: 1px solid rgba(30,215,96,0.25);
    border-radius: 12px; padding: 20px; margin-bottom: 1rem;
  }
  div.stButton > button[kind="primary"] {
    background: #1ed760; color: #121212; border: none; font-weight: 700;
    border-radius: 999px; padding: 0.5rem 1.75rem;
  }
  div.stButton > button[kind="primary"]:hover {
    background: #1fdf64; color: #121212;
  }
  div.stButton > button[kind="secondary"] {
    background: transparent; color: #fff; border: 1px solid rgba(255,255,255,0.3);
    border-radius: 999px;
  }
  .home-shell { max-width: 1100px; margin: 0 auto; }
  .home-muted .home-tile.dummy { opacity: 0.35; }
  .home-connect-chip {
    display: inline-flex; align-items: center; gap: 8px;
    background: rgba(30,215,96,0.12); border: 1px solid rgba(30,215,96,0.3);
    border-radius: 999px; padding: 5px 14px 5px 10px;
    color: #1ed760; font-size: 12px; font-weight: 600; white-space: nowrap;
  }
  .home-connect-chip a {
    color: #1ed760 !important; text-decoration: none;
  }
  .home-tile {
    display: flex; align-items: center; gap: 14px;
    background: rgba(255,255,255,0.08); border-radius: 6px;
    padding: 12px 14px; min-height: 72px; overflow: hidden;
    transition: background 0.15s ease;
  }
  .home-tile.dummy { cursor: default; opacity: 0.85; }
  .home-tile:not(.dummy):hover { background: rgba(255,255,255,0.14); }
  .home-tile-icon {
    width: 56px; height: 56px; border-radius: 5px; flex-shrink: 0;
    display: flex; align-items: center; justify-content: center;
    font-size: 28px; overflow: hidden;
  }
  .home-tile-icon img { width: 100%; height: 100%; object-fit: cover; display: block; }
  .home-tile-label {
    color: #fff; font-size: 15px; font-weight: 700;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .st-key-tile_break_loop button {
    width: 100% !important; min-height: 72px !important; height: 72px !important;
    background-color: #1a2a1a !important;
    background-repeat: no-repeat !important;
    background-position: 10px center !important;
    background-size: 52px 52px !important;
    color: #fff !important; border: 1px solid rgba(30,215,96,0.4) !important;
    border-radius: 6px !important;
    text-align: left !important; padding: 0 14px 0 72px !important;
    font-weight: 700 !important; font-size: 15px !important;
    justify-content: flex-start !important; box-shadow: none !important;
  }
  .st-key-tile_break_loop button:hover:not(:disabled) {
    background-color: #1f3320 !important;
    border-color: #1ed760 !important;
    color: #fff !important;
  }
  .st-key-tile_break_loop button:disabled {
    opacity: 0.35 !important; cursor: not-allowed !important;
  }
  .demo-cta {
    background: linear-gradient(135deg, #1a3a2a 0%, #0d2218 100%);
    border: 1px solid rgba(30,215,96,0.4); border-radius: 10px;
    padding: 18px 22px; margin-bottom: 22px;
  }
  .demo-cta h3 { color: #1ed760; margin: 0 0 4px; font-size: 16px; font-weight: 700; }
  .demo-cta p { color: #b3b3b3; font-size: 13px; margin: 0; }
  .st-key-demo_load button {
    background: #1ed760 !important; color: #121212 !important;
    border: none !important; border-radius: 999px !important;
    font-weight: 700 !important; padding: 0.55rem 1.6rem !important;
    font-size: 14px !important;
  }
  .st-key-demo_load button:hover { background: #1fdf64 !important; }
  .spotify-home-header {
    display: flex; align-items: center; gap: 16px;
    padding: 12px 0 20px; flex-wrap: wrap;
  }
  .home-logo {
    color: #fff; font-size: 28px; font-weight: 700; letter-spacing: -0.04em;
    flex-shrink: 0;
  }
  .home-nav-btn {
    width: 40px; height: 40px; border-radius: 50%; background: #000;
    display: flex; align-items: center; justify-content: center;
    color: #b3b3b3; font-size: 18px; flex-shrink: 0;
  }
  .home-search {
    flex: 1; min-width: 200px; max-width: 420px;
    background: rgba(255,255,255,0.1); border-radius: 999px;
    padding: 10px 16px; color: #b3b3b3; font-size: 14px;
  }
  .home-user {
    margin-left: auto; display: flex; align-items: center; gap: 12px;
    color: #fff; font-size: 14px;
  }
  .home-avatar {
    width: 32px; height: 32px; border-radius: 50%;
    background: #1db954; color: #121212; font-weight: 700;
    display: flex; align-items: center; justify-content: center; font-size: 14px;
  }
  .home-pills { display: flex; gap: 8px; margin-bottom: 20px; flex-wrap: wrap; }
  .home-pill {
    padding: 6px 14px; border-radius: 999px; font-size: 13px; font-weight: 600;
    background: rgba(255,255,255,0.08); color: #fff;
  }
  .home-pill.active { background: #fff; color: #121212; }
  .tile-liked { background: linear-gradient(135deg, #450af5, #c4efd9); }
  .tile-discover { background: linear-gradient(135deg, #1e3264, #1ed760); }
  .tile-daily { background: linear-gradient(135deg, #ba5d07, #e91429); }
  .tile-release { background: linear-gradient(135deg, #148a08, #1ed760); }
  .tile-onrepeat { background: linear-gradient(135deg, #503750, #8c67ab); }
  .home-section-title {
    color: #fff; font-size: 1.4rem; font-weight: 700;
    margin: 28px 0 14px; letter-spacing: -0.02em;
  }
  .back-nav { margin-bottom: 1rem; }

  /* Visible border on text inputs */
  [data-testid="stTextInput"] input {
    border: 1.5px solid rgba(255,255,255,0.25) !important;
    border-radius: 6px !important;
    background: #282828 !important;
    color: #fff !important;
  }
  [data-testid="stTextInput"] input:focus {
    border-color: #1ed760 !important;
    outline: none !important;
    box-shadow: 0 0 0 2px rgba(30,215,96,0.18) !important;
  }
  [data-testid="stTextInput"] input::placeholder { color: #727272 !important; }
</style>
""",
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------- Demo taste profile
DEMO_TASTE_PROFILE = {
    "user": {"display_name": "Demo Listener"},
    "top_artists": [
        "Arctic Monkeys", "Tame Impala", "The Strokes", "Radiohead",
        "Vampire Weekend", "LCD Soundsystem", "Foals", "alt-J",
    ],
    "top_tracks": [
        "Do I Wanna Know? – Arctic Monkeys",
        "The Less I Know the Better – Tame Impala",
        "Last Nite – The Strokes",
        "Creep – Radiohead",
        "Oxford Comma – Vampire Weekend",
        "I Wanted Your Sex – Foals",
        "Fitzpleasure – alt-J",
        "All My Friends – LCD Soundsystem",
    ],
    "summary": (
        "Indie rock / art rock core: Arctic Monkeys, The Strokes, Radiohead, Foals. "
        "Psychedelic pop via Tame Impala. Danceable post-punk from LCD Soundsystem. "
        "Cerebral indie from Vampire Weekend and alt-J. "
        "Mostly British and American guitar-led acts from the 2000s–2010s. "
        "Rarely ventures outside English-language indie/rock."
    ),
}

TAG_CLASS = {"new": "pill-new", "older": "pill-old", "deep": "pill-deep", "edge": "pill-edge"}
TAG_LABEL = {"new": "New", "older": "Older gem", "deep": "Deep cut", "edge": "Edge pick"}

# Heuristic scan for explicit exclusions in user intent (best-effort; LLM re-checks too).
_EXCLUSION_NOT_SKIP = re.compile(
    r"\b(my usual|the mainstream|obvious|chart hits|famous stuff|same old)\b", re.I
)
_EXCLUSION_PATTERNS: list[tuple[re.Pattern[str], re.Pattern[str] | None]] = [
    (re.compile(r"\bavoid(?:ing)?\s+([^,.;]+)", re.I), None),
    (re.compile(r"\b(?:don'?t|do not)\s+want\s+([^,.;]+)", re.I), None),
    (re.compile(r"\bwithout\s+([^,.;]+)", re.I), None),
    (re.compile(r"\bexcluding?\s+([^,.;]+)", re.I), None),
    (
        re.compile(
            r"\bno\s+([^,.;]+?)(?:\s+(?:music|songs|tracks|artists|language|lang|genre))?\b",
            re.I,
        ),
        None,
    ),
    (
        re.compile(
            r"\bnot\s+([^,.;]+?)\s+(?:music|songs|tracks|artists|language|lang|genre)\b",
            re.I,
        ),
        _EXCLUSION_NOT_SKIP,
    ),
    (re.compile(r"\bexcept\s+(?!for\b)([^,.;]+)", re.I), None),
]
_EXCLUSION_TRIM = re.compile(r"\s+(?:please|thanks|at all|whatsoever|anything).*$", re.I)


def extract_excluded_languages(intent: str) -> list[str]:
    """Parse excluded languages/terms from avoid / no / not / without / except phrases."""
    if not intent.strip():
        return []

    found: list[str] = []
    seen: set[str] = set()

    for pattern, skip_if in _EXCLUSION_PATTERNS:
        for match in pattern.finditer(intent):
            phrase = match.group(1).strip().strip("\"'")
            phrase = _EXCLUSION_TRIM.sub("", phrase).strip()
            if len(phrase) < 2 or len(phrase) > 80:
                continue
            if skip_if and skip_if.search(phrase):
                continue
            key = phrase.lower()
            if key not in seen:
                seen.add(key)
                found.append(phrase)

    return found


# Backward-compatible alias used by prompt formatting.
extract_exclusions = extract_excluded_languages

MODEL_PICK_COUNT = 8
DISPLAY_PICK_COUNT = 5


def _format_exclusions_block(exclusions: list[str]) -> str:
    if exclusions:
        items = "\n".join(f"  • {e}" for e in exclusions)
        return (
            "EXTRACTED EXCLUSIONS (from intent — treat as ABSOLUTE, NON-NEGOTIABLE):\n"
            f"{items}"
        )
    return (
        "EXTRACTED EXCLUSIONS: none matched by pattern scan — still read the intent below "
        "for implicit AVOID / NO / NOT / WITHOUT / EXCEPT / DON'T WANT constraints."
    )


_EXCLUSION_SUFFIX = re.compile(
    r"\s+(?:music|songs|tracks|artists|language|lang|genre)$", re.I
)


def _exclusion_match_terms(exclusions: list[str]) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    for exc in exclusions:
        for candidate in (exc, _EXCLUSION_SUFFIX.sub("", exc)):
            key = candidate.lower().strip()
            if key and key not in seen:
                seen.add(key)
                terms.append(key)
    return terms


def filter_picks_by_exclusions(picks: list[dict], excluded_languages: list[str]) -> list[dict]:
    """Drop picks whose language field matches an excluded language from the intent."""
    terms = _exclusion_match_terms(excluded_languages)
    if not terms:
        return picks

    kept: list[dict] = []
    for pick in picks:
        language = (pick.get("language") or "").lower().strip()
        if not language:
            kept.append(pick)
            continue
        if any(term in language or language in term for term in terms):
            continue
        kept.append(pick)
    return kept


def apply_language_filters(picks: list[dict], intent: str) -> tuple[list[dict], dict]:
    """Filter by excluded languages, then return the first DISPLAY_PICK_COUNT survivors."""
    excluded_languages = extract_excluded_languages(intent)
    surviving = filter_picks_by_exclusions(picks, excluded_languages)
    display = surviving[:DISPLAY_PICK_COUNT]
    return display, {
        "excluded_languages": excluded_languages,
        "model_count": len(picks),
        "surviving_count": len(surviving),
        "shown_count": len(display),
    }


# ── Session-dedup helpers ──────────────────────────────────────────────────────

def _format_shown_block(shown: set[tuple[str, str]]) -> str:
    """Format the already-shown list for injection into the steering prompt."""
    if not shown:
        return (
            "ALREADY SHOWN THIS SESSION: none yet — every track is eligible.\n"
            "Maximum ONE track per artist in this batch."
        )
    lines = "\n".join(f'  • "{t}" by {a}' for t, a in sorted(shown))
    return (
        "ALREADY SHOWN THIS SESSION — do NOT recommend any of these tracks or "
        "artists again (neither the exact track nor any other track by the same "
        "artist, even a different song):\n"
        + lines
        + "\nMaximum ONE track per artist in this batch as well."
    )


def _filter_shown_picks(
    picks: list[dict], shown: set[tuple[str, str]]
) -> tuple[list[dict], int]:
    """Drop picks whose track or artist already appeared in a previous batch."""
    shown_track_keys = {t.lower() for t, _ in shown}
    shown_artist_keys = {a.lower() for _, a in shown}
    kept, removed = [], 0
    for pick in picks:
        t_key = pick.get("track", "").lower().strip()
        a_key = pick.get("artist", "").lower().strip()
        if t_key in shown_track_keys or a_key in shown_artist_keys:
            removed += 1
        else:
            kept.append(pick)
    return kept, removed


def _dedup_artist_within_batch(picks: list[dict]) -> list[dict]:
    """Within a single batch, keep at most one track per artist (first occurrence wins)."""
    seen: set[str] = set()
    result = []
    for pick in picks:
        key = pick.get("artist", "").lower().strip()
        if key not in seen:
            seen.add(key)
            result.append(pick)
    return result


# ── Profile-artist backstop helpers ───────────────────────────────────────────

def _norm_artist(s: str) -> str:
    """Lowercase, strip non-alphanumeric (except spaces) for fuzzy matching."""
    return re.sub(r"[^a-z0-9 ]", "", s.lower()).strip()


def _extract_profile_artists(profile: dict | None) -> list[str]:
    """Return the original-cased artist names from a taste profile dict."""
    if not profile:
        return []
    artists: list[str] = list(profile.get("top_artists") or [])
    # Also pull artist portion from top_tracks ("Track – Artist" or "Track - Artist")
    for entry in profile.get("top_tracks") or []:
        parts = re.split(r"\s[–-]\s", entry, maxsplit=1)
        if len(parts) == 2:
            artist_part = parts[1].strip()
            if artist_part and artist_part not in artists:
                artists.append(artist_part)
    return artists


def _format_taste_artist_block(artist_names: list[str]) -> str:
    """Format the profile-artist ban-list for prompt injection."""
    if not artist_names:
        return (
            "TASTE PROFILE ARTISTS — BANNED: none explicitly listed. "
            "Still avoid any artist clearly present in the taste/avoid sections above."
        )
    lines = "\n".join(f"  • {a}" for a in artist_names)
    return (
        "TASTE PROFILE ARTISTS — COMPLETELY BANNED "
        "(do NOT recommend ANY track by these artists, not even a deep cut or B-side):\n"
        + lines
        + "\n\nThey are inside the user's current listening loop — recommending them defeats "
        "the purpose of this tool. "
        "REGIONAL / NON-WESTERN PROFILES: if the profile features Bollywood, K-pop, Afrobeats, "
        "Latin pop, Punjabi pop, or any regional mainstream, do NOT simply return other "
        "chart-level names from the same scene. Instead surface INDEPENDENT, UNDERGROUND, or "
        "CROSS-CULTURAL artists — smaller labels, niche sub-genres, diaspora fusions, or "
        "adjacent cultures — that this specific listener is genuinely unlikely to have discovered."
    )


def _filter_profile_artists(
    picks: list[dict], artist_names: list[str]
) -> tuple[list[dict], int]:
    """Backstop: drop any pick whose artist fuzzy-matches a known profile artist."""
    profile_keys = {_norm_artist(a) for a in artist_names if a.strip()}
    kept, removed = [], 0
    for pick in picks:
        pick_artist_norm = _norm_artist(pick.get("artist", ""))
        clash = any(
            # profile artist wholly contained in pick artist or vice-versa
            (pk and (pk in pick_artist_norm or pick_artist_norm in pk))
            for pk in profile_keys
            if len(pk) >= 3  # skip very short tokens to avoid false positives
        )
        if clash:
            removed += 1
        else:
            kept.append(pick)
    return kept, removed


STEER_PROMPT = """CONSTRAINT EXTRACTION — do this FIRST, before choosing any track:
Read the user's intent below and find every negative constraint — phrases with "avoid", "no",
"not", "without", "except", or "don't want". Each one is an ABSOLUTE filter that OVERRIDES
everything else (taste profile, adjacency, surprise value). A track that violates any exclusion
must NOT appear — no exceptions, no partial matches. Example: "avoid Punjabi" means zero
Punjabi-language or Punjabi-primary tracks in your output.

{exclusions_block}

{shown_block}

---

You are a music discovery engine with one job: break the user out of their listening loop.
A normal recommender predicts "more of what they already like." You do the opposite — you
find music that satisfies their intent but that they would NOT have reached on their own,
given their established taste.

USER'S TASTE PROFILE (from their actual Spotify library and listening history):
{taste}

TRACKS/ARTISTS THEY ALREADY KNOW WELL (do NOT recommend these or obvious hits from them):
{avoid}

{taste_artist_block}

USER'S DISCOVERY INTENT (what they're asking for right now):
"{intent}"

YOUR TASK:
Recommend exactly 8 real, existing tracks that honour the intent while being genuinely new
to THIS user. "New to them" is relative to their own profile — escape whatever rut THIS user
is in (if they already listen to obscure music, breaking their loop might mean something more
accessible).

Two axes — infer which the intent calls for:
- BREADTH (default): new artists/genres a reachable STEP from their taste.
- DEPTH (if they signal going deeper into one artist/album — "go deep", "more from"): surface
  the NON-OBVIOUS parts of that artist — overlooked album tracks, deep cuts, their evolution.
  NOT greatest hits or most-streamed songs (they already know those).

ADJACENCY — critical: when stepping away from their taste, move in ADJACENT steps, not jarring
leaps. Each pick should feel like a REACHABLE next step, not a genre they'd reject outright.

Hard rules:
- Exclusions override everything else — never violate them.
- Honour the positive intent FIRST (after exclusions).
- Recommend ONLY real tracks/artists that exist on Spotify. Use exact, searchable track titles
  and primary artist names (no "feat." clutter unless essential).
- NEVER recommend any artist listed in TASTE PROFILE ARTISTS — not a different song, not a
  collab, not a side project. Those names ARE the loop you must break.
- Do NOT recommend their recently played tracks or obvious chart hits.
- Do NOT recommend any track or artist listed in ALREADY SHOWN THIS SESSION — even a different
  song by the same artist is forbidden.
- Maximum ONE track per artist across all 8 picks in this batch.
- Each pick must plausibly be new to THIS user.
- Every pick MUST include a mandatory, accurate "language" field — the primary language of the
  track's lyrics/vocals (e.g. "English", "Tamil", "Punjabi", "Hindi", "Spanish", "Instrumental").
  Do not omit this field. Do not guess; if unsure, pick a different track you can label accurately.

FINAL SELF-CHECK — before you return JSON:
1. Re-read every exclusion (explicit and implicit) from the intent. For each of your 8 picks,
   verify its "language" and artist do NOT violate any exclusion.
2. For each pick, confirm the artist does NOT appear in TASTE PROFILE ARTISTS. Even a famous
   collaborator being the lead artist is a violation.
3. For each pick, confirm the artist does NOT appear in ALREADY SHOWN THIS SESSION.
If any pick fails any check, discard it and replace it with a compliant track.
Repeat until all 8 picks pass every check.

Return ONLY valid JSON, no prose, no markdown fences:
{{"picks":[{{"track":"...","artist":"...","language":"...","tag":"new|older|deep|edge",
"newness":"short phrase","reason":"one specific line tying it to their profile"}}]}}"""


def _format_duration(duration_ms: int | None) -> str:
    if not duration_ms:
        return "—"
    total_seconds = int(duration_ms) // 1000
    minutes, seconds = divmod(total_seconds, 60)
    return f"{minutes:02d}:{seconds:02d}"


def _spotify_search_url(track: str, artist: str) -> str:
    q = urllib.parse.quote(f"{track} {artist}")
    return f"https://open.spotify.com/search/{q}"


def _render_playlist_view(
    picks: list[dict],
    sp,
) -> None:
    if "saved_tracks" not in st.session_state:
        st.session_state["saved_tracks"] = set()

    for i, pick in enumerate(picks, start=1):
        idx = i - 1
        spotify_id = pick.get("spotify_id")
        uri = pick.get("uri")
        track_name = pick.get("track", "")
        artist_name = pick.get("artist", "")
        title = html.escape(track_name)
        artist = html.escape(artist_name)
        reason = html.escape(pick.get("reason") or "Steered away from your usual rotation.")
        duration = _format_duration(pick.get("duration_ms"))
        art = pick.get("album_art")
        search_url = html.escape(_spotify_search_url(track_name, artist_name))

        art_html = (
            f'<img class="pl-row-art" src="{html.escape(art)}" alt="" />'
            if art
            else (
                '<div class="pl-row-art" style="background:#282828;border-radius:4px;'
                'display:flex;align-items:center;justify-content:center;'
                'color:#555;font-size:20px;">♪</div>'
            )
        )
        dur_html = (
            f'<span style="color:#727272;font-size:12px;margin-left:6px;">{html.escape(duration)}</span>'
            if duration != "—" else ""
        )

        num_col, meta_col, action_col = st.columns([0.28, 5.2, 0.5])
        with num_col:
            st.markdown(
                f'<p style="color:#b3b3b3;font-size:14px;margin:14px 0 0;text-align:center;">{i}</p>',
                unsafe_allow_html=True,
            )
        with meta_col:
            st.markdown(
                f'<div class="pl-row-title">{art_html}<div class="pl-row-meta">'
                f'<div class="pl-row-track">{title}'
                f'&ensp;<a class="open-spotify-link" href="{search_url}"'
                f' target="_blank" rel="noopener">▶ Open in Spotify</a></div>'
                f'<div class="pl-row-artist">{artist}{dur_html}</div>'
                f'<div class="pl-row-reason">{reason}</div>'
                f"</div></div>",
                unsafe_allow_html=True,
            )
        with action_col:
            # Heart only when a real URI was resolved from the Spotify API (requires auth)
            if sp and uri and spotify_id:
                saved = spotify_id in st.session_state["saved_tracks"]
                if st.button("♥" if saved else "♡", key=f"save_{idx}", help="Save to Liked Songs"):
                    try:
                        save_track_to_library(sp, uri)
                        st.session_state["saved_tracks"].add(spotify_id)
                        st.toast("Saved to Liked Songs")
                    except SpotifyException as exc:
                        st.error(f"Could not save: {exc}")
                    st.rerun()


def _render_sticky_player() -> None:
    embed = st.session_state.get("playing_embed")
    if not embed:
        return
    eid = html.escape(embed.get("id", ""))
    etype = html.escape(embed.get("type", "playlist"))
    st.markdown(
        f'<div class="sticky-player"><div class="sticky-player-inner">'
        f'<iframe src="https://open.spotify.com/embed/{etype}/{eid}'
        f'?utm_source=generator&theme=0" '
        f'allow="autoplay; clipboard-write; encrypted-media; fullscreen; picture-in-picture" '
        f'loading="lazy"></iframe></div></div>',
        unsafe_allow_html=True,
    )


def _icon_data_uri(filename: str) -> str:
    path = Path(__file__).parent / "assets" / filename
    if not path.exists():
        return ""
    encoded = base64.b64encode(path.read_bytes()).decode()
    return f"data:image/png;base64,{encoded}"


def _render_home_tile(label: str, icon_html: str, *, dummy: bool = True) -> None:
    dummy_cls = " dummy" if dummy else ""
    st.markdown(
        f'<div class="home-tile{dummy_cls}">'
        f'<div class="home-tile-icon">{icon_html}</div>'
        f'<span class="home-tile-label">{html.escape(label)}</span>'
        f"</div>",
        unsafe_allow_html=True,
    )


def render_home(
    *,
    sp,
    oauth,
    app_secrets: dict[str, str],
    spotify_ready: bool,
    profile: dict | None,
) -> None:
    # "connected" = real Spotify auth OR demo mode loaded
    demo_active = st.session_state.get("demo_mode", False)
    connected = (sp is not None and profile is not None) or demo_active
    # Use PNG icon for the home tile button background
    _icon_path = Path(__file__).parent / "assets" / "BreakloopIcon.png"
    if _icon_path.exists():
        _icon_b64 = base64.b64encode(_icon_path.read_bytes()).decode()
        _icon_uri = f"data:image/png;base64,{_icon_b64}"
        st.markdown(
            f"<style>.st-key-tile_break_loop button {{"
            f"background-image: url('{_icon_uri}') !important;}}</style>",
            unsafe_allow_html=True,
        )

    st.markdown('<div class="home-shell">', unsafe_allow_html=True)

    # ---- header row
    if connected and sp and profile:
        user_name = profile["user"].get("display_name") or "You"
        initial = html.escape(user_name[0].upper())
        header_right = (
            f'<span>{html.escape(user_name)}</span>'
            f'<div class="home-avatar">{initial}</div>'
        )
    elif demo_active:
        header_right = (
            '<span style="color:#1ed760;font-size:13px;">Demo mode</span>'
            '<div class="home-avatar" style="background:#333;color:#1ed760;">▶</div>'
        )
    else:
        header_right = (
            '<span style="color:#b3b3b3;">Guest</span>'
            '<div class="home-avatar" style="background:#333;color:#888;">?</div>'
        )

    st.markdown(
        f'<div class="spotify-home-header">'
        f'<div class="home-nav-btn">⌂</div>'
        f'<div class="home-search">What do you want to play?</div>'
        f'<div class="home-user">{header_right}</div></div>',
        unsafe_allow_html=True,
    )

    # ---- top-right action (log out when connected, connect chip when not)
    if connected and sp:
        _, hc2 = st.columns([5, 1])
        with hc2:
            if st.button("Log out", key="home_logout", use_container_width=True):
                teardown_session_playlist(sp, st.session_state)
                for key in (
                    "spotify_token", "taste_profile", "last_picks", "legacy_playlists_checked",
                    "session_playlist_id", "playing_embed", "saved_tracks",
                    "show_add_for", "page", "pick_filter_meta", "demo_mode",
                ):
                    st.session_state.pop(key, None)
                st.rerun()
    elif demo_active:
        _, hc2 = st.columns([5, 1])
        with hc2:
            if st.button("Exit demo", key="home_exit_demo", use_container_width=True):
                for key in ("demo_mode", "taste_profile", "last_picks", "pick_filter_meta", "page"):
                    st.session_state.pop(key, None)
                st.rerun()
    elif spotify_ready:
        auth_url = oauth.get_authorize_url()
        redirect_uri = app_secrets.get("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8501")
        st.markdown(
            f'<div style="text-align:right;margin-bottom:4px;">'
            f'<a class="home-connect-chip" href="{html.escape(auth_url)}" target="_self">'
            f'<span>🎵</span> Connect your Spotify library'
            f'</a></div>',
            unsafe_allow_html=True,
        )

    # ---- demo CTA (only shown when not connected)
    if not connected:
        st.markdown(
            '<div class="demo-cta">'
            '<h3>▶ Try it instantly — no login needed</h3>'
            '<p>Load a sample indie-rock listener profile and run a full discovery in seconds.</p>'
            '</div>',
            unsafe_allow_html=True,
        )
        if st.button("▶ Load sample listener (demo)", key="demo_load", use_container_width=False):
            st.session_state["demo_mode"] = True
            st.session_state["taste_profile"] = DEMO_TASTE_PROFILE
            st.rerun()

    st.markdown(
        '<div class="home-pills">'
        '<span class="home-pill active">All</span>'
        '<span class="home-pill">Music</span>'
        '<span class="home-pill">Podcasts</span>'
        "</div>",
        unsafe_allow_html=True,
    )

    row1 = st.columns(4)
    with row1[0]:
        _render_home_tile(
            "Liked Songs",
            '<div class="tile-liked" style="width:100%;height:100%;display:flex;align-items:center;justify-content:center;font-size:28px;">♥</div>',
            dummy=True,
        )
    with row1[1]:
        _render_home_tile(
            "Discover Weekly",
            '<div class="tile-discover" style="width:100%;height:100%;"></div>',
            dummy=True,
        )
    with row1[2]:
        if connected:
            if st.button("Break the loop", key="tile_break_loop", use_container_width=True):
                st.session_state.page = "break_the_loop"
                st.rerun()
        else:
            st.button("Break the loop", key="tile_break_loop", use_container_width=True, disabled=True)
    with row1[3]:
        _render_home_tile(
            "Daily Mix 01",
            '<div class="tile-daily" style="width:100%;height:100%;"></div>',
            dummy=True,
        )

    row2 = st.columns(4)
    with row2[0]:
        _render_home_tile(
            "Release Radar",
            '<div class="tile-release" style="width:100%;height:100%;"></div>',
            dummy=True,
        )
    with row2[1]:
        _render_home_tile(
            "On Repeat",
            '<div class="tile-onrepeat" style="width:100%;height:100%;"></div>',
            dummy=True,
        )
    with row2[2]:
        _render_home_tile("Your Episodes", "📚", dummy=True)
    with row2[3]:
        _render_home_tile("Chill Mix", "🎧", dummy=True)

    st.markdown(
        '<p class="home-section-title">Made for you</p>',
        unsafe_allow_html=True,
    )
    if connected:
        st.caption("Tap **Break the loop** to steer away from your usual rotation.")
    else:
        st.caption("Load the demo or connect Spotify to enable **Break the loop**.")
    st.markdown("</div>", unsafe_allow_html=True)


def render_break_the_loop(
    *,
    sp,
    oauth,
    app_secrets: dict[str, str],
    spotify_ready: bool,
    profile: dict | None,
    taste_text: str,
    avoid_text: str,
) -> None:
    if st.button("← Back to home", key="back_home"):
        st.session_state.page = "home"
        st.rerun()

    st.markdown(
        """
<div class="hero">
  <p style="font-size:12px;font-weight:700;letter-spacing:0.08em;text-transform:uppercase;
            color:#b3b3b3;margin-bottom:8px;">Playlist · Discovery</p>
  <h1>Break the Loop</h1>
  <p>Steers <em>away</em> from your usual rotation — a live session, not a playlist archive.</p>
</div>
""",
        unsafe_allow_html=True,
    )

    demo_active = st.session_state.get("demo_mode", False)

    # Auto-mark profile loaded for already-authed Spotify users
    if sp and profile and not st.session_state.get("profile_loaded"):
        st.session_state["profile_loaded"] = True

    profile_loaded = st.session_state.get("profile_loaded", False)

    # Legacy playlist cleanup (silent, runs once per session when authed)
    if sp and not demo_active:
        if "legacy_playlists_checked" not in st.session_state:
            st.session_state["legacy_playlists_checked"] = True
            legacy = find_break_loop_playlists(sp)
            if legacy:
                st.session_state["legacy_playlist_names"] = [p.get("name", "") for p in legacy]
        legacy_names = st.session_state.get("legacy_playlist_names", [])
        if legacy_names:
            st.warning(
                f"Found {len(legacy_names)} old **Break the Loop** playlist(s) in your Spotify library "
                f"from an earlier version."
            )
            if st.button("Remove them from my Spotify library", type="secondary", key="rm_legacy"):
                removed, failed = remove_break_loop_playlists(sp)
                st.session_state.pop("legacy_playlist_names", None)
                if removed:
                    st.success("Removed: " + ", ".join(removed))
                if failed:
                    st.error("Could not remove: " + ", ".join(failed))
                st.rerun()

    # ── Step 1: Load a listener ───────────────────────────────────────────────
    st.markdown("### Step 1 — Load a listener")

    if profile_loaded:
        # Collapsed green summary
        loaded_name = profile["user"].get("display_name") if profile else None
        loaded_name = loaded_name or ("Demo Listener" if demo_active else "Your Spotify library")
        st.success(f"✓ Loaded: **{loaded_name}**")
        # Log-out / exit buttons tucked here when collapsed
        if demo_active:
            if st.button("↩ Change listener (exit demo)", key="btl_exit_demo"):
                for key in (
                    "demo_mode", "taste_profile", "last_picks", "pick_filter_meta",
                    "page", "profile_loaded", "intent_val",
                ):
                    st.session_state.pop(key, None)
                st.rerun()
        elif sp:
            if st.button("↩ Disconnect Spotify", key="btl_logout"):
                teardown_session_playlist(sp, st.session_state)
                for key in (
                    "spotify_token", "taste_profile", "last_picks", "legacy_playlists_checked",
                    "session_playlist_id", "playing_embed", "saved_tracks",
                    "show_add_for", "page", "pick_filter_meta", "profile_loaded", "intent_val",
                ):
                    st.session_state.pop(key, None)
                st.rerun()
    else:
        # Expanded: show load options
        load_col1, load_col2 = st.columns([1, 1])
        with load_col1:
            if st.button("▶ Load sample listener (demo)", key="step1_demo", use_container_width=True, type="primary"):
                st.session_state["demo_mode"] = True
                st.session_state["taste_profile"] = DEMO_TASTE_PROFILE
                st.session_state["profile_loaded"] = True
                # refresh profile variables for this run
                st.rerun()
        with load_col2:
            if sp and profile:
                if st.button("✓ Use my Spotify library", key="step1_spotify", use_container_width=True):
                    st.session_state["profile_loaded"] = True
                    st.rerun()
            elif spotify_ready and not sp:
                auth_url = oauth.get_authorize_url()
                st.link_button("Connect Spotify instead", auth_url, use_container_width=True)
        st.caption("Demo uses a built-in indie-rock listener profile — no login required.")

    st.divider()

    # ── Step 2: Tell it your vibe ─────────────────────────────────────────────
    st.markdown("### Step 2 — Tell it your vibe")

    intent = ""
    if not profile_loaded:
        st.caption("🔒 Complete Step 1 first.")
    else:
        # Re-sync taste_text in case profile was just loaded this run
        _profile = st.session_state.get("taste_profile")
        _taste_text = _profile["summary"] if _profile else taste_text
        _avoid_text = avoid_text
        if _profile:
            _avoid_text = (
                f"Top artists: {', '.join(_profile['top_artists'][:12])}\n"
                f"Top tracks: {'; '.join(_profile['top_tracks'][:10])}"
            )

        intent_val = st.session_state.get("intent_val", "")
        intent_done = bool(intent_val.strip())

        if intent_done:
            st.success(f"✓ Vibe set: **{intent_val}**")
            if st.button("✏️ Change vibe", key="change_vibe"):
                st.session_state["intent_val"] = ""
                st.session_state.pop("last_picks", None)
                st.rerun()
        else:
            intent = st.text_input(
                "What do you want right now?",
                placeholder="e.g. something new but not my usual famous stuff",
                value="",
                key="intent_input",
            )
            chip_cols = st.columns(3)
            for col, example in zip(chip_cols, [
                "surprise me — not the mainstream",
                "older gems in a genre I love",
                "go deep into an artist I half-know",
            ]):
                if col.button(example, use_container_width=True, key=f"chip_{example}"):
                    st.session_state["intent_val"] = example
                    st.rerun()
            # Commit typed value and immediately collapse Step 2
            if intent.strip():
                st.session_state["intent_val"] = intent.strip()
                st.rerun()

        intent = st.session_state.get("intent_val", "")
        taste_text = _taste_text
        avoid_text = _avoid_text

    st.divider()

    # ── Step 3: Break the loop ────────────────────────────────────────────────
    st.markdown("### Step 3 — Break the loop")

    # Pop steer trigger set by "Steer again" buttons — fires discovery without button click
    steer_triggered = st.session_state.pop("steer_trigger", None)
    if steer_triggered:
        intent = steer_triggered  # override intent for this run

    can_run = bool(profile_loaded and intent.strip() and taste_text.strip())

    if not profile_loaded:
        st.caption("🔒 Complete Steps 1 and 2 first.")
    elif not intent.strip():
        st.caption("🔒 Type something in Step 2 first.")

    run_clicked = st.button("Break my loop ↗", type="primary", disabled=not can_run, key="run_discovery")

    if run_clicked or (steer_triggered and can_run):
        try:
            with st.spinner("Steering away from your usual…"):
                shown_tracks: set[tuple[str, str]] = st.session_state.setdefault(
                    "shown_tracks", set()
                )
                _active_profile = st.session_state.get("taste_profile") or profile
                _profile_artists = _extract_profile_artists(_active_profile)
                picks, filter_meta = _run_discovery(
                    taste_text, avoid_text, intent, app_secrets,
                    shown_tracks=shown_tracks,
                    profile_artists=_profile_artists,
                )
                st.session_state["last_picks"] = picks
                st.session_state["pick_filter_meta"] = filter_meta
                st.session_state["last_intent"] = intent

                # Append newly-shown tracks so future runs skip them
                for p in picks:
                    t = p.get("track", "").lower().strip()
                    a = p.get("artist", "").lower().strip()
                    if t and a:
                        shown_tracks.add((t, a))

                if not picks and filter_meta.get("model_count", 0):
                    st.warning(
                        "All suggested tracks were filtered out by your exclusions. "
                        "Try broadening your intent or clicking 'Start fresh'."
                    )

                if sp:
                    with st.spinner("Matching tracks on Spotify catalog…"):
                        resolved = resolve_picks(sp, picks)
                        matched = sum(1 for p in resolved if p.get("spotify_id"))
                        st.session_state["last_picks"] = resolved
                        st.session_state["resolve_debug"] = (
                            f"🔍 Matched {matched} of {len(resolved)} tracks on Spotify"
                            + (" — artwork & save enabled." if matched else " — using search links only.")
                        )
                        uris = [p["uri"] for p in resolved if p.get("uri")]
                        if uris:
                            playlist_id = sync_session_playlist(sp, st.session_state, uris)
                            st.session_state["session_playlist_id"] = playlist_id
                            if playlist_id:
                                st.session_state["playing_embed"] = {
                                    "type": "playlist",
                                    "id": playlist_id,
                                }
                        if matched == 0:
                            st.warning(
                                "Spotify search returned no matches for these tracks. "
                                "You can still open them by name using the ▶ Open in Spotify links."
                            )

        except SpotifyException as exc:
            st.error(f"Spotify API error: {exc}")
        except json.JSONDecodeError:
            st.error("The model returned something unexpected — try again.")
        except KeyError as exc:
            if "ANTHROPIC_API_KEY" in str(exc):
                st.error(
                    "No ANTHROPIC_API_KEY found. "
                    "Add it to Streamlit secrets or `.streamlit/secrets.toml`."
                )
            else:
                st.error(f"Something went wrong (missing key: {exc}) — try again.")
        except Exception as exc:
            st.error(f"Something went wrong: {exc}")

    picks = st.session_state.get("last_picks")
    filter_meta = st.session_state.get("pick_filter_meta")

    if picks:
        if filter_meta:
            shown_count = filter_meta.get("shown_count", len(picks))
            shown_removed = filter_meta.get("shown_removed", 0)
            profile_removed = filter_meta.get("profile_removed", 0)
            excluded = filter_meta.get("excluded_languages") or []
            notes = []
            if excluded and shown_count < DISPLAY_PICK_COUNT:
                notes.append(
                    f"{shown_count}/{DISPLAY_PICK_COUNT} passed language exclusion "
                    f"({', '.join(excluded)})"
                )
            if shown_removed:
                notes.append(
                    f"{shown_removed} track{'s' if shown_removed > 1 else ''} skipped "
                    "— already shown this session"
                )
            if profile_removed:
                notes.append(
                    f"{profile_removed} artist{'s' if profile_removed > 1 else ''} removed "
                    "— in your taste profile"
                )
            if notes:
                st.caption(" · ".join(notes))

        found = sum(1 for p in picks if p.get("spotify_id"))
        subtitle = f"{len(picks)} tracks steered away from your usual"
        if found:
            subtitle += f" · {found} resolved on Spotify (♡ to save)"

        _cover_icon_path = Path(__file__).parent / "assets" / "BreakloopIcon.png"
        _cover_icon_uri = (
            f"data:image/png;base64,{base64.b64encode(_cover_icon_path.read_bytes()).decode()}"
            if _cover_icon_path.exists() else ""
        )

        st.markdown(
            f"""
<div class="playlist-card">
  <div class="cover" style="display:flex;align-items:center;justify-content:center;padding:0;overflow:hidden;"><img src="{_cover_icon_uri}" style="width:100%;height:100%;object-fit:cover;border-radius:8px;" alt="" /></div>
  <div class="meta">
    <div class="sub">Break the Loop · right now</div>
    <h2>Your steer batch</h2>
    <div class="sub">{html.escape(subtitle)}</div>
  </div>
</div>
""",
            unsafe_allow_html=True,
        )

        resolve_debug = st.session_state.get("resolve_debug")
        if resolve_debug:
            st.caption(resolve_debug)

        st.markdown('<div class="spotify-playlist-panel">', unsafe_allow_html=True)
        toolbar_left = f"Your steer batch · {len(picks)} tracks"
        st.markdown(
            f'<div class="pl-toolbar"><span class="label">{html.escape(toolbar_left)}</span>'
            f'<span style="color:#727272;font-size:12px;">Click ▶ Open in Spotify on any track</span></div>',
            unsafe_allow_html=True,
        )

        _render_playlist_view(picks, sp)
        st.markdown("</div>", unsafe_allow_html=True)

        if sp and st.session_state.get("playing_embed"):
            _render_sticky_player()

        shown_count_session = len(st.session_state.get("shown_tracks", set()))
        st.markdown(
            f"**Steer again** "
            f"<span style='color:#727272;font-size:12px;'>({shown_count_session} tracks seen this session)</span>",
            unsafe_allow_html=True,
        )
        steer_cols = st.columns([1, 1, 1, 1])
        last_intent = st.session_state.get("last_intent", intent)
        steer_labels = ["more obscure", "older", "go deeper"]
        for col, label in zip(steer_cols[:3], steer_labels):
            if col.button(label, use_container_width=True, key=f"steer_{label}"):
                new_intent = f"{last_intent} — make these {label}"
                st.session_state["steer_trigger"] = new_intent
                st.session_state["intent_val"] = new_intent
                st.rerun()
        if steer_cols[3].button("↺ Start fresh", use_container_width=True, key="start_fresh"):
            st.session_state["shown_tracks"] = set()
            st.session_state.pop("last_picks", None)
            st.session_state.pop("last_intent", None)
            st.session_state.pop("pick_filter_meta", None)
            st.session_state.pop("resolve_debug", None)
            st.session_state.pop("steer_trigger", None)
            st.session_state["intent_val"] = ""
            st.rerun()

    st.divider()
    st.caption(
        "Why AI: a normal recommender optimises *toward* your history. This instructs an LLM "
        "to optimise *against* it — a live conversation, not a playlist factory."
    )


def _run_discovery(
    taste: str,
    avoid: str,
    intent: str,
    secrets: dict[str, str],
    shown_tracks: set[tuple[str, str]] | None = None,
    profile_artists: list[str] | None = None,
) -> tuple[list[dict], dict]:
    import anthropic

    shown_tracks = shown_tracks or set()
    profile_artists = profile_artists or []
    excluded_languages = extract_excluded_languages(intent)
    exclusions_block = _format_exclusions_block(excluded_languages)
    shown_block = _format_shown_block(shown_tracks)
    taste_artist_block = _format_taste_artist_block(profile_artists)

    client = anthropic.Anthropic(api_key=secrets["ANTHROPIC_API_KEY"])
    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2000,
        messages=[
            {
                "role": "user",
                "content": STEER_PROMPT.format(
                    taste=taste,
                    avoid=avoid,
                    intent=intent,
                    exclusions_block=exclusions_block,
                    shown_block=shown_block,
                    taste_artist_block=taste_artist_block,
                ),
            }
        ],
    )
    raw = msg.content[0].text.strip().replace("```json", "").replace("```", "").strip()
    raw_picks = json.loads(raw)["picks"]

    # Within-batch: drop duplicate artists (keep first occurrence)
    raw_picks = _dedup_artist_within_batch(raw_picks)

    # Language filter — keep ALL survivors (don't slice yet)
    lang_survivors = filter_picks_by_exclusions(raw_picks, excluded_languages)

    # Backstop 1: drop tracks/artists already shown this session
    deduped, shown_removed = _filter_shown_picks(lang_survivors, shown_tracks)

    # Backstop 2: drop any pick whose artist appears in the taste/avoid profile
    clean, profile_removed = _filter_profile_artists(deduped, profile_artists)

    # Take first DISPLAY_PICK_COUNT from what's left
    final_picks = clean[:DISPLAY_PICK_COUNT]

    filter_meta = {
        "excluded_languages": excluded_languages,
        "model_count": len(raw_picks),
        "surviving_count": len(lang_survivors),
        "shown_removed": shown_removed,
        "profile_removed": profile_removed,
        "shown_count": len(final_picks),
    }
    return final_picks, filter_meta


APP_SECRETS = load_app_secrets()
SPOTIFY_READY = has_spotify_credentials(APP_SECRETS)

# ---------------------------------------------------------------- OAuth callback
if SPOTIFY_READY:
    oauth = build_oauth(st.session_state, APP_SECRETS)
    auth_error = st.query_params.get("error")
    auth_code = st.query_params.get("code")

    if auth_error:
        st.error(f"Spotify login failed: {auth_error}")
        st.query_params.clear()
    elif auth_code and not st.session_state.get("spotify_token"):
        try:
            complete_auth(oauth, auth_code)
            st.query_params.clear()
            st.rerun()
        except Exception as exc:
            st.error(f"Could not complete Spotify login: {exc}")

    sp = get_client(oauth) if st.session_state.get("spotify_token") else None
else:
    oauth = None
    sp = None

if "page" not in st.session_state:
    st.session_state.page = "home"

# ---------------------------------------------------------------- taste profile
# Populated by Spotify auth OR demo mode — whichever comes first.
taste_text = ""
avoid_text = "none provided"
profile = None

if sp:
    if "taste_profile" not in st.session_state:
        with st.spinner("Reading your Spotify library…"):
            st.session_state["taste_profile"] = fetch_taste_profile(sp)

# Demo mode sets taste_profile directly; pick it up here regardless of sp.
if "taste_profile" in st.session_state:
    profile = st.session_state["taste_profile"]
    taste_text = profile["summary"]
    avoid_text = (
        f"Top artists: {', '.join(profile['top_artists'][:12])}\n"
        f"Top tracks: {'; '.join(profile['top_tracks'][:10])}"
    )

demo_active = st.session_state.get("demo_mode", False)

# ---------------------------------------------------------------- page routing
if st.session_state.page == "break_the_loop":
    if sp or demo_active:
        render_break_the_loop(
            sp=sp,
            oauth=oauth,
            app_secrets=APP_SECRETS,
            spotify_ready=SPOTIFY_READY,
            profile=profile,
            taste_text=taste_text,
            avoid_text=avoid_text,
        )
    else:
        st.session_state.page = "home"
        render_home(
            sp=sp,
            oauth=oauth,
            app_secrets=APP_SECRETS,
            spotify_ready=SPOTIFY_READY,
            profile=profile,
        )
else:
    render_home(
        sp=sp,
        oauth=oauth,
        app_secrets=APP_SECRETS,
        spotify_ready=SPOTIFY_READY,
        profile=profile,
    )
