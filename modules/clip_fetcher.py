import os
import subprocess
import requests
import time
from pathlib import Path
from urllib.parse import quote

FFMPEG = r"C:\Users\higpu\AppData\Local\Microsoft\WinGet\Links\ffmpeg.exe"
FFPROBE = FFMPEG.replace("ffmpeg.exe", "ffprobe.exe")

MAX_ARCHIVE_SIZE_MB = 200
PREFERRED_FORMATS = ["h.264 HD", "h.264", "MPEG4", "512Kb MPEG4"]


def _get_video_codec(video_path: str) -> str:
    """Detect video codec using ffprobe."""
    result = subprocess.run([
        FFPROBE, "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=codec_name",
        "-of", "default=noprint_wrappers=1:nokey=1",
        video_path
    ], capture_output=True, text=True)
    return result.stdout.strip().lower()


def _validate_clip(video_path: str, min_duration: float = 3.0) -> bool:
    """
    Verify the file is a real playable video with a valid stream and minimum duration.
    Returns False for corrupt files, images masquerading as video, or clips too short.
    """
    try:
        result = subprocess.run([
            FFPROBE, "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=codec_name,duration",
            "-of", "default=noprint_wrappers=1",
            video_path
        ], capture_output=True, text=True, timeout=15)
        output = result.stdout.strip()
        if not output or "codec_name" not in output:
            return False
        # Check duration if available
        for line in output.splitlines():
            if line.startswith("duration="):
                val = line.split("=", 1)[1].strip()
                if val not in ("N/A", "") :
                    if float(val) < min_duration:
                        return False
        return True
    except Exception:
        return False


def _normalize_to_h264(input_path: str, output_path: str) -> bool:
    """Transcode a video to h264/mp4 if it isn't already. Returns True on success."""
    result = subprocess.run([
        FFMPEG, "-y", "-i", input_path,
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        output_path
    ], capture_output=True, text=True)
    return result.returncode == 0


def _try_archive_org(archive_query: str, output_path: str) -> bool:
    """
    Search Internet Archive for public domain video footage.
    Downloads first usable clip found (h.264 preferred, ≤MAX_ARCHIVE_SIZE_MB).
    Returns True if a clip was saved to output_path.
    """
    terms = archive_query.strip()
    search_url = "https://archive.org/advancedsearch.php"

    # Try two query strategies: subject-based then full-text
    queries = [
        f"({terms}) AND mediatype:movies AND (format:(h.264) OR format:(MPEG4))",
        f"{terms} AND mediatype:movies AND (licenseurl:(*creativecommons*) OR licenseurl:(*publicdomain*) OR subject:(newsreel) OR subject:(public domain))",
    ]

    for query in queries:
        try:
            resp = requests.get(search_url, params={
                "q": query,
                "fl[]": ["identifier", "title", "downloads"],
                "rows": 8,
                "sort[]": "downloads desc",
                "output": "json"
            }, timeout=15)
            if resp.status_code != 200:
                continue

            docs = resp.json().get("response", {}).get("docs", [])
            for doc in docs:
                identifier = doc.get("identifier", "")
                if not identifier:
                    continue

                # Get file list for this item
                try:
                    meta = requests.get(
                        f"https://archive.org/metadata/{identifier}/files",
                        timeout=10
                    ).json()
                except Exception:
                    continue

                result_files = meta.get("result", [])

                # Pick the best available video file
                chosen = None
                for fmt in PREFERRED_FORMATS:
                    for f in result_files:
                        if f.get("format", "") == fmt:
                            size_bytes = int(f.get("size", 0))
                            if size_bytes == 0 or size_bytes <= MAX_ARCHIVE_SIZE_MB * 1024 * 1024:
                                chosen = f
                                break
                    if chosen:
                        break

                if not chosen:
                    continue

                filename = chosen["name"]
                download_url = f"https://archive.org/download/{identifier}/{quote(filename)}"
                size_mb = int(chosen.get("size", 0)) / (1024 * 1024)
                safe_title = doc.get('title', identifier)[:50].encode('ascii', errors='replace').decode('ascii')
                print(f"    [archive.org] {safe_title} ({size_mb:.0f}MB)")

                # Download in chunks
                try:
                    dl = requests.get(download_url, stream=True, timeout=60)
                    if dl.status_code != 200:
                        continue

                    raw_path = output_path.replace(".mp4", "_raw" + Path(filename).suffix)
                    Path(raw_path).parent.mkdir(parents=True, exist_ok=True)
                    with open(raw_path, "wb") as fp:
                        for chunk in dl.iter_content(chunk_size=1024 * 1024):
                            fp.write(chunk)
                except Exception as e:
                    print(f"    [archive.org] Download failed: {e}")
                    continue

                # Normalize to h264 if needed
                codec = _get_video_codec(raw_path)
                if codec == "h264" and raw_path.endswith(".mp4"):
                    Path(raw_path).rename(output_path)
                else:
                    print(f"    [archive.org] Transcoding {codec} → h264...")
                    success = _normalize_to_h264(raw_path, output_path)
                    Path(raw_path).unlink(missing_ok=True)
                    if not success:
                        continue

                if Path(output_path).exists() and Path(output_path).stat().st_size > 10000:
                    if _validate_clip(output_path):
                        return True
                    else:
                        print(f"    [archive.org] Invalid/corrupt clip, skipping")
                        Path(output_path).unlink(missing_ok=True)

        except Exception as e:
            print(f"    [archive.org] Search error: {e}")
            continue

        time.sleep(1)

    return False


def _try_pexels_video(pexels_query: str, output_path: str) -> bool:
    """
    Fetch a free stock video clip from Pexels Video API.
    Prefers HD, landscape, duration > 10s.
    Returns True if clip saved to output_path.
    """
    api_key = os.environ.get("PEXELS_API_KEY", "")
    if not api_key:
        return False

    try:
        resp = requests.get(
            "https://api.pexels.com/videos/search",
            headers={"Authorization": api_key},
            params={
                "query": pexels_query,
                "per_page": 10,
                "orientation": "landscape",
                "size": "medium"
            },
            timeout=15
        )
        if resp.status_code != 200:
            return False

        videos = resp.json().get("videos", [])
        # Sort by duration descending (longer = more useful)
        videos = sorted(videos, key=lambda v: v.get("duration", 0), reverse=True)

        for video in videos:
            if video.get("duration", 0) < 10:
                continue

            video_files = video.get("video_files", [])
            hd = [f for f in video_files if f.get("quality") == "hd"]
            sd = [f for f in video_files if f.get("quality") == "sd"]
            candidates = hd or sd
            if not candidates:
                continue

            # Prefer landscape files
            landscape = [f for f in candidates if (f.get("width", 0) or 0) >= (f.get("height", 0) or 0)]
            chosen_file = (landscape or candidates)[0]
            link = chosen_file.get("link", "")
            if not link:
                continue

            print(f"    [pexels] {video.get('url', 'video')[:50]} ({video.get('duration')}s)")
            try:
                dl = requests.get(link, stream=True, timeout=60)
                if dl.status_code != 200:
                    continue

                Path(output_path).parent.mkdir(parents=True, exist_ok=True)
                with open(output_path, "wb") as fp:
                    for chunk in dl.iter_content(chunk_size=1024 * 1024):
                        fp.write(chunk)

                if Path(output_path).exists() and Path(output_path).stat().st_size > 10000:
                    if _validate_clip(output_path):
                        return True
                    else:
                        print(f"    [pexels] Invalid clip, skipping")
                        Path(output_path).unlink(missing_ok=True)
            except Exception as e:
                print(f"    [pexels] Download failed: {e}")
                continue

    except Exception as e:
        print(f"    [pexels] Error: {e}")

    return False


def fetch_clip_from_query(search_query: str, output_path: str) -> bool:
    """
    Derive archive/pexels queries from a general image search_query string
    and attempt to fetch a matching video clip.
    Returns True if a clip was saved to output_path.
    """
    clip_search = {
        "archive_query": search_query,
        "pexels_query": " ".join(search_query.split()[:4]),
    }
    return fetch_clip(clip_search, output_path)


def _try_national_archives(query: str, output_path: str) -> bool:
    """Search US National Archives for public domain government video footage."""
    try:
        resp = requests.get("https://catalog.archives.gov/api/v2/records/search", params={
            "q": query, "resultTypes": "item",
            "mediaTypes": "moving images", "rows": "10"
        }, timeout=15)
        if resp.status_code != 200:
            return False
        results = resp.json().get("body", {}).get("hits", {}).get("hits", [])
        for hit in results:
            src = hit.get("_source", {})
            title = src.get("title", "")
            objects = src.get("objects", [])
            for obj in objects:
                for file_info in obj.get("files", []):
                    url = file_info.get("url", "")
                    if not url or not any(url.lower().endswith(ext) for ext in [".mp4", ".mov", ".mpeg", ".mpg"]):
                        continue
                    size = file_info.get("fileSize", 0)
                    if size and int(size) > MAX_ARCHIVE_SIZE_MB * 1024 * 1024:
                        continue
                    safe_title = title[:50].encode('ascii', errors='replace').decode('ascii')
                    print(f"    [national archives] {safe_title}")
                    try:
                        dl = requests.get(url, stream=True, timeout=60)
                        if dl.status_code != 200:
                            continue
                        raw_path = output_path.replace(".mp4", "_raw.mp4")
                        Path(raw_path).parent.mkdir(parents=True, exist_ok=True)
                        with open(raw_path, "wb") as fp:
                            for chunk in dl.iter_content(chunk_size=1024 * 1024):
                                fp.write(chunk)
                        codec = _get_video_codec(raw_path)
                        if codec == "h264" and raw_path.endswith(".mp4"):
                            Path(raw_path).replace(output_path)
                        else:
                            _normalize_to_h264(raw_path, output_path)
                            Path(raw_path).unlink(missing_ok=True)
                        if Path(output_path).exists() and _validate_clip(output_path):
                            return True
                        Path(output_path).unlink(missing_ok=True)
                    except Exception:
                        continue
    except Exception as e:
        print(f"    [national archives] Error: {e}")
    return False


def _try_nasa_video(query: str, output_path: str) -> bool:
    """Search NASA image/video library for public domain science footage."""
    try:
        resp = requests.get("https://images-api.nasa.gov/search", params={
            "q": query, "media_type": "video"
        }, timeout=15)
        if resp.status_code != 200:
            return False
        items = resp.json().get("collection", {}).get("items", [])
        for item in items:
            data = item.get("data", [{}])[0]
            title = data.get("title", "")
            nasa_id = data.get("nasa_id", "")
            if not nasa_id:
                continue
            # Get asset manifest
            try:
                asset_resp = requests.get(f"https://images-api.nasa.gov/asset/{nasa_id}", timeout=10)
                if asset_resp.status_code != 200:
                    continue
                asset_items = asset_resp.json().get("collection", {}).get("items", [])
                for a in asset_items:
                    href = a.get("href", "")
                    if "~orig." in href or href.endswith(".mp4"):
                        safe_title = title[:50].encode('ascii', errors='replace').decode('ascii')
                        print(f"    [nasa] {safe_title}")
                        dl = requests.get(href, stream=True, timeout=60)
                        if dl.status_code != 200:
                            continue
                        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
                        with open(output_path, "wb") as fp:
                            for chunk in dl.iter_content(chunk_size=1024 * 1024):
                                fp.write(chunk)
                        if Path(output_path).exists() and _validate_clip(output_path):
                            return True
                        Path(output_path).unlink(missing_ok=True)
            except Exception:
                continue
    except Exception as e:
        print(f"    [nasa] Error: {e}")
    return False


def _try_pixabay_video(query: str, output_path: str) -> bool:
    """Search Pixabay for free stock video. Requires PIXABAY_API_KEY."""
    api_key = os.environ.get("PIXABAY_API_KEY", "")
    if not api_key:
        return False
    try:
        resp = requests.get("https://pixabay.com/api/videos/", params={
            "key": api_key, "q": query, "video_type": "film",
            "orientation": "horizontal", "per_page": "10"
        }, timeout=15)
        if resp.status_code != 200:
            return False
        hits = resp.json().get("hits", [])
        for hit in hits:
            videos = hit.get("videos", {})
            # Prefer large, then medium
            for size in ["large", "medium", "small"]:
                v = videos.get(size, {})
                url = v.get("url", "")
                if not url:
                    continue
                print(f"    [pixabay] {hit.get('tags', query)[:50]}")
                dl = requests.get(url, stream=True, timeout=60)
                if dl.status_code != 200:
                    continue
                Path(output_path).parent.mkdir(parents=True, exist_ok=True)
                with open(output_path, "wb") as fp:
                    for chunk in dl.iter_content(chunk_size=1024 * 1024):
                        fp.write(chunk)
                if Path(output_path).exists() and _validate_clip(output_path):
                    return True
                Path(output_path).unlink(missing_ok=True)
    except Exception as e:
        print(f"    [pixabay] Error: {e}")
    return False


def fetch_clip(clip_search: dict, output_path: str) -> bool:
    """
    Attempt to fetch a video clip for a scene.
    Tries Archive.org (public domain) then Pexels Video (free stock).
    clip_search: {"archive_query": str, "pexels_query": str}
    output_path: where to save the .mp4
    Returns True if a clip was fetched, False if caller should fall back to images.
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    archive_q = clip_search.get("archive_query", "")
    pexels_q = clip_search.get("pexels_query", "")

    if _try_archive_org(archive_q, output_path):
        return True
    if _try_national_archives(archive_q, output_path):
        return True
    if _try_nasa_video(archive_q, output_path):
        return True
    if _try_pixabay_video(pexels_q, output_path):
        return True
    if _try_pexels_video(pexels_q, output_path):
        return True

    return False
