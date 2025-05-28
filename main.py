'''
Farm Bot - Automated Clip Harvesting Pipeline

This script runs as a scheduled job (e.g., via GitHub Actions) to:
1. Authenticate to Twitch and fetch top clips (≥ VIEW_THRESHOLD) from specified creators over the past 24 h.
2. Download each clip (and optional 60 s YouTube VOD snippets) using yt-dlp.
3. Authenticate to Google Drive via a Service Account.
4. Ensure a folder hierarchy in Drive: ROOT_FOLDER_ID → The Farm → Inbound → YY‑MM‑DD.
5. Upload all downloaded MP4s to the dated folder.

Sections:
0. Configuration (env-driven)
1. Twitch App Token
2. Working Directories
3. Drive-Folder Helper
4. Fetch Clip Metadata
5. Download Clips
6. Drive Authentication & Upload

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
    CREATORS       = [c.strip() for c in os.getenv("CREATORS", "").split(",") if c]
    CLIP_LIMIT     = int(os.getenv("CLIP_LIMIT", 12))
    VIEW_THRESHOLD = int(os.getenv("VIEW_THRESHOLD", 800))
    YT_CHANNELS    = [u.strip() for u in os.getenv("YT_CHANNELS", "").split(",") if u]
    TARGET_ROOT    = "The Farm"
    TARGET_INBOUND = "Inbound"
    ROOT_FOLDER_ID = os.getenv("ROOT_FOLDER_ID")  # Must be shared with the service account
    today_str      = datetime.datetime.now(datetime.timezone.utc).strftime("%y-%m-%d")

    # 1. Twitch App Token
    print("🔑 Requesting Twitch app token…")
    res = requests.post(
        "https://id.twitch.tv/oauth2/token",
        params={
          "client_id": os.environ["TWITCH_CLIENT_ID"],
          "client_secret": os.environ["TWITCH_CLIENT_SECRET"],
          "grant_type": "client_credentials",
        }
    ).json()
    ACCESS_TOKEN = res.get("access_token")
    if not ACCESS_TOKEN:
        raise RuntimeError(f"Failed to get Twitch token: {res}")
    HEADERS = {
      "Client-ID": os.environ["TWITCH_CLIENT_ID"],
      "Authorization": f"Bearer {ACCESS_TOKEN}"
    }

    # 2. Working Directories
    WORKDIR = pathlib.Path(tempfile.mkdtemp(prefix="farmbot_"))
    DL_DIR  = WORKDIR / "clips"
    DL_DIR.mkdir(parents=True, exist_ok=True)

    # 3. Drive-Folder Helper
    def ensure_folder(drive, title, parent_id="root"):
        query = (
            f"'{parent_id}' in parents and mimeType='application/vnd.google-apps.folder' "
            "and name='{title}' and trashed=false"
        )
        found = drive.ListFile({'q': query}).GetList()
        if found:
            return found[0]['id']
        folder = drive.CreateFile({
            'name': title,
            'mimeType': 'application/vnd.google-apps.folder',
            'parents': [{'id': parent_id}]
        })
        folder.Upload()
        return folder['id']

    # 4. Fetch Clip Metadata
    print("🔍 Fetching clip metadata…")
    now_utc   = datetime.datetime.now(datetime.timezone.utc)
    start_utc = (now_utc - datetime.timedelta(days=1)).isoformat() + 'Z'
    end_utc   = now_utc.isoformat() + 'Z'

    clips = []
    for login in CREATORS:
        resp = requests.get(
            "https://api.twitch.tv/helix/users",
            headers=HEADERS,
            params={"login": login}
        ).json()
        data = resp.get("data", [])
        if not data:
            print(f"⚠️ No such user: {login}")
            continue
        uid = data[0]['id']
        display = data[0]['display_name']
        clip_resp = requests.get(
            "https://api.twitch.tv/helix/clips",
            headers=HEADERS,
            params={
              "broadcaster_id": uid,
              "first": 20,
              "started_at": start_utc,
              "ended_at": end_utc
            }
        ).json().get("data", [])
        for clip in clip_resp:
            if clip.get('view_count', 0) >= VIEW_THRESHOLD:
                clip['broadcaster_display'] = display
                clips.append(clip)
    clips = sorted(clips, key=lambda x: x['view_count'], reverse=True)[:CLIP_LIMIT]
    print(f"✅ Selected {len(clips)} clips.")

    # 5. Download Clips
    downloaded = []
    for clip in clips:
        out = DL_DIR / f"{clip['broadcaster_display']}-{clip['id']}.mp4"
        try:
            subprocess.run([
                'yt-dlp', '--quiet', '-o', str(out), clip['url']
            ], check=True)
            downloaded.append(out)
        except subprocess.CalledProcessError:
            print(f"⚠️ Failed to download clip: {clip['url']}")
    for vod in YT_CHANNELS:
        try:
            start = random.randint(60, 600)
            sec   = f"*{start}-{start+60}"
            out   = DL_DIR / f"YT-{random.randint(100000,999999)}.mp4"
            subprocess.run([
              'yt-dlp', '--quiet', '--download-sections', sec,
              '-o', str(out), vod
            ], check=True)
            downloaded.append(out)
        except Exception:
            print(f"⚠️ Failed slice: {vod}")

    if not downloaded:
        print("No files to upload. Exiting.")
        return

    # 6. Drive Authentication & Upload
    print("🔑 Authenticating to Google Drive…")
    key_bytes = base64.b64decode(os.environ['GDRIVE_KEY_B64'])
    key_dict  = json.loads(key_bytes)
    creds = ServiceAccountCredentials.from_json_keyfile_dict(
        key_dict,
        scopes=['https://www.googleapis.com/auth/drive']
    )
    gauth = GoogleAuth()
    gauth.credentials = creds
    drive = GoogleDrive(gauth)

    # Ensure folder tree
    farm_id     = ensure_folder(drive, TARGET_ROOT, parent_id=ROOT_FOLDER_ID)
    inbound_id  = ensure_folder(drive, TARGET_INBOUND, parent_id=farm_id)
    date_id     = ensure_folder(drive, today_str, parent_id=inbound_id)

    # Upload files
    print(f"📤 Uploading {len(downloaded)} files to Drive folder ID: {date_id}")
    for file_path in downloaded:
        f = drive.CreateFile({
            'name': file_path.name,
            'parents': [{'id': date_id}]
        })
        f.SetContentFile(str(file_path))
        f.Upload()
        print(f"Uploaded {file_path.name} – Drive ID: {f['id']}")

    print("🎉 All done. Clean up and exit.")


if __name__ == '__main__':
    main()
