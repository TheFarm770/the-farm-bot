import os, json, random, datetime, subprocess, pathlib, tempfile
from twitchAPI.twitch import Twitch
from pydrive2.auth import ServiceAccountCredentials
from pydrive2.drive import GoogleDrive

# ---------- configuration ----------
CREATORS = os.getenv("CREATORS", "").split(",")          # env secret
CLIP_LIMIT = 12
VIEW_THRESHOLD = 800
YT_CHANNELS = os.getenv("YT_CHANNELS", "").split(",")    # optional
TARGET_FOLDER = "The Farm/Inbound"
today_str = datetime.datetime.utcnow().strftime("%y-%m-%d")
# -----------------------------------

WORKDIR = pathlib.Path(tempfile.mkdtemp())
DL_DIR  = WORKDIR / "clips"
DL_DIR.mkdir()

# ---------- 1. Fetch top Twitch clips ----------
twitch = Twitch(os.environ["TWITCH_CLIENT_ID"], os.environ["TWITCH_CLIENT_SECRET"])
twitch.authenticate_app([])
clips_meta = []
for channel in CREATORS:
    user = twitch.get_users(logins=[channel])["data"][0]
    resp = twitch.get_clips(broadcaster_id=user["id"], first=20)
    for c in resp["data"]:
        if c["view_count"] >= VIEW_THRESHOLD:
            clips_meta.append(c)
clips_meta = sorted(clips_meta, key=lambda c: c["view_count"], reverse=True)[:CLIP_LIMIT]

# ---------- 2. Download Twitch MP4 ----------
for c in clips_meta:
    out = DL_DIR / f'{c["broadcaster_name"]}-{c["id"]}.mp4'
    subprocess.run([
        "twitch", "download", "clip",
        "--id", c["id"],
        "-o", str(out)
    ], check=True)

# ---------- 3. Optional: slice random YT VODs ----------
if YT_CHANNELS:
    for url in YT_CHANNELS:
        out = DL_DIR / f'YT-{random.randint(1,999999)}.mp4'
        subprocess.run([
            "yt-dlp",
            "--max-filesize", "60M",
            "--no-playlist",
            "--download-sections", "*00:02:00-00:03:00",
            "-o", str(out),
            url
        ], check=True)

# ---------- 4. Upload to Drive ----------
key_path = WORKDIR / "sa.json"
key_path.write_bytes(
    json.loads(os.environ["GDRIVE_KEY_B64"].encode()).decode()
    if os.environ["GDRIVE_KEY_B64"].strip().startswith("{") else
    base64.b64decode(os.environ["GDRIVE_KEY_B64"])
)

creds = ServiceAccountCredentials.from_json_keyfile_name(
    key_path, scopes=['https://www.googleapis.com/auth/drive']
)
drive = GoogleDrive(creds)
# find or create dated folder
query = f"title = '{today_str}' and '{TARGET_FOLDER}' in parents and mimeType = 'application/vnd.google-apps.folder'"
file_list = drive.ListFile({'q': query}).GetList()
if file_list:
    day_folder_id = file_list[0]['id']
else:
    parent = drive.ListFile({'q': f"title = '{TARGET_FOLDER.split('/')[-1]}' and mimeType='application/vnd.google-apps.folder'"}).GetList()[0]
    folder = drive.CreateFile({'title': today_str,
                               'mimeType': 'application/vnd.google-apps.folder',
                               'parents': [{'id': parent['id']}]})
    folder.Upload()
    day_folder_id = folder['id']
# upload each clip
for mp4 in DL_DIR.glob("*.mp4"):
    f = drive.CreateFile({'title': mp4.name,
                          'parents': [{'id': day_folder_id}]})
    f.SetContentFile(str(mp4))
    f.Upload()

print("✅  Upload complete.")
