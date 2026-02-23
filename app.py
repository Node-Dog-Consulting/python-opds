import os
import glob
import hashlib
import re
import time
import zipfile
import io
from datetime import datetime, timezone
from urllib.parse import quote
from xml.sax.saxutils import escape

from flask import Flask, Response, send_file, abort, request

app = Flask(__name__)

BOOKS_DIR = os.environ.get("BOOKS_DIR", "/books")
SERVER_TITLE = os.environ.get("SERVER_TITLE", "OPDS Library")
SCAN_TTL = int(os.environ.get("SCAN_TTL", 300))  # seconds before rescanning

MIME_TYPES = {
    ".epub": "application/epub+zip",
    ".pdf": "application/pdf",
    ".mobi": "application/x-mobipocket-ebook",
    ".azw": "application/vnd.amazon.ebook",
    ".azw3": "application/vnd.amazon.ebook",
    ".fb2": "application/x-fictionbook+xml",
    ".cbz": "application/vnd.comicbook+zip",
    ".cbr": "application/vnd.comicbook-rar",
}

# Simple in-memory cover cache: book_id -> (image_bytes, mime_type) or None
_cover_cache = {}

# Book scan cache
_scan_cache: list = []
_scan_time: float = 0.0
# book_id -> path for fast cover lookups
_book_paths: dict = {}


def base_url():
    return request.url_root.rstrip("/")


def scan_books():
    global _scan_cache, _scan_time, _book_paths
    if time.monotonic() - _scan_time < SCAN_TTL:
        return _scan_cache
    books = []
    for ext in MIME_TYPES:
        pattern = os.path.join(BOOKS_DIR, f"**/*{ext}")
        for path in glob.glob(pattern, recursive=True):
            books.append(path)
    books.sort(key=os.path.getmtime, reverse=True)
    _book_paths = {hashlib.md5(p.encode()).hexdigest(): p for p in books}
    _scan_cache = books
    _scan_time = time.monotonic()
    return books


def extract_epub_cover(epub_path):
    """Return (image_bytes, mime_type) for the cover of an epub, or None."""
    try:
        with zipfile.ZipFile(epub_path) as z:
            names = z.namelist()

            # 1. Find OPF path via META-INF/container.xml
            opf_path = None
            if "META-INF/container.xml" in names:
                container = z.read("META-INF/container.xml").decode("utf-8", errors="ignore")
                m = re.search(r'full-path="([^"]+\.opf)"', container)
                if m:
                    opf_path = m.group(1)

            if opf_path and opf_path in names:
                opf = z.read(opf_path).decode("utf-8", errors="ignore")
                opf_dir = os.path.dirname(opf_path)

                # 2. Find cover item id from <meta name="cover" content="..."/>
                cover_id = None
                m = re.search(r'<meta\s+name=["\']cover["\']\s+content=["\']([^"\']+)["\']', opf)
                if not m:
                    m = re.search(r'<meta\s+content=["\']([^"\']+)["\']\s+name=["\']cover["\']', opf)
                if m:
                    cover_id = m.group(1)

                # Also check for cover item with properties="cover-image"
                if not cover_id:
                    m = re.search(r'<item[^>]+properties=["\']cover-image["\'][^>]+href=["\']([^"\']+)["\']', opf)
                    if not m:
                        m = re.search(r'<item[^>]+href=["\']([^"\']+)["\'][^>]+properties=["\']cover-image["\']', opf)
                    if m:
                        cover_href = m.group(1)
                        cover_full = os.path.join(opf_dir, cover_href).replace("\\", "/")
                        if cover_full in names:
                            data = z.read(cover_full)
                            ext = os.path.splitext(cover_href)[1].lower()
                            mime = "image/png" if ext == ".png" else "image/jpeg"
                            return data, mime

                if cover_id:
                    # Find href for that item id
                    m = re.search(
                        rf'<item[^>]+id=["\'](?:{re.escape(cover_id)})["\'][^>]+href=["\']([^"\']+)["\']',
                        opf,
                    )
                    if not m:
                        m = re.search(
                            rf'<item[^>]+href=["\']([^"\']+)["\'][^>]+id=["\'](?:{re.escape(cover_id)})["\']',
                            opf,
                        )
                    if m:
                        cover_href = m.group(1)
                        cover_full = os.path.join(opf_dir, cover_href).replace("\\", "/")
                        if cover_full in names:
                            data = z.read(cover_full)
                            ext = os.path.splitext(cover_href)[1].lower()
                            mime = "image/png" if ext == ".png" else "image/jpeg"
                            return data, mime

            # 3. Fallback: look for any file named cover.*
            for name in sorted(names):
                basename = os.path.basename(name).lower()
                if basename.startswith("cover") and basename.endswith((".jpg", ".jpeg", ".png")):
                    data = z.read(name)
                    mime = "image/png" if name.lower().endswith(".png") else "image/jpeg"
                    return data, mime

    except Exception:
        pass
    return None


def get_cover(book_id, path):
    if book_id not in _cover_cache:
        ext = os.path.splitext(path)[1].lower()
        if ext == ".epub":
            _cover_cache[book_id] = extract_epub_cover(path)
        else:
            _cover_cache[book_id] = None
    return _cover_cache[book_id]


def book_to_entry(path):
    filename = os.path.basename(path)
    title = os.path.splitext(filename)[0]
    ext = os.path.splitext(filename)[1].lower()
    mime = MIME_TYPES.get(ext, "application/octet-stream")
    book_id = hashlib.md5(path.encode()).hexdigest()
    rel = os.path.relpath(path, BOOKS_DIR)
    mtime = datetime.fromtimestamp(os.path.getmtime(path), tz=timezone.utc)
    download_url = f"{base_url()}/download/{quote(rel)}"
    cover_url = f"{base_url()}/cover/{book_id}"

    # Only include cover links for epub files; cover is fetched lazily on demand
    cover_links = ""
    if ext == ".epub":
        cover_links = f"""    <link rel="http://opds-spec.org/image"
          href="{escape(cover_url)}"
          type="image/jpeg"/>
    <link rel="http://opds-spec.org/image/thumbnail"
          href="{escape(cover_url)}"
          type="image/jpeg"/>
"""

    return f"""  <entry>
    <title>{escape(title)}</title>
    <id>urn:md5:{book_id}</id>
    <updated>{mtime.strftime('%Y-%m-%dT%H:%M:%SZ')}</updated>
{cover_links}    <link rel="http://opds-spec.org/acquisition"
          href="{escape(download_url)}"
          type="{mime}"/>
  </entry>"""


def make_feed(entries):
    now = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    opds_url = f"{base_url()}/opds"
    entries_xml = "\n".join(entries)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:opds="http://opds-spec.org/2010/catalog">
  <id>urn:opds:library</id>
  <title>{escape(SERVER_TITLE)}</title>
  <updated>{now}</updated>
  <link rel="self"
        href="{escape(opds_url)}"
        type="application/atom+xml;profile=opds-catalog;kind=acquisition"/>
  <link rel="start"
        href="{escape(opds_url)}"
        type="application/atom+xml;profile=opds-catalog;kind=acquisition"/>
{entries_xml}
</feed>"""


@app.route("/opds")
@app.route("/opds/")
def catalog():
    books = scan_books()
    entries = [book_to_entry(p) for p in books]
    xml = make_feed(entries)
    return Response(
        xml,
        mimetype="application/atom+xml;profile=opds-catalog;kind=acquisition",
    )


@app.route("/cover/<book_id>")
def cover(book_id):
    scan_books()  # ensure _book_paths is populated
    path = _book_paths.get(book_id)
    if not path:
        abort(404)
    result = get_cover(book_id, path)
    if result:
        data, mime = result
        return Response(data, mimetype=mime)
    abort(404)


@app.route("/download/<path:rel_path>")
def download(rel_path):
    safe_path = os.path.realpath(os.path.join(BOOKS_DIR, rel_path))
    books_real = os.path.realpath(BOOKS_DIR)
    if not safe_path.startswith(books_real + os.sep):
        abort(403)
    if not os.path.isfile(safe_path):
        abort(404)
    return send_file(safe_path, as_attachment=True)


@app.route("/")
def root():
    return '<a href="/opds">OPDS Catalog</a>'


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
