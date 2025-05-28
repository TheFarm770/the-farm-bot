'''
main.py — Local Downloads Edition

This script:
1. Authenticates to Twitch and fetches the top 10 streamers by viewer count.
2. Retrieves the latest 10 clips per streamer.
3. Downloads each clip (and optional 60 s YouTube VOD snippets) directly into your local Downloads folder under:
   `~/Downloads/The Farm/Inbound/YYYY-MM-DD`.

Usage:
- Ensure `TWITCH_CLIENT_ID` and `TWITCH_CLIENT_SECRET` are exported in your shell.
- Run:
  ```bash
  python3 main.py
  ```

'''
import os
import datetime
import pathlib
import subprocess
import random
import requests


def main():
    # 0. Configuration
    HOME = pathlib.Path.home()
    DOWNLOADS_ROOT = HOME / "Downloads"
    TARGET_ROOT    = "The Farm"
    TARGET_INBOUND = "Inbound"
    today_str      = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")

    # Prepare local download directory
    download_dir = DOWNLOADS_ROOT / TARGET_ROOT / TARGET_INBOUND / today_str
    download_dir.mkdir(parents=True, exist_ok=True)
    print(f"📁 Downloading clips into: {download_dir}")

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

    # 2. Fetch top 10 live streamers
    print("🔍 Fetching top 10 live Twitch streamers…")
    streams = requests.get(
        "https://api.twitch.tv/helix/streams",
        headers=headers,
        params={"first": 10}
    ).json().get('data', [])
    creators = [{'id': s['user_id'], 'display': s['user_name']} for s in streams]
    print(f"✅ Streamers: {[c['display'] for c in creators]}")

    # 3. Fetch latest 10 clips per streamer
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

    # 4. Download all clips
    for clip in clips:
        out_path = download_dir / f"{clip['display']}-{clip['id']}.mp4"
        try:
            subprocess.run(['yt-dlp', '--quiet', '-o', str(out_path), clip['url']], check=True)
            print(f"   ✔️ Downloaded: {out_path.name}")
        except subprocess.CalledProcessError as e:
            print(f"   ⚠️ Failed: {clip['url']} → {e}")

    # Optional: Download YouTube VOD snippets
    yt_channels = [u.strip() for u in os.getenv("YT_CHANNELS", "").split(",") if u]
    for vod in yt_channels:
        start = random.randint(60, 600)
        sec   = f"*{start}-{start+60}"
        out_path = download_dir / f"YT-{random.randint(100000,999999)}.mp4"
        try:
            subprocess.run(['yt-dlp', '--quiet', '--download-sections', sec, '-o', str(out_path), vod], check=True)
            print(f"   ✔️ YouTube slice: {out_path.name}")
        except subprocess.CalledProcessError as e:
            print(f"   ⚠️ YouTube slice failed for {vod} → {e}")

    print("🎉 All done. Videos are in your Downloads folder.")

if __name__ == '__main__':
    main()

