import os
import hmac
import ipaddress
import socket
from urllib.parse import urlparse, urljoin

import requests
from flask import Flask, jsonify, request
from bs4 import BeautifulSoup

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024  # API request body limit

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


def validate_public_allowed_url(url):
    """Allow only HTTP(S), explicitly allowlisted hosts, and public IP addresses."""
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError("Only http:// and https:// URLs are accepted.")
        if parsed.username or parsed.password:
            raise ValueError("URLs containing credentials are not accepted.")

        host = parsed.hostname.rstrip(".").lower()
        if not ALLOWED_HOSTS or not any(
            host == allowed or host.endswith("." + allowed)
            for allowed in ALLOWED_HOSTS
        ):
            raise ValueError("This hostname is not in ALLOWED_HOSTS.")

        # Reject private, loopback, link-local, multicast, reserved, and unspecified IPs.
        try:
            addresses = [ipaddress.ip_address(host)]
        except ValueError:
            addresses = [
                ipaddress.ip_address(item[4][0])
                for item in socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80),
                                               type=socket.SOCK_STREAM)
            ]

        if not addresses:
            raise ValueError("Hostname did not resolve.")
        if any(not address.is_global for address in addresses):
            raise ValueError("Non-public network destinations are not accepted.")

        return parsed
    except (socket.gaierror, OSError) as exc:
        raise ValueError("Hostname could not be resolved.") from exc


def fetch_page(start_url):
    """Fetch a small HTML page while validating each redirect destination."""
    current_url = start_url

    for _ in range(MAX_REDIRECTS + 1):
        validate_public_allowed_url(current_url)
        response = requests.get(
            current_url,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html"},
            timeout=TIMEOUT,
            allow_redirects=False,
            stream=True,
        )

        if response.status_code in (301, 302, 303, 307, 308):
            location = response.headers.get("Location")
            response.close()
            if not location:
                raise ValueError("Redirect response did not include a Location header.")
            current_url = urljoin(current_url, location)
            continue

        response.raise_for_status()
        content_type = response.headers.get("Content-Type", "").lower()
        if "text/html" not in content_type and "application/xhtml+xml" not in content_type:
            response.close()
            raise ValueError("The requested URL did not return an HTML page.")

        chunks = []
        total = 0
        try:
            for chunk in response.iter_content(chunk_size=16384):
                if not chunk:
                    continue
                total += len(chunk)
                if total > MAX_BYTES:
                    raise ValueError("HTML response exceeded the 2 MB limit.")
                chunks.append(chunk)
        finally:
            response.close()

        return b"".join(chunks), response.url

    raise ValueError("Too many redirects.")


@app.get("/")
def health():
    return jsonify({"success": True, "service": "authorized-link-extractor", "status": "ok"})


@app.get("/api/links")
def extract_links():
    # Require a configured secret before exposing the fetch endpoint.
    if not API_KEY:
        return jsonify({"success": False, "error": "Server API_KEY is not configured."}), 503

    supplied_key = request.headers.get("X-API-Key", "")
    if not hmac.compare_digest(supplied_key, API_KEY):
        return jsonify({"success": False, "error": "Unauthorized."}), 401

    url = request.args.get("url", "").strip()
    if not url:
        return jsonify({
            "success": False,
            "error": "Missing required query parameter: url",
            "example": "/api/links?url=https%3A%2F%2Fexample.org%2Fpage"
        }), 400

    try:
        page_bytes, final_url = fetch_page(url)
        soup = BeautifulSoup(page_bytes, "html.parser")
        results = []
        seen = set()

        for anchor in soup.find_all("a", href=True):
            href = anchor.get("href", "").strip()
            absolute = urljoin(final_url, href)
            parsed = urlparse(absolute)

            # Return only ordinary web links; omit javascript:, data:, mailto:, etc.
            if parsed.scheme not in ("http", "https") or not parsed.hostname:
                continue
            if absolute in seen:
                continue
            seen.add(absolute)
            results.append({
                "text": anchor.get_text(" ", strip=True)[:200],
                "url": absolute
            })

        return jsonify({
            "success": True,
            "source_url": url,
            "final_url": final_url,
            "count": len(results),
            "links": results
        })

    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    except requests.Timeout:
        return jsonify({"success": False, "error": "The source webpage timed out."}), 504
    except requests.RequestException:
        return jsonify({"success": False, "error": "Could not fetch the source webpage."}), 502
    except Exception:
        app.logger.exception("Unexpected extraction error")
        return jsonify({"success": False, "error": "Internal server error."}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
