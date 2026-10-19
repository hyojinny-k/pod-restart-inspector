"""pod-restart-inspector: 재시작이 잦은 K8s 컨테이너를 Prometheus에서 찾아 요약하는 CLI.

종료 코드
    0  임계치 이상 재시작한 컨테이너 없음
    1  임계치 이상 재시작한 컨테이너 있음 (cron / CI 알림 조건으로 사용)
    2  Prometheus 조회 실패 또는 잘못된 인자

결과 저장(--save), Slack 알림(--slack-webhook) 실패는 경고만 출력하고 종료 코드는 바꾸지 않는다.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import urllib.error

import history
import logs
import notify
import prom_api
from analyzer import summarize
from formatter import render, slack_text

# 시스템/모니터링용 네임스페이스는 기본 제외 (kube-system 에는 Calico, CoreDNS 등이 있음)
DEFAULT_EXCLUDE_NS = "kube-system|kube-public|kube-node-lease|monitoring|opsramp-agent"

# PromQL duration (예: 30m, 1h, 1h30m, 500ms) 과 K8s 네임스페이스 이름(DNS-1123 label) 형식
WINDOW_RE = re.compile(r"(\d+(ms|[smhdwy]))+")
NAMESPACE_RE = re.compile(r"[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?")


def window_arg(value: str) -> str:
    if not WINDOW_RE.fullmatch(value):
        raise argparse.ArgumentTypeError(f"잘못된 기간 형식: {value!r} (예: 30m, 1h, 1h30m, 1d)")
    return value


def namespace_arg(value: str) -> str:
    if not NAMESPACE_RE.fullmatch(value):
        raise argparse.ArgumentTypeError(f"잘못된 네임스페이스 이름: {value!r} (소문자, 숫자, '-' 만, 최대 63자)")
    return value


def exclude_ns_arg(value: str) -> str:
    # 정규식은 PromQL 문자열 "..." 안에 그대로 들어가므로 따옴표/역슬래시는 막는다
    if '"' in value or "\\" in value:
        raise argparse.ArgumentTypeError("--exclude-ns 에는 큰따옴표(\")와 역슬래시(\\)를 쓸 수 없습니다")
    try:
        re.compile(value)
    except re.error as e:
        raise argparse.ArgumentTypeError(f"잘못된 정규식: {e}") from e
    return value


def non_negative_int(value: str) -> int:
    n = int(value)
    if n < 0:
        raise argparse.ArgumentTypeError(f"0 이상이어야 합니다: {value}")
    return n


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="재시작이 잦은 K8s Pod/Container를 Prometheus에서 찾아 요약합니다.")
    p.add_argument("--prometheus", default="http://localhost:9090", help="Prometheus 주소 (기본: %(default)s)")
    p.add_argument("--window", type=window_arg, default="1h", help="조회 기간, PromQL range 형식 (예: 30m, 1h, 1d)")
    p.add_argument("--threshold", type=int, default=3, help="이 횟수 이상 재시작한 컨테이너만 표시")
    p.add_argument("--namespace", type=namespace_arg, help="특정 네임스페이스만 조회 (지정 시 --exclude-ns 무시)")
    p.add_argument("--exclude-ns", type=exclude_ns_arg, default=DEFAULT_EXCLUDE_NS, help="제외할 네임스페이스 정규식")
    p.add_argument("--output", choices=["table", "json", "markdown"], default="table")
    p.add_argument("--logs", type=non_negative_int, default=0, metavar="N",
                   help="임계치 초과 컨테이너마다 재시작 직전 로그(--previous) 마지막 N줄 출력 (기본 0: 끄기)")
    p.add_argument("--save", metavar="FILE", help="실행 결과를 FILE 에 JSON Lines 로 한 줄씩 누적 저장")
    p.add_argument("--slack-webhook", default=os.environ.get("SLACK_WEBHOOK_URL"), metavar="URL",
                   help="임계치 초과 시 Slack Incoming Webhook 으로 알림 (기본: 환경변수 SLACK_WEBHOOK_URL)")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    restarts_q, reason_q = prom_api.build_queries(args.window, args.namespace, args.exclude_ns)

    try:
        items = summarize(
            prom_api.query(args.prometheus, restarts_q),
            prom_api.query(args.prometheus, reason_q),
            args.threshold,
        )
    except (urllib.error.URLError, RuntimeError, TimeoutError) as e:
        print(f"[ERROR] Prometheus 조회 실패 ({args.prometheus}): {e}", file=sys.stderr)
        return 2

    if args.logs:
        for c in items:
            c.previous_logs = logs.previous_logs(c.namespace, c.pod, c.container, args.logs)

    print(render(items, args.output, args.window, args.threshold))

    if args.save:
        try:
            history.append(args.save, items, args.window, args.threshold)
        except OSError as e:
            print(f"[WARN] 결과 저장 실패 ({args.save}): {e}", file=sys.stderr)

    if items and args.slack_webhook:
        try:
            notify.send_slack(args.slack_webhook, slack_text(items, args.window, args.threshold))
        except (OSError, ValueError) as e:  # webhook URL 은 비밀값이므로 메시지에 넣지 않는다
            print(f"[WARN] Slack 알림 전송 실패: {e}", file=sys.stderr)

    return 1 if items else 0


if __name__ == "__main__":
    sys.exit(main())
