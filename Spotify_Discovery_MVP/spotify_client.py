"""Spotify OAuth, library taste profile, catalog search, and ephemeral resolve."""

from __future__ import annotations

from collections import Counter
from typing import Any

import requests
import spotipy
from spotipy.cache_handler import CacheHandler
from spotipy.exceptions import SpotifyException
from spotipy.oauth2 import SpotifyOAuth

# Read for discovery; modify for session player, save track, add to user playlist.
SCOPES = (
    "user-top-read user-library-read user-read-recently-played "
    "playlist-read-private playlist-modify-private playlist-modify-public "
    "user-library-modify"
)

CLEANUP_NAME_MARKERS = ("break my loop", "break the loop")
SESSION_PLAYLIST_NAME = "Break the Loop · session"


def create_private_playlist(sp: spotipy.Spotify, name: str, description: str = "") -> dict | None:
    response = _spotify_request(
        sp,
        "POST",
        "me/playlists",
        json_body={"name": name, "public": False, "description": description},
    )
    if response.status_code == 403:
        return None
    if not response.ok:
        try:
            message = response.json().get("error", {}).get("message", response.text)
        except ValueError:
            message = response.text
        raise SpotifyException(response.status_code, -1, f"{response.url}: {message}")
    return response.json()


def replace_playlist_tracks(sp: spotipy.Spotify, playlist_id: str, uris: list[str]) -> bool:
    """Replace entire playlist — never append."""
    if not uris:
        return False
    response = _spotify_request(
        sp,
        "PUT",
        f"playlists/{playlist_id}/items",
        json_body={"uris": uris},
    )
    if response.status_code == 403:
        return False
    if not response.ok:
        try:
            message = response.json().get("error", {}).get("message", response.text)
        except ValueError:
            message = response.text
        raise SpotifyException(response.status_code, -1, f"{response.url}: {message}")
    return True


def sync_session_playlist(
    sp: spotipy.Spotify,
    session_state: Any,
    uris: list[str],
) -> str | None:
    """
    One playlist per browser session: create once, replace tracks on each steer.
    Avoids stacking new playlist tiles on the Spotify home page.
    """
    if not uris:
        return session_state.get("session_playlist_id")

    playlist_id = session_state.get("session_playlist_id")
    if not playlist_id:
        created = create_private_playlist(
            sp,
            SESSION_PLAYLIST_NAME,
            description="Temporary session player — replaced each steer, removed when you log out.",
        )
        if not created:
            return None
        playlist_id = created["id"]
        session_state["session_playlist_id"] = playlist_id

    if not replace_playlist_tracks(sp, playlist_id, uris):
        return playlist_id
    return playlist_id


def teardown_session_playlist(sp: spotipy.Spotify, session_state: Any) -> None:
    """Remove session player from Spotify library on logout."""
    playlist_id = session_state.pop("session_playlist_id", None)
    if not playlist_id:
        return
    try:
        sp.current_user_unfollow_playlist(playlist_id)
    except SpotifyException:
        response = _spotify_request(sp, "DELETE", f"playlists/{playlist_id}/followers")
        if response.status_code not in (200, 204):
            pass


class SessionCacheHandler(CacheHandler):
    """Persist Spotify tokens in Streamlit session state."""

    def __init__(self, session_state: Any) -> None:
        self._state = session_state

    def get_cached_token(self) -> dict | None:
        return self._state.get("spotify_token")

    def save_token_to_cache(self, token_info: dict) -> None:
        self._state["spotify_token"] = token_info


def build_oauth(session_state: Any, secrets: dict) -> SpotifyOAuth:
    redirect_uri = secrets.get("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8501")
    return SpotifyOAuth(
        client_id=secrets["SPOTIFY_CLIENT_ID"],
        client_secret=secrets["SPOTIFY_CLIENT_SECRET"],
        redirect_uri=redirect_uri,
        scope=SCOPES,
        open_browser=False,
        show_dialog=True,
        cache_handler=SessionCacheHandler(session_state),
    )


def get_client(oauth: SpotifyOAuth) -> spotipy.Spotify | None:
    if not oauth.get_cached_token():
        return None
    return spotipy.Spotify(auth_manager=oauth)


def complete_auth(oauth: SpotifyOAuth, code: str) -> None:
    oauth.get_access_token(code, as_dict=False)


def _access_token(sp: spotipy.Spotify) -> str:
    token = getattr(sp, "_auth", None)
    if isinstance(token, str) and token:
        return token

    manager = getattr(sp, "auth_manager", None)
    if manager is not None:
        try:
            token = manager.get_access_token(as_dict=False)
        except TypeError:
            token = manager.get_access_token()
        if isinstance(token, str) and token:
            return token
        if isinstance(token, dict) and token.get("access_token"):
            return str(token["access_token"])

    raise SpotifyException(401, -1, "Missing Spotify access token")


def _spotify_request(
    sp: spotipy.Spotify,
    method: str,
    path: str,
    *,
    json_body: dict | list | None = None,
) -> requests.Response:
    url = f"https://api.spotify.com/v1/{path.lstrip('/')}"
    headers = {"Authorization": f"Bearer {_access_token(sp)}"}
    if json_body is not None:
        headers["Content-Type"] = "application/json"
    return requests.request(
        method,
        url,
        headers=headers,
        json=json_body,
        timeout=30,
    )


def fetch_taste_profile(sp: spotipy.Spotify) -> dict[str, Any]:
    """Summarize listening history for the LLM and UI."""
    user = sp.current_user()
    top_artists = sp.current_user_top_artists(limit=15, time_range="medium_term")["items"]
    top_tracks = sp.current_user_top_tracks(limit=15, time_range="medium_term")["items"]
    recent = sp.current_user_recently_played(limit=20)["items"]
    saved = sp.current_user_saved_tracks(limit=30)["items"]

    artist_names = [a["name"] for a in top_artists]
    track_lines = [
        f"{t['name']} — {', '.join(a['name'] for a in t['artists'])}"
        for t in top_tracks
    ]
    recent_lines = [
        f"{item['track']['name']} — {', '.join(a['name'] for a in item['track']['artists'])}"
        for item in recent
    ]
    saved_artists = Counter(
        artist["name"]
        for item in saved
        for artist in item["track"]["artists"]
    )

    genre_counter: Counter[str] = Counter()
    for artist in top_artists:
        for genre in artist.get("genres", [])[:3]:
            genre_counter[genre] += 1
    top_genres = [g for g, _ in genre_counter.most_common(8)]

    summary = (
        f"Spotify listener: {user.get('display_name') or user['id']}\n"
        f"Top artists (6 months): {', '.join(artist_names)}\n"
        f"Top tracks (6 months): {'; '.join(track_lines)}\n"
        f"Recently played: {'; '.join(recent_lines)}\n"
        f"Saved-library lean: {', '.join(name for name, _ in saved_artists.most_common(10))}\n"
        f"Inferred genres: {', '.join(top_genres) if top_genres else 'mixed / not tagged'}"
    )

    return {
        "user": user,
        "summary": summary,
        "top_artists": artist_names,
        "top_tracks": track_lines,
        "top_genres": top_genres,
        "known_tracks": {t.split(" — ", 1)[0].lower() for t in track_lines},
        "known_artists": {name.lower() for name in artist_names},
    }


def search_track(sp: spotipy.Spotify, track: str, artist: str) -> dict | None:
    query = f"track:{track} artist:{artist}"
    result = sp.search(q=query, type="track", limit=1)
    items = result.get("tracks", {}).get("items", [])
    if items:
        return items[0]

    fallback = sp.search(q=f"{track} {artist}", type="track", limit=3)
    for item in fallback.get("tracks", {}).get("items", []):
        item_artists = {a["name"].lower() for a in item["artists"]}
        if artist.lower() in item_artists or any(
            artist.lower() in name for name in item_artists
        ):
            return item
    items = fallback.get("tracks", {}).get("items", [])
    return items[0] if items else None


def album_art_url(track_item: dict) -> str | None:
    images = track_item.get("album", {}).get("images", [])
    if not images:
        return None
    return images[0]["url"]  # largest image first in Spotify's list


def resolve_picks(sp: spotipy.Spotify, picks: list[dict]) -> list[dict]:
    """Match AI picks to Spotify catalog — no library writes."""
    resolved: list[dict] = []

    for pick in picks:
        item = search_track(sp, pick.get("track", ""), pick.get("artist", ""))
        if not item:
            resolved.append({**pick, "spotify_id": None, "album_art": None, "uri": None})
            continue
        resolved.append(
            {
                **pick,
                "track": item["name"],
                "artist": ", ".join(a["name"] for a in item["artists"]),
                "album": item.get("album", {}).get("name", ""),
                "spotify_id": item["id"],
                "uri": item["uri"],
                "album_art": album_art_url(item),
                "preview_url": item.get("preview_url"),
                "duration_ms": item.get("duration_ms"),
            }
        )

    return resolved


def save_track_to_library(sp: spotipy.Spotify, uri: str) -> None:
    sp.current_user_saved_tracks_add([uri])


def list_user_playlists(sp: spotipy.Spotify, *, exclude_id: str | None = None) -> list[dict]:
    playlists: list[dict] = []
    offset = 0
    while True:
        page = sp.current_user_playlists(limit=50, offset=offset)
        for item in page.get("items", []):
            if exclude_id and item.get("id") == exclude_id:
                continue
            if item.get("name", "").lower() == SESSION_PLAYLIST_NAME.lower():
                continue
            playlists.append({"id": item["id"], "name": item.get("name", "Playlist")})
        if not page.get("next"):
            break
        offset += 50
    return playlists


def add_track_to_playlist(sp: spotipy.Spotify, playlist_id: str, uri: str) -> None:
    response = _spotify_request(
        sp,
        "POST",
        f"playlists/{playlist_id}/items",
        json_body={"uris": [uri]},
    )
    if not response.ok:
        try:
            message = response.json().get("error", {}).get("message", response.text)
        except ValueError:
            message = response.text
        raise SpotifyException(response.status_code, -1, f"{response.url}: {message}")


def _is_legacy_saved_playlist(name: str) -> bool:
    lowered = (name or "").lower()
    if lowered == SESSION_PLAYLIST_NAME.lower():
        return False
    return any(marker in lowered for marker in CLEANUP_NAME_MARKERS)


def _owned_break_loop_playlists(sp: spotipy.Spotify) -> list[dict]:
    owned: list[dict] = []
    offset = 0
    while True:
        page = sp.current_user_playlists(limit=50, offset=offset)
        for item in page.get("items", []):
            if _is_legacy_saved_playlist(item.get("name", "")):
                owned.append(item)
        if not page.get("next"):
            break
        offset += 50
    return owned


def find_break_loop_playlists(sp: spotipy.Spotify) -> list[dict]:
    """Playlists previously saved by older versions of this app."""
    return _owned_break_loop_playlists(sp)


def remove_break_loop_playlists(sp: spotipy.Spotify) -> tuple[list[str], list[str]]:
    """
    Remove Break the Loop playlists from the user's Spotify library.
    Returns (removed_names, failed_names).
    """
    removed: list[str] = []
    failed: list[str] = []

    for playlist in _owned_break_loop_playlists(sp):
        name = playlist.get("name") or "Break the Loop"
        playlist_id = playlist["id"]
        try:
            sp.current_user_unfollow_playlist(playlist_id)
            removed.append(name)
        except SpotifyException:
            response = _spotify_request(sp, "DELETE", f"playlists/{playlist_id}/followers")
            if response.status_code in (200, 204):
                removed.append(name)
            else:
                failed.append(name)

    return removed, failed
