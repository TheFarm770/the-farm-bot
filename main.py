import os, json, base64, random, datetime, subprocess, tempfile, pathlib
from twitchAPI.twitch import Twitch
from pydrive2.auth import ServiceAccountCredentials
from pydrive2.drive import GoogleDrive

# ─────────────────────────────
# Configuration (env-driven)
# ─────────────────────────────
CREATORS       = [c.strip() for c in os.getenv("CREATORS", "").split(",") if c.strip()]
CLIP_LIMIT     = int(os.getenv("CLIP_LIMIT", 12))
VIEW_THRESHOLD = int(os.getenv("VIEW_THRESHOLD", 800))

YT_CHANNELS    = [u.strip() for u in os.getenv("YT_CHANNELS", "").split(",") if u.strip()]
TARGET_ROOT    = "The Farm"
TARGET_INBOUND = "Inbound"

today_str = datetime.datetime.utcnow().strftime("%y-%m-%d")

# ─────────────────────────────
# Working directory
# ─────────────────────────────
WORKDIR = pathlib.Path(tempfile.mkdtemp(prefix="farmbot_"))
DL_DIR  = WORKDIR / "clips"
DL_DIR.mkdir(parents=True, exist_ok=True)

# ─────────────────────────────
# 1.  Fetch top clips from Twitch
# ─────────────────────────────
print("🔍  Fetching clips metadata…")
twitch = Twitch(os.environ["TWITCH_CLIENT_ID"], os.environ["TWITCH_CLIENT_SECRET"])
twitch.authenticate_app([])

clips_meta = []
for channel in CREATORS:
    user = twitch.get_users(logins=[channel])["data"][0]
    for c in twitch.get_clips(broadcaster_id=user["id"], first=20)["data"]:
        if c["view_count"] >= VIEW_THRESHOLD:
            clips_meta.append(c)

clips_meta = sorted(clips_meta, key=lambda x: x["view_count"], reverse=True)[:CLIP_LIMIT]
print(f"✅  Selected {len(clips_meta)} clips.")

# ─────────────────────────────
# 2.  Download each clip with yt-dlp
# ─────────────────────────────
def download_clip(meta):
    out_path = DL_DIR / f'{meta["broadcaster_name"]}-{meta["id"]}.mp4'
    subprocess.run(
        ["yt-dlp", "-f", "best", "-o", str(out_path), meta["url"]],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )
    return out_path

downloaded_files = [download_clip(m) for m in clips_meta]

# ─────────────────────────────
# 3.  (Optional) cut 60-s chunks from YT VODs
# ─────────────────────────────
def slice_youtube(url):
    rand_start = random.randint(60, 600)          # somewhere 1–10 min in
    section    = f"*{rand_start}-{rand_start+60}"
    out_file   = DL_DIR / f"YT-{random.randint(100000,999999)}.mp4"
    subprocess.run(
        ["yt-dlp", "--download-sections", section, "-o", str(out_file), url],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )
    return out_file

for vod_url in YT_CHANNELS:
    try:
        downloaded_files.append(slice_youtube(vod_url))
    except subprocess.CalledProcessError:
        print(f"⚠️  Failed to slice {vod_url}")

# ─────────────────────────────
# 4.  Decode Drive key & auth
# ─────────────────────────────
key_bytes = base64.b64decode(os.environ["GDRIVE_KEY_B64"])
key_path  = WORKDIR / "sa.json"
key_path.write_bytes(key_bytes)

creds = ServiceAccountCredentials.from_json_keyfile_name(
    key_path, scopes=["https://www.googleapis.com/auth/drive"]
)
drive = GoogleDrive(creds)

# ─────────────────────────────
# 5.  Helper: ensure a folder exists, return its ID
# ─────────────────────────────
def ensure_folder(title, parent_id="root"):
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

root_id     = ensure_folder(TARGET_ROOT)                  # The Farm
inbound_id  = ensure_folder(TARGET_INBOUND, root_id)      # Inbound
today_id    = ensure_folder(today_str, inbound_id)        # YY-MM-DD

# ─────────────────────────────
# 6.  Upload everything
# ─────────────────────────────
print("⬆️  Uploading to Drive…")
for file_path in downloaded_files:
    f = drive.CreateFile({"title": file_path.name, "parents": [{"id": today_id}]})
    f.SetContentFile(str(file_path))
    f.Upload()

print("✅  All done – uploaded", len(downloaded_files), "file(s).")
