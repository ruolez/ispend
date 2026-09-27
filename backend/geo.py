"""Country for an IP address, without calling anyone.

Two local sources, both optional:
- a CDN header (Cloudflare's CF-IPCountry) when TRUST_CF_COUNTRY=1 says one sits in front;
- a MaxMind-format country database at GEOIP_COUNTRY_DB, read with the `maxminddb` package if it
  is installed.
Neither configured means no country, which every caller handles.
"""

import ipaddress
import logging
import os
import re

log = logging.getLogger(__name__)

_COUNTRY = re.compile(r"^[A-Z]{2}$")
_READER = {}     # opened at most once per process; {"reader": obj-or-None} once tried


def _trust_cf():
    return os.environ.get("TRUST_CF_COUNTRY", "0") == "1"


def _db_reader():
    if "reader" in _READER:
        return _READER["reader"]
    _READER["reader"] = None
    path = os.environ.get("GEOIP_COUNTRY_DB", "")
    if not path or not os.path.isfile(path):
        return None
    try:
        import maxminddb
        _READER["reader"] = maxminddb.open_database(path)
    except Exception:
        log.warning("GeoIP database at %s could not be opened; countries stay blank", path)
    return _READER["reader"]


def country(ip, headers=None):
    """Two-letter ISO code or None. 'XX'/'T1' (unknown, Tor) read as None."""
    if headers is not None and _trust_cf():
        code = (headers.get("CF-IPCountry") or "").strip().upper()
        if _COUNTRY.match(code) and code != "XX":
            return code
    reader = _db_reader()
    if reader is None or not ip:
        return None
    try:
        if not ipaddress.ip_address(ip).is_global:
            return None
        rec = reader.get(ip) or {}
    except Exception:
        return None
    code = ((rec.get("country") or rec.get("registered_country") or {}).get("iso_code") or "").upper()
    return code if _COUNTRY.match(code) else None


def network_prefix(ip):
    """The /24 (IPv4) or /48 (IPv6) an address sits in, as text for a CIDR column; None if unparsable.
    Home and mobile addresses churn inside these, so comparing prefixes flags a new place rather
    than a DHCP renewal."""
    try:
        addr = ipaddress.ip_address(ip)
    except (TypeError, ValueError):
        return None
    bits = 24 if addr.version == 4 else 48
    return str(ipaddress.ip_network(f"{addr}/{bits}", strict=False))
