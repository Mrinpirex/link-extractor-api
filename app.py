
import ipaddress
import socket
from urllib.parse import urlparse, urljoin

import requests
from flask import Flask, request, jsonify
from bs4 import BeautifulSoup

app = Flask(__name__)

TIMEOUT = 15
MAX_BYTES = 2 * 1024 * 1024

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}


def valid_public_url(url):
    try:
        parsed = urlparse(url)

        if parsed.scheme not in ("http", "https"):
            return False

        if not parsed.hostname or parsed.username or parsed.password:
            return False

        addresses = socket.getaddrinfo(
            parsed.hostname, None, type=socket.SOCK_STREAM
        )

        return all(
            ipaddress.ip_address(item[4][0]).is_global
            for item in addresses
        )
    except (ValueError, OSError):
        return False


def fetch_page(url):
    if not valid_public_url(url):
        raise ValueError("Invalid or non-public URL.")

    response = requests.get(
        url,
        headers=HEADERS,
        timeout=TIMEOUT,
        allow_redirects=True,
        stream=True,
    )

    try:
        response.raise_for_status()

        content_type = response.headers.get("Content-Type", "")
        if "html" not in content_type.lower():
            return {
                "source": response.url,
                "content_type": content_type,
                "links": [],
            }

        chunks = []
        total = 0

        for chunk in response.iter_content(16384):
            total += len(chunk)
            if total > MAX_BYTES:
                raise ValueError("HTML page exceeds 2 MB.")
            chunks.append(chunk)

        html = b"".join(chunks)
        final_url = response.url
    finally:
        response.close()

    soup = BeautifulSoup(html, "html.parser")
    links = []
    seen = set()

    for tag in soup.find_all("a", href=True):
        link = urljoin(final_url, tag["href"].strip())

        if link.startswith(("http://", "https://")) and link not in seen:
            seen.add(link)
            links.append({
                "text": tag.get_text(" ", strip=True),
                "url": link,
            })

    return {
        "source": final_url,
        "count": len(links),
        "links": links,
    }


@app.get("/")
def home():
    return jsonify({
        "success": True,
        "service": "link-extractor",
    })


@app.get("/api/links")
def api_links():
    url = request.args.get("url", "").strip()

    if not url:
        return jsonify({
            "success": False,
            "error": "Missing url parameter",
        }), 400

    try:
        result = fetch_page(url)
        result["success"] = True
        return jsonify(result)

    except ValueError as exc:
        return jsonify({
            "success": False,
            "error": str(exc),
        }), 400

    except requests.RequestException:
        return jsonify({
            "success": False,
            "error": "Failed to fetch the page",
        }), 502


if __name__ == "__main__":
    import os
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "5000")),
    )
