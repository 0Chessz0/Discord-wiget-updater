#!/usr/bin/env python3
"""
Steam -> Discord profile widget updater.

Fetches Steam stats via the official Steam Web API and PATCHes them to your
Discord application's widget. Runs on a schedule via GitHub Actions (or
anywhere with Python 3). No third-party dependencies.

Required environment variables (set as GitHub Actions secrets):
  STEAM_API_KEY      - free key from https://steamcommunity.com/dev/apikey
  STEAM_ID           - SteamID64                 (default: 76561199250384751)
  DISCORD_APP_ID     - your application ID
  DISCORD_USER_ID    - your Discord user ID
  DISCORD_BOT_TOKEN  - your bot token (reset it in the Bot tab to get one)

Profile + Game Details must be set to PUBLIC on Steam, and Friends List
public too if you want the friends count (otherwise it shows "Private").

------------------------------------------------------------------------------
DISCORD WIDGET FIELDS — create these 8 dynamic (User Data) fields, using these
exact Data Field keys:
  total_playtime, recent_playtime, profile_age, badges,
  games_owned, friends, steam_level, top_game
Each: Label slot -> Value Type "User Data", Data Field = key above, Fallback on.
------------------------------------------------------------------------------
"""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# --------------------------------------------------------------------------
# CONFIG
# --------------------------------------------------------------------------
STEAM_API_KEY = os.environ.get("STEAM_API_KEY", "")
STEAM_ID = os.environ.get("STEAM_ID", "76561199250384751")

APP_ID = os.environ.get("DISCORD_APP_ID", "")
USER_ID = os.environ.get("DISCORD_USER_ID", "")
BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")

STEAM_API = "https://api.steampowered.com"
DISCORD_USER_AGENT = "DiscordBot (https://github.com/discord/discord-api-docs, 1.0.0)"
PUSH_URL = f"https://discord.com/api/v9/applications/{APP_ID}/users/{USER_ID}/identities/0/profile"

# The order these appear in the pushed payload. The "name" must match the
# Data Field key you set in the Discord widget editor.
STATS_ORDER = [
    "total_playtime",
    "recent_playtime",
    "profile_age",
    "badges",
    "games_owned",
    "friends",
    "steam_level",
    "top_game",
]

# --------------------------------------------------------------------------
# STEAM HELPERS
# --------------------------------------------------------------------------

def steam_get(interface_method: str, **params) -> dict:
    """GET a Steam Web API method and return parsed JSON (or {} on failure)."""
    params.setdefault("key", STEAM_API_KEY)
    url = f"{STEAM_API}/{interface_method}/?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "steam-discord-widget/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError) as e:
        # Most often a private endpoint (e.g. friends list) -> treat as empty.
        print(f"  note: {interface_method} returned no data ({e})")
        return {}


def fmt_int(n) -> str:
    try:
        return f"{int(n):,}"
    except (TypeError, ValueError):
        return str(n)


def collect_stats() -> dict:
    out = {}

    # --- Player summary: account age + display name ---
    summary = steam_get("ISteamUser/GetPlayerSummaries/v2",
                        steamids=STEAM_ID).get("response", {})
    players = summary.get("players", [])
    player = players[0] if players else {}
    persona = player.get("personaname", "chessz__")
    created = player.get("timecreated")
    if created:
        years = (time.time() - created) / (365.25 * 86400)
        out["profile_age"] = f"{years:.1f} years"
    else:
        out["profile_age"] = "Private"

    # --- Owned games: total playtime, games owned, most played ---
    owned = steam_get("IPlayerService/GetOwnedGames/v1",
                      steamid=STEAM_ID, include_appinfo=1,
                      include_played_free_games=1).get("response", {})
    games = owned.get("games", [])
    out["games_owned"] = fmt_int(owned.get("game_count", len(games)))

    total_minutes = sum(g.get("playtime_forever", 0) for g in games)
    out["total_playtime"] = f"{round(total_minutes / 60):,} h"

    if games:
        top = max(games, key=lambda g: g.get("playtime_forever", 0))
        out["top_game"] = top.get("name", f"App {top.get('appid', '?')}")
    else:
        out["top_game"] = "Private"

    # --- Recent (2-week) playtime ---
    recent = steam_get("IPlayerService/GetRecentlyPlayedGames/v1",
                       steamid=STEAM_ID).get("response", {})
    recent_minutes = sum(g.get("playtime_2weeks", 0) for g in recent.get("games", []))
    out["recent_playtime"] = f"{recent_minutes / 60:.1f} h"

    # --- Steam level ---
    level = steam_get("IPlayerService/GetSteamLevel/v1",
                      steamid=STEAM_ID).get("response", {})
    out["steam_level"] = fmt_int(level.get("player_level", 0))

    # --- Badges ---
    badges = steam_get("IPlayerService/GetBadges/v1",
                       steamid=STEAM_ID).get("response", {})
    out["badges"] = fmt_int(len(badges.get("badges", [])))

    # --- Friends (needs friends list public) ---
    friends = steam_get("ISteamUser/GetFriendList/v1",
                        steamid=STEAM_ID, relationship="friend")
    friend_list = friends.get("friendslist", {}).get("friends")
    out["friends"] = fmt_int(len(friend_list)) if friend_list else "Private"

    out["_persona"] = persona
    return out


# --------------------------------------------------------------------------
# DISCORD PUSH
# --------------------------------------------------------------------------

def build_payload(stats: dict) -> dict:
    dynamic = [
        {"type": 1, "name": name, "value": str(stats.get(name, "0"))}
        for name in STATS_ORDER
    ]
    return {"username": stats.get("_persona", "chessz__"), "data": {"dynamic": dynamic}}


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
        "STEAM_API_KEY": STEAM_API_KEY,
        "DISCORD_APP_ID": APP_ID,
        "DISCORD_USER_ID": USER_ID,
        "DISCORD_BOT_TOKEN": BOT_TOKEN,
    }.items() if not v]
    if missing:
        print(f"Missing required secrets: {', '.join(missing)}", file=sys.stderr)
        return 1

    print(f"Fetching Steam stats for SteamID {STEAM_ID}...")
    stats = collect_stats()

    print("Collected stats:")
    print(json.dumps({k: v for k, v in stats.items() if k != "_persona"}, indent=2))

    payload = build_payload(stats)
    print("Payload being PATCHed to Discord:")
    print(json.dumps(payload, indent=2))

    push_to_discord(payload)
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
