# Pod Restart Inspector

![ci](https://github.com/hyojinny-k/pod-restart-inspector/actions/workflows/ci.yml/badge.svg)

Prometheus API에서 Kubernetes Pod/Container 재시작 메트릭을 조회해
**최근 N시간 동안 재시작이 잦은 컨테이너**를 종료 사유(OOMKilled / Error 등)와 함께 요약하는 CLI 도구입니다.

## Why
<!-- TODO: 본인 말로 2~3줄 다듬기 -->
OpsRamp로 K8s 클러스터를 관측하면서, 반복적으로 죽는 컨테이너를 찾으려면 대시보드에서 Pod별 Restart 그래프를 하나씩 열어봐야 했습니다.
`kubectl get pods`의 RESTARTS는 Pod 생성 이후 누적값이라 "지금 문제인지"를 구분하기 어렵습니다.
그래서 **기간 기준으로, 왜 죽었는지까지** 한 번에 확인하고 장애 기록에 바로 붙일 수 있는 도구를 만들었습니다.

## Features
- Prometheus HTTP API 조회 (외부 라이브러리 없이 표준 라이브러리만 사용)
- `increase(kube_pod_container_status_restarts_total[window])` 로 **기간 내 재시작 횟수** 집계
- `kube_pod_container_status_last_terminated_reason` 결합 → OOMKilled / Error 구분
- 임계치 필터, 재시작 횟수 내림차순 정렬, `[Summary]` 요약
- 출력 형식: `table` / `json` / `markdown`
- 종료 코드: `0` 정상 · `1` 임계치 초과 · `2` 조회 실패 → cron / CI 알림 조건으로 사용 가능
- 시스템 네임스페이스(kube-system, monitoring 등) 기본 제외, `--namespace` / `--exclude-ns` 로 조정

## Example
<!-- TODO: 실제 클러스터 실행 결과로 교체 (sample_output.txt, screenshots/demo.png) -->
```
$ python3 main.py --prometheus http://localhost:9090 --window 30m --threshold 2
NAMESPACE  POD       CONTAINER  RESTARTS  LAST_REASON
default    crasher   crasher    7         Error
default    oom-test  app        4         OOMKilled

[Summary] window=30m, threshold=2
2 container(s) exceeded restart threshold.
Highest: default/crasher/crasher = 7 (last reason: Error)
OOMKilled: 1 container(s) -> memory limit 확인 필요
```

## Usage
```bash
# 1) Prometheus 접근 (kube-prometheus-stack 기준)
kubectl port-forward -n monitoring svc/kps-kube-prometheus-stack-prometheus 9090:9090 &

# 2) 실행
python3 main.py --window 1h --threshold 3
python3 main.py --namespace default --output json
python3 main.py --output markdown > report.md
python3 main.py --help
```

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
```
<!-- TODO: 결과 스크린샷 첨부 후 체크 -->
- [ ] crasher → `Error` 표시
- [ ] oom-test → `OOMKilled` 표시 (`kubectl describe` 의 Last State 와 일치)
- [ ] Prometheus 연결 실패 시 exit code 2
- 단위 테스트: `pytest -q` (GitHub Actions 에서 push 마다 실행)

## Design Notes
- **누적 카운터 대신 `increase()`**: 오래전에 많이 죽은 Pod가 상위에 뜨는 문제 방지
- `increase()` 는 외삽 때문에 `2.9998` 같은 소수가 나와 반올림 처리
- kube-prometheus-stack 이 설치하는 kube-state-metrics(monitoring)는 기존 OpsRamp 수집용 kube-state-metrics(kube-system)와 분리해 기존 관측 구성에 영향을 주지 않도록 함

## Limitations / Next
- Pod가 삭제되면 메트릭도 사라져 이력이 남지 않음 → 결과 주기적 저장
- 재시작 직전 로그 함께 출력 (`kubectl logs --previous`)
- Slack/Webhook 알림, 컨테이너 이미지로 패키징해 CronJob 으로 실행

## AI 활용과 검증
<!-- TODO: 본인 경험대로 수정 -->
코드 초안과 PromQL 작성에 AI를 활용했습니다. 메트릭 이름과 라벨은
[kube-state-metrics 문서](https://github.com/kubernetes/kube-state-metrics/blob/main/docs/metrics/workload/pod-metrics.md)와
Prometheus 에서 직접 조회한 결과로 대조했고, 동작은 위 재현 시나리오와 단위 테스트로 검증했습니다.
