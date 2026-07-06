# Break the Loop — AI Music Discovery MVP

An AI-native discovery experience that deliberately steers **against** your listening history — surfacing genuinely new music that still fits, so the repetitive loop breaks instead of tightening.

**Live app:** https://spotify-breaktheloop.streamlit.app/  ·  *no login needed — use the built-in sample listener*

## The idea

A normal recommender optimises *toward* your taste — more of what you already play. This one does the opposite: it asks a model to recommend music *outside* your established pattern, but only a reachable step away, so the result is new without being jarring.

## How it works

1. **Load a listener** — real Spotify library (OAuth via Spotipy) *or* a built-in demo profile, so the taste input is visible.
2. **Steer** — the taste, artists to avoid, and a plain-language intent are formatted into a prompt and sent to Claude, which returns candidate tracks (track, artist, language, tag, reason).
3. **Filter (in code, not the prompt)** — the model output runs through a five-stage pipeline: regex exclusion-extraction → within-batch artist dedup → language filter → session backstop → profile-artist backstop → top 5.
4. **Deliver** — results render with a reason per track; if Spotify-authed, tracks resolve to real IDs (artwork, save-to-library, session playlist), otherwise they open in Spotify via search links.

## Scope

The AI steering and the full filter pipeline run live in both paths. Only in-app playback is simplified (the Spotify Web Playback SDK needs an allow-listed Premium login), so tracks open in Spotify instead.

## Tech

Python · Streamlit · Anthropic API (`claude-sonnet-4-6`) · Spotipy (Spotify Web API)

## Run locally

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY="sk-ant-..."
# optional, for the real-Spotify path:
export SPOTIFY_CLIENT_ID="..."
export SPOTIFY_CLIENT_SECRET="..."
streamlit run app.py
```

The app reads keys from Streamlit secrets or the environment. The demo path needs only `ANTHROPIC_API_KEY`.
