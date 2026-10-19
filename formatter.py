"""table / json / markdown 출력과 요약(Summary)."""

from __future__ import annotations

import json
from dataclasses import asdict

from analyzer import ContainerRestart

HEADERS = ["NAMESPACE", "POD", "CONTAINER", "RESTARTS", "LAST_REASON"]


def _rows(items: list[ContainerRestart]) -> list[list[str]]:
    return [[c.namespace, c.pod, c.container, str(c.restarts), c.last_reason] for c in items]


def summary_lines(items: list[ContainerRestart], window: str, threshold: int) -> list[str]:
    lines = [f"[Summary] window={window}, threshold={threshold}"]
    if not items:
        lines.append("No containers exceeded restart threshold.")
        return lines
    top = items[0]
    lines.append(f"{len(items)} container(s) exceeded restart threshold.")
    lines.append(
        f"Highest: {top.namespace}/{top.pod}/{top.container} = {top.restarts} (last reason: {top.last_reason})"
    )
    oom = sum(1 for c in items if c.last_reason == "OOMKilled")
    if oom:
        lines.append(f"OOMKilled: {oom} container(s) -> memory limit 확인 필요")
    return lines


def to_table(items: list[ContainerRestart]) -> str:
    data = _rows(items)
    widths = [max(len(h), *(len(r[i]) for r in data)) if data else len(h) for i, h in enumerate(HEADERS)]

    def fmt(row: list[str]) -> str:
        return "  ".join(c.ljust(w) for c, w in zip(row, widths)).rstrip()

    return "\n".join([fmt(HEADERS)] + [fmt(r) for r in data])


def to_markdown(items: list[ContainerRestart]) -> str:
    lines = ["| " + " | ".join(HEADERS) + " |", "|" + "---|" * len(HEADERS)]
    lines += ["| " + " | ".join(r) + " |" for r in _rows(items)]
    return "\n".join(lines)


def render(items: list[ContainerRestart], fmt: str, window: str, threshold: int) -> str:
    if fmt == "json":
        return json.dumps(
            {"window": window, "threshold": threshold, "containers": [asdict(c) for c in items]},
            ensure_ascii=False,
            indent=2,
        )

    summary = summary_lines(items, window, threshold)
    if fmt == "markdown":
        body = to_markdown(items) if items else ""
        return "\n".join([f"### Pod restart report (window={window})", "", body, "", *summary]).strip()

    body = to_table(items) if items else ""
    return "\n".join([body, "", *summary]).strip()
