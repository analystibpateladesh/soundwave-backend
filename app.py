"""
SoundWave Backend - Deezer API for search + YouTube IFrame for playback
No API keys needed. Free forever.
"""

from flask import Flask, jsonify, request, send_file, redirect
from flask_cors import CORS
import subprocess, json, os, sys, re
from pathlib import Path
import urllib.request, urllib.parse

app = Flask(__name__)
CORS(app)

CACHE_DIR = Path("./audio_cache")
CACHE_DIR.mkdir(exist_ok=True)
search_cache = {}

# ── Find yt-dlp ──
YTDLP_CMD = None
def find_ytdlp():
    global YTDLP_CMD
    for c in [[sys.executable,"-m","yt_dlp"],["yt-dlp"],["yt_dlp"]]:
        try:
            r = subprocess.run(c+["--version"],capture_output=True,text=True,timeout=10)
            if r.returncode==0: YTDLP_CMD=c; print(f"✅ yt-dlp: {c}"); return
        except: continue
    print("❌ yt-dlp not found")
find_ytdlp()

def run_ytdlp(args, timeout=120):
    if not YTDLP_CMD:
        class F: returncode=1;stdout="";stderr="not found"
        return F()
    try: return subprocess.run(YTDLP_CMD+args,capture_output=True,text=True,timeout=timeout)
    except subprocess.TimeoutExpired:
        class T: returncode=1;stdout="";stderr="timeout"
        return T()

def deezer_search(query, limit=20):
    """Search Deezer for tracks - free, no API key."""
    try:
        url = f"https://api.deezer.com/search?q={urllib.parse.quote(query)}&limit={limit}&output=json"
        req = urllib.request.Request(url, headers={'User-Agent':'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode())
        
        songs = []
        for t in data.get('data', []):
            songs.append({
                "id": str(t.get('id')),
                "title": t.get('title','Unknown'),
                "artist": t.get('artist',{}).get('name','Unknown'),
                "album": t.get('album',{}).get('title',''),
                "duration": t.get('duration',0),
                "duration_str": f"{t.get('duration',0)//60}:{t.get('duration',0)%60:02d}",
                "thumbnail": t.get('album',{}).get('cover_medium',''),
                "preview": t.get('preview',''),  # 30-sec free preview MP3
                "deezer_id": str(t.get('id')),
                # Search YouTube for full song playback
                "yt_query": f"{t.get('artist',{}).get('name','')} {t.get('title','')} official audio",
            })
        return songs
    except Exception as e:
        print(f"Deezer search failed: {e}")
        return []

def get_yt_id(query):
    """Get YouTube video ID for a song query."""
    try:
        result = run_ytdlp([
            f"ytsearch1:{query}",
            "--dump-json","--no-playlist","--skip-download",
            "--quiet","--no-warnings","--socket-timeout","20"
        ], timeout=40)
        if result.stdout:
            d = json.loads(result.stdout.strip().split('\n')[0])
            return d.get('id')
    except: pass
    return None

@app.route("/")
def index():
    return jsonify({"status":"SoundWave API running","ytdlp":YTDLP_CMD is not None})

@app.route("/api/health")
def health():
    return jsonify({"status":"ok","ytdlp":YTDLP_CMD is not None})

@app.route("/api/search")
def search():
    query = request.args.get("q","").strip()
    limit = int(request.args.get("limit",20))
    if not query: return jsonify({"error":"No query"}),400
    
    key = f"{query}_{limit}"
    if key in search_cache: return jsonify(search_cache[key])
    
    songs = deezer_search(query, limit)
    resp = {"results": songs, "query": query}
    if songs: search_cache[key] = resp
    return jsonify(resp)

@app.route("/api/yt_id")
def yt_id_route():
    """Get YouTube ID for a Deezer track."""
    query = request.args.get("q","").strip()
    if not query: return jsonify({"error":"No query"}),400
    vid = get_yt_id(query)
    if vid: return jsonify({"yt_id": vid})
    return jsonify({"error":"Not found"}),404

@app.route("/api/download/<video_id>")
def download(video_id):
    cache_file = CACHE_DIR / f"{video_id}.mp3"
    if cache_file.exists() and cache_file.stat().st_size > 10000:
        return send_file(cache_file,mimetype="audio/mpeg",as_attachment=True,download_name=f"{video_id}.mp3")
    
    output = str(CACHE_DIR/f"{video_id}.%(ext)s")
    result = run_ytdlp([
        f"https://www.youtube.com/watch?v={video_id}",
        "-x","--audio-format","mp3","--audio-quality","192K",
        "-o",output,"--no-playlist","--quiet","--no-warnings"
    ], timeout=300)
    
    if cache_file.exists() and cache_file.stat().st_size > 10000:
        return send_file(cache_file,mimetype="audio/mpeg",as_attachment=True,download_name=f"{video_id}.mp3")
    
    # Fallback: redirect to stream
    r2 = run_ytdlp([f"https://www.youtube.com/watch?v={video_id}",
        "--get-url","-f","bestaudio/best","--no-playlist","--quiet"],timeout=60)
    if r2.returncode==0 and r2.stdout.strip():
        return redirect(r2.stdout.strip().split('\n')[0])
    return jsonify({"error":"Download failed"}),500

@app.route("/api/trending")
def trending():
    songs = deezer_search("top hits 2024", 20)
    if not songs: songs = deezer_search("popular songs", 20)
    return jsonify({"trending": songs})

@app.route("/api/playlist/generate", methods=["POST"])
def playlist():
    data = request.json or {}
    seeds = data.get("seeds",[])
    if not seeds: return jsonify({"error":"No seeds"}),400
    all_songs = []
    for s in seeds[:3]:
        all_songs.extend(deezer_search(s, 8))
    seen,unique = set(),[]
    for s in all_songs:
        if s["id"] not in seen: seen.add(s["id"]); unique.append(s)
    return jsonify({"playlist":unique[:24]})

if __name__ == "__main__":
    port = int(os.environ.get("PORT",5000))
    print(f"🎵 SoundWave on port {port}")
    app.run(host="0.0.0.0",port=port,debug=False,threaded=True)
