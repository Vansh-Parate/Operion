# CrashLoopBackOff

## Symptoms
- Pod repeatedly restarts.
- Pod may show `CrashLoopBackOff`.
- Restart count increases.
- Container may exit with a non-zero exit code.

## Common Causes
- Missing environment variables.
- Invalid application configuration.
- Application startup exceptions.
- Missing secrets or ConfigMaps.
- Dependency initialization failure.

## Investigation
1. Check pod state and restart count.
2. Inspect the last container exit code and termination reason.
3. Inspect application logs.
4. Inspect deployment environment variables.
5. Check referenced ConfigMaps and Secrets.

## Example
If application logs show:

`CONFIG_ERROR: PAYMENT_PROVIDER_URL environment variable is required`

and the Deployment does not contain `PAYMENT_PROVIDER_URL`, the root cause is missing application configuration.

## Remediation
- Add the required configuration.
- Redeploy the application.
- Verify the pod becomes Ready.
- Confirm restart count stops increasing.