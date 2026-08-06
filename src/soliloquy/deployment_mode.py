# ───────────────────────────────────────────────────────────────────
# deployment_mode.py — is this pointed at localhost/LAN, or the cloud?
# ───────────────────────────────────────────────────────────────────
# A lightweight, informational signal (not an access-control boundary
# by itself) derived from where the app is actually configured to
# connect -- DATABASE_URL, S3_ENDPOINT_URL, MQTT_HOST -- rather than a
# flag someone has to remember to set. "local" means every one of
# those resolves to localhost or a private LAN address (the default
# docker-compose posture, where 0.0.0.0 bindings and dev credentials
# are a reasonable tradeoff); "cloud" means at least one of them is a
# real public host, where those same defaults would be a real problem.
#
# Deliberately just describes the situation in one line at startup --
# no refuse-to-start, no loud warning. See CHECKLIST.md for why (and
# for what a stronger enforcement mechanism would look like later, if
# ever wanted).
# ───────────────────────────────────────────────────────────────────

from __future__ import annotations

import ipaddress
import os
from urllib.parse import urlparse

from .actions import DEFAULT_DATABASE_URL


def _is_local_host(host: str) -> bool:
    if not host or host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_private
    except ValueError:
        return False  # a real hostname -- a managed cloud provider, a public IP, etc.


def get_deployment_mode() -> str:
    """"local" if Postgres, object storage, and MQTT are all on
    localhost/a private address; "cloud" if any of them is a real
    public host."""
    database_url = os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)
    s3_endpoint = os.environ.get("S3_ENDPOINT_URL", "http://localhost:9000")
    mqtt_host = os.environ.get("MQTT_HOST", "localhost")

    hosts = [urlparse(database_url).hostname, urlparse(s3_endpoint).hostname, mqtt_host]
    return "local" if all(_is_local_host(host) for host in hosts) else "cloud"


def describe_deployment_mode() -> str:
    if get_deployment_mode() == "local":
        return "Running in LOCAL mode -- Postgres/object storage/MQTT are all on localhost or a private LAN address."
    return "Running in CLOUD mode -- at least one backend (Postgres/object storage/MQTT) is a public host."
