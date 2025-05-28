'''
main.py — USB Copy Edition

This script:
1. Authenticates to Twitch and fetches the top 10 streamers by viewer count.
2. Retrieves the latest 10 clips per streamer.
3. Downloads clips via yt-dlp.
4. Saves all MP4s to a connected pen drive under `The Farm/Inbound/YYYY-MM-DD`.

Usage:
- Ensure your USB is mounted, then set `PEN_DRIVE_PATH` to its mount point (e.g., `/Volumes/The Farm`).
- From the project folder, run: `python3 main.py`.

'''
import os
import datetime
import tempfile
import pathlib
import subprocess
import random
import shutil
import requests


def main():
    # 0. Configuration
    USB_MOUNT_PATH = os.getenv("PEN_DRIVE_PATH", "/Volumes/The Farm")
    TARGET_ROOT    = "The Farm"
    TARGET_INBOUND = "Inbound"
    today_str      = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")

    # Validate pen drive mount
    usb_root = pathlib.Path(USB_MOUNT_PATH)
    if not usb_root.is_dir():
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
    access_token = token_res.get("access_token")
    if not access_token:
        print(f"❌ Twitch token error: {token_res}")
        return
    headers = {
        "Client-ID": os.getenv("TWITCH_CLIENT_ID"),
        "Authorization": f"Bearer {access_token}"
    }

    # 2. Temporary download directory
    workdir = pathlib.Path(tempfile.mkdtemp(prefix="farmbot_"))
    dl_dir = workdir / "clips"
    dl_dir.mkdir(parents=True, exist_ok=True)
    print(f"🗂️ Downloading clips into: {dl_dir}")

    # 3. Fetch top 10 live streamers
    print("🔍 Fetching top 10 live Twitch streamers…")
    streams = requests.get(
        "https://api.twitch.tv/helix/streams",
        headers=headers,
        params={"first": 10}
    ).json().get('data', [])
    creators = [{'id': s['user_id'], 'display': s['user_name']} for s in streams]
    print(f"✅ Streamers: {[c['display'] for c in creators]}")

    # 4. Fetch latest 10 clips per streamer
    clips = []
    for c in creators:
        print(f"🔍 Fetching clips for {c['display']}…")
        clip_data = requests.get(
            "https://api.twitch.tv/helix/clips",
            headers=headers,
            params={"broadcaster_id": c['id'], "first": 10}
        ).json().get('data', [])
        sorted_clips = sorted(clip_data, key=lambda x: x['created_at'], reverse=True)[:10]
        clips.extend(({
            'url': clip['url'],
            'display': c['display'],
            'id': clip['id']
        } for clip in sorted_clips))
        print(f"   → {len(sorted_clips)} clips queued")

    if not clips:
        print("❌ No clips found. Exiting.")
        return
    print(f"✅ Total clips: {len(clips)}")

    # 5. Download all clips
    downloaded = []
    for clip in clips:
        out_path = dl_dir / f"{clip['display']}-{clip['id']}.mp4"
        try:
            subprocess.run(['yt-dlp', '--quiet', '-o', str(out_path), clip['url']], check=True)
            downloaded.append(out_path)
        except subprocess.CalledProcessError as e:
            print(f"⚠️ Failed: {clip['url']} → {e}")

    # 6. Copy to pen drive
    target_folder = usb_root / TARGET_ROOT / TARGET_INBOUND / today_str
    target_folder.mkdir(parents=True, exist_ok=True)
    print(f"📁 Saving to USB: {target_folder}")
    for f in downloaded:
        try:
            dest = target_folder / f.name
            shutil.copy2(f, dest)
            print(f"   ✔️ {f.name}")
        except Exception as e:
            print(f"   ⚠️ Copy failed: {f.name} → {e}")

    # 7. Cleanup
    shutil.rmtree(workdir)
    print("🎉 Done. Clips are on your pen drive.")

if __name__ == '__main__':
    main()
