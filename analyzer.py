"""두 쿼리 결과를 컨테이너 단위로 합치고 임계치로 거른다."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class ContainerRestart:
    namespace: str
    pod: str
    container: str
    restarts: int
    last_reason: str  # OOMKilled, Error, Completed ... 정보가 없으면 "-"
    previous_logs: str | None = None  # --logs 지정 시 재시작 직전 로그


def _key(metric: dict) -> tuple[str, str, str]:
    return (metric.get("namespace", ""), metric.get("pod", ""), metric.get("container", ""))


def summarize(restart_rows: list[dict], reason_rows: list[dict], threshold: int) -> list[ContainerRestart]:
    reasons = {_key(r["metric"]): r["metric"].get("reason", "-") for r in reason_rows}

    result = []
    for r in restart_rows:
        value = float(r["value"][1])
        # Prometheus 는 계산할 수 없는 값을 "NaN" / "+Inf" 문자열로 줄 수 있다 → 재시작 수로 볼 수 없으므로 건너뜀
        if not math.isfinite(value):
            continue
        # increase()는 외삽 때문에 2.9998 같은 소수가 나오므로 반올림.
        # round()는 2.5 -> 2 처럼 짝수 쪽으로 반올림하므로 사사오입(floor(x + 0.5))을 쓴다
        count = math.floor(value + 0.5)
        if count < threshold:
            continue
        key = _key(r["metric"])
        result.append(ContainerRestart(*key, restarts=count, last_reason=reasons.get(key, "-")))

    result.sort(key=lambda c: c.restarts, reverse=True)
    return result
