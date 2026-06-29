#!/usr/bin/env python3
"""
Geometry Dash -> Discord profile widget updater.

Fetches your GD stats from the gdbrowser API and PATCHes them to your Discord
application's widget so your profile shows live stats. Runs on a schedule via
GitHub Actions (or anywhere with Python 3). No third-party dependencies.

Required environment variables (set as GitHub Actions secrets):
  GD_USERNAME        - your Geometry Dash username       (default: chessz0)
  DISCORD_APP_ID     - your application ID
  DISCORD_USER_ID    - your Discord user ID
  DISCORD_BOT_TOKEN  - your bot token (reset it in the Bot tab to get one)

------------------------------------------------------------------------------
HOW THE WIDGET FIELDS WORK (read this before editing FIELDS):

In the Widget editor every field has a Value Type:
  * Custom String / Application Asset = STATIC. Same for everyone, baked into
    the published widget. Use these for labels ("Stars:"), logos, etc.
    >>> These are NOT sent by this script. Do not list them in FIELDS. <<<
  * User Data = DYNAMIC. Changes per user. Set its "Data Field" to a key like
    `stars` and give it a fallback value.
    >>> ONLY these go in FIELDS, and "name" must equal that Data Field key. <<<
------------------------------------------------------------------------------
"""

import json
import os
import sys
import urllib.error
import urllib.request

# --------------------------------------------------------------------------
# CONFIG
# --------------------------------------------------------------------------
GD_USERNAME = os.environ.get("GD_USERNAME", "chessz0")
APP_ID = os.environ.get("DISCORD_APP_ID", "")
USER_ID = os.environ.get("DISCORD_USER_ID", "")
BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")

GDBROWSER_URL = f"https://gdbrowser.com/api/profile/{GD_USERNAME}"

# gdbrowser blocks blank/suspicious user-agents.
GD_USER_AGENT = "gd-discord-widget/1.0 (personal stats updater)"
# Discord expects a DiscordBot-style UA on this endpoint.
DISCORD_USER_AGENT = "DiscordBot (https://github.com/discord/discord-api-docs, 1.0.0)"

# Confirmed endpoint (Chloe Cinders / rohan.run guides). identity id 0 works.
PUSH_URL = f"https://discord.com/api/v9/applications/{APP_ID}/users/{USER_ID}/identities/0/profile"

# --------------------------------------------------------------------------
# WIDGET FIELD MAPPING  <-- EDIT THIS TO MATCH YOUR DYNAMIC FIELDS
# --------------------------------------------------------------------------
# One entry per DYNAMIC (User Data) field in your widget.
#   "name"   = the field's Data Field key in the editor (must match exactly).
#   "gd_key" = which value to read from the gdbrowser response.
#   "type"   = 1 string | 2 number (raw int) | 3 image ({"url": ...}).
#
# Match "type" to how you set the field's Presentation Type in the editor:
#   Number  -> type 2   (sent as a raw number, e.g. 12345)
#   Text    -> type 1   (sent as a string; set "comma": True for "12,345")
#   Image   -> type 3   (gd_key should resolve to an image URL)
FIELDS = [
    # All type 1 (string) because these drive the Label slot, which is text.
    # comma=True formats big numbers as 12,345 (set to False for raw 12345).
    {"name": "stars",      "gd_key": "stars",     "type": 1, "comma": True},
    {"name": "moons",      "gd_key": "moons",     "type": 1, "comma": True},
    {"name": "coins",      "gd_key": "coins",     "type": 1, "comma": True},  # secret/official coins
    {"name": "user_coins", "gd_key": "userCoins", "type": 1, "comma": True},
    {"name": "demons",     "gd_key": "demons",    "type": 1, "comma": True},
    {"name": "diamonds",   "gd_key": "diamonds",  "type": 1, "comma": True},
]

# --------------------------------------------------------------------------
# CORE LOGIC
# --------------------------------------------------------------------------

def fetch_gd_stats(username: str) -> dict:
    req = urllib.request.Request(GDBROWSER_URL, headers={"User-Agent": GD_USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read().decode("utf-8").strip()
    if raw == "-1":
        raise RuntimeError(
            f"gdbrowser returned -1 for '{username}'. Check the username spelling."
        )
    data = json.loads(raw)
    if isinstance(data, dict) and data.get("error"):
        raise RuntimeError(f"gdbrowser error: {data['error']}")
    return data


def coerce_value(raw, field: dict):
    """Convert a raw GD value into the shape Discord wants for this field type."""
    ftype = field["type"]
    if ftype == 2:  # number -> raw int
        try:
            return int(raw)
        except (TypeError, ValueError):
            return 0
    if ftype == 3:  # image -> {"url": ...}
        return {"url": str(raw)}
    # type 1 -> string (optionally comma-formatted)
    if field.get("comma"):
        try:
            return f"{int(raw):,}"
        except (TypeError, ValueError):
            pass
    return str(raw)


def build_payload(stats: dict) -> dict:
    dynamic = []
    for field in FIELDS:
        raw = stats.get(field["gd_key"], 0)
        dynamic.append({
            "type": field["type"],
            "name": field["name"],
            "value": coerce_value(raw, field),
        })
    return {"username": GD_USERNAME, "data": {"dynamic": dynamic}}


def push_to_discord(payload: dict) -> None:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        PUSH_URL,
        data=body,
        method="PATCH",
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
        "DISCORD_APP_ID": APP_ID,
        "DISCORD_USER_ID": USER_ID,
        "DISCORD_BOT_TOKEN": BOT_TOKEN,
    }.items() if not v]
    if missing:
        print(f"Missing required secrets: {', '.join(missing)}", file=sys.stderr)
        return 1

    print(f"Fetching GD stats for '{GD_USERNAME}'...")
    stats = fetch_gd_stats(GD_USERNAME)

    print("Raw gdbrowser response (use this to confirm your field names):")
    print(json.dumps(stats, indent=2))

    payload = build_payload(stats)
    print("Payload being PATCHed to Discord:")
    print(json.dumps(payload, indent=2))

    push_to_discord(payload)
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
