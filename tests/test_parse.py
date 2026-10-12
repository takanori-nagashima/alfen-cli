from alfen_cli.client import parse_log_line, resolve_id
from alfen_cli.cli import build_parser


def test_parse_log_line():
    e = parse_log_line("1234_2026-10-12T13:41:23.456Z:INFO:ocpp.c:812:RequestStartTransaction rejected")
    assert e and e.id == 1234 and e.level == "INFO" and e.source == "ocpp.c:812"
    assert e.message.startswith("RequestStartTransaction")


def test_parse_log_line_fixed_width():
    e = parse_log_line("7_2026-10-12T13:41:23.456Z DEBUG:main.c:1:hello: world")
    assert e and e.id == 7 and e.message == "hello: world"


def test_resolve_id():
    assert resolve_id("OD_mainAuthorizationMethod") == "2126_0"
    assert resolve_id("20F2_3") == "20F2_3"


def test_parser():
    a = build_parser().parse_args(["--host", "x", "get", "-c", "ocpp"])
    assert a.category == "ocpp" and a.fn.__name__ == "cmd_get"
