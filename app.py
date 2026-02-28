"""
SoundWave Backend - Flask API
Handles YouTube search, audio streaming, and download via yt-dlp
"""

from flask import Flask, jsonify, request, send_file, Response
from flask_cors import CORS
import subprocess
import json
import os
import re
import sys
import tempfile
import threading
import time
import hashlib
from pathlib import Path

app = Flask(__name__)
CORS(app)

# Cache directory for downloaded audio files
CACHE_DIR = Path("./audio_cache")
CACHE_DIR.mkdir(exist_ok=True)

# Simple in-memory cache for search results
search_cache = {}

# ── Find yt-dlp using the same Python that's running this script ──
def get_ytdlp_cmd():
    """Find yt-dlp executable, works on Windows and Mac/Linux."""
    # Try using Python module first (most reliable on Windows)
    try:
        result = subprocess.run(
            [sys.executable, "-m", "yt_dlp", "--version"],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            return [sys.executable, "-m", "yt_dlp"]
    except Exception:
        pass

    # Try direct command
    for cmd in ["yt-dlp", "yt_dlp"]:
        try:
            result = subprocess.run([cmd, "--version"], capture_output=True, text=True)
            if result.returncode == 0:
                return [cmd]
        except FileNotFoundError:
            continue

    # Try common Windows paths
    python_dir = Path(sys.executable).parent
    for candidate in [
        python_dir / "Scripts" / "yt-dlp.exe",
        python_dir / "yt-dlp.exe",
        Path.home() / "AppData" / "Roaming" / "Python" / f"Python{sys.version_info.major}{sys.version_info.minor}" / "Scripts" / "yt-dlp.exe",
    ]:
        if candidate.exists():
            return [str(candidate)]

    return None

YTDLP_CMD = get_ytdlp_cmd()
print(f"🔍 yt-dlp found: {YTDLP_CMD}")

def run_ytdlp(args):
    """Run yt-dlp as subprocess and return output."""
    if not YTDLP_CMD:
        class FakeResult:
            returncode = 1
            stdout = ""
            stderr = "yt-dlp not found"
        return FakeResult()
    cmd = YTDLP_CMD + args
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    return result

def sanitize_filename(name):
    return re.sub(r'[^\w\-_\. ]', '_', name)

# ─────────────────────────────────────────────
#  ROUTES
# ─────────────────────────────────────────────

@app.route("/api/search", methods=["GET"])
def search():
    """Search YouTube for songs."""
    query = request.args.get("q", "").strip()
    limit = int(request.args.get("limit", 10))

    if not query:
        return jsonify({"error": "No query provided"}), 400

    cache_key = f"{query}_{limit}"
    if cache_key in search_cache:
        return jsonify(search_cache[cache_key])

    try:
        result = run_ytdlp([
            f"ytsearch{limit}:{query}",
            "--dump-json",
            "--no-playlist",
            "--skip-download",
            "--quiet",
            "--no-warnings",
            "--extractor-args", "youtube:skip=dash,hls"
        ])

        if result.returncode != 0 and not result.stdout:
            return jsonify({"error": "Search failed", "detail": result.stderr}), 500

        songs = []
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            try:
                data = json.loads(line)
                # Filter out non-music results (long videos likely not songs)
                duration = data.get("duration", 0) or 0
                if duration > 600:  # skip videos > 10 min
                    continue

                songs.append({
                    "id": data.get("id"),
                    "title": data.get("title"),
                    "artist": data.get("uploader", "Unknown Artist"),
                    "duration": duration,
                    "duration_str": f"{int(duration//60)}:{int(duration%60):02d}" if duration else "?",
                    "thumbnail": data.get("thumbnail"),
                    "view_count": data.get("view_count", 0),
                    "url": f"https://www.youtube.com/watch?v={data.get('id')}",
                })
            except json.JSONDecodeError:
                continue

        response = {"results": songs, "query": query}
        search_cache[cache_key] = response
        return jsonify(response)

    except subprocess.TimeoutExpired:
        return jsonify({"error": "Search timed out"}), 504
    except FileNotFoundError:
        return jsonify({"error": "yt-dlp not installed. Run: pip install yt-dlp"}), 500


@app.route("/api/stream/<video_id>")
def stream_audio(video_id):
    """Get direct audio stream URL for a YouTube video."""
    try:
        result = run_ytdlp([
            f"https://www.youtube.com/watch?v={video_id}",
            "--get-url",
            "-f", "bestaudio[ext=m4a]/bestaudio/best",
            "--no-playlist",
            "--quiet",
            "--no-warnings",
        ])

        if result.returncode != 0:
            return jsonify({"error": "Failed to get stream URL"}), 500

        stream_url = result.stdout.strip().split("\n")[0]
        return jsonify({"stream_url": stream_url, "video_id": video_id})

    except FileNotFoundError:
        return jsonify({"error": "yt-dlp not installed"}), 500


@app.route("/api/download/<video_id>")
def download_audio(video_id):
    """Download audio as MP3 and serve it."""
    cache_file = CACHE_DIR / f"{video_id}.mp3"

    # Serve from cache if exists
    if cache_file.exists():
        return send_file(
            cache_file,
            mimetype="audio/mpeg",
            as_attachment=True,
            download_name=f"{video_id}.mp3"
        )

    try:
        title_result = run_ytdlp([
            f"https://www.youtube.com/watch?v={video_id}",
            "--get-title",
            "--no-playlist",
            "--quiet",
        ])
        title = sanitize_filename(title_result.stdout.strip() or video_id)
        output_path = CACHE_DIR / f"{video_id}.%(ext)s"

        result = run_ytdlp([
            f"https://www.youtube.com/watch?v={video_id}",
            "-x",
            "--audio-format", "mp3",
            "--audio-quality", "192K",
            "-o", str(output_path),
            "--no-playlist",
            "--quiet",
            "--no-warnings",
        ])

        if result.returncode != 0:
            return jsonify({"error": "Download failed", "detail": result.stderr}), 500

        if cache_file.exists():
            return send_file(
                cache_file,
                mimetype="audio/mpeg",
                as_attachment=True,
                download_name=f"{title}.mp3"
            )
        else:
            return jsonify({"error": "File not found after download"}), 500

    except FileNotFoundError:
        return jsonify({"error": "yt-dlp not installed. Run: pip install yt-dlp"}), 500


@app.route("/api/info/<video_id>")
def get_info(video_id):
    """Get detailed info about a video."""
    try:
        result = run_ytdlp([
            f"https://www.youtube.com/watch?v={video_id}",
            "--dump-json",
            "--no-playlist",
            "--skip-download",
            "--quiet",
        ])

        if result.returncode != 0:
            return jsonify({"error": "Failed to get info"}), 500

        data = json.loads(result.stdout.strip())
        return jsonify({
            "id": data.get("id"),
            "title": data.get("title"),
            "artist": data.get("uploader"),
            "duration": data.get("duration"),
            "thumbnail": data.get("thumbnail"),
            "description": data.get("description", "")[:200],
            "tags": data.get("tags", [])[:10],
        })
    except FileNotFoundError:
        return jsonify({"error": "yt-dlp not installed"}), 500


@app.route("/api/playlist/generate", methods=["POST"])
def generate_playlist():
    """Generate a playlist based on search history / seed songs."""
    data = request.json or {}
    seeds = data.get("seeds", [])  # list of song titles or video IDs
    limit = data.get("limit", 20)

    if not seeds:
        return jsonify({"error": "No seed songs provided"}), 400

    # Build a combined search query from seeds
    query = " ".join(seeds[:3]) + " mix playlist"
    all_songs = []

    for seed in seeds[:3]:
        result = run_ytdlp([
            f"ytsearch5:{seed} official audio",
            "--dump-json",
            "--no-playlist",
            "--skip-download",
            "--quiet",
            "--no-warnings",
        ])
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            try:
                d = json.loads(line)
                duration = d.get("duration", 0) or 0
                if 60 < duration < 600:
                    all_songs.append({
                        "id": d.get("id"),
                        "title": d.get("title"),
                        "artist": d.get("uploader", "Unknown"),
                        "duration": duration,
                        "duration_str": f"{int(duration//60)}:{int(duration%60):02d}",
                        "thumbnail": d.get("thumbnail"),
                        "url": f"https://www.youtube.com/watch?v={d.get('id')}",
                    })
            except:
                continue

    # Deduplicate by id
    seen = set()
    unique = []
    for s in all_songs:
        if s["id"] not in seen:
            seen.add(s["id"])
            unique.append(s)

    return jsonify({"playlist": unique[:limit], "generated_from": seeds})


@app.route("/api/trending")
def trending():
    """Get trending music."""
    try:
        result = run_ytdlp([
            "ytsearch15:top hits 2024 official audio",
            "--dump-json",
            "--no-playlist",
            "--skip-download",
            "--quiet",
            "--no-warnings",
        ])

        songs = []
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            try:
                data = json.loads(line)
                duration = data.get("duration", 0) or 0
                if 60 < duration < 600:
                    songs.append({
                        "id": data.get("id"),
                        "title": data.get("title"),
                        "artist": data.get("uploader", "Unknown"),
                        "duration": duration,
                        "duration_str": f"{int(duration//60)}:{int(duration%60):02d}",
                        "thumbnail": data.get("thumbnail"),
                        "url": f"https://www.youtube.com/watch?v={data.get('id')}",
                    })
            except:
                continue

        return jsonify({"trending": songs})
    except FileNotFoundError:
        return jsonify({"error": "yt-dlp not installed"}), 500


@app.route("/api/health")
def health():
    """Check if backend is running and yt-dlp is available."""
    try:
        result = subprocess.run(["yt-dlp", "--version"], capture_output=True, text=True)
        ytdlp_version = result.stdout.strip()
        ytdlp_ok = result.returncode == 0
    except FileNotFoundError:
        ytdlp_version = None
        ytdlp_ok = False

    return jsonify({
        "status": "ok",
        "ytdlp": ytdlp_ok,
        "ytdlp_version": ytdlp_version,
        "cache_size": len(list(CACHE_DIR.glob("*.mp3")))
    })


if __name__ == "__main__":
    print("🎵 SoundWave Backend starting on http://localhost:5000")
    if YTDLP_CMD:
        print(f"✅ yt-dlp ready!")
    else:
        print("❌ yt-dlp NOT found! Run: python -m pip install yt-dlp")
    app.run(debug=True, port=5000, threaded=True)
