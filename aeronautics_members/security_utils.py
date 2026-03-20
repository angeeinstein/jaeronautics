from urllib.parse import urljoin, urlsplit

from flask import current_app, has_request_context, request, url_for

LOCAL_ALLOWED_HOSTS = {"127.0.0.1", "localhost", "::1"}


def normalize_public_base_url(value):
    raw_value = (value or "").strip()
    if not raw_value:
        return ""
    parsed = urlsplit(raw_value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")


def get_public_base_url():
    configured = normalize_public_base_url(current_app.config.get("PUBLIC_BASE_URL"))
    if configured:
        return configured
    if has_request_context():
        return normalize_public_base_url(request.url_root)
    raise RuntimeError("PUBLIC_BASE_URL is not configured.")


def build_public_url(endpoint, **values):
    base_url = get_public_base_url()
    if has_request_context():
        relative_path = url_for(endpoint, _external=False, **values)
    else:
        with current_app.test_request_context(base_url=base_url):
            relative_path = url_for(endpoint, _external=False, **values)
    return urljoin(f"{base_url}/", relative_path.lstrip("/"))


def get_allowed_hosts():
    hosts = set(LOCAL_ALLOWED_HOSTS)
    configured = normalize_public_base_url(current_app.config.get("PUBLIC_BASE_URL"))
    if configured:
        public_hostname = urlsplit(configured).hostname
        if public_hostname:
            hosts.add(public_hostname.lower())

    extra_hosts = current_app.config.get("ADDITIONAL_ALLOWED_HOSTS", "")
    for raw_host in str(extra_hosts or "").split(","):
        host = raw_host.strip().lower().rstrip(".")
        if host:
            hosts.add(host)
    return hosts


def is_trusted_host(host):
    normalized_host = (host or "").strip().lower().rstrip(".")
    if not normalized_host:
        return False
    if normalized_host.startswith("[") and normalized_host.endswith("]"):
        normalized_host = normalized_host[1:-1]
    return normalized_host in get_allowed_hosts()
