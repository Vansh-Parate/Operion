# Redis Dependency Unavailable

## Symptoms
- Application may remain running and Ready.
- Some application functionality may fail.
- Logs contain Redis connection errors.
- Connections may be refused or time out.

## Common Causes
- Redis process is unavailable.
- Incorrect Redis hostname.
- Incorrect Redis port.
- Network connectivity failure.
- Kubernetes Service configuration issue.

## Investigation
1. Inspect application logs.
2. Look for connection errors such as `REDIS_CONNECTION_ERROR`.
3. Check configured Redis hostname and port.
4. Verify the Redis service or process is running.
5. Test network connectivity from the application pod.
6. Check Kubernetes Service and DNS configuration if Redis runs in Kubernetes.

## Example
A log such as:

`REDIS_CONNECTION_ERROR: failed to connect to Redis at 127.0.0.1:6399: Connection refused`

indicates that the application cannot reach Redis at the configured address.

## Remediation
- Verify the Redis host and port.
- Verify Redis is running.
- Correct service discovery or networking configuration.
- Retry the dependency health check.
- Confirm application functionality is restored.