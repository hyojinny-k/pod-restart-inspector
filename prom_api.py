"""Prometheus HTTP API 조회와 PromQL 생성."""

from __future__ import annotations

import json
import urllib.parse
import urllib.request


def query(prom_url: str, promql: str, timeout: float = 10.0) -> list[dict]:
    """/api/v1/query 로 instant query를 실행하고 result 배열을 반환한다."""
    url = f"{prom_url.rstrip('/')}/api/v1/query?" + urllib.parse.urlencode({"query": promql})
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        body = json.load(resp)
    if body.get("status") != "success":
        raise RuntimeError(f"Prometheus error: {body.get('error', body)}")
    return body["data"]["result"]


def build_queries(window: str, namespace: str | None, exclude_ns: str) -> tuple[str, str]:
    """(기간 내 재시작 증가량 쿼리, 마지막 종료 사유 쿼리)를 만든다.

    restarts_total 은 Pod 생성 이후 누적 카운터이므로 그대로 쓰면
    '예전에 많이 죽었던' 컨테이너가 상위에 뜬다. increase()로 기간 내 증가량만 본다.
    """
    selector = f'namespace="{namespace}"' if namespace else f'namespace!~"{exclude_ns}"'
    restarts_q = (
        "sum by (namespace, pod, container) "
        f"(increase(kube_pod_container_status_restarts_total{{{selector}}}[{window}]))"
    )
    reason_q = f"kube_pod_container_status_last_terminated_reason{{{selector}}} == 1"
    return restarts_q, reason_q
