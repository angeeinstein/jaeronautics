"""Only one nginx configuration serves the portal (install.sh,
``retire_other_nginx_configs``).

On the live site an old conf.d/jaeronautics.conf from an earlier installation
kept answering for the domain -- nginx reads conf.d/ first -- while every
update wrote the real configuration to sites-available/. Its fixed security
policy blocked the new front end's styles. Run in bash against a folder
standing in for /etc/nginx; nothing here needs root or nginx.
"""
import subprocess

import pytest

from test_install_frontend import lib  # noqa: F401  (the fixture)

DOMAIN = "members.example.org"


@pytest.fixture
def nginx(tmp_path):
    root = tmp_path / "nginx"
    for folder in ("conf.d", "sites-available", "sites-enabled"):
        (root / folder).mkdir(parents=True)
    return root


def retire(lib, nginx, tmp_path, *, layout="sites"):  # noqa: F811
    if layout == "sites":
        ours, enabled = nginx / "sites-available/portal.conf", nginx / "sites-enabled/portal.conf"
    else:
        ours, enabled = nginx / "conf.d/portal.conf", ""
    script = f"""
source "{lib}"
trap - ERR
warn() {{ echo "WARN $*"; }}
NGINX_DIR="{nginx}" NGINX_CONF_PATH="{ours}" NGINX_ENABLED_PATH="{enabled}"
SERVICE_NAME=portal APP_NAME=portal BACKUP_DIR="{tmp_path / 'backups'}" DOMAIN="{DOMAIN}"
retire_other_nginx_configs
"""
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_an_old_conf_d_file_is_moved_to_the_backups(lib, nginx, tmp_path):  # noqa: F811
    old = nginx / "conf.d/portal.conf"
    old.write_text(f"server {{ server_name {DOMAIN}; add_header Content-Security-Policy \"style-src 'self'\"; }}")

    said = retire(lib, nginx, tmp_path)

    assert not old.exists()
    kept = list((tmp_path / "backups").iterdir())
    assert len(kept) == 1 and "Content-Security-Policy" in kept[0].read_text()
    assert "second nginx configuration" in said


def test_and_the_other_way_round(lib, nginx, tmp_path):  # noqa: F811
    old = nginx / "sites-available/portal.conf"
    old.write_text("server {}")
    (nginx / "sites-enabled/portal.conf").symlink_to(old)

    retire(lib, nginx, tmp_path, layout="conf.d")

    assert not old.exists() and not (nginx / "sites-enabled/portal.conf").is_symlink()


def test_its_own_file_is_left_alone(lib, nginx, tmp_path):  # noqa: F811
    ours = nginx / "sites-available/portal.conf"
    ours.write_text(f"server {{ server_name {DOMAIN}; }}")
    (nginx / "sites-enabled/portal.conf").symlink_to(ours)

    said = retire(lib, nginx, tmp_path)

    assert ours.exists() and (nginx / "sites-enabled/portal.conf").is_symlink()
    assert said == ""


def test_somebody_elses_file_for_the_same_domain_is_only_reported(lib, nginx, tmp_path):  # noqa: F811
    theirs = nginx / "conf.d/handmade.conf"
    theirs.write_text(f"server {{ listen 80; server_name {DOMAIN} www.example.org; }}")
    unrelated = nginx / "conf.d/other.conf"
    unrelated.write_text("server { server_name shop.example.org; }")

    said = retire(lib, nginx, tmp_path)

    assert theirs.exists() and unrelated.exists()
    assert f"{theirs} also serves {DOMAIN}" in said
    assert "other.conf" not in said
