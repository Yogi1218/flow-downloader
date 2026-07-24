import os
import sys
import json
import uuid
import shutil
import platform
import threading
import yt_dlp
import certifi

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

def get_extractor_args(url, has_cookies=False):
    """Return platform-specific extractor args."""
    if 'youtube.com' in url or 'youtu.be' in url:
        if 'list=' in url or 'playlist' in url:
            return {}  # no extractor args for playlists
        if has_cookies:
            # If cookies are provided, let yt-dlp use default web client list so it can apply the web session cookies.
            return {}
        return {"youtube": {"player_client": ["ios", "android"]}}
    return {}

def get_effective_cookies(client_cookies_text):
    """Return the client cookies or fallback to server_cookies.txt if empty."""
    if client_cookies_text and client_cookies_text.strip():
        return client_cookies_text

    server_cookies_path = os.path.join(os.path.dirname(__file__), "server_cookies.txt")
    if os.path.exists(server_cookies_path):
        try:
            with open(server_cookies_path, "r") as f:
                return f.read()
        except Exception:
            pass
    return ""

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

def bg_download(download_id, url, quality, filename, save_dir, audio_format, audio_bitrate, ratelimit, cookies_browser="", cookies_text="", user_agent="", client_id="", download_subtitles=False, download_metadata=False):
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

        if download_subtitles:
            ydl_opts["writesubtitles"] = True
            ydl_opts["writeautomaticsub"] = True
            ydl_opts["subtitleslangs"] = ['all']
            ydl_opts["subtitlesformat"] = "best"

        if download_metadata:
            ydl_opts["writeinfojson"] = True
            ydl_opts["writethumbnail"] = True
        effective_cookies_text = get_effective_cookies(cookies_text)
        has_cookies = bool(effective_cookies_text or cookies_browser)
        ext_args = get_extractor_args(url, has_cookies=has_cookies)
        if ext_args:
            ydl_opts["extractor_args"] = ext_args
        if user_agent:
            ydl_opts["user_agent"] = user_agent
        if ratelimit:
            ydl_opts["ratelimit"] = ratelimit

        if effective_cookies_text:
            import tempfile
            tf = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False)
            tf.write(effective_cookies_text)
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
                "timestamp": int(uuid.uuid4().time / 10000000),
                "client_id": client_id
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


def bg_batch_download(batch_id, items, quality, save_dir, audio_format, audio_bitrate, ratelimit, cookies_browser="", cookies_text="", user_agent="", client_id="", download_subtitles=False, download_metadata=False):
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
        }

        if download_subtitles:
            ydl_opts["writesubtitles"] = True
            ydl_opts["writeautomaticsub"] = True
            ydl_opts["subtitleslangs"] = ['all']
            ydl_opts["subtitlesformat"] = "best"

        if download_metadata:
            ydl_opts["writeinfojson"] = True
            ydl_opts["writethumbnail"] = True
        effective_cookies_text = get_effective_cookies(cookies_text)
        has_cookies = bool(effective_cookies_text or cookies_browser)
        ext_args = get_extractor_args(url, has_cookies=has_cookies)
        if ext_args:
            ydl_opts["extractor_args"] = ext_args
        if user_agent:
            ydl_opts["user_agent"] = user_agent
        if ratelimit:
            ydl_opts["ratelimit"] = ratelimit

        temp_cookie_file = None
        try:
            effective_cookies_text = get_effective_cookies(cookies_text)
            if effective_cookies_text:
                import tempfile
                tf = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False)
                tf.write(effective_cookies_text)
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
                "timestamp": int(uuid.uuid4().time / 10000000),
                "client_id": client_id
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
