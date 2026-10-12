"""Client for the local HTTPS API of Alfen Eve / NG9xx chargers."""

from __future__ import annotations

import json
import re
import ssl
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterator

import requests
import urllib3
from requests.adapters import HTTPAdapter

from .properties import PROPERTY_IDS, PROPERTY_NAMES

# Alfen firmware ships an old self-signed certificate and old TLS ciphers.
_CIPHERS = "DEFAULT:@SECLEVEL=0"


class _LegacyTLSAdapter(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        ctx.set_ciphers(_CIPHERS)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        kwargs["ssl_context"] = ctx
        return super().init_poolmanager(*args, **kwargs)


class AlfenError(RuntimeError):
    pass


@dataclass
class Property:
    id: str
    value: Any
    name: str
    cat: str | None = None
    raw: dict | None = None


@dataclass
class LogLine:
    id: int
    timestamp: datetime | None
    level: str
    source: str
    message: str


_LOG_RE = re.compile(
    r"^(?P<id>\d+)_(?P<ts>\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:?\d\d)?)[\s:]+(?P<level>[A-Z]+):(?P<file>[^:]*):(?P<line>\d+):(?P<msg>.*)$"
)


def parse_log_line(line: str) -> LogLine | None:
    m = _LOG_RE.match(line.strip())
    if not m:
        # Fixed-width fallback: "<id>_<24-char timestamp> <level>:<file>:<line>:<msg>"
        try:
            lid, rest = line.split("_", 1)
            level, file, num, msg = rest[25:].split(":", 3)
            ts = datetime.fromisoformat(rest[:24].replace("Z", "+00:00"))
            return LogLine(int(lid), ts, level, f"{file}:{num}", msg.strip())
        except ValueError:
            return None
    ts = None
    try:
        ts = datetime.fromisoformat(m["ts"].rstrip(":").replace("Z", "+00:00"))
    except ValueError:
        pass
    return LogLine(int(m["id"]), ts, m["level"], f'{m["file"]}:{m["line"]}', m["msg"].strip())


class Alfen:
    """Session against https://<host>/api. Use as a context manager to log in/out.

    The charger allows one API session at a time; logging in here ends an
    ACE Service Installer session and vice versa.
    """

    def __init__(self, host: str, username: str = "admin", password: str | None = None,
                 timeout: float = 15.0, displayname: str = "alfen-cli"):
        self.base = host if host.startswith("http") else f"https://{host}"
        self.base = self.base.rstrip("/")
        self.username = username
        self.password = password
        self.timeout = timeout
        self.displayname = displayname
        self.session = requests.Session()
        self.session.verify = False
        self.session.mount("https://", _LegacyTLSAdapter())
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    # -- low level -------------------------------------------------------
    def _url(self, path: str) -> str:
        return f"{self.base}/api/{path.lstrip('/')}"

    def _request(self, method: str, path: str, *, retry_login: bool = True, **kw) -> requests.Response:
        r = self.session.request(method, self._url(path), timeout=self.timeout, **kw)
        if r.status_code in (401, 403) and retry_login and self.password:
            self.login()
            return self._request(method, path, retry_login=False, **kw)
        if r.status_code >= 400:
            raise AlfenError(f"{method} /api/{path}: HTTP {r.status_code} {r.text[:200]}")
        return r

    @staticmethod
    def _json(r: requests.Response) -> Any:
        if not r.text:
            return None
        try:
            return r.json()
        except json.JSONDecodeError:
            # Some firmware emits bare nan.
            return json.loads(r.text.replace(":nan", ":NaN"))

    # -- session ---------------------------------------------------------
    def login(self) -> None:
        if not self.password:
            raise AlfenError("password required (ALFEN_PASSWORD or --password)")
        self.session.cookies.clear()
        self._request("POST", "login", retry_login=False, json={
            "username": self.username, "password": self.password, "displayname": self.displayname})

    def logout(self) -> None:
        try:
            self._request("POST", "logout", retry_login=False, json=None)
        except (AlfenError, requests.RequestException):
            pass
        self.session.cookies.clear()

    def __enter__(self) -> "Alfen":
        self.login()
        return self

    def __exit__(self, *exc) -> None:
        self.logout()

    # -- API -------------------------------------------------------------
    def info(self) -> dict:
        """Unauthenticated: identity, model, firmware."""
        return self._json(self._request("GET", "info", retry_login=False))

    def categories(self) -> list[str]:
        return [c for c in self._json(self._request("GET", "categories"))["categories"] if c]

    def properties(self, category: str | None = None, ids: list[str] | None = None) -> Iterator[Property]:
        if ids:
            params: dict[str, Any] = {"ids": ",".join(resolve_id(i) for i in ids)}
        elif category:
            params = {"cat": category}
        else:
            raise AlfenError("give a category or property ids")
        data = self._json(self._request("GET", "prop", params=params))
        version = data.get("version")
        if version == 1:
            for key, p in data.items():
                if key in ("count", "version"):
                    continue
                yield Property(id=p.get("id", key), value=p.get("value"), name=key, cat=p.get("cat"), raw=p)
            return
        if version != 2:
            raise AlfenError(f"unsupported /api/prop version {version}")
        props = list(data["properties"])
        total = data.get("total", len(props))
        while len(props) < total:
            params["offset"] = len(props)
            more = self._json(self._request("GET", "prop", params=params))["properties"]
            if not more:
                break
            props.extend(more)
        for p in props:
            yield Property(id=p["id"], value=p.get("value"), name=PROPERTY_NAMES.get(p["id"], p["id"]),
                           cat=p.get("cat"), raw=p)

    def set_properties(self, values: dict[str, Any]) -> Any:
        body = {}
        for key, value in values.items():
            pid = resolve_id(key)
            body[pid] = {"id": pid, "value": value}
        return self._json(self._request("POST", "prop", json=body))

    def command(self, command: str) -> Any:
        return self._json(self._request("POST", "cmd", json={"command": command}))

    def reboot(self) -> Any:
        return self.command("reboot")

    def log_page(self, offset: int = 0) -> list[str]:
        return self._request("GET", "log", params={"offset": offset}).text.splitlines()

    def logs(self, since: int | None = None, limit: int | None = None) -> Iterator[LogLine]:
        """Newest first. Stops at `since` (exclusive) or after `limit` lines."""
        offset, n, seen = 0, 0, set()
        while True:
            lines = self.log_page(offset)
            parsed = [p for p in (parse_log_line(l) for l in lines) if p]
            if not parsed:
                return
            for entry in sorted(parsed, key=lambda e: e.id, reverse=True):
                if entry.id in seen:
                    continue
                if since is not None and entry.id <= since:
                    return
                seen.add(entry.id)
                yield entry
                n += 1
                if limit and n >= limit:
                    return
            if min(e.id for e in parsed) <= 0:
                return
            offset += 32

    def transactions(self) -> Iterator[str]:
        offset = 0
        while True:
            lines = self._request("GET", "transactions", params={"offset": offset}).text.splitlines()
            if not lines:
                return
            last_id, last_type = None, None
            for line in lines:
                yield line
                head = line.split(" ", 1)[0]
                if "_" in head:
                    last_id, _, rest = head.partition("_")
                    last_type = rest.rstrip(":")
            if last_id is None or last_type == "txstop" or not last_id.isdigit():
                return
            if int(last_id) == offset:
                return
            offset = int(last_id)

    def whitelist(self, index: int = 1) -> list[dict]:
        return [e for e in self._json(self._request("GET", "whitelist", params={"index": index}))
                .get("whitelist", []) if e]


def resolve_id(key: str) -> str:
    """Accept a raw id (2126_0) or a known name (OD_mainAuthorizationMethod)."""
    return PROPERTY_IDS.get(key, key)
