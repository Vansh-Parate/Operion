# Operion incident console

A portfolio-ready, evidence-grounded Kubernetes investigation demo built with Next.js App Router, TypeScript, Tailwind CSS, and Lucide icons. One responsive console presents four controlled incidents, evidence, diagnosis, runbook retrieval, human review, workflow trace, benchmark results, and system architecture.

## Development

Use Node.js 22 LTS (minimum 20.9).

```bash
cd /mnt/c/Dev/operion/frontend
npm ci
npm run dev
```

Open http://localhost:3000. From Windows, use `cd C:\Dev\operion\frontend`.

```bash
npm run lint
npm run typecheck
npm run build
npm start
```

No environment variables, backend, cluster, database, or authentication are required. Fonts are local system fonts; builds do not download fonts.

## Deploy to Vercel

1. Commit `frontend/`, including `package-lock.json`, and push to your GitHub repository.
2. In Vercel, choose **Add New → Project** and import the repository.
3. Set **Root Directory** to `frontend`.
4. Select **Next.js** as the framework and **22.x** as the Node.js version.
5. Leave install/build/output defaults (`npm install`, `npm run build`, Next.js output). No environment variables are needed.
6. Click **Deploy**. Use the resulting HTTPS project URL on your resume and portfolio. Subsequent pushes create deployments automatically.

## Demo Mode and evidence fidelity

All data is bundled in `lib/demo-incidents.ts`; the console never calls Kubernetes or pretends to poll a live backend. Evidence cards are representative excerpts of the supplied controlled scenarios, with omitted details explicitly marked. Severity is presentation metadata, not a measured benchmark metric. OOM restart count and Redis LLM confidence are unavailable in the supplied facts. Only supplied Top-1 retrieval results are displayed; no extra similarity scores or latency measurements are invented.

Approval advances a clearly labeled simulated workflow: **Approval Received → Remediation Planned → Awaiting Live Agent Integration**. Execution and recovery stay pending, and pod data never changes. Rejection blocks the workflow. Switching scenarios resets the local approval state. Redis abstains because the evidence does not identify a safe configuration mutation. No backend or Kubernetes resources were changed.

The benchmark reports four scenarios and 100% deterministic root-cause, LLM+RAG root-cause, and Top-1 retrieval accuracy. This is a controlled four-scenario benchmark, not a production generalization claim.

## Future FastAPI / LangGraph integration

- `lib/types.ts` defines the presentation contract: `Incident`, `Evidence`, `Diagnosis`, `RetrievedRunbook`, `RemediationPlan`, and `AgentStep`.
- `lib/api.ts` defines `IncidentSource`. Replace `listIncidents()` with a server-side FastAPI fetch and validate/map responses to `Incident[]`. Keep URLs and server secrets out of the browser. `app/page.tsx` consumes this adapter; the demo deploy does not depend on FastAPI.
- `components/incident-console.tsx` currently owns local approval state and derived demo steps. Replace these with backend approval/rejection endpoints and LangGraph state events (SSE or WebSocket) once implemented. Have the backend enforce approval scope, plan identity, safety checks, and execution authorization. Derive execution and verification status only from actual agent events.
- Add a visible Live Mode label only after that integration exists. Do not infer successful remediation from an approval response. Add pending/error states for real requests; `app/loading.tsx` already provides a route loading skeleton.

The Python backend is deliberately independent of this frontend.
