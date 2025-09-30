# Discogs Tag & Art Fixer

Fetch release **Year** and **Record Label** from Discogs, write them into your audio files’ tags, and fix missing/low-res/placeholder album art.  
Supports **MP3, FLAC, M4A/MP4/ALAC, AAC, WAV, AIFF, OGG/Vorbis, OPUS, WMA**.  
Album-art embedding is implemented for **MP3, FLAC, and MP4/M4A/ALAC**.

> ⚠️ You **must** use your own Discogs Personal Access Token. This repository should **not** contain anyone else’s token.  
> The token is currently hardcoded in the script under `DISCOGS_TOKEN`. Replace it with your own.

---

## Table of contents
- [What it does](#what-it-does)
- [Prerequisites (Windows fresh install)](#prerequisites-windows-fresh-install)
- [Get a Discogs Personal Access Token](#get-a-discogs-personal-access-token)
- [Setup](#setup)
- [Usage](#usage)
- [Options](#options)
- [How matching works](#how-matching-works)
- [CSV output](#csv-output)
- [Supported formats & tags](#supported-formats--tags)
- [Troubleshooting](#troubleshooting)
- [Safety & privacy notes](#safety--privacy-notes)
- [License](#license)

---

## What it does
- Reads **Artist** and **Title** from your files’ **tags** (falls back to filename).
- Searches Discogs for a best match.
- Writes a clean 4-digit **Year** (e.g., `2025`), sanitizing values like `2025//2025`.
- Writes the **Record Label**.
- Fixes album art when:
  - it’s **missing**, or
  - the largest dimension is **< 500px** (configurable), or
  - it matches your **placeholder** image.
- Handles Discogs **rate limits (429)** and temporary network errors by **queuing** and **retrying** at the end.
- Produces a **CSV audit** of everything it did.

---

## Prerequisites (Windows fresh install)

1. **Install Python (3.10+ recommended)**  
   - Download from the official site: [python.org](https://www.python.org/downloads/windows/)  
   - Run the installer and **tick “Add Python to PATH.”**  
   - Finish setup and (optionally) click **“Disable path length limit.”**

2. **Open PowerShell** (Win+X → Windows PowerShell) and verify:
   ```powershell
   python --version
   ```

3. **Install required Python packages**:
   ```powershell
   python -m pip install --upgrade pip
   python -m pip install requests mutagen pillow
   ```

If `python` or `pip` isn’t recognized, see [Troubleshooting](#troubleshooting).

---

## Get a Discogs Personal Access Token

1. Create a Discogs account if you don’t have one.  
2. Go to **Settings → Developers** and **Generate token**.  
3. Copy your token (keep it private).

In the script (`discogs_years_labels_art.py`), find:
```python
DISCOGS_TOKEN = "REPLACE_THIS_WITH_YOUR_OWN_TOKEN"
```
Replace the value with **your** token.  
> If you fork this repo, **do not commit** your token.

---

## Setup

- Place **`discogs_years_labels_art.py`** anywhere you like.
- (Optional but recommended) Put your **placeholder image** named **`placeholder.jpg`** in the **same folder** as the script.  
  The program uses its MD5 to detect and replace that placeholder art in your files.

---

## Usage

From PowerShell:

```powershell
# Basic: scan a folder (including subfolders), write results.csv, update tags and art
python discogs_years_labels_art.py "D:\Music" -r -o results.csv

# Don’t modify album art (still writes year/label and CSV)
python discogs_years_labels_art.py "D:\Music" -r -o results.csv --no-art

# Increase minimum art size requirement to 1000px
python discogs_years_labels_art.py "D:\Music" -r -o results.csv --min-art 1000
```

**Tip:** If you store large libraries on external drives, run against a small test folder first.

---

## Options

- `folder` (positional): Root folder to scan.
- `-r, --recursive`: Include subfolders.
- `-o, --out`: Output CSV path (default: `discogs_results.csv`).
- `--delay`: Delay between Discogs calls in seconds (default: `0.6`).
- `--min-art`: Minimum acceptable album-art dimension (default: `500`).
- `--no-art`: Don’t change or embed art (still writes tags + CSV).

---

## How matching works

1. The program reads **Artist** and **Title** from each file’s tags.  
   - If missing, it falls back to parsing filenames like `Artist - Title (Mix).ext`.
2. It runs **Discogs search** with a few query variations (artist, title, optional mix).
3. Results are **ranked** by artist/title similarity, “master” priority, and presence of a valid year.
4. The best candidate is selected if it passes a minimum confidence threshold.

It then:
- Pulls **Year** and **Labels** from the matched release/master.
- Cleans the year to a 4-digit value.
- Writes Year/Label to tags.
- Updates album art (MP3/FLAC/MP4/M4A/ALAC) if needed.

**Rate limiting:** If Discogs returns **429 Too Many Requests** or there’s a transient error, the file is queued and **retried at the end** (up to 3 rounds with backoff).

---

## CSV output

The script writes a CSV audit with these columns:

```
file, artist, title, mix, year, label,
discogs_url, match_confidence, tag_status,
art_status, art_source_url, notes
```

- **tag_status**: `updated`, `unchanged`, `unsupported_format`, or a `write_failed: ...` note  
- **art_status**: `downloaded (...)`, `kept_existing`, `no_image_available`, `write_failed`, `download_failed`, or `skipped_no_pillow`  
- **notes**: `no_confident_match` or error details (if any)

---

## Supported formats & tags

**Read & Write Year/Label**  
- **MP3 / WAV / AIFF (ID3):**  
  - Year → `TDRC` + legacy `TYER`  
  - Label → `TPUB` + `TXXX:LABEL`  
- **FLAC / Vorbis / Opus:**  
  - Year → `DATE` + `YEAR`  
  - Label → `LABEL` + `PUBLISHER`  
- **MP4 / M4A / ALAC (iTunes-style atoms):**  
  - Year → `©day`  
  - Label → `----:com.apple.iTunes:LABEL`  
- **WMA (ASF):**  
  - Year → `WM/Year`  
  - Label → `WM/Publisher`  

**Album art embedding**  
- **Implemented:** MP3 (APIC), FLAC (PICTURE), MP4/M4A/ALAC (`covr` with JPEG/PNG)  
- **Not embedded (read-only for art):** AAC (raw AAC), OGG, OPUS, WMA, WAV/AIFF (unless they carry ID3 art—script currently focuses on MP3/FLAC/MP4 families for art writing)

---

## Troubleshooting

**“pip is not recognized” / “python is not recognized”**  
- Re-install Python from python.org and **tick “Add Python to PATH.”**  
- Open a **new** PowerShell and try:  
  ```powershell
  python --version
  python -m pip --version
  ```

**`ModuleNotFoundError: No module named 'PIL'`**  
- Install Pillow:  
  ```powershell
  python -m pip install pillow
  ```

**Discogs 429 Too Many Requests**  
- The script queues these and retries automatically at the end.  
- You can also **increase `--delay`** or run smaller batches.

**Art didn’t update**  
- Ensure **`placeholder.jpg`** is next to the script if you want placeholder detection.  
- Make sure your file is MP3/FLAC/MP4/M4A/ALAC (the formats where art writing is implemented).  
- Install Pillow (`pillow` package).

**No matches for some tracks**  
- Clean up Artist/Title tags if they’re messy.  
- Consider removing extra text like `[Remastered]` or `(Radio Edit)` from titles.

---

## Safety & privacy notes

- Your Discogs token grants access to your Discogs account’s API quota.  
  **Do not share or commit your token.**  
- This script modifies your files’ tags (and, for certain formats, album art).  
  Consider running on a **copy** or making a **backup** first.

---

## License

Include whichever license you prefer (e.g., MIT). Example:

```text
MIT License

Copyright (c) YEAR YOUR_NAME

Permission is hereby granted, free of charge, to any person obtaining a copy
...
```

---

### Quick start (TL;DR)

```powershell
# 1) Install Python + add to PATH (from python.org)
# 2) Install deps:
python -m pip install requests mutagen pillow

# 3) Put your Discogs token into the script (DISCOGS_TOKEN = "...")

# 4) (Optional) place placeholder.jpg next to the script

# 5) Run:
python discogs_years_labels_art.py "D:\Music" -r -o results.csv
```
