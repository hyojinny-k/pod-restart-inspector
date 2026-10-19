"""Prometheus HTTP API 조회와 PromQL 생성."""

from __future__ import annotations

import json
import urllib.parse
import urllib.request


def query(prom_url: str, promql: str, timeout: float = 10.0) -> list[dict]:
    """/api/v1/query 로 instant query를 실행하고 result 배열을 반환한다."""
    url = f"{prom_url.rstrip('/')}/api/v1/query?" + urllib.parse.urlencode({"query": promql})
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        try:
            body = json.load(resp)
        except ValueError as e:  # 프록시 에러 페이지 등 JSON 이 아닌 응답
            raise RuntimeError(f"JSON 이 아닌 응답: {e}") from e
    if not isinstance(body, dict):
        body = {"error": f"예상과 다른 응답 형식: {body}"}
    if body.get("status") != "success":
        raise RuntimeError(f"Prometheus error: {body.get('error', body)}")
    try:
        return body["data"]["result"]
    except (KeyError, TypeError) as e:
        raise RuntimeError(f"예상과 다른 응답 형식: {body}") from e


def build_queries(window: str, namespace: str | None, exclude_ns: str) -> tuple[str, str]:
    """(기간 내 재시작 증가량 쿼리, 마지막 종료 사유 쿼리)를 만든다.

    restarts_total 은 Pod 생성 이후 누적 카운터이므로 그대로 쓰면
    '예전에 많이 죽었던' 컨테이너가 상위에 뜬다. increase()로 기간 내 증가량만 본다.

    종료 사유는 현재 값을 우선 쓰고, 값이 없으면(조회 시점에 Pod 가 이미 삭제된 경우)
    기간 내 마지막 값(last_over_time)으로 채운다. `or on(...)` 은 왼쪽에 없는 컨테이너만 오른쪽에서 가져온다.
    """
    selector = f'namespace="{namespace}"' if namespace else f'namespace!~"{exclude_ns}"'
    restarts_q = (
        "sum by (namespace, pod, container) "
        f"(increase(kube_pod_container_status_restarts_total{{{selector}}}[{window}]))"
    )
    reason_metric = f"kube_pod_container_status_last_terminated_reason{{{selector}}}"
    reason_q = (
        f"({reason_metric} == 1) or on (namespace, pod, container) "
        f"(last_over_time({reason_metric}[{window}]) == 1)"
    )
    return restarts_q, reason_q
