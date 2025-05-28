import os
import json
import base64
import random
import datetime
import subprocess
import tempfile
import pathlib
import requests

from pydrive2.auth import GoogleAuth
from pydrive2.auth import ServiceAccountCredentials
from pydrive2.drive import GoogleDrive

# ─────────────────────────────
# 0.  Environment-driven config
# ─────────────────────────────
CREATORS       = [c.strip() for c in os.getenv("CREATORS", "").split(",") if c.strip()]
CLIP_LIMIT     = int(os.getenv("CLIP_LIMIT", 12))
VIEW_THRESHOLD = int(os.getenv("VIEW_THRESHOLD", 800))

YT_CHANNELS    = [u.strip() for u in os.getenv("YT_CHANNELS", "").split(",") if u.strip()]

TARGET_ROOT    = "The Farm"     # top-level folder
TARGET_INBOUND = "Inbound"      # subfolder

today_str = datetime.datetime.now(datetime.timezone.utc).strftime("%y-%m-%d")

# ─────────────────────────────
# 1.  Get Twitch app token
# ─────────────────────────────
print("🔑  Requesting Twitch app token…")
token_res = requests.post(
    "https://id.twitch.tv/oauth2/token",
    params={
        "client_id": os.environ["TWITCH_CLIENT_ID"],
        "client_secret": os.environ["TWITCH_CLIENT_SECRET"],
        "grant_type": "client_credentials",
    },
    timeout=15,
).json()

ACCESS_TOKEN = token_res["access_token"]
HEADERS = {
    "Client-ID": os.environ["TWITCH_CLIENT_ID"],
    "Authorization": f"Bearer {ACCESS_TOKEN}",
}

# ─────────────────────────────
# 2.  Working directories
# ─────────────────────────────
WORKDIR = pathlib.Path(tempfile.mkdtemp(prefix="farmbot_"))
DL_DIR  = WORKDIR / "clips"
DL_DIR.mkdir(parents=True, exist_ok=True)

# ─────────────────────────────
# 3.  Helper → ensure Drive folder exists
# ─────────────────────────────
def ensure_folder(drive, title, parent_id="root"):
    query = (
        f"'{parent_id}' in parents and "
        f"mimeType='application/vnd.google-apps.folder' and "
        f"title='{title}' and trashed=false"
    )
    found = drive.ListFile({"q": query}).GetList()
    if found:
        return found[0]["id"]

    folder = drive.CreateFile(
        {
            "title": title,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [{"id": parent_id}],
        }
    )
    folder.Upload()
    return folder["id"]

# ─────────────────────────────
# 4.  Fetch clip metadata
# ─────────────────────────────
print("🔍  Fetching clip metadata…")
utc_now   = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
utc_start = utc_now - datetime.timedelta(days=1)
start_iso = utc_start.isoformat().replace("+00:00", "Z")
end_iso   = utc_now.isoformat().replace("+00:00", "Z")

clips_meta = []
for login in CREATORS:
    # 4a. resolve user-ID
    user_res = requests.get(
        "https://api.twitch.tv/helix/users",
        headers=HEADERS,
        params={"login": login},
        timeout=10,
    ).json()
    if not user_res["data"]:
        print(f"⚠️  No such user: {login}")
        continue
    user_id = user_res["data"][0]["id"]
    display = user_res["data"][0]["display_name"]

    # 4b. get clips
    clips_res = requests.get(
        "https://api.twitch.tv/helix/clips",
        headers=HEADERS,
        params={
            "broadcaster_id": user_id,
            "first": 20,
            "started_at": start_iso,
            "ended_at": end_iso,
        },
        timeout=10,
    ).json()

    for clip in clips_res.get("data", []):
        if clip["view_count"] >= VIEW_THRESHOLD:
            clip["broadcaster_display"] = display
            clips_meta.append(clip)

clips_meta = sorted(clips_meta, key=lambda c: c["view_count"], reverse=True)[:CLIP_LIMIT]
print(f"✅  Selected {len(clips_meta)} clips.")

# ─────────────────────────────
# 5. Download clips with yt-dlp
# ─────────────────────────────
downloaded = []
for clip in clips_meta:
    out_path = DL_DIR / f'{clip["broadcaster_display"]}-{clip["id"]}.mp4'
    subprocess.run(
        ["yt-dlp", "--quiet", "-o", str(out_path), clip["url"]],
        check=True,
    )
    downloaded.append(out_path)

# Optional: 60-sec slices from YouTube VODs
for vod in YT_CHANNELS:
    try:
        start = random.randint(60, 600)
        section = f"*{start}-{start+60}"
        out_file = DL_DIR / f"YT-{random.randint(100000,999999)}.mp4"
        subprocess.run(
            ["yt-dlp", "--quiet", "--download-sections", section, "-o", str(out_file), vod],
            check=True,
        )
        downloaded.append(out_file)
    except subprocess.CalledProcessError:
        print(f"⚠️  Failed YT slice: {vod}")

# ─────────────────────────────
# 6.  Decode key & authenticate Drive
# ─────────────────────────────
key_bytes = base64.b64decode(os.environ["GDRIVE_KEY_B64"])
key_path  = WORKDIR / "sa.json"
key_path.write_bytes(key_bytes)

gauth = GoogleAuth()
gauth.credentials = ServiceAccountCredentials.from_json_keyfile_name(
    key_path, scopes=["https://www.googleapis.com/auth/drive"]
)
drive = GoogleDrive(gauth)

root_id    = ensure_folder(drive, TARGET_ROOT)
inbound_id = ensure_folder(drive, TARGET_INBOUND, root_id)
today_id   = ensure_folder(drive, today_str, inbound_id)

# ─────────────────────────────
# 7.  Upload files
# ─────────────────────────────
print("⬆️  Uploading to Drive…")
for fpath in downloaded:
    file_obj = drive.CreateFile({"title": fpath.name, "parents": [{"id": today_id}]})
    file_obj.SetContentFile(str(fpath))
    file_obj.Upload()

print(f"🎉  Done – {len(downloaded)} file(s) uploaded.")
