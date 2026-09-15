"""Bounded public-page reader shared by all fetch aliases.

Resolve and validate every redirect, then connect to the validated address
while preserving the hostname for TLS verification. No ambient proxy/cookies.
"""
import http.client
import ipaddress
import socket
import ssl
import time
from urllib.parse import urlsplit, urljoin

MAX_BYTES = 2 * 1024 * 1024


def validate_public_url(url):
    p = urlsplit(url)
    if p.scheme not in ('http', 'https') or not p.hostname or p.username or p.password:
        raise ValueError('Only public HTTP(S) URLs without credentials are allowed')
    port = p.port or (443 if p.scheme == 'https' else 80)
    addresses = socket.getaddrinfo(p.hostname, port, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise ValueError('Private, loopback, and reserved addresses are not public pages')
    return p, addresses[0][4][0], port


def read_page(url, max_length=8000, timeout=15, output_format='text'):
    from .session import current_session
    session = current_session.get()
    if session:
        session.reserve('fetch', {'url': url})
    deadline = time.monotonic() + min(max(float(timeout), .1), 30)
    if session:
        deadline = min(deadline, session.deadline)
    for _ in range(6):
        if session:
            session.check()
        parsed, address, port = validate_public_url(url)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('Page read deadline exceeded')
        connection = http.client.HTTPConnection(parsed.hostname, port, timeout=remaining)
        sock = socket.create_connection((address, port), timeout=remaining)
        if parsed.scheme == 'https':
            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=parsed.hostname)
        connection.sock = sock
        try:
            target = parsed.path or '/'
            if parsed.query:
                target += '?' + parsed.query
            connection.request('GET', target, headers={'User-Agent': 'Novi/0.2', 'Accept-Encoding': 'identity'})
            response = connection.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                url = urljoin(url, response.getheader('Location', ''))
                continue
            if response.status >= 400:
                raise ValueError(f'Page returned HTTP {response.status}')
            content_type = response.getheader('Content-Type', '').lower()
            if not any(t in content_type for t in ('text/', 'application/xhtml')):
                raise ValueError('Page is not readable text')
            chunks, size = [], 0
            while True:
                if session:
                    session.check()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('Page read deadline exceeded')
                sock.settimeout(remaining)
                chunk = response.read1(min(65536, MAX_BYTES + 1 - size))
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_BYTES:
                    raise ValueError('Page exceeds download size limit')
                chunks.append(chunk)
            raw = b''.join(chunks).decode('utf-8', errors='replace')
        finally:
            connection.close()
        if output_format == 'html':
            text = raw
        else:
            import trafilatura
            text = trafilatura.extract(raw, include_comments=False, include_tables=True) or ''
            if not text and content_type.startswith('text/plain'):
                text = raw
        text = text[:max(1, min(int(max_length), 16000))] or 'No readable content found.'
        if session and output_format != 'html':
            record = next((r for r in session.results if r['url'] == url), None)
            if record is not None:
                record['text'] = text[:4000]
            else:
                session.results.append(dict(url=url, text=text[:4000], title='', published_at=''))
        return text
    raise ValueError('Too many redirects')
