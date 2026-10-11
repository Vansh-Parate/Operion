# Operion

Operion is a Kubernetes incident-investigation sandbox. It combines live, read-only cluster observations with an LLM-driven investigation flow, deterministic evidence validation, and a narrowly allowlisted remediation path. Its safety goal is to abstain when evidence is incomplete or contradictory instead of turning a plausible hypothesis into an unsafe mutation.

## What it solves

Operion investigates Kubernetes workload failures such as startup configuration errors, OOM kills, readiness failures, dependency failures, and Service routing problems. It collects target-scoped evidence, asks Ollama for hypotheses and diagnosis, checks mandatory evidence deterministically, and separates:

- evidence-grounded recommendations, which are informational and non-executable; from
- the one supported mutation path, which requires deterministic policy approval, explicit operator approval, execution, and observed recovery verification.

Historical restarts, logs, and LLM claims cannot authorize a change by themselves. Current evidence must support both the diagnosis and its parameters.

## Architecture and workflow

~~~mermaid
flowchart LR
    CLI[app.agent.run] --> G[LangGraph state graph]
    G --> C[Collect Kubernetes evidence]
    C --> I[LLM investigation + bounded read-only tools]
    I --> CG[Deterministic completeness gate]
    CG -->|missing evidence, max 5 calls| I
    CG --> K[RAG knowledge]
    K --> Q[Qdrant runbooks]
    K --> D[Curated local Kubernetes docs]
    K --> DX[Ollama diagnosis context]
    DX --> V[Deterministic diagnosis validation]
    V --> R[Remediation proposal validation]
    R --> P[Deterministic policy]
    P -->|supported mutation only| A[Operator approval]
    P -->|recommendation-only or abstain| E[End]
    A -->|--approve| X[Allowlisted executor]
    A -->|not approved| E
    X --> Y[Observed recovery verifier]
    Y --> E
~~~

End-to-end flow:

1. The CLI creates a target from namespace, workload/service name, Deployment, and optional label selector.
2. Initial collection gathers Pod status, Deployment configuration, logs, events, and the available Service view.
3. LangGraph routes state through investigation, completeness, knowledge retrieval, diagnosis, proposal validation, policy, approval, execution, and verification.
4. Ollama proposes hypotheses and may request additional read-only observations. It does not authorize tools or mutations.
5. The completeness gate requires fresh, target-scoped mandatory evidence. Missing observations are requested through existing read-only Kubernetes tools. Investigation is bounded at five tool calls.
6. RAG supplies operational context. Runbooks come from Qdrant when the embedding model is ready; curated Kubernetes documentation remains an offline fallback. Knowledge is not live incident evidence.
7. Diagnosis and remediation proposals are validated against observed evidence.
8. Deterministic policy validates the operation, target, parameters, allowlists, and approval boundary.
9. The executor can perform only the existing allowlisted payment environment-variable patch.
10. The verifier observes Pod recovery; it does not trust an executor return value.

### Component roles

- **LangGraph** owns stateful workflow and conditional routing.
- **Ollama** supplies bounded investigation and diagnosis proposals. It is not a security boundary.
- **RAG** adds operational context, never proof of a live incident.
- **Qdrant** stores and searches operion_runbooks using sentence-transformers/all-MiniLM-L6-v2.
- **Curated Kubernetes docs** provide a local fallback without Qdrant.
- **Deterministic policy** validates evidence, targets, parameters, and supported operations.
- **Approval** requires --approve before the supported mutation can execute.
- **Execution** is limited to PAYMENT_PROVIDER_URL on Deployment/payment-service in operon-sandbox.
- **Verification** polls observed payment-service Pod recovery.

## Supported scenarios

Payment-service incident manifests cover:

- INC-001: missing PAYMENT_PROVIDER_URL, producing CONFIG_ERROR and CrashLoopBackOff.
- INC-002: OOMKilled under a constrained memory limit.
- INC-003: readiness probe failure caused by an invalid health path.
- INC-004: unavailable Redis dependency at 127.0.0.1:6399.

The separate selector-demo scenario covers a Service selector mismatch: Pods are labeled app=selector-demo, the Service observes app=wrong-label, and ready endpoints are absent. Operion can recommend patch_service_selector with {"app":"selector-demo"} grounded in observed evidence. It is not executable through the current executor.

A healthy payment-service with a real Service 404 is classified as workload-only when no Service requirement is established. Healthy Pod and Deployment evidence can produce no_active_incident; historical restarts alone do not create an incident.

## Setup

Use WSL from the repository root:

~~~sh
cd /mnt/c/Dev/operion
source .venv-wsl/bin/activate
cd backend
~~~

The application expects a local Kubernetes context and Ollama for live LLM calls. A Minikube sandbox can be prepared with:

~~~sh
kubectl create namespace operon-sandbox --dry-run=client -o yaml | kubectl apply -f -
minikube image build -t operion-payment-service:v1 sandbox/payment-service
kubectl apply -f k8s/payment-service.yaml
kubectl -n operion-sandbox rollout restart deployment/payment-service
kubectl -n operion-sandbox rollout status deployment/payment-service
~~~

Reset to healthy with:

~~~sh
kubectl apply -f k8s/payment-service.yaml
kubectl -n operon-sandbox rollout status deployment/payment-service
~~~

Run one incident manifest at a time. The existing incident manifests also provide direct kubectl get, describe, and logs checks for each scenario.

## Tests

From backend:

~~~sh
PYTHONPATH=. ../.venv-wsl/bin/python -m pytest -q
~~~

The current backend suite passes 243 tests. Tests mock Kubernetes, Ollama, model loading, and Qdrant where appropriate; they do not require a model download or live Qdrant.

## RAG readiness and current limitation

Model provisioning is explicit. Normal incident handling is local-cache-only and will not unexpectedly download from Hugging Face.

~~~sh
PYTHONPATH=. ../.venv-wsl/bin/python -u -m app.rag.readiness
~~~

If provisioning is needed, explicitly opt in:

~~~sh
PYTHONPATH=. ../.venv-wsl/bin/python -u -m app.rag.readiness --allow-download
~~~

Then rerun the cache-only check. The command validates model loading, a finite non-empty test embedding, and its dimension.

In the current local environment, the Hugging Face snapshot appears complete at /home/radeon/.cache/huggingface/hub, including model.safetensors, but SentenceTransformer/PyTorch initialization exceeds the readiness timeout. Live runbook retrieval has therefore not been verified: it fails before Qdrant search at the embedding stage. Retrieval degrades safely to the curated Kubernetes documentation corpus. Do not describe runbook retrieval as working until readiness succeeds and a Qdrant-backed query returns results.

## Safe 3–5 minute demo

These commands are read-only from Operion's perspective. Do not add --approve.

### 1. Healthy payment-service: no action

~~~sh
PYTHONPATH=. timeout 180 ../.venv-wsl/bin/python -u -m app.agent.run \
  --namespace operon-sandbox \
  --service payment-service \
  --deployment payment-service \
  --label-selector app=payment-service
~~~

Expected verified behavior: a real Service 404 classifies the target as workload-only; current healthy Pod and Deployment evidence leads to no_active_incident; no remediation is proposed or executed, and the graph cannot reach execution.

### 2. selector-demo: grounded recommendation only

~~~sh
PYTHONPATH=. timeout 180 ../.venv-wsl/bin/python -u -m app.agent.run \
  --namespace operon-sandbox \
  --service selector-demo \
  --deployment selector-demo \
  --label-selector app=selector-demo
~~~

Expected verified behavior: the investigation collects the Service selector, stable Pod labels, and zero ready endpoints. The recommendation is:

~~~text
operation: patch_service_selector
target: Service/selector-demo
selector: {"app": "selector-demo"}
risk: medium
executable_now: false
~~~

It must cite observed evidence IDs, must not include pod-template-hash, and must not modify Kubernetes.

### 3. Approved payment environment-variable path: tests/mocks only

Do not break the healthy Deployment to demonstrate this path. Run the existing mocked policy, executor, and graph tests:

~~~sh
PYTHONPATH=. ../.venv-wsl/bin/python -m pytest -q \
  tests/test_agent_graph.py \
  tests/test_agent_policy.py \
  tests/test_generic_remediation_planning.py
~~~

These tests verify that direct current evidence for the missing PAYMENT_PROVIDER_URL is required, the value is allowlisted and normalized, approval is required, unsupported operations are rejected, and recovery is checked from observed Pod state. They do not perform a live Kubernetes mutation.

## Remediation boundary

--approve is not a general-purpose execution switch. It only allows the existing policy-approved payment environment-variable operation to proceed after deterministic checks pass. Selector repairs, generic proposals, unsupported operations, stale evidence, contradictory evidence, and model-only claims remain recommendations or safe abstentions. No Kubernetes write tools are added by RAG or documentation paths.

For a real approved mutation demonstration, use a disposable incident environment and the existing INC-001 flow only after reviewing the target and approval boundary. The safe mock demo above is the repeatable interview path.
