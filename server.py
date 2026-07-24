import os
import sys
import json
import uuid
import shutil
import platform
import threading
import certifi
import ssl
import yt_dlp
import subprocess
from flask import Flask, request, jsonify, send_from_directory
from downloader import (
    normalize_url,
    get_platform_headers,
    get_extractor_args,
    get_effective_cookies,
    try_with_cookie_fallback,
    active_downloads,
    load_history,
    save_history_item,
    get_ffmpeg_path,
    cleanup_temp_files,
    bg_download,
    bg_batch_download
)

app = Flask(__name__, static_folder=".")
ssl._create_default_https_context = ssl._create_unverified_context

@app.route("/")
def index():
    return send_from_directory(".", "index.html")

@app.route("/favicon.ico")
def favicon():
    return send_from_directory(".", "favicon.jpg", mimetype="image/jpeg")

@app.route("/api/browse_directory", methods=["GET"])
def browse_directory():
    try:
        req_path = request.args.get("path", "").strip()
        if not req_path:
            req_path = os.path.expanduser("~")
        else:
            req_path = os.path.abspath(os.path.expanduser(req_path))

        if not os.path.exists(req_path) or not os.path.isdir(req_path):
            req_path = os.path.expanduser("~")

        subdirs = []
        try:
            for item in sorted(os.listdir(req_path)):
                if item.startswith('.'):
                    continue
                full_path = os.path.join(req_path, item)
                if os.path.isdir(full_path):
                    try:
                        os.access(full_path, os.R_OK)
                        subdirs.append(item)
                    except Exception:
                        pass
        except Exception:
            pass

        parent_path = os.path.dirname(req_path)
        if parent_path == req_path:
            parent_path = ""

        return jsonify({
            "current_path": req_path,
            "parent_path": parent_path,
            "subdirs": subdirs
        })
    except Exception as e:
        return jsonify({ "error": str(e) }), 500

@app.route("/api/create_directory", methods=["POST"])
def create_directory():
    try:
        data = request.json or {}
        parent = data.get("parent", "").strip()
        name = data.get("name", "").strip()
        if not parent or not name:
            return jsonify({ "error": "Parent path and directory name are required" }), 400
        
        if "/" in name or "\\" in name or name.startswith("."):
            return jsonify({ "error": "Invalid folder name" }), 400

        target_path = os.path.join(parent, name)
        if os.path.exists(target_path):
            return jsonify({ "error": "Folder already exists" }), 400

        os.makedirs(target_path, exist_ok=True)
        return jsonify({ "success": True, "path": target_path })
    except Exception as e:
        return jsonify({ "error": str(e) }), 500

@app.route("/api/open", methods=["POST"])
def open_path():
    data = request.json or {}
    path = data.get("path")
    if not path or not os.path.exists(path):
        return jsonify({ "error": f"Path does not exist: {path}" }), 400
        
    try:
        if platform.system() == "Darwin":
            subprocess.run(["open", path])
        elif platform.system() == "Windows":
            os.startfile(path)
        else:
            subprocess.run(["xdg-open", path])
        return jsonify({ "success": True })
    except Exception as e:
        return jsonify({ "error": str(e) }), 500

@app.route("/api/download_file", methods=["GET"])
def download_file():
    try:
        path = request.args.get("path", "").strip()
        if not path:
            return jsonify({ "error": "Path is required" }), 400
        path = os.path.abspath(os.path.expanduser(path))
        if not os.path.exists(path) or not os.path.isfile(path):
            return jsonify({ "error": f"File does not exist: {path}" }), 404
            
        directory = os.path.dirname(path)
        filename = os.path.basename(path)
        return send_from_directory(directory, filename, as_attachment=True)
    except Exception as e:
        return jsonify({ "error": str(e) }), 500

@app.route("/api/default_save_dir", methods=["GET"])
def default_save_dir():
    try:
        path = os.path.abspath(os.path.join(os.path.dirname(__file__), "downloads"))
        os.makedirs(path, exist_ok=True)
        return jsonify({ "path": path })
    except Exception as e:
        return jsonify({ "error": str(e) }), 500

@app.route("/api/save_global_cookies", methods=["POST"])
def save_global_cookies():
    try:
        data = request.json or {}
        cookies_text = data.get("cookies_text", "").strip()
        
        cookie_file_path = os.path.join(os.path.dirname(__file__), "server_cookies.txt")
        if cookies_text:
            with open(cookie_file_path, "w") as f:
                f.write(cookies_text)
            return jsonify({ "success": True, "message": "Cookies saved globally on server" })
        else:
            if os.path.exists(cookie_file_path):
                os.remove(cookie_file_path)
            return jsonify({ "success": True, "message": "Global cookies cleared" })
    except Exception as e:
        return jsonify({ "error": str(e) }), 500

@app.route("/api/get_global_cookies", methods=["GET"])
def get_global_cookies():
    try:
        cookie_file_path = os.path.join(os.path.dirname(__file__), "server_cookies.txt")
        if os.path.exists(cookie_file_path):
            with open(cookie_file_path, "r") as f:
                return jsonify({ "cookies_text": f.read() })
        return jsonify({ "cookies_text": "" })
    except Exception as e:
        return jsonify({ "error": str(e) }), 500

@app.route("/api/analyze", methods=["POST"])
def analyze():
    data = request.json or {}
    url = data.get("url")
    cookies_browser = data.get("cookies_browser")
    cookies_text = data.get("cookies_text")
    user_agent = data.get("user_agent")
    if not url:
        return jsonify({ "error": "URL is required" }), 400
        
    temp_cookie_file = None
    try:
        url = normalize_url(url)
        headers = get_platform_headers(url)
        if user_agent:
            headers["User-Agent"] = user_agent

        ydl_opts = {
            "extract_flat": "in_playlist",
            "noplaylist": False,
            "cacert": certifi.where(),
            "geo_bypass": True,
            "geo_bypass_country": "US",
            "http_headers": headers,
        }
        effective_cookies_text = get_effective_cookies(cookies_text)
        has_cookies = bool(effective_cookies_text or cookies_browser)
        ext_args = get_extractor_args(url, has_cookies=has_cookies)
        if ext_args:
            ydl_opts["extractor_args"] = ext_args
        
        if effective_cookies_text:
            import tempfile
            tf = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False)
            tf.write(effective_cookies_text)
            tf.close()
            temp_cookie_file = tf.name
            ydl_opts["cookiefile"] = temp_cookie_file
        elif cookies_browser:
            ydl_opts["cookiesfrombrowser"] = (cookies_browser,)

        def do_analyze(opts):
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(url, download=False)

        info = try_with_cookie_fallback(ydl_opts, do_analyze, url)
            
        if "entries" in info:
            entries = []
            for entry in info["entries"]:
                if entry:
                    entry_id = entry.get("id")
                    entry_thumb = entry.get("thumbnail")
                    if not entry_thumb and entry.get("thumbnails"):
                        entry_thumb = entry["thumbnails"][-1].get("url")
                    if not entry_thumb and entry_id and len(entry_id) == 11:
                        entry_thumb = f"https://i.ytimg.com/vi/{entry_id}/hqdefault.jpg"

                    entries.append({
                        "id": entry_id,
                        "title": entry.get("title") or f"Video #{len(entries)+1}",
                        "url": entry.get("url") or entry.get("webpage_url") or f"https://www.youtube.com/watch?v={entry_id}",
                        "duration": entry.get("duration"),
                        "thumbnail": entry_thumb
                    })
            
            playlist_thumb = info.get("thumbnail")
            if not playlist_thumb and info.get("thumbnails"):
                playlist_thumb = info["thumbnails"][-1].get("url")
            if not playlist_thumb and entries:
                playlist_thumb = entries[0].get("thumbnail")

            return jsonify({
                "isPlaylist": True,
                "title": info.get("title") or "Playlist",
                "entries": entries,
                "estSize": f"{len(entries)} items",
                "thumbnail": playlist_thumb or ""
            })
        
        formats = info.get("formats", [])
        est_size = "N/A"
        for f in reversed(formats):
            size = f.get("filesize") or f.get("filesize_approx")
            if size:
                est_size = f"{size / (1024*1024):.1f} MB"
                if size > 1024*1024*1024:
                    est_size = f"{size / (1024*1024*1024):.1f} GB"
                break
        
        views = info.get("view_count")
        views_str = f"{views:,}" if isinstance(views, (int, float)) else "0"
        
        return jsonify({
            "isPlaylist": False,
            "id": info.get("id"),
            "title": info.get("title") or "Media Download",
            "channel": info.get("uploader") or info.get("uploader_id") or "Creator",
            "views": views_str,
            "duration": info.get("duration"),
            "thumbnail": info.get("thumbnail") or "https://images.unsplash.com/photo-1511447333015-45b65e60f6d5?auto=format&fit=crop&q=80&w=640&h=360",
            "description": info.get("description") or "",
            "estSize": est_size
        })
    except Exception as e:
        return jsonify({ "error": str(e) }), 500
    finally:
        if temp_cookie_file and os.path.exists(temp_cookie_file):
            try:
                os.remove(temp_cookie_file)
            except Exception:
                pass

@app.route("/api/download", methods=["POST"])
def download():
    data = request.json or {}
    url = data.get("url")
    quality = data.get("quality", "720p")
    filename = data.get("filename", "download")
    thumbnail = data.get("thumbnail", "")
    audio_format = data.get("audio_format", "mp3")
    audio_bitrate = data.get("audio_bitrate", "192")
    save_dir = data.get("save_dir")
    if not save_dir:
        home_downloads = os.path.expanduser("~/Downloads")
        if os.path.isdir(home_downloads) and os.access(home_downloads, os.W_OK):
            save_dir = home_downloads
        else:
            save_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "downloads"))
            os.makedirs(save_dir, exist_ok=True)
    ratelimit = data.get("ratelimit")
    cookies_browser = data.get("cookies_browser", "")
    cookies_text = data.get("cookies_text", "")
    user_agent = data.get("user_agent", "")
    client_id = data.get("client_id", "")
    download_subtitles = data.get("download_subtitles", False)
    download_metadata = data.get("download_metadata", False)

    if not url:
        return jsonify({ "error": "URL is required" }), 400

    download_id = str(uuid.uuid4())
    active_downloads[download_id] = {
        "id": download_id,
        "title": filename,
        "thumbnail": thumbnail,
        "status": "Initializing",
        "progress": 0,
        "speed": "0 MB/s",
        "eta": "00:00",
        "downloaded": "0 MB",
        "total": "N/A",
        "cancelled": False,
        "error_log": "",
        "client_id": client_id
    }

    t = threading.Thread(target=bg_download, args=(
        download_id, url, quality, filename, save_dir, audio_format, audio_bitrate, ratelimit, cookies_browser, cookies_text, user_agent, client_id, download_subtitles, download_metadata
    ))
    t.daemon = True
    t.start()

    return jsonify({ "download_id": download_id })

@app.route("/api/download/batch", methods=["POST"])
def download_batch():
    data = request.json or {}
    items = data.get("items", [])
    quality = data.get("quality", "720p")
    save_dir = data.get("save_dir")
    if not save_dir:
        home_downloads = os.path.expanduser("~/Downloads")
        if os.path.isdir(home_downloads) and os.access(home_downloads, os.W_OK):
            save_dir = home_downloads
        else:
            save_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "downloads"))
            os.makedirs(save_dir, exist_ok=True)
    audio_format = data.get("audio_format", "mp3")
    audio_bitrate = data.get("audio_bitrate", "192")
    ratelimit = data.get("ratelimit")
    cookies_browser = data.get("cookies_browser", "")
    cookies_text = data.get("cookies_text", "")
    user_agent = data.get("user_agent", "")
    client_id = data.get("client_id", "")
    download_subtitles = data.get("download_subtitles", False)
    download_metadata = data.get("download_metadata", False)

    if not items:
        return jsonify({ "error": "No items to download" }), 400

    batch_id = str(uuid.uuid4())
    active_downloads[batch_id] = {
        "id": batch_id,
        "title": f"Batch Download ({len(items)} items)",
        "thumbnail": items[0].get("thumbnail") if items else "",
        "status": "Initializing",
        "progress": 0,
        "speed": "0 MB/s",
        "eta": "00:00",
        "downloaded": f"0 of {len(items)} items",
        "total": f"{len(items)} items",
        "cancelled": False,
        "error_log": "",
        "client_id": client_id
    }

    t = threading.Thread(target=bg_batch_download, args=(
        batch_id, items, quality, save_dir, audio_format, audio_bitrate, ratelimit, cookies_browser, cookies_text, user_agent, client_id, download_subtitles, download_metadata
    ))
    t.daemon = True
    t.start()

    return jsonify({ "download_id": batch_id })

@app.route("/api/progress/<download_id>", methods=["GET"])
def progress(download_id):
    state = active_downloads.get(download_id)
    if not state:
        return jsonify({ "error": "Download ID not found" }), 404
    return jsonify(state)

@app.route("/api/download/cancel/<download_id>", methods=["POST"])
def cancel(download_id):
    state = active_downloads.get(download_id)
    if not state:
        return jsonify({ "error": "Download ID not found" }), 404
    state["cancelled"] = True
    return jsonify({ "success": True })

@app.route("/api/download/clear/<download_id>", methods=["POST"])
def clear_download(download_id):
    if download_id in active_downloads:
        del active_downloads[download_id]
        return jsonify({ "success": True })
    return jsonify({ "error": "Download ID not found" }), 404

@app.route("/api/downloads", methods=["GET"])
def get_downloads():
    client_id = request.args.get("client_id", "").strip()
    results = [
        val for val in active_downloads.values()
        if not client_id or val.get("client_id") == client_id
    ]
    return jsonify(results)

@app.route("/api/history", methods=["GET"])
def get_history():
    client_id = request.args.get("client_id", "").strip()
    history = load_history()
    results = [
        item for item in history
        if not client_id or item.get("client_id") == client_id
    ]
    return jsonify(results)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=True)
