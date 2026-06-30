#!/usr/bin/env python3
"""
Last.fm -> Discord profile widget updater.

Pulls your Last.fm stats (the same data .fmbot reads) and PATCHes them to your
Discord application's widget. Runs on a schedule via GitHub Actions. No
third-party dependencies.

Required environment variables (set as GitHub Actions secrets / workflow env):
  LASTFM_API_KEY            - free key from https://www.last.fm/api/account/create
  LASTFM_USER               - your Last.fm username
  LASTFM_DISCORD_APP_ID     - the Last.fm Discord application's ID
  LASTFM_DISCORD_USER_ID    - your Discord user ID
  LASTFM_DISCORD_BOT_TOKEN  - the Last.fm Discord app's bot token

------------------------------------------------------------------------------
DISCORD WIDGET FIELDS — create these 6 dynamic (User Data) fields, using these
exact Data Field keys:
  total_scrobbles, scrobbling_since, top_artist,
  scrobbles_month, top_track, top_genre
Each: Label slot -> Value Type "User Data", Data Field = key above, Fallback on.
------------------------------------------------------------------------------
"""

import calendar
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter

# --------------------------------------------------------------------------
# CONFIG
# --------------------------------------------------------------------------
LASTFM_API_KEY = os.environ.get("LASTFM_API_KEY", "")
LASTFM_USER = os.environ.get("LASTFM_USER", "")

APP_ID = os.environ.get("LASTFM_DISCORD_APP_ID", "")
USER_ID = os.environ.get("LASTFM_DISCORD_USER_ID", "")
BOT_TOKEN = os.environ.get("LASTFM_DISCORD_BOT_TOKEN", "")

LASTFM_API = "https://ws.audioscrobbler.com/2.0/"
DISCORD_USER_AGENT = "DiscordBot (https://github.com/discord/discord-api-docs, 1.0.0)"
PUSH_URL = f"https://discord.com/api/v9/applications/{APP_ID}/users/{USER_ID}/identities/0/profile"

STATS_ORDER = [
    "total_scrobbles",
    "scrobbling_since",
    "top_artist",
    "scrobbles_month",
    "top_track",
    "top_genre",
]

DASH = "\u2013"  # en dash

# How many top artists to inspect when deriving the top genre.
GENRE_ARTIST_SAMPLE = 10

# Tags that aren't really genres - skipped when picking a genre.
TAG_BLOCKLIST = {
    "seen live", "favorites", "favourites", "favorite", "favourite",
    "spotify", "love", "awesome", "beautiful", "amazing", "best",
    "male vocalists", "female vocalists", "male vocalist", "female vocalist",
    "00s", "10s", "20s", "90s", "80s", "70s", "60s", "50s",
    "under 2000 listeners", "albums i own", "my music", "all",
}


# --------------------------------------------------------------------------
# LAST.FM HELPERS
# --------------------------------------------------------------------------

def lastfm_get(method: str, params: dict) -> dict:
    p = dict(params)
    p.update({"method": method, "api_key": LASTFM_API_KEY, "format": "json"})
    url = LASTFM_API + "?" + urllib.parse.urlencode(p)
    req = urllib.request.Request(url, headers={"User-Agent": "lastfm-discord-widget/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError) as e:
        print(f"  note: {method} returned no data ({e})")
        return {}


def as_list(value):
    """Last.fm returns a dict for single items, a list for many. Normalize."""
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return [value]
    return []


def fmt_int(n) -> str:
    try:
        return f"{int(n):,}"
    except (TypeError, ValueError):
        return str(n)


def derive_top_genre(artists) -> str:
    """Most common 'real' top-tag across the given top artists."""
    counts = Counter()
    for a in artists[:GENRE_ARTIST_SAMPLE]:
        name = a.get("name")
        if not name:
            continue
        tags = as_list(lastfm_get("artist.getTopTags",
                                  {"artist": name, "autocorrect": 1})
                       .get("toptags", {}).get("tag"))
        for tag in tags:
            tname = (tag.get("name") or "").strip().lower()
            if tname and tname not in TAG_BLOCKLIST:
                counts[tname] += 1
                break  # only the #1 valid tag per artist
    if not counts:
        return "—"
    return counts.most_common(1)[0][0].title()


def collect_stats() -> dict:
    out = {}

    # --- total scrobbles + join date ---
    info = lastfm_get("user.getInfo", {"user": LASTFM_USER}).get("user", {})
    out["total_scrobbles"] = fmt_int(info.get("playcount", 0))
    reg = info.get("registered", {}).get("unixtime")
    out["scrobbling_since"] = time.strftime("%b %Y", time.gmtime(int(reg))) if reg else "—"

    # --- top artists (month): used for top_artist AND genre ---
    artists = as_list(lastfm_get("user.getTopArtists",
                                 {"user": LASTFM_USER, "period": "1month",
                                  "limit": GENRE_ARTIST_SAMPLE})
                      .get("topartists", {}).get("artist"))
    out["top_artist"] = artists[0].get("name", "—") if artists else "—"

    # --- top track (month) ---
    tt = as_list(lastfm_get("user.getTopTracks",
                            {"user": LASTFM_USER, "period": "1month", "limit": 1})
                 .get("toptracks", {}).get("track"))
    if tt:
        out["top_track"] = f"{tt[0].get('artist', {}).get('name', '?')} {DASH} {tt[0].get('name', '?')}"
    else:
        out["top_track"] = "—"

    # --- scrobbles this calendar month (since the 1st, UTC) ---
    now = time.gmtime()
    month_start = calendar.timegm((now.tm_year, now.tm_mon, 1, 0, 0, 0, 0, 0, 0))
    mo = lastfm_get("user.getRecentTracks",
                    {"user": LASTFM_USER, "from": month_start, "limit": 1})
    out["scrobbles_month"] = fmt_int(
        mo.get("recenttracks", {}).get("@attr", {}).get("total", 0))

    # --- top genre (derived from top artists' tags) ---
    out["top_genre"] = derive_top_genre(artists)

    return out


# --------------------------------------------------------------------------
# DISCORD PUSH
# --------------------------------------------------------------------------

def build_payload(stats: dict) -> dict:
    dynamic = [
        {"type": 1, "name": name, "value": str(stats.get(name, "—"))}
        for name in STATS_ORDER
    ]
    return {"username": LASTFM_USER, "data": {"dynamic": dynamic}}


def push_to_discord(payload: dict) -> None:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        PUSH_URL, data=body, method="PATCH",
        headers={
            "Authorization": f"Bot {BOT_TOKEN}",
            "Content-Type": "application/json",
            "User-Agent": DISCORD_USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            print(f"Discord responded {resp.status}: widget updated.")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        raise RuntimeError(f"Discord push failed ({e.code}): {detail}") from e


def main() -> int:
    missing = [n for n, v in {
        "LASTFM_API_KEY": LASTFM_API_KEY,
        "LASTFM_USER": LASTFM_USER,
        "LASTFM_DISCORD_APP_ID": APP_ID,
        "LASTFM_DISCORD_USER_ID": USER_ID,
        "LASTFM_DISCORD_BOT_TOKEN": BOT_TOKEN,
    }.items() if not v]
    if missing:
        print(f"Missing required values: {', '.join(missing)}", file=sys.stderr)
        return 1

    print(f"Fetching Last.fm stats for '{LASTFM_USER}'...")
    stats = collect_stats()

    print("Collected stats:")
    print(json.dumps(stats, indent=2, ensure_ascii=False))

    payload = build_payload(stats)
    print("Payload being PATCHed to Discord:")
    print(json.dumps(payload, indent=2, ensure_ascii=False))

    push_to_discord(payload)
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
