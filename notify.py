"""Slack Incoming Webhook 알림."""

from __future__ import annotations

import json
import urllib.request


def send_slack(webhook_url: str, text: str, timeout: float = 10.0) -> None:
    data = json.dumps({"text": text}).encode("utf-8")
    req = urllib.request.Request(webhook_url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        resp.read()
