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

TARGET_ROOT    = "The Farm"
TARGET_INBOUND = "Inbound"
ROOT_FOLDER_ID = os.getenv("ROOT_FOLDER_ID")   # optional override

today_str = datetime.datetime.now(datetime.timezone.utc).strftime("%y-%m-%d")

# ─────────────────────────────
# 1.  Twitch app token
# ─────────────────────────────
print("🔑  Requesting Twitch app token…")
token = requests.post(
    "https://id.twitch.tv/oauth2/token",
    params={
        "client_id": os.environ["TWITCH_CLIENT_ID"],
        "client_secret": os.environ["TWITCH_CLIENT_SECRET"],
        "grant_type": "client_credentials",
    },
    timeout=15,
).json()["access_token"]

HEADERS = {
    "Client-ID": os.environ["TWITCH_CLIENT_ID"],
    "Authorization": f"Bearer {token}",
}

# ─────────────────────────────
# 2.  Temp working dirs
# ─────────────────────────────
WORKDIR = pathlib.Path(tempfile.mkdtemp(prefix="farmbot_"))
DL_DIR  = WORKDIR / "clips"
DL_DIR.mkdir(parents=True, exist_ok=True)

# ─────────────────────────────
# 3.  Drive helper
# ─────────────────────────────
def ensure_folder(drive, title, parent_id="root"):
    q = (
        f"'{parent_id}' in parents and "
        f"mimeType='application/vnd.google-apps.folder' and "
        f"title='{title}' and trashed=false"
    )
    found = drive.ListFile({"q": q}).GetList()
    if found:
        return found[0]["id"]
    f = drive.CreateFile(
        {
            "title": title,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [{"id": parent_id}],
        }
    )
    f.Upload()
    return f["id"]

# ─────────────────────────────
# 4.  Fetch clip metadata
# ─────────────────────────────
print("🔍  Fetching clip metadata…")
utc_now   = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
utc_start = utc_now - datetime.timedelta(days=1)
start_iso = utc_start.isoformat().replace("+00:00", "Z")
end_iso   = utc_now.isoformat().replace("+00:00", "Z")

clips = []
for login in CREATORS:
    u = requests.get(
        "https://api.twitch.tv/helix/users",
        headers=HEADERS,
        params={"login": login},
        timeout=10,
    ).json()
    if not u["data"]:
        print(f"⚠️  No such user: {login}")
        continue
    uid      = u["data"][0]["id"]
    display  = u["data"][0]["display_name"]

    resp = requests.get(
        "https://api.twitch.tv/helix/clips",
        headers=HEADERS,
        params={
            "broadcaster_id": uid,
            "first": 20,
            "started_at": start_iso,
            "ended_at": end_iso,
        },
        timeout=10,
    ).json()

    for c in resp.get("data", []):
        if c["view_count"] >= VIEW_THRESHOLD:
            c["broadcaster_display"] = display
            clips.append(c)

clips = sorted(clips, key=lambda x: x["view_count"], reverse=True)[:CLIP_LIMIT]
print(f"✅  Selected {len(clips)} clips.")

# ─────────────────────────────
# 5.  Download clips with yt-dlp
# ─────────────────────────────
downloaded = []
for c in clips:
    out = DL_DIR / f'{c["broadcaster_display"]}-{c["id"]}.mp4'
    subprocess.run(["yt-dlp", "--quiet", "-o", str(out), c["url"]], check=True)
    downloaded.append(out)

# Optional: YouTube slices
for vod in YT_CHANNELS:
    try:
        s  = random.randint(60, 600)
        sec = f"*{s}-{s+60}"
        out = DL_DIR / f"YT-{random.randint(100000,999999)}.mp4"
        subprocess.run(
            ["yt-dlp", "--quiet", "--download-sections", sec, "-o", str(out), vod],
            check=True,
        )
        downloaded.append(out)
    except subprocess.CalledProcessError:
        print(f"⚠️  Failed YT slice: {vod}")

# ─────────────────────────────
# 6.  Drive auth
# ─────────────────────────────
key = base64.b64decode(os.environ["GDRIVE_KEY_B64"])
key_path = WORKDIR / "sa.json"
key_path.write_bytes(key)

gauth = GoogleAuth()
gauth.credentials = ServiceAccountCredentials.from_json_keyfile_name(
    key_path, scopes=["https://www.googleapis.com/auth/drive"]
)
drive = GoogleDrive(gauth)

# choose root folder
root_id = ROOT_FOLDER_ID if ROOT_FOLDER_ID else ensure_folder(drive, TARGET_ROOT)
inb_id  = ensure_folder(drive, TARGET_INBOUND, root_id)
day_id  = ensure_folder(drive, today_str, inb_id)

# ─────────────────────────────
# 7.  Upload
# ─────────────────────────────
print("⬆️  Uploading to Drive…")
for f in downloaded:
    fobj = drive.CreateFile({"title": f.name, "parents": [{"id": day_id}]})
    fobj.SetContentFile(str(f))
    fobj.Upload()

print(f"🎉  Done – {len(downloaded)} file(s) uploaded.")
