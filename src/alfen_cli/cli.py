"""alfen: command line for the local API of Alfen Eve / NG9xx chargers."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

from . import __version__
from .client import Alfen, AlfenError


def _print(rows: list[dict], as_json: bool) -> None:
    if as_json:
        json.dump(rows, sys.stdout, indent=2, default=str)
        sys.stdout.write("\n")
        return
    if not rows:
        print("(no results)")
        return
    cols = list(rows[0].keys())
    widths = {c: min(60, max(len(c), *(len(str(r.get(c, ""))) for r in rows))) for c in cols}
    print("  ".join(c.ljust(widths[c]) for c in cols))
    print("  ".join("-" * widths[c] for c in cols))
    for r in rows:
        print("  ".join(str(r.get(c, ""))[:60].ljust(widths[c]) for c in cols))


def _client(a: argparse.Namespace) -> Alfen:
    if not a.host:
        sys.exit("error: --host or ALFEN_HOST is required")
    return Alfen(a.host, a.username, a.password, timeout=a.timeout)


def cmd_info(a):
    _print([_client(a).info()], a.json)


def cmd_categories(a):
    with _client(a) as c:
        _print([{"category": x} for x in c.categories()], a.json)


def cmd_get(a):
    with _client(a) as c:
        props = c.properties(category=a.category, ids=a.ids or None)
        rows = [{"id": p.id, "name": p.name, "value": p.value, **({"cat": p.cat} if a.verbose else {})}
                for p in props]
    _print(rows, a.json)


def _parse_value(v: str):
    for conv in (int, float):
        try:
            return conv(v)
        except ValueError:
            pass
    return v


def cmd_set(a):
    values = {}
    for item in a.assignments:
        key, sep, value = item.partition("=")
        if not sep:
            sys.exit(f"error: expected ID=VALUE, got {item!r}")
        values[key] = value if a.string else _parse_value(value)
    with _client(a) as c:
        result = c.set_properties(values)
        after = {p.id: p.value for p in c.properties(ids=list(values))}
    _print([{"id": k, "value": v} for k, v in after.items()], a.json)
    if a.verbose:
        print(json.dumps(result, default=str), file=sys.stderr)


# Display pricing (OD 3262_*). A set StartPrice/EnergyPrice with an empty
# Currency is an incomplete tariff and the charger faults with
# ErrorMissingTariffs, so `tariff` is the shortcut for getting all three right.
_TARIFF_IDS = {"currency": "3262_1", "start_price": "3262_2", "energy_price": "3262_3"}


def cmd_tariff(a):
    updates = {}
    if a.currency is not None:
        updates["3262_1"] = a.currency
    if a.start_price is not None:
        updates["3262_2"] = a.start_price
    if a.energy_price is not None:
        updates["3262_3"] = a.energy_price
    with _client(a) as c:
        if updates:
            c.set_properties(updates)
        props = list(c.properties(ids=list(_TARIFF_IDS.values())))
    _print([{"id": p.id, "name": p.name, "value": p.value} for p in props], a.json)


def cmd_logs(a):
    with _client(a) as c:
        if not a.follow:
            rows = list(c.logs(since=a.since, limit=a.limit))
            rows.reverse()
            for e in rows:
                _log(e, a.json)
            return
        last = a.since
        if last is None:
            first = next(iter(c.logs(limit=1)), None)
            last = first.id - 1 if first else None
        try:
            while True:
                new = list(c.logs(since=last, limit=a.limit))
                for e in reversed(new):
                    _log(e, a.json)
                    last = e.id
                time.sleep(a.interval)
        except KeyboardInterrupt:
            pass


def _log(e, as_json: bool):
    if as_json:
        print(json.dumps({"id": e.id, "timestamp": e.timestamp, "level": e.level,
                          "source": e.source, "message": e.message}, default=str), flush=True)
    else:
        ts = e.timestamp.isoformat(timespec="seconds") if e.timestamp else "-"
        print(f"{e.id:>8} {ts} {e.level:<5} {e.source:<28} {e.message}", flush=True)


def cmd_transactions(a):
    with _client(a) as c:
        for line in c.transactions():
            print(line)


def cmd_whitelist(a):
    with _client(a) as c:
        _print(c.whitelist(a.index), a.json)


def cmd_reboot(a):
    if not a.yes:
        sys.exit("refusing to reboot without --yes")
    with _client(a) as c:
        print(json.dumps(c.reboot(), default=str))


def cmd_verify(a):
    from .signed import verify
    result = verify(a.public_key, a.signed_data)
    print(json.dumps(result, indent=2))
    sys.exit(0 if result.get("verified") else 1)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="alfen", description=__doc__)
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("--host", default=os.environ.get("ALFEN_HOST"),
                   help="charger address, e.g. 192.168.1.20 (env ALFEN_HOST)")
    p.add_argument("--username", default=os.environ.get("ALFEN_USERNAME", "admin"),
                   help="default: admin (env ALFEN_USERNAME)")
    p.add_argument("--password", default=os.environ.get("ALFEN_PASSWORD"),
                   help="env ALFEN_PASSWORD (preferred over the flag)")
    p.add_argument("--timeout", type=float, default=15.0)
    p.add_argument("--json", action="store_true", help="JSON output")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("info", help="identity, model, firmware (no login)").set_defaults(fn=cmd_info)
    sub.add_parser("categories", help="list property categories").set_defaults(fn=cmd_categories)

    g = sub.add_parser("get", help="read properties by category or id/name")
    g.add_argument("ids", nargs="*", help="property ids (2126_0) or names (OD_mainAuthorizationMethod)")
    g.add_argument("-c", "--category", help="e.g. generic, ocpp, comm, meter1, states")
    g.add_argument("-v", "--verbose", action="store_true")
    g.set_defaults(fn=cmd_get)

    s = sub.add_parser("set", help="write properties: ID=VALUE ...")
    s.add_argument("assignments", nargs="+")
    s.add_argument("--string", action="store_true", help="send values as strings")
    s.add_argument("-v", "--verbose", action="store_true")
    s.set_defaults(fn=cmd_set)

    t = sub.add_parser("tariff", help="show or set display pricing (currency/start/energy); "
                                      "an empty currency causes ErrorMissingTariffs")
    t.add_argument("--currency", help="ISO currency code, e.g. EUR")
    t.add_argument("--start-price", dest="start_price", type=float, help="session start price")
    t.add_argument("--energy-price", dest="energy_price", type=float, help="price per kWh")
    t.set_defaults(fn=cmd_tariff)

    lg = sub.add_parser("logs", help="charger event log (plain text, newest last)")
    lg.add_argument("-n", "--limit", type=int, default=200)
    lg.add_argument("--since", type=int, help="only entries with id greater than this")
    lg.add_argument("-f", "--follow", action="store_true")
    lg.add_argument("--interval", type=float, default=5.0)
    lg.set_defaults(fn=cmd_logs)

    sub.add_parser("transactions", help="local transaction log").set_defaults(fn=cmd_transactions)

    w = sub.add_parser("whitelist", help="local whitelist (RFID)")
    w.add_argument("--index", type=int, default=1)
    w.set_defaults(fn=cmd_whitelist)

    r = sub.add_parser("reboot", help="reboot the charger")
    r.add_argument("--yes", action="store_true")
    r.set_defaults(fn=cmd_reboot)

    v = sub.add_parser("verify", help="verify an Alfen signed meter value (needs alfen-cli[signed])")
    v.add_argument("public_key", help="meter public key, base64")
    v.add_argument("signed_data", help="signedMeterData, base64 (AP;... envelope)")
    v.set_defaults(fn=cmd_verify)
    return p


def main(argv: list[str] | None = None) -> None:
    a = build_parser().parse_args(argv)
    try:
        a.fn(a)
    except AlfenError as e:
        sys.exit(f"error: {e}")
    except Exception as e:  # network errors etc.
        if os.environ.get("ALFEN_DEBUG"):
            raise
        hint = ""
        if "Connection reset by peer" in str(e) or isinstance(e, ConnectionResetError):
            hint = ("\nhint: the charger serves one API client at a time. Close ACE Service Installer,"
                    " the web UI or any other client connected to it, then retry.")
        sys.exit(f"error: {type(e).__name__}: {e}{hint}")


if __name__ == "__main__":
    main()
