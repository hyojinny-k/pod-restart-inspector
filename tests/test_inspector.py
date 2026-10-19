import io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import main
import prom_api
from analyzer import summarize
from formatter import render, summary_lines

RESTARTS = [
    {"metric": {"namespace": "default", "pod": "crasher", "container": "crasher"}, "value": [0, "5.0001"]},
    {"metric": {"namespace": "default", "pod": "oom-test", "container": "app"}, "value": [0, "2.9998"]},
    {"metric": {"namespace": "default", "pod": "nginx-1", "container": "nginx"}, "value": [0, "0"]},
]
REASONS = [
    {"metric": {"namespace": "default", "pod": "crasher", "container": "crasher", "reason": "Error"}, "value": [0, "1"]},
    {"metric": {"namespace": "default", "pod": "oom-test", "container": "app", "reason": "OOMKilled"}, "value": [0, "1"]},
]


def test_queries_use_increase_and_exclude_system_ns():
    q, _ = prom_api.build_queries("1h", None, "kube-system|monitoring")
    assert "increase(" in q and "[1h]" in q
    assert 'namespace!~"kube-system|monitoring"' in q


def test_namespace_option_overrides_exclude():
    q, r = prom_api.build_queries("30m", "game", "kube-system")
    assert 'namespace="game"' in q and 'namespace="game"' in r


def test_summarize_rounds_filters_and_sorts():
    items = summarize(RESTARTS, REASONS, threshold=3)
    assert [(c.pod, c.restarts, c.last_reason) for c in items] == [
        ("crasher", 5, "Error"),
        ("oom-test", 3, "OOMKilled"),  # 2.9998 -> 3
    ]


def test_summary_mentions_top_and_oom():
    text = "\n".join(summary_lines(summarize(RESTARTS, REASONS, 3), "1h", 3))
    assert "2 container(s) exceeded" in text
    assert "default/crasher/crasher = 5" in text
    assert "OOMKilled: 1" in text


def test_render_empty_and_json():
    assert "No containers exceeded" in render([], "table", "1h", 3)
    data = json.loads(render(summarize(RESTARTS, REASONS, 3), "json", "1h", 3))
    assert data["containers"][0]["pod"] == "crasher"


def test_exit_codes(monkeypatch):
    monkeypatch.setattr(prom_api, "query", lambda url, q: RESTARTS if "increase" in q else REASONS)
    assert main.main(["--threshold", "3"]) == 1
    assert main.main(["--threshold", "100"]) == 0

    def down(url, q):
        raise RuntimeError("down")

    monkeypatch.setattr(prom_api, "query", down)
    assert main.main([]) == 2


def test_rounding_is_half_up():
    rows = [{"metric": {"namespace": "d", "pod": "p", "container": "c"}, "value": [0, "2.5"]}]
    assert summarize(rows, [], threshold=0)[0].restarts == 3  # round(2.5) 는 2


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.mark.parametrize("payload", [b"<html>502 Bad Gateway</html>", b"[]", b'{"status": "success"}'])
def test_query_bad_response_raises_runtime_error(monkeypatch, payload):
    monkeypatch.setattr(prom_api.urllib.request, "urlopen", lambda url, timeout: _Resp(payload))
    with pytest.raises(RuntimeError):
        prom_api.query("http://prom", "up")


def test_non_json_response_exits_2(monkeypatch, capsys):
    monkeypatch.setattr(prom_api.urllib.request, "urlopen", lambda url, timeout: _Resp(b"not json"))
    assert main.main([]) == 2
    assert "[ERROR]" in capsys.readouterr().err


@pytest.mark.parametrize("value", ["1h", "30m", "1h30m", "500ms", "7d"])
def test_valid_window(value):
    assert main.parse_args(["--window", value]).window == value


@pytest.mark.parametrize(
    "args",
    [
        ["--window", "1 hour"],
        ["--window", "1h]"],
        ["--window", ""],
        ["--namespace", 'default"} or up{x="'],
        ["--namespace", "Default"],
        ["--namespace", "a" * 64],
        ["--exclude-ns", 'kube-system"'],
        ["--exclude-ns", "kube-(system"],
    ],
)
def test_invalid_args_exit_2(args, capsys):
    with pytest.raises(SystemExit) as e:
        main.parse_args(args)
    assert e.value.code == 2
    assert "error" in capsys.readouterr().err
