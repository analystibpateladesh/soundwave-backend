"""
SoundWave Backend - Flask API (Railway-ready)
"""

from flask import Flask, jsonify, request, send_file
from flask_cors import CORS
import subprocess
import json
import os
import re
import sys
from pathlib import Path

app = Flask(__name__)
CORS(app)

CACHE_DIR = Path("./audio_cache")
CACHE_DIR.mkdir(exist_ok=True)

search_cache = {}

# Find yt-dlp
YTDLP_CMD = None

def find_ytdlp():
    global YTDLP_CMD
    try:
        r = subprocess.run([sys.executable, "-m", "yt_dlp", "--version"],
                           capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            YTDLP_CMD = [sys.executable, "-m", "yt_dlp"]
            print("✅ yt-dlp found as python module")
            return
    except Exception as e:
        print(f"Module check: {e}")

    for cmd in ["yt-dlp", "yt_dlp"]:
        try:
            r = subprocess.run([cmd, "--version"], capture_output=True, text=True, timeout=10)
            if r.returncode == 0:
                YTDLP_CMD = [cmd]
                print(f"✅ yt-dlp binary: {cmd}")
                return
        except Exception:
            continue
    print("❌ yt-dlp not found")

find_ytdlp()

def run_ytdlp(args):
    if not YTDLP_CMD:
        class Fail:
            returncode = 1; stdout = ""; stderr = "yt-dlp not found"
        return Fail()
    try:
        return subprocess.run(YTDLP_CMD + args, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        class T:
            returncode = 1; stdout = ""; stderr = "Timeout"
        return T()

@app.route("/")
def index():
    return jsonify({"status": "SoundWave API running", "ytdlp": YTDLP_CMD is not None})

@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "ytdlp": YTDLP_CMD is not None})

@app.route("/api/search")
def search():
    query = request.args.get("q", "").strip()
    limit = int(request.args.get("limit", 10))
    if not query:
        return jsonify({"error": "No query"}), 400
    cache_key = f"{query}_{limit}"
    if cache_key in search_cache:
        return jsonify(search_cache[cache_key])
    result = run_ytdlp([f"ytsearch{limit}:{query}", "--dump-json", "--no-playlist",
                        "--skip-download", "--quiet", "--no-warnings"])
    if not result.stdout:
        return jsonify({"error": "Search failed", "detail": result.stderr}), 500
    songs = []
    for line in result.stdout.strip().split("\n"):
        if not line: continue
        try:
            d = json.loads(line)
            duration = d.get("duration", 0) or 0
            if duration > 600: continue
            songs.append({"id": d.get("id"), "title": d.get("title"),
                          "artist": d.get("uploader", "Unknown"), "duration": duration,
                          "duration_str": f"{int(duration//60)}:{int(duration%60):02d}" if duration else "?",
                          "thumbnail": d.get("thumbnail"),
                          "url": f"https://www.youtube.com/watch?v={d.get('id')}"})
        except: continue
    resp = {"results": songs, "query": query}
    search_cache[cache_key] = resp
    return jsonify(resp)

@app.route("/api/stream/<video_id>")
def stream(video_id):
    result = run_ytdlp([f"https://www.youtube.com/watch?v={video_id}",
                        "--get-url", "-f", "bestaudio[ext=m4a]/bestaudio/best",
                        "--no-playlist", "--quiet", "--no-warnings"])
    if result.returncode != 0:
        return jsonify({"error": "Failed", "detail": result.stderr}), 500
    url = result.stdout.strip().split("\n")[0]
    return jsonify({"stream_url": url, "video_id": video_id})

@app.route("/api/download/<video_id>")
def download(video_id):
    cache_file = CACHE_DIR / f"{video_id}.mp3"
    if cache_file.exists():
        return send_file(cache_file, mimetype="audio/mpeg",
                         as_attachment=True, download_name=f"{video_id}.mp3")
    output_path = CACHE_DIR / f"{video_id}.%(ext)s"
    result = run_ytdlp([f"https://www.youtube.com/watch?v={video_id}",
                        "-x", "--audio-format", "mp3", "--audio-quality", "192K",
                        "-o", str(output_path), "--no-playlist", "--quiet", "--no-warnings"])
    if result.returncode != 0:
        return jsonify({"error": "Download failed", "detail": result.stderr}), 500
    if cache_file.exists():
        return send_file(cache_file, mimetype="audio/mpeg",
                         as_attachment=True, download_name=f"{video_id}.mp3")
    return jsonify({"error": "File not found"}), 500

@app.route("/api/trending")
def trending():
    result = run_ytdlp(["ytsearch15:top hits 2024 official audio", "--dump-json",
                         "--no-playlist", "--skip-download", "--quiet", "--no-warnings"])
    songs = []
    for line in result.stdout.strip().split("\n"):
        if not line: continue
        try:
            d = json.loads(line)
            duration = d.get("duration", 0) or 0
            if 60 < duration < 600:
                songs.append({"id": d.get("id"), "title": d.get("title"),
                              "artist": d.get("uploader", "Unknown"), "duration": duration,
                              "duration_str": f"{int(duration//60)}:{int(duration%60):02d}",
                              "thumbnail": d.get("thumbnail")})
        except: continue
    return jsonify({"trending": songs})

@app.route("/api/playlist/generate", methods=["POST"])
def generate_playlist():
    data = request.json or {}
    seeds = data.get("seeds", [])
    if not seeds:
        return jsonify({"error": "No seeds"}), 400
    all_songs = []
    for seed in seeds[:3]:
        result = run_ytdlp([f"ytsearch5:{seed} official audio", "--dump-json",
                             "--no-playlist", "--skip-download", "--quiet", "--no-warnings"])
        for line in result.stdout.strip().split("\n"):
            if not line: continue
            try:
                d = json.loads(line)
                duration = d.get("duration", 0) or 0
                if 60 < duration < 600:
                    all_songs.append({"id": d.get("id"), "title": d.get("title"),
                                      "artist": d.get("uploader", "Unknown")})
            except: continue
    seen, unique = set(), []
    for s in all_songs:
        if s["id"] not in seen:
            seen.add(s["id"]); unique.append(s)
    return jsonify({"playlist": unique[:20], "generated_from": seeds})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"🎵 SoundWave on port {port}")
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
