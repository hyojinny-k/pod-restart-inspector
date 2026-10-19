"""재시작 직전 컨테이너 로그(kubectl logs --previous) 조회.

- 클러스터 밖(master 노드 등): kubectl 실행
- 클러스터 안(CronJob): kubectl 없이 ServiceAccount 토큰으로 Kubernetes API 직접 호출
"""

from __future__ import annotations

import http.client
import os
import ssl
import subprocess
import urllib.error
import urllib.parse
import urllib.request

SA_DIR = "/var/run/secrets/kubernetes.io/serviceaccount"


def in_cluster() -> bool:
    return "KUBERNETES_SERVICE_HOST" in os.environ and os.path.exists(f"{SA_DIR}/token")


def previous_logs(namespace: str, pod: str, container: str, tail: int, timeout: float = 10.0) -> str:
    """마지막 tail 줄을 반환한다. 실패해도 전체 실행을 멈추지 않도록 에러 문구를 반환한다."""
    try:
        fetch = _from_api if in_cluster() else _from_kubectl
        text = fetch(namespace, pod, container, tail, timeout)
    except (OSError, http.client.HTTPException, subprocess.SubprocessError, RuntimeError) as e:
        if _pod_not_found(e):
            return "(Pod 가 이미 삭제되어 로그를 볼 수 없음)"
        return f"(로그 조회 실패: {e})"
    return text.rstrip() or "(로그 없음)"


def _pod_not_found(e: Exception) -> bool:
    # kubectl: 'Error from server (NotFound): pods "x" not found' / API: HTTP 404
    # (이전 컨테이너가 없을 때는 400 BadRequest 라서 구분된다)
    if isinstance(e, urllib.error.HTTPError):
        return e.code == 404
    return "(NotFound)" in str(e)


def _from_kubectl(namespace: str, pod: str, container: str, tail: int, timeout: float) -> str:
    cmd = ["kubectl", "logs", "-n", namespace, pod, "-c", container, "--previous", f"--tail={tail}"]
    r = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or f"kubectl exit {r.returncode}")
    return r.stdout


def _from_api(namespace: str, pod: str, container: str, tail: int, timeout: float) -> str:
    host = os.environ["KUBERNETES_SERVICE_HOST"]
    port = os.environ.get("KUBERNETES_SERVICE_PORT", "443")
    with open(f"{SA_DIR}/token", encoding="utf-8") as f:
        token = f.read().strip()
    query = urllib.parse.urlencode({"container": container, "previous": "true", "tailLines": tail})
    path = f"/api/v1/namespaces/{urllib.parse.quote(namespace)}/pods/{urllib.parse.quote(pod)}/log"
    req = urllib.request.Request(f"https://{host}:{port}{path}?{query}", headers={"Authorization": f"Bearer {token}"})
    ctx = ssl.create_default_context(cafile=f"{SA_DIR}/ca.crt")
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        return resp.read().decode("utf-8", errors="replace")
