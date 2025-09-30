#!/usr/bin/env python3
import argparse, csv, hashlib, io, re, sys, time
from pathlib import Path
from typing import Optional, Tuple, Dict, Any, List

import requests

# Pillow for image size/format (art updates use it)
try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    Image = None
    PIL_AVAILABLE = False
    print("[INFO] Pillow not installed; album-art size checks/embeds will be partially disabled.")

# ===================== CONFIG =====================
# Discogs credentials
# Prefer Consumer Key/Secret for official app + higher rate limit + image URLs
DISCOGS_KEY    = "xACNKfxTAAJOoYlrUPWT"
DISCOGS_SECRET = "OWMNTnnqQgCHPlIJHIHYTmOqCuVEAHWJ"

# (Optional) If you also want to support a personal token for yourself,
# put it here; the script will send both header and token param when helpful.
DISCOGS_TOKEN = None  # e.g., "put-your-personal-access-token-here"

DISCOGS_SEARCH = "https://api.discogs.com/database/search"
USER_AGENT = "Discogs Tag & Art Fixer/2.9 (+https://github.com/your-username/discogs-tag-art-fixer)"

PLACEHOLDER_FILENAME = "placeholder.jpg"  # file next to script
MIN_ART_SIZE = 500                         # px (width or height)
RETRY_MAX_ROUNDS = 3                       # retry waves after the main pass
# ==================================================

# --- mutagen (tags) ---
try:
    from mutagen import MutagenError, File as MutagenFile
    from mutagen.id3 import ID3, APIC, TDRC, TYER, TXXX, TPUB, ID3NoHeaderError
    from mutagen.flac import FLAC, Picture
    from mutagen.mp4 import MP4, MP4Cover, MP4FreeForm
    from mutagen.oggvorbis import OggVorbis
    from mutagen.oggopus import OggOpus
    from mutagen.aiff import AIFF
    from mutagen.wave import WAVE
    from mutagen.asf import ASF
except ImportError as e:
    print("ERROR: mutagen is required. Install with:  python -m pip install mutagen")
    sys.exit(1)

MIX_RE = re.compile(r"\(([^)]+)\)", flags=re.IGNORECASE)

ALL_AUDIO_EXTS = {
    ".mp3", ".flac", ".m4a", ".mp4", ".alac", ".aac",
    ".wav", ".aif", ".aiff",
    ".ogg", ".oga", ".opus",
    ".wma"
}

# ---------------------- util ----------------------
def md5_bytes(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()

def image_size_from_bytes(img_bytes: bytes) -> Tuple[int, int]:
    if not PIL_AVAILABLE:
        return (0, 0)
    im = Image.open(io.BytesIO(img_bytes))
    return im.width, im.height

def normalize(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"[^\w\s&]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()

def tokens(s: str) -> set:
    return set(normalize(s).split())

def title_similarity(a: str, b: str) -> float:
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb: return 0.0
    return len(ta & tb) / len(ta | tb)

def coerce_year(value) -> str:
    """Return a clean 'YYYY' from values like 2025, '2025//2025', '2019\\2019', '1999-03-01'."""
    if value is None:
        return ""
    if isinstance(value, int):
        return str(value) if 1900 <= value <= 2100 else ""
    s = str(value)
    # Normalize weird slashes/backslashes like "2019\\2019"
    s = s.replace("\\", "/")
    parts = s.split("/")
    if parts and len(parts[0]) == 4 and parts[0].isdigit():
        y0 = int(parts[0])
        return str(y0) if 1900 <= y0 <= 2100 else ""
    # fallback regex search
    m = re.search(r'(?<!\d)(19\d{2}|20\d{2}|2100)(?!\d)', s)
    if not m:
        return ""
    y = int(m.group(0))
    return str(y) if 1900 <= y <= 2100 else ""

# ---------------------- filename fallback ----------------------
def parse_filename(name: str) -> Tuple[str, str, Optional[str]]:
    stem = Path(name).stem
    parts = stem.split(" - ", 1)
    if len(parts) != 2:
        return ("", stem.strip(), None)
    artist, right = parts[0].strip(), parts[1].strip()
    mix = None
    paren = MIX_RE.findall(right)
    if paren:
        mix = paren[-1].strip()
        right = MIX_RE.sub("", right).strip()
    title = re.sub(r"\s+", " ", right).strip()
    return (artist, title, mix)

# ---------------------- read artist/title from tags ----------------------
def get_artist_title_from_tags(path: Path) -> Tuple[str, str, Optional[str]]:
    artist, title, mix = "", "", None
    try:
        audio = MutagenFile(path)
        if audio is None:
            raise MutagenError("Unrecognized format")

        # MP3 (ID3)
        if isinstance(audio, ID3) or path.suffix.lower() == ".mp3":
            try:
                tags = ID3(path)
            except ID3NoHeaderError:
                tags = None
            if tags:
                if "TPE1" in tags and tags["TPE1"].text:
                    artist = str(tags["TPE1"].text[0]).strip()
                if "TIT2" in tags and tags["TIT2"].text:
                    title = str(tags["TIT2"].text[0]).strip()

        # FLAC / Ogg Vorbis / Opus
        if isinstance(audio, (FLAC, OggVorbis, OggOpus)):
            for k in ("artist","ARTIST"):
                if k in audio and audio[k]:
                    artist = artist or audio[k][0].strip()
            for k in ("title","TITLE"):
                if k in audio and audio[k]:
                    title = title or audio[k][0].strip()

        # MP4/M4A/ALAC
        if isinstance(audio, MP4) or path.suffix.lower() in (".m4a",".mp4",".alac"):
            if audio.tags:
                if "\xa9ART" in audio.tags and audio.tags["\xa9ART"]:
                    artist = artist or str(audio.tags["\xa9ART"][0]).strip()
                if "\xa9nam" in audio.tags and audio.tags["\xa9nam"]:
                    title = title or str(audio.tags["\xa9nam"][0]).strip()

        # WMA (ASF)
        if isinstance(audio, ASF) or path.suffix.lower() == ".wma":
            if audio.tags:
                if "Author" in audio.tags and audio.tags["Author"]:
                    artist = artist or str(audio.tags["Author"][0].value).strip()
                if "Title" in audio.tags and audio.tags["Title"]:
                    title = title or str(audio.tags["Title"][0].value).strip()

        # WAV/AIFF – try ID3
        if isinstance(audio, (WAVE, AIFF)) or path.suffix.lower() in (".wav",".aif",".aiff"):
            try:
                tags = ID3(path)
                if "TPE1" in tags and tags["TPE1"].text:
                    artist = artist or str(tags["TPE1"].text[0]).strip()
                if "TIT2" in tags and tags["TIT2"].text:
                    title = title or str(tags["TIT2"].text[0]).strip()
            except ID3NoHeaderError:
                pass

    except MutagenError:
        pass

    if not artist or not title:
        fa, ft, fm = parse_filename(path.name)
        artist = artist or fa
        title  = title  or ft
        mix    = mix or fm

    if title and mix is None:
        paren = MIX_RE.findall(title)
        if paren: mix = paren[-1].strip()

    title_for_search = re.sub(r"\s*\([^)]*\)\s*", " ", title or "").strip()
    return artist or "", title_for_search or "", mix

# ---------------------- Discogs auth helpers ----------------------
def build_auth_headers() -> Dict[str, str]:
    # Use Discogs Auth header; key/secret identify the app and raise rate limits
    hdr = {
        "User-Agent": USER_AGENT,
        "Authorization": f"Discogs key={DISCOGS_KEY}, secret={DISCOGS_SECRET}",
    }
    return hdr

def maybe_auth_params(existing: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    # Optionally include token in query params (not required for search/details)
    params = dict(existing or {})
    if DISCOGS_TOKEN:
        params["token"] = DISCOGS_TOKEN
    return params

# ---------------------- Discogs search + ranking ----------------------
class RetryableDiscogsError(Exception): ...

def rank_results(results: List[Dict[str, Any]], artist: str, title: str, mix: Optional[str]) -> Optional[Dict[str, Any]]:
    artist_n, title_n, mix_n = normalize(artist), normalize(title), normalize(mix) if mix else None
    best, best_score = None, -1.0
    for res in results:
        res_title, res_year, res_type = res.get("title",""), res.get("year"), res.get("type")
        if " - " in res_title:
            r_artist, r_title = res_title.split(" - ", 1)
        else:
            r_artist, r_title = "", res_title
        r_artist_n, r_title_n = normalize(r_artist), normalize(r_title)
        artist_score = 1.0 if artist_n and (artist_n in r_artist_n or r_artist_n in artist_n) else title_similarity(artist_n, r_artist_n)
        title_score  = title_similarity(title_n, r_title_n)
        mix_bonus    = 0.15 if (mix_n and mix_n not in {"original mix"} and mix_n in normalize(res_title)) else 0.0
        type_bonus   = 0.2 if res_type == "master" else 0.0
        year_bonus   = 0.1 if isinstance(res_year, int) and (1900 <= res_year <= 2100) else 0.0
        score = 0.45*artist_score + 0.45*title_score + mix_bonus + type_bonus + year_bonus
        if score > best_score:
            best_score, best = score, res
    if best and best_score >= 0.35:
        best["_match_score"] = round(best_score, 3)
        return best
    return None

def discogs_search(artist: str, title: str, mix: Optional[str], delay: float) -> Optional[Dict[str, Any]]:
    headers = build_auth_headers()
    sess = requests.Session()
    queries: List[Dict[str, str]] = []
    if artist and title:
        q1 = {"artist": artist, "track": title}
        if mix and mix.lower() != "original mix": q1["q"] = mix
        queries.append(q1)
        queries.append({"artist": artist, "title": title})
    queries.append({"q": f"{artist} {title}".strip()})

    for params in queries:
        params = maybe_auth_params({
            **params,
            "type": "release",
            "sort": "relevance",
            "per_page": "10",
        })
        try:
            r = sess.get(DISCOGS_SEARCH, headers=headers, params=params, timeout=20)
            if r.status_code == 429:
                raise RetryableDiscogsError("Rate limited (429)")
            r.raise_for_status()
            data = r.json()
        except RetryableDiscogsError:
            raise
        except requests.RequestException as e:
            raise RetryableDiscogsError(f"Request failed: {e}")

        results = data.get("results", [])
        if not results:
            time.sleep(delay); continue
        ranked = rank_results(results, artist, title, mix)
        if ranked:
            time.sleep(delay); return ranked
        time.sleep(delay)
    return None

def fetch_release_details(resource_url: str) -> Optional[Dict[str, Any]]:
    headers = build_auth_headers()
    params  = maybe_auth_params({})
    try:
        r = requests.get(resource_url, headers=headers, params=params, timeout=20)
        if r.status_code == 429:
            raise RetryableDiscogsError("Rate limited (429) on details")
        r.raise_for_status()
        return r.json()
    except RetryableDiscogsError:
        raise
    except requests.RequestException as e:
        raise RetryableDiscogsError(f"Details request failed: {e}")

def choose_best_image(images: List[Dict[str, Any]], min_size: int = MIN_ART_SIZE) -> Optional[str]:
    if not images: return None
    scored = []
    for img in images:
        w, h = int(img.get("width",0)), int(img.get("height",0))
        uri   = img.get("uri") or img.get("resource_url")
        if not uri: continue
        scored.append(((w*h) + (1_000_000 if img.get("type")=="primary" else 0), w, h, uri))
    if not scored: return None
    scored.sort(reverse=True)
    for _, w, h, uri in scored:
        if max(w,h) >= min_size: return uri
    return scored[0][3]

def download_image(url: str) -> Optional[bytes]:
    headers = build_auth_headers()
    try:
        # Discogs image endpoints usually work without auth, but include header anyway
        r = requests.get(url, headers=headers, timeout=30)
        if r.status_code in (401, 403):
            # As fallback, try passing token param if available
            r = requests.get(url, headers=headers, params=maybe_auth_params({}), timeout=30)
        r.raise_for_status()
        return r.content
    except requests.RequestException:
        return None

# ---------------------- art read/write (MP3/FLAC/MP4-M4A) ----------------------
def read_embedded_art(path: Path) -> Optional[bytes]:
    try:
        ext = path.suffix.lower()
        if ext == ".mp3":
            try: tags = ID3(path)
            except ID3NoHeaderError: return None
            apics = tags.getall("APIC")
            return bytes(apics[0].data) if apics else None
        elif ext == ".flac":
            audio = FLAC(path)
            return bytes(audio.pictures[0].data) if audio.pictures else None
        elif ext in (".m4a", ".mp4", ".alac"):
            audio = MP4(path)
            covr = audio.tags.get("covr")
            if covr and len(covr) > 0:
                c0 = covr[0]
                return bytes(c0)  # MP4Cover is bytes-like
    except MutagenError:
        return None
    return None

def remove_all_art(path: Path) -> None:
    try:
        ext = path.suffix.lower()
        if ext == ".mp3":
            try: tags = ID3(path)
            except ID3NoHeaderError: return
            tags.delall("APIC"); tags.save(path)
        elif ext == ".flac":
            audio = FLAC(path); audio.clear_pictures(); audio.save()
        elif ext in (".m4a", ".mp4", ".alac"):
            audio = MP4(path)
            if "covr" in audio.tags:
                audio.tags["covr"] = []
                audio.save()
    except MutagenError as e:
        print(f"   [WARN] Failed to remove existing art: {e}")

def write_single_cover(path: Path, img_bytes: bytes, mime: str = "image/jpeg") -> bool:
    if not PIL_AVAILABLE:
        return False
    try:
        ext = path.suffix.lower()
        if ext == ".mp3":
            try: tags = ID3(path)
            except ID3NoHeaderError: tags = ID3()
            tags.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=img_bytes))
            tags.save(path); return True
        elif ext == ".flac":
            audio = FLAC(path)
            w,h = image_size_from_bytes(img_bytes)
            pic = Picture(); pic.type=3; pic.mime=mime; pic.desc="Cover"; pic.width=w; pic.height=h; pic.depth=24; pic.data=img_bytes
            audio.add_picture(pic); audio.save(); return True
        elif ext in (".m4a", ".mp4", ".alac"):
            audio = MP4(path)
            fmt = MP4Cover.FORMAT_JPEG
            if mime.lower() == "image/png":
                fmt = MP4Cover.FORMAT_PNG
            audio["covr"] = [MP4Cover(img_bytes, imageformat=fmt)]
            audio.save()
            return True
    except MutagenError as e:
        print(f"   [WARN] Failed to write art: {e}")
        return False
    return False

# ---------------------- tag writing (ALL formats) ----------------------
def write_year_label_tags(path: Path, year: Optional[str], label: Optional[str]) -> Tuple[bool, str]:
    """
    Write Year + Label into tags for MP3, WAV, AIFF (ID3), FLAC/Vorbis/Opus,
    MP4/M4A/ALAC, WMA(ASF). Returns (changed, note).
    """
    y   = coerce_year(year)
    lbl = (label.strip() if label else "")
    changed = False
    ext = path.suffix.lower()

    # ID3-targeted formats: MP3, WAV, AIFF
    if ext in (".mp3", ".wav", ".aif", ".aiff"):
        try:
            try:
                tags = ID3(path)
            except ID3NoHeaderError:
                tags = ID3()
            if y:
                tags.delall("TDRC"); tags.add(TDRC(encoding=3, text=y))
                tags.delall("TYER"); tags.add(TYER(encoding=3, text=y))
                changed = True
            if lbl:
                tags.delall("TPUB"); tags.add(TPUB(encoding=3, text=lbl))
                removed_any = False
                for frame in list(tags.getall("TXXX")):
                    if getattr(frame, "desc", "").upper() == "LABEL":
                        removed_any = True
                if removed_any:
                    tags.delall("TXXX")
                tags.add(TXXX(encoding=3, desc="LABEL", text=lbl))
                changed = True
            if changed: tags.save(path)
            return changed, "ok" if changed else "unchanged"
        except MutagenError as e:
            return False, f"write_failed: {e}"

    # FLAC / Vorbis / Opus
    if ext in (".flac", ".ogg", ".oga", ".opus"):
        try:
            audio = MutagenFile(path)
            if audio is None: return False, "unsupported_format"
            if y:
                audio["DATE"] = [y]
                audio["YEAR"] = [y]
                changed = True
            if lbl:
                audio["LABEL"] = [lbl]
                audio["PUBLISHER"] = [lbl]
                changed = True
            if changed: audio.save()
            return changed, "ok" if changed else "unchanged"
        except MutagenError as e:
            return False, f"write_failed: {e}"

    # MP4/M4A/ALAC (+ .aac in MP4 container)
    if ext in (".m4a", ".mp4", ".alac", ".aac"):
        try:
            audio = MP4(path)
            if y:
                audio["\xa9day"] = [y]   # Year/Date
                changed = True
            if lbl:
                audio["----:com.apple.iTunes:LABEL"] = [MP4FreeForm(lbl.encode("utf-8"))]
                changed = True
            if changed: audio.save()
            return changed, "ok" if changed else "unchanged"
        except MutagenError as e:
            return False, f"write_failed: {e}"

    # WMA / ASF
    if ext == ".wma":
        try:
            audio = ASF(path)
            if y:
                audio.tags["WM/Year"] = [y]
                changed = True
            if lbl:
                audio.tags["WM/Publisher"] = [lbl]
                changed = True
            if changed: audio.save()
            return changed, "ok" if changed else "unchanged"
        except MutagenError as e:
            return False, f"write_failed: {e}"

    return False, "unsupported_format"

# ---------------------- discovery ----------------------
def find_audio_files(root: Path, recursive: bool) -> List[Path]:
    files: List[Path] = []
    if recursive:
        for ext in ALL_AUDIO_EXTS:
            files.extend(root.rglob(f"*{ext}")); files.extend(root.rglob(f"*{ext.upper()}"))
    else:
        for ext in ALL_AUDIO_EXTS:
            files.extend(root.glob(f"*{ext}")); files.extend(root.glob(f"*{ext.upper()}"))
    return sorted(files)

# ---------------------- per-file processing ----------------------
def process_one_file(f: Path, args, placeholder_md5: Optional[str]) -> Dict[str, Any]:
    artist, title, mix = get_artist_title_from_tags(f)
    row: Dict[str, Any] = {
        "file": str(f), "artist": artist, "title": title, "mix": mix or "",
        "year": "", "label": "", "discogs_url": "", "match_confidence": "",
        "tag_status": "unchanged", "art_status": "unchanged", "art_source_url": "", "notes": ""
    }
    print(f"    Lookup: {artist} - {title}" + (f" ({mix})" if mix else ""))

    # Search (may raise RetryableDiscogsError)
    res = discogs_search(artist, title, mix, args.delay)
    if not res:
        row["notes"] = "no_confident_match"; return row

    row["match_confidence"] = res.get("_match_score", "")
    url = res.get("uri") or ""
    row["discogs_url"] = f"https://www.discogs.com{url}" if url and url.startswith(("/", "release", "master")) else url

    # Details (may raise RetryableDiscogsError)
    details = fetch_release_details(res.get("resource_url", ""))

    # Year/Labels
    labels = ""
    if details:
        if "labels" in details and isinstance(details["labels"], list):
            labels = ", ".join(sorted({lab.get("name","").strip() for lab in details["labels"] if lab.get("name")}))
        elif "label" in details and isinstance(details["label"], list):
            labels = ", ".join(sorted({lab.get("name","").strip() for lab in details["label"] if lab.get("name")}))
        raw_year = details.get("year", res.get("year", "")) or ""
        year = coerce_year(raw_year)
        img_url = choose_best_image(details.get("images", []), min_size=args.min_art) or ""
    else:
        year = coerce_year(res.get("year", ""))
        img_url = ""

    row["year"] = year
    row["label"] = labels

    # Write tags for ALL supported formats
    try:
        changed, note = write_year_label_tags(f, year, labels)
        row["tag_status"] = "updated" if changed else note
    except Exception as e:
        row["tag_status"] = f"failed: {e}"

    # --- Artwork (MP3/FLAC/MP4/M4A/ALAC) ---
    art_status, art_src = "unchanged", ""
    art_supported_exts = (".mp3", ".flac", ".m4a", ".mp4", ".alac")
    if not args.no_art and f.suffix.lower() in art_supported_exts and PIL_AVAILABLE:
        try:
            embedded = read_embedded_art(f)
        except Exception:
            embedded = None

        need_art, reason = False, ""
        if embedded is None:
            need_art, reason = True, "missing"
        else:
            try: w,h = image_size_from_bytes(embedded)
            except Exception: w,h = (0,0)
            if max(w,h) < int(args.min_art):
                need_art, reason = True, f"too small ({w}x{h})"
            else:
                if placeholder_md5 and md5_bytes(embedded) == placeholder_md5:
                    need_art, reason = True, "placeholder"

        if need_art and img_url:
            img_bytes = download_image(img_url)
            if img_bytes:
                remove_all_art(f)
                mime = "image/png" if img_url.lower().endswith(".png") else "image/jpeg"
                if write_single_cover(f, img_bytes, mime=mime):
                    try: w2,h2 = image_size_from_bytes(img_bytes); art_status=f"downloaded ({w2}x{h2}) due to {reason}"
                    except Exception: art_status=f"downloaded due to {reason}"
                    art_src = img_url
                else:
                    art_status, art_src = "write_failed", img_url
            else:
                art_status, art_src = "download_failed", img_url
        elif need_art and not img_url:
            art_status = "no_image_available"
        else:
            art_status = "kept_existing"
    elif not PIL_AVAILABLE and f.suffix.lower() in art_supported_exts and not args.no_art:
        art_status = "skipped_no_pillow"

    row["art_status"], row["art_source_url"] = art_status, art_src
    return row

# ---------------------- main program w/ retries ----------------------
def main():
    ap = argparse.ArgumentParser(description="Discogs year+label to CSV, write tags (ALL formats), update art (MP3/FLAC/MP4-M4A).")
    ap.add_argument("folder", help="Folder to scan")
    ap.add_argument("-o","--out", default="discogs_results.csv", help="Output CSV path")
    ap.add_argument("-r","--recursive", action="store_true", help="Scan subfolders")
    ap.add_argument("--delay", type=float, default=0.6, help="Delay between Discogs calls (seconds)")
    ap.add_argument("--min-art", type=int, default=MIN_ART_SIZE, help="Minimum art size (px)")
    ap.add_argument("--no-art", action="store_true", help="Don’t modify/insert album art")
    args = ap.parse_args()

    # placeholder.jpg MD5 (optional)
    placeholder_md5 = None
    p_path = Path(__file__).with_name(PLACEHOLDER_FILENAME)
    if p_path.exists():
        try:
            placeholder_md5 = md5_bytes(p_path.read_bytes())
            print(f"Loaded placeholder '{PLACEHOLDER_FILENAME}' MD5: {placeholder_md5}")
        except Exception as e:
            print(f"[WARN] Could not read {PLACEHOLDER_FILENAME}: {e}")
    else:
        print("[INFO] placeholder.jpg not found; placeholder matching disabled.")

    root = Path(args.folder)
    if not root.exists() or not root.is_dir():
        print(f"ERROR: Folder '{root}' not found or not a directory.")
        sys.exit(1)

    files = find_audio_files(root, args.recursive)
    if not files:
        print("No audio files found."); sys.exit(0)

    print(f"Scanning {len(files)} file(s)...\n")

    rows: List[Dict[str, Any]] = []
    retry_queue: List[Path] = []

    # Main pass
    for idx, f in enumerate(files, 1):
        print(f"[{idx}/{len(files)}] {f.name}")
        try:
            row = process_one_file(f, args, placeholder_md5)
            rows.append(row)
        except RetryableDiscogsError as e:
            print(f"   [RETRY] {e}; will retry later")
            retry_queue.append(f)
        except Exception as e:
            print(f"   [ERROR] Unexpected: {e}")
            rows.append({
                "file": str(f), "artist": "", "title": "", "mix": "",
                "year": "", "label": "", "discogs_url": "", "match_confidence": "",
                "tag_status": "unchanged", "art_status": "unchanged", "art_source_url": "",
                "notes": f"error: {e}"
            })

    # Retry waves
    for round_idx in range(1, RETRY_MAX_ROUNDS+1):
        if not retry_queue: break
        backoff = args.delay * (2**round_idx) + 1.0
        print(f"\n== Retry round {round_idx} ({len(retry_queue)} items), sleeping {backoff:.1f}s before retry ==")
        time.sleep(backoff)
        current = retry_queue; retry_queue = []
        for f in current:
            print(f"[retry {round_idx}] {f.name}")
            try:
                row = process_one_file(f, args, placeholder_md5)
                rows.append(row)
            except RetryableDiscogsError as e:
                print(f"   [RETRY-KEEP] {e}")
                retry_queue.append(f)
            except Exception as e:
                print(f"   [ERROR] Unexpected during retry: {e}")
                rows.append({
                    "file": str(f), "artist": "", "title": "", "mix": "",
                    "year": "", "label": "", "discogs_url": "", "match_confidence": "",
                    "tag_status": "unchanged", "art_status": "unchanged", "art_source_url": "",
                    "notes": f"retry_error: {e}"
                })

    if retry_queue:
        print(f"\n[WARN] {len(retry_queue)} file(s) still pending due to rate limits/errors; you can re-run to process them.")

    out_path = Path(args.out)
    with out_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "file","artist","title","mix","year","label",
                "discogs_url","match_confidence","tag_status",
                "art_status","art_source_url","notes"
            ]
        )
        writer.writeheader(); writer.writerows(rows)

    print(f"\nDone. Wrote {len(rows)} rows to: {out_path.resolve()}")
    print("CSV columns: file, artist, title, mix, year, label, discogs_url, match_confidence, tag_status, art_status, art_source_url, notes")

# ---- end main ----
if __name__ == "__main__":
    main()
