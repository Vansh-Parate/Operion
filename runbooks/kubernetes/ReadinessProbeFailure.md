# Readiness Probe Failure

## Symptoms
- Container is running.
- Pod remains `Ready=False`.
- Restart count may remain zero.
- Kubernetes events report `Unhealthy`.
- Readiness probe returns an error such as HTTP 404 or 500.

## Meaning
A readiness probe failure does not necessarily mean the application has crashed.

It means Kubernetes considers the pod unable to receive traffic.

## Common Causes
- Incorrect readiness probe path.
- Incorrect probe port.
- Health endpoint does not exist.
- Application is still initializing.
- Dependency required for readiness is unavailable.

## Investigation
1. Confirm container state is running.
2. Check whether the pod is Ready.
3. Inspect Kubernetes events for `Unhealthy`.
4. Inspect the exact readiness probe path and port.
5. Call the health endpoint directly.

## Example
If the readiness probe requests:

`/health/invalid`

and receives HTTP `404`, the readiness probe configuration is incorrect.

## Remediation
- Correct the readiness probe path or port.
- Verify the application health endpoint.
- Redeploy the workload.
- Confirm the pod becomes Ready.