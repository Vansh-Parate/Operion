# Operion sandbox incidents

Run these commands from the repository root against a local Minikube cluster. The manifests change only the `payment-service` Deployment in `operion-sandbox`. They use the same image and container name as the existing sandbox, so the Kubernetes evidence collectors remain compatible.

Prepare the namespace and build the payment service image inside Minikube:

```sh
kubectl create namespace operion-sandbox --dry-run=client -o yaml | kubectl apply -f -
minikube image build -t operion-payment-service:v1 sandbox/payment-service
kubectl apply -f k8s/payment-service.yaml
kubectl -n operion-sandbox rollout restart deployment/payment-service
kubectl -n operion-sandbox rollout status deployment/payment-service
```

The rollout restart makes an existing Deployment use the freshly built local image. The healthy configuration supplies `PAYMENT_PROVIDER_URL` and probes `/health`. Each incident manifest replaces the same Deployment template. The rollout settings remove the previous pod before starting the incident pod, which keeps observations unambiguous.

## Activate and verify

Run one incident at a time. After applying a manifest, allow a few seconds for the new pod to start. `kubectl get pods` may briefly show a terminating pod during the switch.

**INC-001 — missing environment variable**

```sh
kubectl apply -f k8s/incidents/inc-001-missing-env.yaml
kubectl -n operion-sandbox get pods -l app=payment-service
kubectl -n operion-sandbox logs deployment/payment-service --previous --tail=50
```

The container fails at startup with `CONFIG_ERROR: PAYMENT_PROVIDER_URL environment variable is required` and enters `CrashLoopBackOff` after retries.

**INC-002 — OOMKilled**

```sh
kubectl apply -f k8s/incidents/inc-002-oom.yaml
kubectl -n operion-sandbox get pods -l app=payment-service
kubectl -n operion-sandbox describe pods -l app=payment-service
```

`INCIDENT_MODE=oom` starts a memory allocation loop under a 128Mi limit. After a restart, the container's last termination reason should be `OOMKilled`, usually with exit code `137`.

**INC-003 — readiness probe failure**

```sh
kubectl apply -f k8s/incidents/inc-003-readiness.yaml
kubectl -n operion-sandbox get pods -l app=payment-service
kubectl -n operion-sandbox describe pods -l app=payment-service
```

The probe requests `/health/invalid`, which returns HTTP 404. The container keeps running, the pod stays `0/1` Ready, and the pod events show `Readiness probe failed`.

**INC-004 — Redis dependency unavailable**

```sh
kubectl apply -f k8s/incidents/inc-004-redis-unavailable.yaml
kubectl -n operion-sandbox get pods -l app=payment-service
kubectl -n operion-sandbox logs deployment/payment-service --tail=50
```

At startup the service attempts a TCP connection to `127.0.0.1:6399`, where no Redis instance is configured in the pod. The application stays running and logs `REDIS_CONNECTION_ERROR` with the target and operating system connection error. `/health/dependency` repeats the check and returns HTTP 503 while Redis is unavailable.

## Reset to healthy

```sh
kubectl apply -f k8s/payment-service.yaml
kubectl -n operion-sandbox rollout status deployment/payment-service
kubectl -n operion-sandbox get pods -l app=payment-service
```

The healthy pod should show `1/1` Ready. Applying the healthy manifest also removes incident environment variables and the OOM memory limit through Kubernetes' normal apply reconciliation.
