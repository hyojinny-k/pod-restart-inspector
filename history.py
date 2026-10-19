"""조회 결과를 JSON Lines 파일에 누적 저장한다.

Pod 가 삭제되면 kube-state-metrics 메트릭도 사라지므로, 실행할 때마다 한 줄씩 남겨 이력을 보존한다.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from analyzer import ContainerRestart


def append(path: str, items: list[ContainerRestart], window: str, threshold: int, now: datetime | None = None) -> None:
    record = {
        "time": (now or datetime.now().astimezone()).isoformat(timespec="seconds"),
        "window": window,
        "threshold": threshold,
        "containers": [asdict(c) for c in items],
    }
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
