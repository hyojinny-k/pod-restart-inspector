import json
import sys
from pathlib import Path

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
