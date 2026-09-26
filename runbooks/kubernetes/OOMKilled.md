# OOMKilled

## Symptoms
- Container is terminated by Kubernetes.
- Last termination reason is `OOMKilled`.
- Exit code is commonly `137`.
- Restart count may increase.
- Pod may eventually enter `CrashLoopBackOff`.

## Common Causes
- Application memory usage exceeds the configured container memory limit.
- Container memory limit is too low for the workload.
- Excessive in-memory caching or large allocations.
- A memory leak may be possible, but OOMKilled alone does not prove one.

## Investigation
1. Inspect pod container status.
2. Check `last_termination_reason`.
3. Check the last exit code.
4. Inspect Kubernetes memory requests and limits.
5. Inspect application memory behavior and metrics if available.

## Strong Evidence
- `last_termination_reason = OOMKilled`
- exit code `137`
- memory limit configured on the container

## Remediation
- Inspect application memory consumption.
- Determine whether memory usage is expected.
- Increase the memory limit if it is undersized.
- Fix abnormal memory growth if present.
- Redeploy and verify the container remains stable.