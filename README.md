# Pod Restart Inspector

![ci](https://github.com/hyojinny-k/pod-restart-inspector/actions/workflows/ci.yml/badge.svg)

Prometheus API에서 Kubernetes Pod/Container 재시작 메트릭을 조회해
**최근 N시간 동안 재시작이 잦은 컨테이너**를 종료 사유(OOMKilled / Error 등)와 함께 요약하는 CLI 도구입니다.

## Why
OpsRamp로 K8s 클러스터를 관측하면서, 반복적으로 죽는 컨테이너를 찾으려면 대시보드에서 Pod별 Restart 그래프를 하나씩 열어봐야 했습니다.
`kubectl get pods`의 RESTARTS는 Pod 생성 이후 누적값이라 "지금 문제인지"를 구분하기 어렵습니다.
그래서 **기간 기준으로, 왜 죽었는지까지** 한 번에 확인하고 장애 기록에 바로 붙일 수 있는 도구를 만들었습니다.

## Features
- Prometheus HTTP API 조회 (외부 라이브러리 없이 표준 라이브러리만 사용)
- `increase(kube_pod_container_status_restarts_total[window])` 로 **기간 내 재시작 횟수** 집계
- `kube_pod_container_status_last_terminated_reason` 결합 → OOMKilled / Error 구분
- 임계치 필터, 재시작 횟수 내림차순 정렬, `[Summary]` 요약
- 출력 형식: `table` / `json` / `markdown`
- 종료 코드: `0` 정상 · `1` 임계치 초과 · `2` 조회 실패 또는 잘못된 인자 → cron / CI 알림 조건으로 사용 가능
- `--window`, `--namespace`, `--exclude-ns` 입력값 검증 (잘못된 값은 쿼리 전에 에러 메시지로 안내)
- 시스템 네임스페이스(kube-system, monitoring 등) 기본 제외, `--namespace` / `--exclude-ns` 로 조정
- `--logs N`: 임계치 초과 컨테이너의 **재시작 직전 로그**(`kubectl logs --previous`) 마지막 N줄 함께 출력
- `--save FILE`: 실행 결과를 JSON Lines 로 **누적 저장** → Pod 가 삭제돼 메트릭이 사라져도 이력 보존
- `--slack-webhook URL` (또는 `SLACK_WEBHOOK_URL`): 임계치 초과 시 **Slack 알림**
- 컨테이너 이미지(`Dockerfile`, GitHub Actions → GHCR 자동 빌드) + Kubernetes **CronJob**(`deploy/cronjob.yaml`)으로 주기 실행

## Example
실제 클러스터에서 `examples/crash-pods.yaml` 로 장애를 재현한 뒤 실행한 결과입니다.
```
$ python3 main.py --window 30m --threshold 2
NAMESPACE  POD       CONTAINER  RESTARTS  LAST_REASON
default    oom-test  app        4         OOMKilled
default    crasher   crasher    3         Error

[Summary] window=30m, threshold=2
2 container(s) exceeded restart threshold.
Highest: default/oom-test/app = 4 (last reason: OOMKilled)
OOMKilled: 1 container(s) -> memory limit 확인 필요
```
![demo](screenshots/demo.png)

## Usage
```bash
# 1) Prometheus 접근 (kube-prometheus-stack 기준)
kubectl port-forward -n monitoring svc/kps-kube-prometheus-stack-prometheus 9090:9090 &

# 2) 실행
python3 main.py --window 1h --threshold 3
python3 main.py --namespace default --output json
python3 main.py --output markdown > report.md
python3 main.py --help

# 3) 재시작 직전 로그 20줄 + 결과 이력 저장 + Slack 알림
export SLACK_WEBHOOK_URL='https://hooks.slack.com/services/...'
python3 main.py --window 30m --threshold 2 --logs 20 --save history.jsonl
```

`--save` 파일은 실행마다 한 줄씩 추가됩니다 (임계치 초과가 없어도 빈 목록으로 기록).
```json
{"time": "2026-10-19T15:30:00+09:00", "window": "30m", "threshold": 2, "containers": [{"namespace": "default", "pod": "crasher", "container": "crasher", "restarts": 3, "last_reason": "Error", "previous_logs": "start"}]}
```

결과 저장이나 Slack 전송이 실패해도 경고(`[WARN]`)만 출력하고 종료 코드는 그대로 둡니다.

## CronJob 으로 실행
`deploy/cronjob.yaml` 은 30분마다 실행되며, 이력은 control-plane 노드의
`/var/lib/pod-restart-inspector/history.jsonl` 에 쌓입니다. 클러스터 안에서는 kubectl 대신
ServiceAccount 토큰으로 Kubernetes API 를 직접 호출해 로그를 읽습니다 (`pods/log` get 권한만 부여).

이미지는 GitHub Actions 가 테스트 통과 후 main push 마다 빌드해
`ghcr.io/hyojinny-k/pod-restart-inspector` 에 올립니다 (`latest`, `sha-<커밋>` 태그).
노드에 docker 나 빌드 도구를 설치할 필요가 없습니다.

```bash
# 1) 이미지 받아지는지 확인 (노드에서)
sudo crictl pull ghcr.io/hyojinny-k/pod-restart-inspector:latest

# 2) 배포 (+ Slack 을 쓰는 경우 Secret 생성)
kubectl apply -f deploy/cronjob.yaml
kubectl -n pod-restart-inspector create secret generic slack-webhook --from-literal=url='https://hooks.slack.com/services/...'

# 3) 스케줄을 기다리지 않고 바로 한 번 실행
kubectl -n pod-restart-inspector create job --from=cronjob/pod-restart-inspector manual-1
kubectl -n pod-restart-inspector logs job/manual-1
```

- 종료 코드 `1`(임계치 초과)은 정상 결과이므로 Job 을 실패로 표시하지 않고, `2`(조회 실패)만 실패로 둡니다.
- 재시도하면 Slack 알림이 중복되므로 `backoffLimit: 0` 입니다.

## Test Environment
| 항목 | 구성 |
|---|---|
| Cluster | kubeadm v1.29.15, 3 nodes (control-plane 1, worker 2), Ubuntu 24.04 |
| Runtime / CNI | containerd 2.2.1 / Calico v3.27.0 (Pod CIDR 10.244.0.0/16) |
| Monitoring | kube-prometheus-stack (Helm), kube-state-metrics |

## Verification
`examples/crash-pods.yaml` 로 장애를 재현하고, 도구 결과를 `kubectl` 상태와 대조했습니다.
```bash
kubectl apply -f examples/crash-pods.yaml   # 20초마다 종료되는 Pod + 메모리 초과(OOMKilled) Pod
# 10~15분 대기
python3 main.py --window 30m --threshold 2
kubectl describe pod oom-test | grep -A3 "Last State"
kubectl describe pod crasher | grep -A3 "Last State"
```

> `CrashLoopBackOff` 는 재시작을 기다리는 **대기(Waiting) 상태**이고, 도구가 보여주는 값은
> 마지막 **종료(Terminated) 사유**입니다. 그래서 비교는 `describe` 의 `Last State` 기준으로 합니다.

| 검증 항목 | 도구 결과 | `kubectl` 실제 상태 | 결과 |
|---|---|---|---|
| oom-test 재시작 횟수 | 4 | RESTARTS 4 | ✅ |
| crasher 재시작 횟수 | 3 | RESTARTS 3 | ✅ |
| oom-test 종료 사유 | OOMKilled | Last State: Terminated, Reason OOMKilled, Exit Code 137 | ✅ |
| crasher 종료 사유 | Error | Last State: Terminated, Reason Error, Exit Code 1 (현재 상태는 CrashLoopBackOff) | ✅ |
| 임계치 초과 시 종료 코드 | `exit=1` | - | ✅ |
| Prometheus 연결 실패 | `exit=2` + 에러 메시지 | - | ✅ |

- 단위 테스트: `pytest -q` (GitHub Actions 에서 push 마다 실행)
- 전체 출력: [`sample_output.txt`](sample_output.txt)

## Design Notes
- **누적 카운터 대신 `increase()`**: 오래전에 많이 죽은 Pod가 상위에 뜨는 문제 방지
- **삭제된 Pod 의 종료 사유**: 재시작 횟수는 기간 내 이력으로 계산되지만 종료 사유를 현재 값으로만 조회하면
  장애 후 Pod 가 삭제됐을 때 `-` 로 나옴 → `or on(namespace, pod, container) last_over_time(...[window])` 로 기간 내 마지막 사유를 대신 사용
- `increase()` 는 외삽 때문에 `2.9998` 같은 소수가 나와 반올림 처리 (Python `round()` 는 2.5 → 2 이므로 사사오입 사용)
- kube-prometheus-stack 이 설치하는 kube-state-metrics(monitoring)는 기존 OpsRamp 수집용 kube-state-metrics(kube-system)와 분리해 기존 관측 구성에 영향을 주지 않도록 함

## Limitations / Next
- `--logs` 는 직전 1회 종료 로그만 볼 수 있음 (그 이전 재시작 로그는 kubelet 이 보관하지 않음). Pod 가 삭제되면 로그도 볼 수 없음
- 삭제된 Pod 가 기간 내에 종료 사유가 여러 번 바뀐 경우, 대체로 쓰는 사유는 그중 하나만 표시
- CronJob 이력은 control-plane 노드 한 곳의 hostPath 에 저장 → 여러 노드나 장기 보관이 필요하면 PVC / 외부 저장소로 변경
- Deployment 처럼 Pod 가 계속 교체되는 경우 재시작이 여러 Pod 이름으로 나뉨 → 워크로드 단위 집계

## AI 활용과 검증
코드 초안과 PromQL 작성에 AI를 활용했습니다. 메트릭 이름과 라벨은
[kube-state-metrics 문서](https://github.com/kubernetes/kube-state-metrics/blob/main/docs/metrics/workload/pod-metrics.md)와
Prometheus 에서 직접 조회한 결과로 대조했고, 동작은 위 재현 시나리오와 단위 테스트로 검증했습니다.
