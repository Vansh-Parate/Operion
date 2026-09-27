import type { Evidence, Incident } from "./types";
const evidence = (
  status: string,
  events: string,
  logs: string,
  config: string,
  highlights: string[],
): Evidence[] => [
  {
    id: "E1",
    title: "Pod status",
    source: "Kubernetes · pod snapshot",
    content: status,
    highlight: highlights[0],
  },
  {
    id: "E2",
    title: "Kubernetes events",
    source: "Kubernetes · event excerpt",
    content: events,
    highlight: highlights[1],
  },
  {
    id: "E3",
    title: "Application logs",
    source: "Container · log excerpt",
    content: logs,
    highlight: highlights[2],
  },
  {
    id: "E4",
    title: "Deployment configuration",
    source: "Deployment · relevant YAML",
    content: config,
    highlight: highlights[3],
  },
];
const common = { service: "payment-service", namespace: "operion-sandbox" };
export const demoIncidents: Incident[] = [
  {
    ...common,
    id: "INC-001",
    title: "Missing Environment Variable",
    severity: "Critical",
    podStatus: "CrashLoopBackOff",
    ready: false,
    restarts: 11,
    evidence: evidence(
      "state: waiting\nreason: CrashLoopBackOff\nready: false\nrestartCount: 11\nlastTermination.exitCode: 1",
      "Warning  BackOff\nBack-off restarting failed container payment-service",
      "CONFIG_ERROR: PAYMENT_PROVIDER_URL environment variable is required",
      "containers:\n  - name: payment-service\n    image: operion-payment-service:v1\n    # PAYMENT_PROVIDER_URL is absent from env",
      [
        "Startup failure · exit 1",
        "Container restart back-off",
        "Required environment variable missing",
        "PAYMENT_PROVIDER_URL absent",
      ],
    ),
    diagnosis: {
      rootCause: "missing_environment_variable",
      confidence: 1,
      summary:
        "The application exits during startup because PAYMENT_PROVIDER_URL is required but absent from the Deployment. Repeated startup failures cause CrashLoopBackOff.",
      supportingEvidence: ["E1", "E3", "E4"],
      recommendedActions: [
        "Configure PAYMENT_PROVIDER_URL with the intended provider endpoint.",
        "Redeploy and verify startup logs and pod readiness.",
      ],
    },
    runbooks: [
      { rank: 1, filename: "CrashLoopBackOff.md", similarity: 0.7935 },
    ],
    remediation: {
      action: "Configure PAYMENT_PROVIDER_URL and redeploy",
      reason:
        "The startup error names the missing variable; the Deployment confirms its absence.",
      evidence: ["E3", "E4"],
      safety:
        "Human approval required · endpoint must be supplied by an operator",
      abstained: false,
    },
  },
  {
    ...common,
    id: "INC-002",
    title: "Container OOMKilled",
    severity: "Critical",
    podStatus: "CrashLoopBackOff",
    ready: false,
    restarts: null,
    evidence: evidence(
      "state: waiting\nreason: CrashLoopBackOff\nlastTermination.reason: OOMKilled\nlastTermination.exitCode: 137",
      "Warning  BackOff\nBack-off restarting failed container payment-service",
      "# Controlled scenario: memory allocation loop.\n# Termination reason is established by pod status (E1), not logs.",
      "resources:\n  requests:\n    memory: 64Mi\n  limits:\n    memory: 128Mi",
      [
        "OOMKilled · exit 137",
        "Container restart back-off",
        "Memory allocation scenario",
        "64Mi request / 128Mi limit",
      ],
    ),
    diagnosis: {
      rootCause: "container_memory_limit_exceeded",
      confidence: 0.95,
      summary:
        "The container exceeded its 128Mi memory limit and was terminated with OOMKilled (exit 137). The termination reason and configured limit support a memory capacity failure.",
      supportingEvidence: ["E1", "E4"],
      recommendedActions: [
        "Propose a bounded increase to the container memory limit.",
        "Review memory consumption and verify stability after an approved rollout.",
      ],
    },
    runbooks: [{ rank: 1, filename: "OOMKilled.md", similarity: 0.782 }],
    remediation: {
      action: "Increase container memory limit",
      reason:
        "Container terminated with OOMKilled and exit code 137 under a 128Mi limit.",
      evidence: ["E1", "E4"],
      safety:
        "Human approval required · bounded limit to be reviewed before execution",
      abstained: false,
    },
  },
  {
    ...common,
    id: "INC-003",
    title: "Readiness Probe Failure",
    severity: "High",
    podStatus: "Running",
    ready: false,
    restarts: 0,
    evidence: evidence(
      "state: running\nready: false\nrestartCount: 0",
      "Warning  Unhealthy\nReadiness probe failed: HTTP probe failed with statuscode: 404",
      "GET /health/invalid → HTTP 404",
      "readinessProbe:\n  httpGet:\n    path: /health/invalid\n    port: 8000",
      [
        "Running · not ready",
        "Readiness probe failed · HTTP 404",
        "Invalid health path returns 404",
        "Probe targets /health/invalid",
      ],
    ),
    diagnosis: {
      rootCause: "readiness_probe_failure",
      confidence: 0.9,
      summary:
        "The container is running without restarts, but its readiness probe requests /health/invalid and receives HTTP 404. The incorrect probe path prevents the pod from becoming ready.",
      supportingEvidence: ["E1", "E2", "E3", "E4"],
      recommendedActions: [
        "Correct the readiness probe configuration to use the intended health endpoint.",
        "Verify successful probe responses and Ready status after rollout.",
      ],
    },
    runbooks: [
      { rank: 1, filename: "ReadinessProbeFailure.md", similarity: 0.7865 },
    ],
    remediation: {
      action: "Correct the readiness probe configuration",
      reason:
        "The configured /health/invalid endpoint returns HTTP 404 while the container remains running.",
      evidence: ["E2", "E3", "E4"],
      safety: "Human approval required · verify the intended health endpoint",
      abstained: false,
    },
  },
  {
    ...common,
    id: "INC-004",
    title: "Redis Dependency Unavailable",
    severity: "High",
    podStatus: "Running",
    ready: true,
    restarts: 0,
    evidence: evidence(
      "state: running\nready: true\nrestartCount: 0",
      "# No decisive Kubernetes event is supplied for this scenario.\n# Application evidence identifies the dependency failure.",
      "REDIS_CONNECTION_ERROR\nConnection refused at 127.0.0.1:6399",
      "# Observed Redis connection target:\n# host: 127.0.0.1\n# port: 6399\n# Evidence does not establish the intended Redis topology.",
      [
        "Running · Ready true",
        "No decisive event evidence",
        "Redis connection refused",
        "Target 127.0.0.1:6399",
      ],
    ),
    diagnosis: {
      rootCause: "redis_dependency_unavailable",
      confidence: null,
      summary:
        "The application cannot connect to Redis at 127.0.0.1:6399, although the container is running and ready. The evidence establishes dependency unavailability, but does not establish whether Redis, connectivity, host configuration, or the port is the underlying failure.",
      supportingEvidence: ["E1", "E3"],
      recommendedActions: [
        "Check Redis availability, expected host and port, and network connectivity.",
        "Collect additional evidence before proposing a configuration change.",
      ],
    },
    runbooks: [
      { rank: 1, filename: "RedisUnavailable.md", similarity: 0.6728 },
    ],
    remediation: {
      action: "Automatic remediation abstained",
      reason:
        "Available evidence does not justify a specific configuration mutation. Investigate Redis availability, connectivity, host configuration, and port first.",
      evidence: ["E1", "E3"],
      safety: "Safety gate closed · additional evidence required",
      abstained: true,
    },
  },
];
