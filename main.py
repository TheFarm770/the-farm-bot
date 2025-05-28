'''
Farm Bot - Automated Clip Harvesting Pipeline (USB Edition)

This script runs as a scheduled job (e.g., via GitHub Actions) to:
1. Authenticate to Twitch and fetch the top 10 creators by viewer count.
2. Retrieve the latest 10 clips for each creator.
3. Download each clip (and optional 60 s YouTube VOD snippets) using yt-dlp.
4. Save all downloaded MP4s to a mounted USB drive (e.g., pen drive) instead of Google Drive.

Sections:
0. Configuration (env-driven)
1. Twitch App Token
2. Working Directories
3. Fetch Top Creators
4. Fetch Latest Clips
5. Download Clips
6. USB Drive Save

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
import shutil


def main():
    # 0. Configuration (env-driven)
    # Path where USB drive is mounted (e.g., '/media/usb' or '/mnt/usb')
    USB_MOUNT_PATH = os.getenv("PEN_DRIVE_PATH", "/mnt/usb")
    TARGET_ROOT    = "The Farm"
    TARGET_INBOUND = "Inbound"
    today_str      = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")

    # Validate USB mount
    usb_root = pathlib.Path(USB_MOUNT_PATH)
    if not usb_root.exists() or not usb_root.is_dir():
        print(f"❌ USB mount path not found: {USB_MOUNT_PATH}")
        return

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

    # 2. Working Directories (temporary)
    WORKDIR = pathlib.Path(tempfile.mkdtemp(prefix="farmbot_"))
    DL_DIR  = WORKDIR / "clips"
    DL_DIR.mkdir(parents=True, exist_ok=True)
    print(f"🗂️ Download directory: {DL_DIR}")

    # 3. Fetch Top 10 Creators
    print("🔍 Fetching top 10 Twitch creators by viewer count…")
    streams_res = requests.get(
        "https://api.twitch.tv/helix/streams",
        headers=HEADERS,
        params={"first": 10}
    ).json()
    streams = streams_res.get('data', [])
    creators = [{'id': s['user_id'], 'display': s['user_name']} for s in streams]
    print(f"✅ Found {len(creators)} creators: {[c['display'] for c in creators]}")

    # 4. Fetch Latest Clips
    clips = []
    for creator in creators:
        print(f"🔍 Fetching latest 10 clips for {creator['display']}…")
        clip_data = requests.get(
            "https://api.twitch.tv/helix/clips",
            headers=HEADERS,
            params={"broadcaster_id": creator['id'], "first": 10}
        ).json().get('data', [])
        # Sort by created_at to ensure latest
        clip_data = sorted(clip_data, key=lambda x: x['created_at'], reverse=True)[:10]
        print(f"   Retrieved {len(clip_data)} clips")
        for clip in clip_data:
            clip['broadcaster_display'] = creator['display']
            clips.append(clip)
    print(f"✅ Total clips to download: {len(clips)}")

    if not clips:
        print("❌ No clips found. Exiting.")
        return

    # 5. Download Clips
    downloaded = []
    for clip in clips:
        out = DL_DIR / f"{clip['broadcaster_display']}-{clip['id']}.mp4"
        try:
            subprocess.run(['yt-dlp', '--quiet', '-o', str(out), clip['url']], check=True)
            downloaded.append(out)
        except subprocess.CalledProcessError as e:
            print(f"⚠️ Download failed for {clip['id']}: {e}")
    # Optional YouTube VOD slices
    yt_channels = [u.strip() for u in os.getenv("YT_CHANNELS", "").split(",") if u]
    for vod in yt_channels:
        try:
            start = random.randint(60, 600)
            sec   = f"*{start}-{start+60}"
            out   = DL_DIR / f"YT-{random.randint(100000,999999)}.mp4"
            subprocess.run(['yt-dlp', '--quiet', '--download-sections', sec, '-o', str(out), vod], check=True)
            downloaded.append(out)
        except Exception as e:
            print(f"⚠️ YouTube slice failed for {vod}: {e}")

    # 6. Save to USB Drive
    target_folder = usb_root / TARGET_ROOT / TARGET_INBOUND / today_str
    target_folder.mkdir(parents=True, exist_ok=True)
    print(f"📁 Copying {len(downloaded)} files to {target_folder}")

    for file_path in downloaded:
        try:
            dest = target_folder / file_path.name
            shutil.copy2(file_path, dest)
            print(f"✔️ Copied: {file_path.name}")
        except Exception as e:
            print(f"⚠️ Failed to copy {file_path.name}: {e}")

    print("🎉 All clips saved to USB drive. Clean up local temp files.")

    # Cleanup temporary folder
    try:
        shutil.rmtree(WORKDIR)
    except Exception:
        pass

if __name__ == '__main__':
    main()
