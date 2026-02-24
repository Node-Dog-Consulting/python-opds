# OPDS Server

A minimal [OPDS 1.2](https://specs.opds.io/opds-1.2) catalog server for serving ebooks. Scans a directory of ebook files and exposes them as a browsable, downloadable feed compatible with any OPDS-capable reading app.

## Features

- Serves an OPDS 1.2 acquisition feed
- Recursively scans a books directory
- Extracts and serves cover images from epub files
- Path traversal protection on downloads
- Runs under Gunicorn
- Single Python file, no database

## Supported Formats

| Extension | Format |
|-----------|--------|
| `.epub` | EPUB |
| `.pdf` | PDF |
| `.mobi` | Mobipocket |
| `.azw` / `.azw3` | Kindle |
| `.fb2` | FictionBook |
| `.cbz` | Comic Book ZIP |
| `.cbr` | Comic Book RAR |

## Quick Start

### Docker Compose (recommended)

```bash
# Clone the repo
git clone https://github.com/Node-Dog-Consulting/opds.git
cd opds

# Put your books in ./books/ or edit the volume path in docker-compose.yml
docker compose up -d
```

### Docker

```bash
docker run -d \
  --name opds \
  -p 8080:8080 \
  -v /path/to/your/books:/books:ro \
  -v /path/to/cache:/cache \
  -e SERVER_TITLE="My Library" \
  ghcr.io/node-dog-consulting/opds:latest
```

### Local (Python)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

BOOKS_DIR=/path/to/your/books python app.py
```

## Configuration

All configuration is via environment variables.

| Variable | Default | Description |
|----------|---------|-------------|
| `BOOKS_DIR` | `/books` | Path to scan for ebook files (recursive) |
| `SERVER_TITLE` | `OPDS Library` | Title shown in OPDS clients |
| `CACHE_DIR` | `/cache` | Path for filesystem cache (scan results and cover images) |
| `SCAN_TTL` | `60` | Seconds before the book list is rescanned |
| `PORT` | `8080` | Port used in dev mode (`python app.py`) |

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /opds` | OPDS acquisition feed (all books) |
| `GET /cover/<id>` | Cover image for a book |
| `GET /download/<path>` | Download a book file |

## Connecting a Client

Point your OPDS reader to:

```
http://<host>:8080/opds
```

## Project Structure

```
opds/
├── app.py              # Flask application (single file)
├── requirements.txt    # Python dependencies (flask, gunicorn)
├── Dockerfile          # Container image definition
├── docker-compose.yml  # Compose stack for easy local deployment
├── cache/              # Filesystem cache (created automatically, mount to /cache)
└── .github/
    └── workflows/
        └── docker.yml  # CI/CD: build & push to ghcr.io
```

## Caching

The server uses a flat filesystem cache at `CACHE_DIR` (`/cache` by default) shared across all Gunicorn workers and persistent across restarts.

- **Scan results** — `scan.json` stores the book file list. A fresh worker reads this instead of doing a full directory glob, avoiding worker timeouts on large libraries.
- **Cover images** — extracted covers are written as `<book-id>.jpg` or `<book-id>.png`. A `.none` sentinel is written for books with no cover so the epub is not re-opened on subsequent requests.

Mount the cache directory as a volume to persist it across container restarts. For Docker Compose this is done automatically via the `./cache:/cache` volume.

## Cover Image Extraction

For epub files, covers are extracted at first request and written to the cache. The extraction checks (in order):

1. `<meta name="cover">` in the OPF manifest
2. `properties="cover-image"` on a manifest item
3. Any file named `cover.*` inside the epub zip

Non-epub formats (PDF, MOBI, etc.) do not show cover images.


