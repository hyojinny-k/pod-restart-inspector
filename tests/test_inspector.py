import io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import history
import logs
import main
import notify
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


# --- --logs / --save / --slack-webhook -------------------------------------------------


@pytest.fixture
def fake_prom(monkeypatch):
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    monkeypatch.setattr(prom_api, "query", lambda url, q: RESTARTS if "increase" in q else REASONS)


def test_previous_logs_uses_kubectl_outside_cluster(monkeypatch):
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        return logs.subprocess.CompletedProcess(cmd, 0, stdout="start\nboom\n", stderr="")

    monkeypatch.setattr(logs, "in_cluster", lambda: False)
    monkeypatch.setattr(logs.subprocess, "run", run)
    assert logs.previous_logs("default", "crasher", "crasher", 5) == "start\nboom"
    assert calls[0] == ["kubectl", "logs", "-n", "default", "crasher", "-c", "crasher", "--previous", "--tail=5"]


def test_previous_logs_failure_is_not_fatal(monkeypatch):
    monkeypatch.setattr(logs, "in_cluster", lambda: False)

    def missing(cmd, **kw):
        raise FileNotFoundError("kubectl")

    monkeypatch.setattr(logs.subprocess, "run", missing)
    assert logs.previous_logs("d", "p", "c", 5).startswith("(로그 조회 실패")

    monkeypatch.setattr(
        logs.subprocess, "run", lambda cmd, **kw: logs.subprocess.CompletedProcess(cmd, 1, "", "previous terminated container not found")
    )
    assert "not found" in logs.previous_logs("d", "p", "c", 5)


def test_logs_option_adds_section(fake_prom, monkeypatch, capsys):
    monkeypatch.setattr(logs, "previous_logs", lambda ns, pod, c, tail: f"{pod} last {tail}")
    assert main.main(["--threshold", "3", "--logs", "7"]) == 1
    out = capsys.readouterr().out
    assert "--- default/crasher/crasher (previous logs) ---\ncrasher last 7" in out


def test_logs_off_by_default(fake_prom, monkeypatch, capsys):
    monkeypatch.setattr(logs, "previous_logs", lambda *a: pytest.fail("logs should not be fetched"))
    main.main(["--threshold", "3"])
    assert "previous logs" not in capsys.readouterr().out


def test_markdown_logs_in_code_block():
    items = summarize(RESTARTS, REASONS, 3)
    items[0].previous_logs = "boom"
    assert "#### default/crasher/crasher (previous logs)\n```\nboom\n```" in render(items, "markdown", "1h", 3)


def test_history_append_jsonl(tmp_path):
    path = tmp_path / "sub" / "history.jsonl"
    history.append(str(path), summarize(RESTARTS, REASONS, 3), "1h", 3)
    history.append(str(path), [], "1h", 3)
    first, second = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert first["window"] == "1h" and first["containers"][0]["pod"] == "crasher"
    assert second["containers"] == [] and "time" in second


def test_save_option_and_failure_keeps_exit_code(fake_prom, tmp_path, capsys):
    path = tmp_path / "h.jsonl"
    assert main.main(["--threshold", "3", "--save", str(path)]) == 1
    assert len(path.read_text(encoding="utf-8").splitlines()) == 1

    assert main.main(["--threshold", "3", "--save", str(tmp_path)]) == 1  # 디렉터리라 쓰기 실패
    assert "[WARN] 결과 저장 실패" in capsys.readouterr().err


def test_send_slack_posts_json(monkeypatch):
    sent = {}

    def urlopen(req, timeout):
        sent["url"], sent["body"] = req.full_url, json.loads(req.data)
        return _Resp(b"ok")

    monkeypatch.setattr(notify.urllib.request, "urlopen", urlopen)
    notify.send_slack("https://hooks.example/x", "hello")
    assert sent == {"url": "https://hooks.example/x", "body": {"text": "hello"}}


def test_slack_only_when_threshold_exceeded(fake_prom, monkeypatch):
    sent = []
    monkeypatch.setattr(notify, "send_slack", lambda url, text: sent.append(text))
    assert main.main(["--threshold", "100", "--slack-webhook", "https://hooks.example/x"]) == 0
    assert sent == []
    assert main.main(["--threshold", "3", "--slack-webhook", "https://hooks.example/x"]) == 1
    assert "Pod restart alert" in sent[0] and "crasher" in sent[0]


def test_slack_webhook_from_env(fake_prom, monkeypatch):
    sent = []
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.example/env")
    monkeypatch.setattr(notify, "send_slack", lambda url, text: sent.append(url))
    main.main(["--threshold", "3"])
    assert sent == ["https://hooks.example/env"]


def test_slack_failure_warns_without_leaking_url(fake_prom, monkeypatch, capsys):
    def down(url, text):
        raise OSError("connection refused")

    monkeypatch.setattr(notify, "send_slack", down)
    assert main.main(["--threshold", "3", "--slack-webhook", "https://hooks.example/secret"]) == 1
    err = capsys.readouterr().err
    assert "[WARN] Slack 알림 전송 실패" in err and "secret" not in err


def test_logs_must_be_non_negative():
    with pytest.raises(SystemExit):
        main.parse_args(["--logs", "-1"])


def test_reason_query_falls_back_to_window_for_deleted_pods():
    _, r = prom_api.build_queries("30m", "default", "")
    metric = 'kube_pod_container_status_last_terminated_reason{namespace="default"}'
    assert r == f"({metric} == 1) or on (namespace, pod, container) (last_over_time({metric}[30m]) == 1)"


def test_previous_logs_deleted_pod_message(monkeypatch):
    monkeypatch.setattr(logs, "in_cluster", lambda: False)
    monkeypatch.setattr(
        logs.subprocess, "run",
        lambda cmd, **kw: logs.subprocess.CompletedProcess(cmd, 1, "", 'Error from server (NotFound): pods "crasher" not found'),
    )
    assert logs.previous_logs("default", "crasher", "crasher", 5) == "(Pod 가 이미 삭제되어 로그를 볼 수 없음)"

    def http_404(*a):
        raise logs.urllib.error.HTTPError("https://k8s/api", 404, "Not Found", None, None)

    monkeypatch.setattr(logs, "in_cluster", lambda: True)
    monkeypatch.setattr(logs, "_from_api", http_404)
    assert logs.previous_logs("default", "crasher", "crasher", 5) == "(Pod 가 이미 삭제되어 로그를 볼 수 없음)"
