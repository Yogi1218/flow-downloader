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

app = Flask(__name__, static_folder=".")
ssl._create_default_https_context = ssl._create_unverified_context

# --- Platform helpers ---
def normalize_url(url):
    """Normalize/fix URLs for known platforms before passing to yt-dlp."""
    import re
    # Reddit: convert post URLs to ensure they work
    if 'reddit.com' in url:
        # Remove query params that break extraction
        url = url.split('?')[0].rstrip('/')
        # Ensure it ends with the post path (no trailing slash issues)
        if '/comments/' in url and not url.endswith('/'):
            url = url + '/'
    # Twitter/X: normalize x.com → twitter.com (yt-dlp handles both but twitter.com is more reliable)
    url = re.sub(r'https?://x\.com/', 'https://twitter.com/', url)
    # TikTok: remove tracking params
    if 'tiktok.com' in url:
        url = url.split('?')[0]
    # Pinterest: ensure it's a pin URL
    if 'pinterest.com' in url and '/pin/' not in url:
        pass  # leave as-is
    return url

def get_platform_headers(url):
    """Return platform-specific HTTP headers."""
    base = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    if 'tiktok.com' in url:
        base.update({
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
            "Referer": "https://www.tiktok.com/",
        })
    elif 'twitter.com' in url or 'x.com' in url:
        base.update({
            "Referer": "https://twitter.com/",
        })
    elif 'instagram.com' in url:
        base.update({
            "Referer": "https://www.instagram.com/",
        })
    elif 'reddit.com' in url or 'v.redd.it' in url:
        base.update({
            "Referer": "https://www.reddit.com/",
        })
    elif 'hotstar.com' in url:
        base["Referer"] = "https://www.hotstar.com/"
    elif 'facebook.com' in url:
        base.update({
            "Referer": "https://www.facebook.com/",
        })
    return base

def get_extractor_args(url):
    """Return platform-specific extractor args."""
    if 'youtube.com' in url or 'youtu.be' in url:
        if 'list=' in url or 'playlist' in url:
            return {}  # no extractor args for playlists
        return {"youtube": {"player_client": ["ios", "android"]}}
    return {}

NEEDS_COOKIES = ['instagram.com', 'facebook.com', 'tiktok.com', 'twitter.com', 'x.com', 'reddit.com']

def try_with_cookie_fallback(ydl_opts_base, action_fn, url):
    """Try action_fn with ydl_opts, then retry with browser cookies if it fails on auth-required sites."""
    try:
        return action_fn(ydl_opts_base)
    except Exception as initial_error:
        needs_auth = any(p in url for p in NEEDS_COOKIES)
        no_cookies = not ydl_opts_base.get('cookiefile') and not ydl_opts_base.get('cookiesfrombrowser')
        if needs_auth and no_cookies:
            for browser in ["chrome", "safari", "firefox", "chromium", "edge"]:
                try:
                    opts = dict(ydl_opts_base)
                    opts["cookiesfrombrowser"] = (browser,)
                    return action_fn(opts)
                except Exception:
                    continue
        raise initial_error

# State Management
active_downloads = {}
HISTORY_FILE = os.path.join(os.path.dirname(__file__), "history.json")

def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return []
    return []

def save_history_item(item):
    history = load_history()
    history.insert(0, item)
    history = history[:50]
    try:
        with open(HISTORY_FILE, "w") as f:
            json.dump(history, f, indent=4)
    except Exception as e:
        print("Save history error:", e)

def get_ffmpeg_path():
    path = shutil.which("ffmpeg")
    if path:
        return path
    if platform.system() == "Darwin" and os.path.exists("/opt/homebrew/bin/ffmpeg"):
        return "/opt/homebrew/bin/ffmpeg"
    if platform.system() == "Darwin" and os.path.exists("/usr/local/bin/ffmpeg"):
        return "/usr/local/bin/ffmpeg"
    windows_paths = [
        "C:\\ffmpeg\\bin\\ffmpeg.exe",
        "C:\\Program Files\\ffmpeg\\bin\\ffmpeg.exe"
    ]
    for p in windows_paths:
        if os.path.exists(p):
            return p
    return "ffmpeg"

def cleanup_temp_files(save_dir, filename):
    try:
        if not os.path.exists(save_dir):
            return
        for f in os.listdir(save_dir):
            if f.startswith(filename):
                full_path = os.path.join(save_dir, f)
                if f.endswith(".part") or f.endswith(".ytdl") or ".temp" in f or ".f" in f:
                    if os.path.exists(full_path):
                        os.remove(full_path)
    except Exception as e:
        print("Cleanup error:", e)

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
        ext_args = get_extractor_args(url)
        if ext_args:
            ydl_opts["extractor_args"] = ext_args
        
        if cookies_text:
            import tempfile
            tf = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False)
            tf.write(cookies_text)
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

# Background worker for a single download
def bg_download(download_id, url, quality, filename, save_dir, audio_format, audio_bitrate, ratelimit, cookies_browser="", cookies_text="", user_agent=""):
    temp_cookie_file = None
    try:
        os.makedirs(save_dir, exist_ok=True)
        if quality == "360p":
            format_code = "bestvideo[height<=360]+bestaudio/best[height<=360]/best"
        elif quality == "720p":
            format_code = "bestvideo[height<=720]+bestaudio/best[height<=720]/best"
        elif quality == "1080p":
            format_code = "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best"
        elif quality == "4k":
            format_code = "bestvideo[height<=2160]+bestaudio/best[height<=2160]/best"
        else:
            format_code = "bestaudio/best"

        def progress_hook(d):
            if active_downloads.get(download_id, {}).get("cancelled"):
                raise Exception("Download cancelled")

            if d["status"] == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                downloaded = d.get("downloaded_bytes", 0)
                
                percent = downloaded / total * 100 if total else 0
                speed = d.get("speed", 0) or 0
                eta = int(d.get("eta", 0) or 0)
                
                active_downloads[download_id].update({
                    "progress": percent,
                    "speed": f"{speed / (1024*1024):.1f} MB/s" if speed else "N/A",
                    "eta": f"{eta // 60:02d}:{eta % 60:02d}" if eta else "00:00",
                    "downloaded": f"{downloaded / (1024*1024):.1f} MB",
                    "total": f"{total / (1024*1024):.1f} MB" if total else "N/A",
                    "status": "Downloading"
                })

        url = normalize_url(url)
        headers = get_platform_headers(url)
        if user_agent:
            headers["User-Agent"] = user_agent

        ydl_opts = {
            "format": format_code,
            "outtmpl": os.path.join(save_dir, f"{filename}.%(ext)s"),
            "noplaylist": True,
            "progress_hooks": [progress_hook],
            "merge_output_format": "mp4",
            "postprocessors": [
                {
                    "key": "FFmpegVideoConvertor",
                    "preferedformat": "mp4"
                }
            ] if quality != "Audio only" else [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": audio_format,
                    "preferredquality": audio_bitrate
                }
            ],
            "cacert": certifi.where(),
            "ffmpeg_location": get_ffmpeg_path(),
            "geo_bypass": True,
            "geo_bypass_country": "US",
            "http_headers": headers,
            "concurrent_fragment_downloads": 8,
        }
        ext_args = get_extractor_args(url)
        if ext_args:
            ydl_opts["extractor_args"] = ext_args
        if user_agent:
            ydl_opts["user_agent"] = user_agent
        if ratelimit:
            ydl_opts["ratelimit"] = ratelimit

        if cookies_text:
            import tempfile
            tf = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False)
            tf.write(cookies_text)
            tf.close()
            temp_cookie_file = tf.name
            ydl_opts["cookiefile"] = temp_cookie_file
        elif cookies_browser:
            ydl_opts["cookiesfrombrowser"] = (cookies_browser,)

        def do_download(opts):
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])

        try_with_cookie_fallback(ydl_opts, do_download, url)

        if active_downloads.get(download_id, {}).get("cancelled"):
            active_downloads[download_id]["status"] = "Cancelled"
            cleanup_temp_files(save_dir, filename)
        else:
            active_downloads[download_id]["status"] = "Completed"
            active_downloads[download_id]["progress"] = 100
            
            ext = "mp3" if quality == "Audio only" else "mp4"
            final_path = os.path.join(save_dir, f"{filename}.{ext}")
            active_downloads[download_id]["file_path"] = final_path

            # Save entry to local history JSON file
            save_history_item({
                "title": filename,
                "thumbnail": active_downloads[download_id].get("thumbnail", ""),
                "quality": quality if quality != "Audio only" else f"{audio_format.upper()} ({audio_bitrate}k)",
                "type": "audio" if quality == "Audio only" else "video",
                "size": active_downloads[download_id].get("total", "N/A"),
                "file_path": final_path,
                "timestamp": int(uuid.uuid4().time / 10000000)
            })

    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        active_downloads[download_id]["status"] = f"Error: {str(e)}"
        active_downloads[download_id]["error_log"] = tb
        cleanup_temp_files(save_dir, filename)
    finally:
        if temp_cookie_file and os.path.exists(temp_cookie_file):
            try:
                os.remove(temp_cookie_file)
            except Exception:
                pass

# Background worker for batch downloads
def bg_batch_download(batch_id, items, quality, save_dir, audio_format, audio_bitrate, ratelimit, cookies_browser="", cookies_text="", user_agent=""):
    os.makedirs(save_dir, exist_ok=True)
    total_items = len(items)
    active_downloads[batch_id]["status"] = "Processing Batch..."
    
    for index, item in enumerate(items):
        if active_downloads.get(batch_id, {}).get("cancelled"):
            break
            
        url = item.get("url")
        title = item.get("title", "video")
        clean_title = "".join([c for c in title if c.isalpha() or c.isdigit() or c in ' -_']).strip()
        
        active_downloads[batch_id].update({
            "status": f"Downloading {index+1} of {total_items}: {title}",
            "downloaded": f"{index} of {total_items} items"
        })
        
        def sub_progress_hook(d):
            if d["status"] == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                downloaded = d.get("downloaded_bytes", 0)
                sub_percent = downloaded / total * 100 if total else 0
                
                batch_progress = (index / total_items * 100) + (sub_percent / total_items)
                speed = d.get("speed", 0) or 0
                eta = int(d.get("eta", 0) or 0)
                
                active_downloads[batch_id].update({
                    "progress": batch_progress,
                    "speed": f"{speed / (1024*1024):.1f} MB/s" if speed else "N/A",
                    "eta": f"{eta // 60:02d}:{eta % 60:02d}" if eta else "00:00"
                })

        headers = {
            "User-Agent": user_agent or "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9"
        }
        if "hotstar.com" in url:
            headers["Referer"] = "https://www.hotstar.com/"

        ydl_opts = {
            "format": "bestvideo[height<=720]+bestaudio/best[height<=720]/best" if quality != "Audio only" else "bestaudio/best",
            "outtmpl": os.path.join(save_dir, f"{clean_title}.%(ext)s"),
            "noplaylist": True,
            "progress_hooks": [sub_progress_hook],
            "merge_output_format": "mp4",
            "postprocessors": [
                {
                    "key": "FFmpegVideoConvertor",
                    "preferedformat": "mp4"
                }
            ] if quality != "Audio only" else [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": audio_format,
                    "preferredquality": audio_bitrate
                }
            ],
            "cacert": certifi.where(),
            "ffmpeg_location": get_ffmpeg_path(),
            "geo_bypass": True,
            "http_headers": headers,
            "concurrent_fragment_downloads": 8,
            "extractor_args": {
                "youtube": {
                    "player_client": ["android_vr"]
                }
            }
        }
        if user_agent:
            ydl_opts["user_agent"] = user_agent
        if ratelimit:
            ydl_opts["ratelimit"] = ratelimit

        temp_cookie_file = None
        try:
            if cookies_text:
                import tempfile
                tf = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False)
                tf.write(cookies_text)
                tf.close()
                temp_cookie_file = tf.name
                ydl_opts["cookiefile"] = temp_cookie_file
            elif cookies_browser:
                ydl_opts["cookiesfrombrowser"] = (cookies_browser,)

            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([url])
            except Exception as initial_error:
                if not cookies_text and not cookies_browser and ("instagram.com" in url or "x.com" in url or "twitter.com" in url):
                    fallback_browsers = ["chrome", "safari", "firefox"]
                    success = False
                    last_err = initial_error
                    for browser in fallback_browsers:
                        try:
                            ydl_opts_copy = ydl_opts.copy()
                            ydl_opts_copy["cookiesfrombrowser"] = (browser,)
                            with yt_dlp.YoutubeDL(ydl_opts_copy) as ydl:
                                ydl.download([url])
                            success = True
                            break
                        except Exception as e:
                            last_err = e
                            continue
                    if not success:
                        raise initial_error
                else:
                    raise initial_error
                
            ext = "mp3" if quality == "Audio only" else "mp4"
            final_path = os.path.join(save_dir, f"{clean_title}.{ext}")
            
            # Save completed video entry to local history
            save_history_item({
                "title": clean_title,
                "thumbnail": item.get("thumbnail", ""),
                "quality": quality if quality != "Audio only" else f"{audio_format.upper()}",
                "type": "audio" if quality == "Audio only" else "video",
                "size": "N/A",
                "file_path": final_path,
                "timestamp": int(uuid.uuid4().time / 10000000)
            })
            
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            active_downloads[batch_id]["status"] = f"Error: {str(e)}"
            active_downloads[batch_id]["error_log"] = tb
            cleanup_temp_files(save_dir, clean_title)
        finally:
            if temp_cookie_file and os.path.exists(temp_cookie_file):
                try:
                    os.remove(temp_cookie_file)
                except Exception:
                    pass

    if active_downloads.get(batch_id, {}).get("cancelled"):
        active_downloads[batch_id]["status"] = "Cancelled"
    else:
        active_downloads[batch_id].update({
            "status": "Completed",
            "progress": 100,
            "downloaded": f"{total_items} of {total_items} items"
        })

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
        "error_log": ""
    }

    t = threading.Thread(target=bg_download, args=(
        download_id, url, quality, filename, save_dir, audio_format, audio_bitrate, ratelimit, cookies_browser, cookies_text, user_agent
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
        "error_log": ""
    }

    t = threading.Thread(target=bg_batch_download, args=(
        batch_id, items, quality, save_dir, audio_format, audio_bitrate, ratelimit, cookies_browser, cookies_text, user_agent
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
    return jsonify(list(active_downloads.values()))

@app.route("/api/history", methods=["GET"])
def get_history():
    return jsonify(load_history())

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=True)
