
import os
import ipaddress
import socket
from urllib.parse import urlparse, urljoin

import requests
from flask import Flask, request, jsonify
from bs4 import BeautifulSoup

app = Flask(__name__)

API_KEY = os.environ.get("API_KEY", "")
TIMEOUT = 15
MAX_BYTES = 2 * 1024 * 1024

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; LinkExtractor/1.0)"
}


def valid_public_url(url):
    try:
        parsed = urlparse(url)

        if parsed.scheme not in ("http", "https"):
            return False

        if not parsed.hostname or parsed.username or parsed.password:
            return False

        # Reject local and private network addresses.
        addresses = socket.getaddrinfo(
            parsed.hostname, None, type=socket.SOCK_STREAM
        )

        for item in addresses:
            ip = ipaddress.ip_address(item[4][0])
            if not ip.is_global:
                return False

        return True
    except (ValueError, OSError):
        return False


def extract_links(url):
    if not valid_public_url(url):
        raise ValueError("URL must use a public HTTP or HTTPS address.")

    response = requests.get(
        url,
        headers=HEADERS,
        timeout=TIMEOUT,
        allow_redirects=False,
        stream=True,
    )

    try:
        if response.is_redirect:
            location = response.headers.get("Location")
            if location:
                return {
                    "source": url,
                    "redirect": urljoin(url, location),
                    "links": [],
                }

        response.raise_for_status()

        content_type = response.headers.get("Content-Type", "")
        if "html" not in content_type.lower():
            return {
                "source": url,
                "content_type": content_type,
                "links": [],
                "message": "The URL did not return an HTML page.",
            }

        chunks = []
        total = 0

        for chunk in response.iter_content(chunk_size=16384):
            total += len(chunk)
            if total > MAX_BYTES:
                raise ValueError("Page exceeds the 2 MB size limit.")
            chunks.append(chunk)

        html = b"".join(chunks)
    finally:
        response.close()

    soup = BeautifulSoup(html, "html.parser")
    links = []
    seen = set()

    for tag in soup.find_all("a", href=True):
        href = urljoin(url, tag["href"].strip())

        if not href.startswith(("http://", "https://")):
            continue

        if href not in seen:
            seen.add(href)
            links.append({
                "text": tag.get_text(" ", strip=True),
                "url": href,
            })

    return {
        "source": url,
        "count": len(links),
        "links": links,
    }


@app.get("/")
def home():
    return jsonify({
        "service": "link-extractor",
        "status": "ok",
        "usage": "/api/links?url=https://example.com"
    })


@app.get("/api/links")
def api_links():
    supplied_key = request.headers.get("X-API-Key", "")

    if not API_KEY or supplied_key != API_KEY:
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    url = request.args.get("url", "").strip()
    if not url:
        return jsonify({
            "success": False,
            "error": "Missing required query parameter: url"
        }), 400

    try:
        result = extract_links(url)
        result["success"] = True
        return jsonify(result)
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    except requests.RequestException as exc:
        return jsonify({
            "success": False,
            "error": "Could not fetch the requested page.",
            "details": str(exc),
        }), 502


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
