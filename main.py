'''
Farm Bot - Automated Clip Harvesting Pipeline

This script runs as a scheduled job (e.g., via GitHub Actions) to:
1. Authenticate to Twitch and fetch the top 10 creators by viewer count.
2. Retrieve the latest 10 clips for each of those creators.
3. Download each clip (and optional 60 s YouTube VOD snippets) using yt-dlp.
4. Authenticate to Google Drive via a Service Account.
5. Ensure a folder hierarchy in Drive: ROOT_FOLDER_ID → The Farm → Inbound → YYYY-MM-DD.
6. Upload all downloaded MP4s to the dated folder.

Sections:
0. Configuration (env-driven)
1. Twitch App Token
2. Working Directories
3. Drive-Folder Helper
4. Fetch Top Creators
5. Fetch Latest Clips
6. Download Clips
7. Drive Authentication & Upload

'''  
import os
import json
import base64
import random
import datetime
import subprocess
import tempfile
import pathlib
import requests

from pydrive2.auth import GoogleAuth, ServiceAccountCredentials
from pydrive2.drive import GoogleDrive


def main():
    # 0. Configuration (env-driven)
    YT_CHANNELS    = [u.strip() for u in os.getenv("YT_CHANNELS", "").split(",") if u]
    TARGET_ROOT    = "The Farm"
    TARGET_INBOUND = "Inbound"
    ROOT_FOLDER_ID = os.getenv("ROOT_FOLDER_ID") or "root"  # Fallback to 'root' if unset
    if os.getenv("ROOT_FOLDER_ID") is None:
        print("⚠️ ROOT_FOLDER_ID not set; defaulting to 'root'")
    today_str      = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")

    # 1. Twitch App Token
    print("🔑 Requesting Twitch app token…")
    token_res = requests.post(
        "https://id.twitch.tv/oauth2/token",
        params={
            "client_id": os.getenv("TWITCH_CLIENT_ID"),
            "client_secret": os.getenv("TWITCH_CLIENT_SECRET"),
            "grant_type": "client_credentials",
        }
    ).json()
    ACCESS_TOKEN = token_res.get("access_token")
    if not ACCESS_TOKEN:
        print(f"❌ Token error: {token_res}")
        return
    HEADERS = {
        "Client-ID": os.getenv("TWITCH_CLIENT_ID"),
        "Authorization": f"Bearer {ACCESS_TOKEN}"
    }

    # 2. Working Directories
    WORKDIR = pathlib.Path(tempfile.mkdtemp(prefix="farmbot_"))
    DL_DIR  = WORKDIR / "clips"
    DL_DIR.mkdir(parents=True, exist_ok=True)
    print(f"🗂️ Download directory: {DL_DIR}")

    # 3. Drive-Folder Helper
    def ensure_folder(drive, title, parent_id="root"):
        parent = parent_id or "root"
        query = (
            f"'{parent}' in parents and mimeType='application/vnd.google-apps.folder' "
            f"and title='{title}' and trashed=false"
        )
        try:
            items = drive.ListFile({'q': query}).GetList()
        except Exception as e:
            print(f"❌ Drive query failed: {e}\nQuery: {query}")
            raise
        if items:
            return items[0]['id']
        folder = drive.CreateFile({
            'title': title,
            'mimeType': 'application/vnd.google-apps.folder',
            'parents': [{'id': parent}]
        })
        folder.Upload()
        return folder['id']

    # 4. Fetch Top Creators
    print("🔍 Fetching top 10 Twitch creators by viewer count…")
    streams_res = requests.get(
        "https://api.twitch.tv/helix/streams",
        headers=HEADERS,
        params={"first": 10}
    ).json()
    streams = streams_res.get('data', [])
    creators = [{'id': s['user_id'], 'display': s['user_name']} for s in streams]
    print(f"✅ Found {len(creators)} creators: {[c['display'] for c in creators]}")

    # 5. Fetch Latest Clips
    clips = []
    for creator in creators:
        print(f"🔍 Fetching latest 10 clips for {creator['display']}…")
        clip_data = requests.get(
            "https://api.twitch.tv/helix/clips",
            headers=HEADERS,
            params={
                "broadcaster_id": creator['id'],
                "first": 10
            }
        ).json().get('data', [])
        clip_data = sorted(clip_data, key=lambda x: x['created_at'], reverse=True)[:10]
        print(f"   Retrieved {len(clip_data)} clips")
        for clip in clip_data:
            clip['broadcaster_display'] = creator['display']
            clips.append(clip)
    print(f"✅ Total clips to download: {len(clips)}")

    if not clips:
        print("❌ No clips found. Exiting.")
        return

    # 6. Download Clips
    downloaded = []
    for clip in clips:
        out = DL_DIR / f"{clip['broadcaster_display']}-{clip['id']}.mp4"
        try:
            subprocess.run(['yt-dlp', '--quiet', '-o', str(out), clip['url']], check=True)
            downloaded.append(out)
        except subprocess.CalledProcessError as e:
            print(f"⚠️ Download failed for {clip['id']}: {e}")
    for vod in YT_CHANNELS:
        try:
            start = random.randint(60, 600)
            sec = f"*{start}-{start+60}"
            out = DL_DIR / f"YT-{random.randint(100000,999999)}.mp4"
            subprocess.run(['yt-dlp', '--quiet', '--download-sections', sec, '-o', str(out), vod], check=True)
            downloaded.append(out)
        except Exception as e:
            print(f"⚠️ YouTube slice failed for {vod}: {e}")

    # 7. Drive Authentication & Upload
    print("🔑 Authenticating to Google Drive…")
    try:
        key_dict = json.loads(base64.b64decode(os.getenv('GDRIVE_KEY_B64')))
        creds = ServiceAccountCredentials.from_json_keyfile_dict(
            key_dict,
            scopes=['https://www.googleapis.com/auth/drive']
        )
    except Exception as e:
        print(f"❌ GDrive auth failed: {e}")
        return
    gauth = GoogleAuth()
    gauth.credentials = creds
    drive = GoogleDrive(gauth)

    farm_id    = ensure_folder(drive, TARGET_ROOT, parent_id=ROOT_FOLDER_ID)
    inbound_id = ensure_folder(drive, TARGET_INBOUND, parent_id=farm_id)
    date_id    = ensure_folder(drive, today_str, parent_id=inbound_id)

    print(f"📤 Uploading {len(downloaded)} files to folder ID {date_id}")
    for path in downloaded:
        f = drive.CreateFile({'title': path.name, 'parents': [{'id': date_id}]})
        f.SetContentFile(str(path))
        f.Upload()
        print(f"   Uploaded: {path.name}")

    print("🎉 Done.")

if __name__ == '__main__':
    main()
