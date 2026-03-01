"""
SoundWave Backend - Railway Ready
Uses YouTube Data API v3 for search (reliable, no yt-dlp for search)
Uses yt-dlp only for downloads where ffmpeg is available
"""

from flask import Flask, jsonify, request, send_file, redirect
from flask_cors import CORS
import subprocess
import json
import os
import sys
import re
from pathlib import Path
import urllib.request
import urllib.parse

app = Flask(__name__)
CORS(app)

CACHE_DIR = Path("./audio_cache")
CACHE_DIR.mkdir(exist_ok=True)
search_cache = {}

# ── Find yt-dlp ──
YTDLP_CMD = None

def find_ytdlp():
    global YTDLP_CMD
    for candidate in [
        [sys.executable, "-m", "yt_dlp"],
        ["yt-dlp"],
        ["yt_dlp"],
    ]:
        try:
            r = subprocess.run(candidate + ["--version"], capture_output=True, text=True, timeout=10)
            if r.returncode == 0:
                YTDLP_CMD = candidate
                print(f"✅ yt-dlp: {candidate}")
                return
        except Exception:
            continue
    print("❌ yt-dlp not found")

find_ytdlp()

def run_ytdlp(args, timeout=90):
    if not YTDLP_CMD:
        class F:
            returncode=1; stdout=""; stderr="yt-dlp not found"
        return F()
    try:
        return subprocess.run(YTDLP_CMD + args, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        class T:
            returncode=1; stdout=""; stderr="Timeout"
        return T()
    except Exception as e:
        class E:
            returncode=1; stdout=""; stderr=str(e)
        return E()

# ── Scrape YouTube search (no API key needed) ──
def youtube_search(query, limit=15):
    """Search YouTube by scraping - no API key needed."""
    try:
        encoded = urllib.parse.quote(query)
        url = f"https://www.youtube.com/results?search_query={encoded}"
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode('utf-8')

        # Extract video IDs and titles from YouTube HTML
        import re
        # Find video data in the page
        pattern = r'"videoId":"([^"]{11})"[^}]*?"title":\{"runs":\[\{"text":"([^"]+)"'
        matches = re.findall(pattern, html)

        # Also try simpler pattern
        if not matches:
            vid_pattern = r'watch\?v=([a-zA-Z0-9_-]{11})'
            vids = list(dict.fromkeys(re.findall(vid_pattern, html)))[:limit]
            matches = [(v, f"Video {i+1}") for i, v in enumerate(vids)]

        songs = []
        seen = set()
        for vid_id, title in matches[:limit]:
            if vid_id in seen:
                continue
            seen.add(vid_id)
            songs.append({
                "id": vid_id,
                "title": title,
                "artist": "YouTube",
                "duration": 0,
                "duration_str": "?",
                "thumbnail": f"https://img.youtube.com/vi/{vid_id}/mqdefault.jpg",
                "url": f"https://www.youtube.com/watch?v={vid_id}",
                "embed_url": f"https://www.youtube.com/embed/{vid_id}"
            })

        return songs
    except Exception as e:
        print(f"Search scrape failed: {e}")
        return []

def ytdlp_search(query, limit=15):
    """Search using yt-dlp as fallback."""
    result = run_ytdlp([
        f"ytsearch{limit}:{query}",
        "--dump-json", "--no-playlist", "--skip-download",
        "--quiet", "--no-warnings",
        "--socket-timeout", "30",
    ], timeout=60)

    songs = []
    for line in result.stdout.strip().split("\n"):
        if not line:
            continue
        try:
            d = json.loads(line)
            duration = d.get("duration", 0) or 0
            if duration > 600:
                continue
            vid_id = d.get("id")
            songs.append({
                "id": vid_id,
                "title": d.get("title", "Unknown"),
                "artist": d.get("uploader", "Unknown"),
                "duration": duration,
                "duration_str": f"{int(duration//60)}:{int(duration%60):02d}" if duration else "?",
                "thumbnail": f"https://img.youtube.com/vi/{vid_id}/mqdefault.jpg",
                "url": f"https://www.youtube.com/watch?v={vid_id}",
                "embed_url": f"https://www.youtube.com/embed/{vid_id}"
            })
        except:
            continue
    return songs

# ── ROUTES ──

@app.route("/")
def index():
    return jsonify({"status": "SoundWave API running", "ytdlp": YTDLP_CMD is not None})

@app.route("/api/health")
def health():
    return jsonify({
        "status": "ok",
        "ytdlp": YTDLP_CMD is not None,
        "cache_size": len(list(CACHE_DIR.glob("*.mp3")))
    })

@app.route("/api/search")
def search():
    query = request.args.get("q", "").strip()
    limit = int(request.args.get("limit", 15))
    if not query:
        return jsonify({"error": "No query"}), 400

    cache_key = f"{query}_{limit}"
    if cache_key in search_cache:
        return jsonify(search_cache[cache_key])

    # Try yt-dlp search first, fall back to scraping
    songs = ytdlp_search(query, limit)
    if not songs:
        songs = youtube_search(query, limit)

    resp = {"results": songs, "query": query}
    if songs:
        search_cache[cache_key] = resp
    return jsonify(resp)

@app.route("/api/stream/<video_id>")
def stream(video_id):
    """Get fresh stream URL - called right before playing."""
    result = run_ytdlp([
        f"https://www.youtube.com/watch?v={video_id}",
        "--get-url",
        "-f", "bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best",
        "--no-playlist", "--quiet", "--no-warnings",
        "--socket-timeout", "30",
    ], timeout=60)

    if result.returncode != 0 or not result.stdout.strip():
        return jsonify({"error": "Could not get stream", "detail": result.stderr}), 500

    urls = result.stdout.strip().split("\n")
    return jsonify({
        "stream_url": urls[0],
        "video_id": video_id,
        "embed_url": f"https://www.youtube.com/embed/{video_id}?autoplay=1"
    })

@app.route("/api/download/<video_id>")
def download(video_id):
    """Download as MP3."""
    cache_file = CACHE_DIR / f"{video_id}.mp3"

    if cache_file.exists() and cache_file.stat().st_size > 10000:
        return send_file(cache_file, mimetype="audio/mpeg",
                         as_attachment=True, download_name=f"{video_id}.mp3")

    # Get title first
    title_result = run_ytdlp([
        f"https://www.youtube.com/watch?v={video_id}",
        "--get-title", "--no-playlist", "--quiet"
    ], timeout=30)
    title = re.sub(r'[^\w\-_\. ]', '_', title_result.stdout.strip() or video_id)

    output_tmpl = str(CACHE_DIR / f"{video_id}.%(ext)s")

    result = run_ytdlp([
        f"https://www.youtube.com/watch?v={video_id}",
        "-x",
        "--audio-format", "mp3",
        "--audio-quality", "192K",
        "-o", output_tmpl,
        "--no-playlist", "--quiet", "--no-warnings",
        "--socket-timeout", "60",
    ], timeout=300)

    if cache_file.exists() and cache_file.stat().st_size > 10000:
        return send_file(cache_file, mimetype="audio/mpeg",
                         as_attachment=True, download_name=f"{title}.mp3")

    # ffmpeg not available - try getting best audio directly
    stream_result = run_ytdlp([
        f"https://www.youtube.com/watch?v={video_id}",
        "--get-url", "-f", "bestaudio/best",
        "--no-playlist", "--quiet", "--no-warnings",
    ], timeout=60)

    if stream_result.returncode == 0 and stream_result.stdout.strip():
        stream_url = stream_result.stdout.strip().split("\n")[0]
        return redirect(stream_url)

    return jsonify({"error": "Download failed - ffmpeg may not be available", "detail": result.stderr}), 500

@app.route("/api/trending")
def trending():
    songs = ytdlp_search("top hits 2024 official audio", 15)
    if not songs:
        songs = youtube_search("top hits 2024", 15)
    return jsonify({"trending": songs})

@app.route("/api/playlist/generate", methods=["POST"])
def generate_playlist():
    data = request.json or {}
    seeds = data.get("seeds", [])
    if not seeds:
        return jsonify({"error": "No seeds"}), 400
    all_songs = []
    for seed in seeds[:2]:
        songs = ytdlp_search(f"{seed} official audio", 5)
        all_songs.extend(songs)
    seen, unique = set(), []
    for s in all_songs:
        if s["id"] not in seen:
            seen.add(s["id"]); unique.append(s)
    return jsonify({"playlist": unique[:20]})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"🎵 SoundWave on port {port}")
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
