import os
import hmac
import ipaddress
import socket
from urllib.parse import urlparse, urljoin

import requests
from flask import Flask, jsonify, request
from bs4 import BeautifulSoup

app = Flask(__name__)

API_KEY = os.environ.get("API_KEY", "")
ALLOWED_HOSTS = {
    host.strip().lower()
    for host in os.environ.get("ALLOWED_HOSTS", "").split(",")
    if host.strip()
}

TIMEOUT = 15
MAX_BYTES = 2_000_000
MAX_REDIRECTS = 4
USER_AGENT = "AuthorizedLinkExtractor/1.0"


def check_api_key(supplied_key):
    return bool(API_KEY) and hmac.compare_digest(
        supplied_key or "", API_KEY
    )


def validate_url(url):
    try:
        parsed = urlparse(url)

        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError("Only HTTP and HTTPS URLs are allowed.")

        if parsed.username or parsed.password:
            raise ValueError("URLs containing credentials are not allowed.")

        host = parsed.hostname.rstrip(".").lower()

        if not ALLOWED_HOSTS or not any(
            host == allowed or host.endswith("." + allowed)
            for allowed in ALLOWED_HOSTS
        ):
            raise ValueError("Hostname is not in ALLOWED_HOSTS.")

        try:
            addresses = [ipaddress.ip_address(host)]
        except ValueError:
            addresses = [
                ipaddress.ip_address(item[4][0])
                for item in socket.getaddrinfo(
                    host,
                    parsed.port or (443 if parsed.scheme == "https" else 80),
                    type=socket.SOCK_STREAM,
                )
            ]

        if not addresses or any(not ip.is_global for ip in addresses):
            raise ValueError("Non-public network destinations are not allowed.")

        return parsed

    except (socket.gaierror, OSError) as exc:
        raise ValueError("Hostname could not be resolved.") from exc


def fetch_page(start_url):
    current_url = start_url

    for _ in range(MAX_REDIRECTS + 1):
        validate_url(current_url)

        response = requests.get(
            current_url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html",
            },
            timeout=TIMEOUT,
            allow_redirects=False,
            stream=True,
        )

        if response.status_code in (301, 302, 303, 307, 308):
            location = response.headers.get("Location")
            response.close()

            if not location:
                raise ValueError("Redirect did not provide a destination.")

            current_url = urljoin(current_url, location)
            continue

        response.raise_for_status()

        content_type = response.headers.get("Content-Type", "").lower()

        if "text/html" not in content_type and \
                "application/xhtml+xml" not in content_type:
            response.close()
            raise ValueError("URL did not return an HTML page.")

        chunks = []
        total = 0

        try:
            for chunk in response.iter_content(chunk_size=16384):
                if not chunk:
                    continue

                total += len(chunk)

                if total > MAX_BYTES:
                    raise ValueError("HTML response exceeds the 2 MB limit.")

                chunks.append(chunk)

            final_url = response.url

        finally:
            response.close()

        return b"".join(chunks), final_url

    raise ValueError("Too many redirects.")


def extract_page_links(source_url):
    page_bytes, final_url = fetch_page(source_url)
    soup = BeautifulSoup(page_bytes, "html.parser")

    links = []
    seen = set()

    for anchor in soup.find_all("a", href=True):
        href = anchor.get("href", "").strip()
        absolute_url = urljoin(final_url, href)
        parsed = urlparse(absolute_url)

        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            continue

        if absolute_url in seen:
            continue

        seen.add(absolute_url)

        links.append({
            "text": anchor.get_text(" ", strip=True)[:200],
            "url": absolute_url,
        })

    return {
        "success": True,
        "source_url": source_url,
        "final_url": final_url,
        "count": len(links),
        "links": links,
    }


@app.get("/")
def health():
    return jsonify({
        "success": True,
        "service": "authorized-link-extractor",
        "status": "ok",
    })


@app.get("/api/links")
def api_links():
    if not check_api_key(request.headers.get("X-API-Key", "")):
        return jsonify({
            "success": False,
            "error": "Unauthorized. Supply a valid X-API-Key header.",
        }), 401

    return handle_extraction()


@app.get("/url")
def direct_url():
    # Allows direct browser access using ?url=...&key=...
    if not check_api_key(request.args.get("key", "")):
        return jsonify({
            "success": False,
            "error": "Unauthorized. Add your API key using the key parameter.",
        }), 401

    return handle_extraction()


def handle_extraction():
    source_url = request.args.get("url", "").strip()

    if not source_url:
        return jsonify({
            "success": False,
            "error": "Missing required url parameter.",
        }), 400

    try:
        return jsonify(extract_page_links(source_url))

    except ValueError as exc:
        return jsonify({
            "success": False,
            "error": str(exc),
        }), 400

    except requests.Timeout:
        return jsonify({
            "success": False,
            "error": "Source webpage timed out.",
        }), 504

    except requests.RequestException:
        return jsonify({
            "success": False,
            "error": "Could not fetch source webpage.",
        }), 502

    except Exception:
        app.logger.exception("Unexpected extraction error")
        return jsonify({
            "success": False,
            "error": "Internal server error.",
        }), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)

