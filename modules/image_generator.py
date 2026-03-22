import os
import time
import requests
from pathlib import Path
from urllib.parse import quote
try:
    from ddgs import DDGS
except ImportError:
    from duckduckgo_search import DDGS

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


def _subject_words(subject: str) -> list:
    """Extract meaningful words from subject for result validation (skip short words and titles)."""
    skip = {"dr", "mr", "mrs", "ms", "prof", "sir", "the", "of", "in", "at", "and", "for"}
    return [w.lower().strip(".,()") for w in subject.split()
            if len(w) > 3 and w.lower().strip(".,()") not in skip]


def _score_candidate(title: str, subject: str, search_query: str) -> float:
    """
    Score a result candidate by how well its title matches the subject and query.
    Higher = better match. Used when strict validation fails to pick best available.
    """
    import re
    title_lower = title.lower()
    score = 0.0

    # Subject word matches (most important)
    name_skip = {"mr", "mrs", "ms", "sir", "jr", "sr"}
    name_words = [w.lower().strip(".,()") for w in subject.split()
                  if w.lower().strip(".,()") not in name_skip and len(w) > 1]
    if name_words:
        matched = sum(1 for w in name_words if w in title_lower)
        score += (matched / len(name_words)) * 10  # up to 10 points for subject match

    # Query keyword matches (secondary)
    query_words = [w.lower() for w in search_query.split() if len(w) > 3]
    if query_words:
        matched = sum(1 for w in query_words if w in title_lower)
        score += (matched / len(query_words)) * 4  # up to 4 points for query match

    # Year match bonus
    years = re.findall(r'\b(19\d\d|20\d\d)\b', search_query)
    if years and any(yr in title_lower for yr in years):
        score += 2

    return score


def _collect_candidates_ddg(search_query: str, subject: str, max_results: int = 15) -> list:
    """Collect scored (url, title, score) candidates from DDG without downloading."""
    candidates = []
    try:
        with DDGS() as ddgs:
            results = list(ddgs.images(search_query, max_results=max_results, type_image="photo"))
        for r in results:
            title = r.get("title", "") + " " + r.get("url", "")
            score = _score_candidate(title, subject, search_query)
            if score > 0:
                candidates.append((r["image"], title.strip(), score))
    except Exception:
        pass
    return candidates


def _collect_candidates_wikimedia(search_query: str, subject: str) -> list:
    """Collect scored candidates from Wikimedia Commons."""
    candidates = []
    try:
        resp = requests.get("https://commons.wikimedia.org/w/api.php", params={
            "action": "query", "list": "search", "srsearch": search_query,
            "srnamespace": "6", "srlimit": "20", "format": "json"
        }, headers=HEADERS, timeout=15)
        for item in resp.json().get("query", {}).get("search", []):
            title = item.get("title", "")
            score = _score_candidate(title, subject, search_query)
            if score > 0:
                # Get image URL
                info = requests.get("https://commons.wikimedia.org/w/api.php", params={
                    "action": "query", "titles": title,
                    "prop": "imageinfo", "iiprop": "url", "format": "json"
                }, headers=HEADERS, timeout=10).json()
                for page in info.get("query", {}).get("pages", {}).values():
                    url = page.get("imageinfo", [{}])[0].get("url", "")
                    if url:
                        candidates.append((url, title, score))
    except Exception:
        pass
    return candidates


def _last_name(subject: str) -> str:
    """Extract last name from a person/place subject — the most distinctive word."""
    skip = {"dr", "mr", "mrs", "ms", "prof", "sir", "jr", "sr", "phd", "md"}
    words = [w.strip(".,()") for w in subject.split()]
    # Return the last word that isn't a suffix/title
    for w in reversed(words):
        if w.lower() not in skip and len(w) > 2:
            return w.lower()
    return ""


def _title_matches_subject(title: str, subject: str, search_query: str = "") -> bool:
    """
    Return True if the result title credibly matches the subject.

    For person subjects (2+ name words): requires BOTH first name AND last name in title.
    This prevents 'Vladimir Putin' matching 'Vladimir Pasechnik', or a different
    'Dr. Robert Schwartz' (plastic surgeon) matching the murdered microbiologist.

    For single-word subjects (places, events, acronyms): requires that word in title.
    """
    if not subject:
        return True
    title_lower = title.lower()

    import re
    # Keep professional titles (dr, prof) as required match words — if the script says
    # "Dr. John Tate" then results must also reference "dr" to avoid matching unrelated
    # people who share the same first/last name (e.g. John Tate the boxer).
    # Only skip non-title filler words.
    name_skip = {"mr", "mrs", "ms", "sir", "jr", "sr"}
    name_words = [w.lower().strip(".,()") for w in subject.split()
                  if w.lower().strip(".,()") not in name_skip and len(w) > 1]

    if not name_words:
        return True

    if len(name_words) >= 2:
        # Require ALL name words to appear in the result title.
        # e.g. "Dr. Victor Korshunov" needs "dr", "victor", AND "korshunov" —
        # prevents "Dr. Yevgeniy Korshunov" (orthopedic surgeon) matching.
        if all(w in title_lower for w in name_words):
            return True
        # Relaxed fallback: all words match AND year from search_query is in title
        last_word = name_words[-1]
        if search_query and last_word in title_lower:
            years = re.findall(r'\b(19\d\d|20\d\d)\b', search_query)
            if any(yr in title_lower for yr in years):
                # Still require at least prefix+last or first+last
                if name_words[0] in title_lower:
                    return True
        return False
    else:
        # Single-word subject: just require it's present
        return name_words[0] in title_lower


def _try_web_search(search_query: str, output_path: str, subject: str = "") -> bool:
    """Try to find a real photo via DuckDuckGo. Returns True if successful."""
    try:
        with DDGS() as ddgs:
            results = list(ddgs.images(search_query, max_results=15, type_image="photo"))

        for result in results:
            # Validate that the result title/url relates to the subject
            result_text = (result.get("title", "") + " " + result.get("url", "")).lower()
            if subject and not _title_matches_subject(result_text, subject, search_query):
                continue
            try:
                response = requests.get(result["image"], headers=HEADERS, timeout=10)
                if response.status_code == 200 and len(response.content) > 10000:
                    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
                    with open(output_path, "wb") as f:
                        f.write(response.content)
                    print(f"  [web] {result['title'][:60]}")
                    return True
            except Exception:
                continue
    except Exception as e:
        if "Ratelimit" not in str(e):
            print(f"  DDG error: {e}")
    return False


def _try_archive_org_image(search_query: str, output_path: str, subject: str = "", orig_query: str = "") -> bool:
    """
    Search Internet Archive for real historical photos and scanned newspapers/documents.
    Uses the Archive.org search API and item thumbnail service.
    When a year is detected in the query, restricts results to that era (±3 years).
    Validates results against subject name to avoid unrelated matches.
    Returns True if a usable image is saved to output_path.
    """
    import re
    search_url = "https://archive.org/advancedsearch.php"

    # Extract year from the original full query for date-range filtering
    ref_query = orig_query or search_query
    year_match = re.search(r'\b(19\d\d|20\d\d)\b', ref_query)
    date_filter = ""
    if year_match:
        yr = int(year_match.group(1))
        date_filter = f" date:[{yr-3}-01-01 TO {yr+3}-12-31]"

    # Two passes: first look for photos/images, then scanned texts (newspapers, magazines)
    searches = [
        {"q": f"{search_query}{date_filter} mediatype:image", "sort": "downloads desc"},
        {"q": f"{search_query}{date_filter} mediatype:texts subject:(newspaper OR magazine OR clipping)", "sort": "downloads desc"},
    ]

    for search in searches:
        try:
            resp = requests.get(search_url, params={
                "q": search["q"],
                "fl[]": ["identifier", "title"],
                "rows": 20,
                "sort[]": search["sort"],
                "output": "json"
            }, timeout=15)
            if resp.status_code != 200:
                continue

            docs = resp.json().get("response", {}).get("docs", [])
            for doc in docs:
                identifier = doc.get("identifier", "")
                title = doc.get("title", identifier)
                if not identifier:
                    continue

                # Validate result title matches subject before downloading
                check_text = f"{title} {identifier}"
                if not _title_matches_subject(check_text, subject, orig_query or search_query):
                    continue

                img_url = f"https://archive.org/services/img/{identifier}"
                try:
                    response = requests.get(img_url, headers=HEADERS, timeout=15)
                    if response.status_code == 200 and len(response.content) > 10000:
                        content_type = response.headers.get("content-type", "")
                        if "image" not in content_type:
                            continue
                        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
                        with open(output_path, "wb") as f:
                            f.write(response.content)
                        safe_title = title[:60].encode('ascii', errors='replace').decode('ascii')
                        print(f"  [archive.org] {safe_title}")
                        return True
                except Exception:
                    continue

            time.sleep(1)

        except Exception as e:
            print(f"  [archive.org image] Error: {e}")
            continue

    return False


def _download_image(url: str, output_path: str, label: str, subject: str = "", search_query: str = "", title_check: str = "") -> bool:
    """Shared helper: validate title, download image, save to output_path."""
    if title_check and subject and not _title_matches_subject(title_check, subject, search_query):
        return False
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code == 200 and len(resp.content) > 10000:
            ct = resp.headers.get("content-type", "")
            if "image" not in ct and not url.lower().endswith((".jpg", ".jpeg", ".png", ".gif", ".webp")):
                return False
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, "wb") as f:
                f.write(resp.content)
            print(f"  [{label}] {title_check[:60].encode('ascii', errors='replace').decode('ascii')}")
            return True
    except Exception:
        pass
    return False


def _try_wikimedia_commons(search_query: str, output_path: str, subject: str = "") -> bool:
    """Search Wikimedia Commons for free images. No API key required."""
    try:
        resp = requests.get("https://commons.wikimedia.org/w/api.php", params={
            "action": "query", "list": "search", "srsearch": search_query,
            "srnamespace": "6", "srlimit": "20", "format": "json"
        }, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            return False
        results = resp.json().get("query", {}).get("search", [])
        for item in results:
            title = item.get("title", "")
            if not _title_matches_subject(title, subject, search_query):
                continue
            # Get direct image URL
            info_resp = requests.get("https://commons.wikimedia.org/w/api.php", params={
                "action": "query", "titles": title,
                "prop": "imageinfo", "iiprop": "url", "format": "json"
            }, headers=HEADERS, timeout=10)
            pages = info_resp.json().get("query", {}).get("pages", {})
            for page in pages.values():
                img_url = page.get("imageinfo", [{}])[0].get("url", "")
                if img_url and _download_image(img_url, output_path, "wikimedia", subject, search_query, title):
                    return True
    except Exception as e:
        print(f"  [wikimedia] Error: {e}")
    return False


def _try_loc_images(search_query: str, output_path: str, subject: str = "") -> bool:
    """Search Library of Congress free photo collection."""
    try:
        resp = requests.get("https://www.loc.gov/photos/", params={
            "q": search_query, "fo": "json", "c": "20"
        }, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            return False
        results = resp.json().get("results", [])
        for item in results:
            title = item.get("title", "")
            if not _title_matches_subject(title, subject, search_query):
                continue
            # Try to get a usable image URL
            image_url = ""
            for img in item.get("image_url", []):
                if isinstance(img, str) and img.startswith("http"):
                    image_url = img
                    break
            if not image_url:
                continue
            if _download_image(image_url, output_path, "loc.gov", subject, search_query, title):
                return True
    except Exception as e:
        print(f"  [loc.gov] Error: {e}")
    return False


def _try_chronicling_america(search_query: str, output_path: str, subject: str = "") -> bool:
    """Search Chronicling America (Library of Congress) for historical newspaper pages."""
    import re
    try:
        params = {"andtext": search_query, "format": "json", "rows": "20", "sort": "relevance"}
        # Add date range if year found
        year_match = re.search(r'\b(19\d\d|20\d\d)\b', search_query)
        if year_match:
            yr = int(year_match.group(1))
            params["date1"] = str(max(1770, yr - 3))
            params["date2"] = str(min(1963, yr + 3))
        resp = requests.get("https://chroniclingamerica.loc.gov/search/pages/results/",
                            params=params, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            return False
        items = resp.json().get("items", [])
        for item in items:
            title = item.get("title", "") + " " + " ".join(item.get("subject", []))
            if not _title_matches_subject(title, subject, search_query):
                continue
            thumb = item.get("thumbnail", "")
            if not thumb:
                continue
            url = f"https://chroniclingamerica.loc.gov{thumb}" if thumb.startswith("/") else thumb
            if _download_image(url, output_path, "chronicling america", subject, search_query, item.get("title", "")):
                return True
    except Exception as e:
        print(f"  [chronicling america] Error: {e}")
    return False


def _try_flickr(search_query: str, output_path: str, subject: str = "") -> bool:
    """Search Flickr Creative Commons photos. Requires FLICKR_API_KEY."""
    api_key = os.environ.get("FLICKR_API_KEY", "")
    if not api_key:
        return False
    try:
        resp = requests.get("https://api.flickr.com/services/rest/", params={
            "method": "flickr.photos.search", "api_key": api_key,
            "text": search_query, "license": "1,2,3,4,5,6,9,10",
            "content_type": "1", "per_page": "20", "format": "json",
            "nojsoncallback": "1", "sort": "relevance",
            "extras": "url_l,url_o,title"
        }, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            return False
        photos = resp.json().get("photos", {}).get("photo", [])
        for photo in photos:
            title = photo.get("title", "")
            if not _title_matches_subject(title, subject, search_query):
                continue
            img_url = photo.get("url_l") or photo.get("url_o", "")
            if not img_url:
                continue
            if _download_image(img_url, output_path, "flickr", subject, search_query, title):
                return True
    except Exception as e:
        print(f"  [flickr] Error: {e}")
    return False


def _try_pollinations(ai_prompt: str, output_path: str) -> bool:
    """Generate AI image via Pollinations.ai (free, no API key). Returns True if successful."""
    try:
        encoded = quote(ai_prompt[:500])
        url = f"https://image.pollinations.ai/prompt/{encoded}?width=1920&height=1080&nologo=true&seed={int(time.time())}"
        response = requests.get(url, timeout=60)
        if response.status_code == 200 and len(response.content) > 5000:
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, "wb") as f:
                f.write(response.content)
            print(f"  [AI generated] scene image")
            return True
    except Exception as e:
        print(f"  Pollinations error: {e}")
    return False

def _try_pexels(query: str, output_path: str) -> bool:
    """Fallback to Pexels stock photos."""
    api_key = os.environ.get("PEXELS_API_KEY", "")
    if not api_key:
        return False
    try:
        response = requests.get(
            "https://api.pexels.com/v1/search",
            headers={"Authorization": api_key},
            params={"query": query, "per_page": 1, "orientation": "landscape"}
        )
        if response.ok and response.json().get("photos"):
            img_url = response.json()["photos"][0]["src"]["landscape"]
            img_data = requests.get(img_url).content
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, "wb") as f:
                f.write(img_data)
            print(f"  [pexels fallback] {query[:50]}")
            return True
    except Exception:
        pass
    return False

def search_real_image(search_query: str, output_path: str, subject: str = "") -> bool:
    """
    Try to find a real photo for the query across multiple sources.
    All sources apply the same subject-name validation before accepting a result.
    Order: DDG → Wikimedia Commons → Library of Congress → Flickr CC →
           Archive.org → Chronicling America → subject-name fallbacks
    Returns True if a real image was saved, False otherwise.
    """
    if _try_web_search(search_query, output_path, subject=subject):
        return True
    time.sleep(0.5)
    if _try_wikimedia_commons(search_query, output_path, subject=subject):
        return True
    time.sleep(0.5)
    if _try_loc_images(search_query, output_path, subject=subject):
        return True
    time.sleep(0.5)
    if _try_flickr(search_query, output_path, subject=subject):
        return True
    time.sleep(0.5)
    if _try_archive_org_image(search_query, output_path, subject=subject, orig_query=search_query):
        return True
    time.sleep(0.5)
    if _try_chronicling_america(search_query, output_path, subject=subject):
        return True
    # Fallback: search by subject name alone across all sources
    if subject and subject.strip() and subject.strip().lower() != search_query.strip().lower():
        time.sleep(0.5)
        for fn in [_try_web_search, _try_wikimedia_commons, _try_loc_images,
                   _try_flickr, _try_archive_org_image, _try_chronicling_america]:
            try:
                kwargs = {"subject": subject}
                if fn == _try_archive_org_image:
                    kwargs["orig_query"] = search_query
                if fn(subject.strip(), output_path, **kwargs):
                    return True
                time.sleep(0.5)
            except Exception:
                continue
    # Last real-photo fallback: Pexels stock (real photos, just generic)
    time.sleep(0.5)
    if _try_pexels(search_query, output_path):
        return True
    return False


def search_best_available_image(search_query: str, output_path: str, subject: str = "") -> bool:
    """
    Fallback when strict subject validation found nothing.
    Collects candidates from DDG and Wikimedia Commons, scores each by how well
    its title matches the subject + query, and downloads the best scoring one.
    """
    all_candidates = []

    # Collect from DDG
    all_candidates.extend(_collect_candidates_ddg(search_query, subject))
    time.sleep(0.5)
    # Also try subject-only search for more candidates
    if subject and subject.lower() != search_query.lower():
        all_candidates.extend(_collect_candidates_ddg(subject, subject))
        time.sleep(0.5)
    # Collect from Wikimedia
    all_candidates.extend(_collect_candidates_wikimedia(search_query, subject))

    if not all_candidates:
        return False

    # Sort by score descending, try downloading from highest score first
    all_candidates.sort(key=lambda x: x[2], reverse=True)
    for url, title, score in all_candidates[:5]:  # try top 5
        try:
            resp = requests.get(url, headers=HEADERS, timeout=10)
            if resp.status_code == 200 and len(resp.content) > 10000:
                ct = resp.headers.get("content-type", "")
                if "image" in ct or url.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
                    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
                    with open(output_path, "wb") as f:
                        f.write(resp.content)
                    safe_title = title[:60].encode('ascii', errors='replace').decode('ascii')
                    print(f"  [best match] {safe_title} (score={score:.1f})")
                    return True
        except Exception:
            continue
    return False


def generate_ai_image(ai_prompt: str, search_query: str, output_path: str) -> bool:
    """
    Generate or fetch a fallback image when no real photo was found.
    Tries Pollinations.ai then Pexels stock.
    Returns True if an image was saved.
    """
    if _try_pollinations(ai_prompt, output_path):
        return True
    keywords = " ".join(search_query.split()[:4])
    if _try_pexels(keywords, output_path):
        return True
    return False


def generate_image(search_query: str, ai_prompt: str, output_path: str, subject: str = "") -> str:
    """
    Backward-compatible wrapper: find or generate the best single image.
    """
    if search_real_image(search_query, output_path, subject=subject):
        return output_path
    time.sleep(1)
    if generate_ai_image(ai_prompt, search_query, output_path):
        return output_path
    raise Exception(f"Could not find or generate image for: {search_query}")
