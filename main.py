"""pod-restart-inspector: 재시작이 잦은 K8s 컨테이너를 Prometheus에서 찾아 요약하는 CLI.

종료 코드
    0  임계치 이상 재시작한 컨테이너 없음
    1  임계치 이상 재시작한 컨테이너 있음 (cron / CI 알림 조건으로 사용)
    2  Prometheus 조회 실패
"""

from __future__ import annotations

import argparse
import sys
import urllib.error

import prom_api
from analyzer import summarize
from formatter import render

# 시스템/모니터링용 네임스페이스는 기본 제외 (kube-system 에는 Calico, CoreDNS 등이 있음)
DEFAULT_EXCLUDE_NS = "kube-system|kube-public|kube-node-lease|monitoring|opsramp-agent"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="재시작이 잦은 K8s Pod/Container를 Prometheus에서 찾아 요약합니다.")
    p.add_argument("--prometheus", default="http://localhost:9090", help="Prometheus 주소 (기본: %(default)s)")
    p.add_argument("--window", default="1h", help="조회 기간, PromQL range 형식 (예: 30m, 1h, 1d)")
    p.add_argument("--threshold", type=int, default=3, help="이 횟수 이상 재시작한 컨테이너만 표시")
    p.add_argument("--namespace", help="특정 네임스페이스만 조회 (지정 시 --exclude-ns 무시)")
    p.add_argument("--exclude-ns", default=DEFAULT_EXCLUDE_NS, help="제외할 네임스페이스 정규식")
    p.add_argument("--output", choices=["table", "json", "markdown"], default="table")
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

    print(render(items, args.output, args.window, args.threshold))
    return 1 if items else 0


if __name__ == "__main__":
    sys.exit(main())
