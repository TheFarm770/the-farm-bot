import os, json, base64, random, datetime, subprocess, tempfile, pathlib, requests
from pydrive2.auth import ServiceAccountCredentials
from pydrive2.drive import GoogleDrive

# ─────────────────────────────
# 0.  Env-driven configuration
# ─────────────────────────────
CREATORS       = [c.strip() for c in os.getenv("CREATORS", "").split(",") if c.strip()]
CLIP_LIMIT     = int(os.getenv("CLIP_LIMIT", 12))
VIEW_THRESHOLD = int(os.getenv("VIEW_THRESHOLD", 800))
YT_CHANNELS    = [u.strip() for u in os.getenv("YT_CHANNELS", "").split(",") if u.strip()]

TARGET_ROOT    = "The Farm"        # top-level folder in Drive
TARGET_INBOUND = "Inbound"         # subfolder under TARGET_ROOT

today_str = datetime.datetime.utcnow().strftime("%y-%m-%d")

# ─────────────────────────────
# 1.  Get an **app token** from Twitch
# ─────────────────────────────
print("🔑  Requesting Twitch app token…")
token_res = requests.post(
    "https://id.twitch.tv/oauth2/token",
    params={
        "client_id": os.environ["TWITCH_CLIENT_ID"],
        "client_secret": os.environ["TWITCH_CLIENT_SECRET"],
        "grant_type": "client_credentials",
    },
    timeout=10,
).json()

ACCESS_TOKEN = token_res["access_token"]
HEADERS = {
    "Client-ID": os.environ["TWITCH_CLIENT_ID"],
    "Authorization": f"Bearer {ACCESS_TOKEN}",
}

# ─────────────────────────────
# 2.  Build working dirs
# ─────────────────────────────
WORKDIR = pathlib.Path(tempfile.mkdtemp(prefix="farmbot_"))
DL_DIR  = WORKDIR / "clips"
DL_DIR.mkdir(parents=True, exist_ok=True)

# ─────────────────────────────
# 3.  Helper – ensure Drive folder exists
# ─────────────────────────────
def ensure_folder(drive, title, parent_id="root"):
    q = (
        f"'{parent_id}' in parents and "
        f"mimeType='application/vnd.google-apps.folder' and "
        f"title='{title}' and trashed=false"
    )
    res = drive.ListFile({"q": q}).GetList()
    if res:
        return res[0]["id"]
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
# 4.  Collect top clips
# ─────────────────────────────
print("🔍  Fetching clip metadata…")
UTC_NOW   = datetime.datetime.utcnow().replace(microsecond=0)
UTC_START = (UTC_NOW - datetime.timedelta(days=1))
start_iso = UTC_START.isoformat() + "Z"
end_iso   = UTC_NOW.isoformat() + "Z"

clips_meta = []
for login in CREATORS:
    # 4a – look up user-ID
    u_resp = requests.get(
        "https://api.twitch.tv/helix/users",
        headers=HEADERS,
        params={"login": login},
        timeout=10,
    ).json()
    if not u_resp["data"]:
        print(f"⚠️  No such user: {login}")
        continue
    user_id = u_resp["data"][0]["id"]
    display = u_resp["data"][0]["display_name"]

    # 4b – fetch clips in the last 24 h
    c_resp = requests.get(
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

    for clip in c_resp.get("data", []):
        if clip["view_count"] >= VIEW_THRESHOLD:
            clip["broadcaster_display"] = display
            clips_meta.append(clip)

# keep only the top N by view-count
clips_meta = sorted(clips_meta, key=lambda x: x["view_count"], reverse=True)[:CLIP_LIMIT]
print(f"✅  Selected {len(clips_meta)} clips.")

# ─────────────────────────────
# 5.  Download clips with yt-dlp
# ─────────────────────────────
downloaded = []
for clip in clips_meta:
    out_path = DL_DIR / f'{clip["broadcaster_display"]}-{clip["id"]}.mp4'
    subprocess.run(
        ["yt-dlp", "--quiet", "-f", "best", "-o", str(out_path), clip["url"]],
        check=True,
    )
    downloaded.append(out_path)

# (Optional) random 60-s slice from any YT URLs you listed
for vod_url in YT_CHANNELS:
    try:
        rand_start = random.randint(60, 600)
        section    = f"*{rand_start}-{rand_start + 60}"
        out_file   = DL_DIR / f"YT-{random.randint(100000,999999)}.mp4"
        subprocess.run(
            ["yt-dlp", "--quiet", "--download-sections", section,
             "-o", str(out_file), vod_url],
            check=True,
        )
        downloaded.append(out_file)
    except subprocess.CalledProcessError:
        print(f"⚠️  Failed YT slice: {vod_url}")

# ─────────────────────────────
# 6.  Decode Drive key + authenticate
# ─────────────────────────────
key_bytes = base64.b64decode(os.environ["GDRIVE_KEY_B64"])
key_path  = WORKDIR / "sa.json"
key_path.write_bytes(key_bytes)

creds = ServiceAccountCredentials.from_json_keyfile_name(
    key_path, scopes=["https://www.googleapis.com/auth/drive"]
)
drive = GoogleDrive(creds)

root_id    = ensure_folder(drive, TARGET_ROOT)         # The Farm
inbound_id = ensure_folder(drive, TARGET_INBOUND, root_id)
today_id   = ensure_folder(drive, today_str, inbound_id)

# ─────────────────────────────
# 7.  Upload everything
# ─────────────────────────────
print("⬆️  Uploading to Drive…")
for fpath in downloaded:
    file_obj = drive.CreateFile({"title": fpath.name, "parents": [{"id": today_id}]})
    file_obj.SetContentFile(str(fpath))
    file_obj.Upload()

print("🎉  Done –", len(downloaded), "clip(s) uploaded.")
