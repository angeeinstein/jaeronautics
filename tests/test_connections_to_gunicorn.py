"""nginx keeps its connections to gunicorn open instead of opening one per request.

Found on the Azure portal VM with scripts/edge-check.ps1: half a minute of
thirty readers at once, and every request hung for thirty seconds while the
CPU sat idle and nothing was logged. dmesg said "nf_conntrack: table full,
dropping packet". A new connection to gunicorn for every request left
thousands of closed ones behind, the kernel tracks each of those for a while,
and on 1 GiB its table holds 7,168. Kept-alive connections are a handful.

These read install.sh, because that is where the configuration is written and
nothing else in this suite would notice it going back.
"""
import re
from pathlib import Path

INSTALL = (Path(__file__).resolve().parents[1] / "install.sh").read_text()


def test_nginx_talks_to_gunicorn_over_kept_alive_connections():
    assert re.search(r"upstream \$\{upstream_name\} \{\s*server 127\.0\.0\.1:\$\{APP_PORT\};\s*keepalive \d+;", INSTALL)
    # Both variants of the site, with and without a certificate.
    assert INSTALL.count("proxy_http_version 1.1;") == 2
    assert INSTALL.count('proxy_set_header Connection "";') == 2
    assert "proxy_pass http://127.0.0.1:${APP_PORT};" not in INSTALL


def test_gunicorn_holds_an_idle_connection_longer_than_nginx_does():
    """So nginx closes first, and never sends a request down a closing one."""
    gunicorn = int(re.search(r"--keep-alive (\d+)", INSTALL).group(1))
    nginx = int(re.search(r"keepalive_timeout (\d+)s;", INSTALL).group(1))

    assert gunicorn > nginx


def test_the_connection_tracking_table_is_sized_on_install_and_update():
    body = INSTALL.split("install_or_update() {", 1)[1].split("\n}\n", 1)[0]

    assert "configure_connection_tracking" in body
    assert "net.netfilter.nf_conntrack_max = 65536" in INSTALL
