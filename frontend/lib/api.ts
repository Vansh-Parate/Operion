import { demoIncidents } from "./demo-incidents";
import type {
  Incident,
  InvestigationJob,
  InvestigationSummary,
  InvestigationTargetInput,
} from "./types";

const API_BASE_URL =
  process.env.NEXT_PUBLIC_OPERION_API_URL || "http://127.0.0.1:8000";

export class BackendConnectionError extends Error {
  constructor(message: string, public readonly endpoint?: string) {
    super(message);
    this.name = "BackendConnectionError";
  }
}

/** Check backend health */
export async function checkBackendHealth(): Promise<{
  ok: boolean;
  status: string;
  error?: string;
}> {
  try {
    const res = await fetch(`${API_BASE_URL}/health`, {
      method: "GET",
      cache: "no-store",
    });
    if (!res.ok) {
      return {
        ok: false,
        status: `HTTP ${res.status}`,
        error: `Backend responded with HTTP ${res.status}`,
      };
    }
    const data = await res.json();
    return { ok: true, status: data.status || "healthy" };
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err);
    return {
      ok: false,
      status: "unreachable",
      error: `Cannot reach Operion backend at ${API_BASE_URL}: ${msg}`,
    };
  }
}

/** Submit a real investigation target to the backend */
export async function startInvestigation(
  target: InvestigationTargetInput,
): Promise<{ job_id: string; status: string }> {
  try {
    const payload = {
      namespace: target.namespace,
      service: target.service,
      deployment: target.deployment || null,
      label_selector: target.labelSelector || null,
      require_service: !!target.requireService,
      approve: !!target.approve,
    };
    const res = await fetch(`${API_BASE_URL}/api/investigations`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const errBody = await res.text();
      throw new Error(`Backend error (${res.status}): ${errBody}`);
    }
    return await res.json();
  } catch (err: unknown) {
    if (err instanceof Error && err.name === "TypeError") {
      throw new BackendConnectionError(
        `Failed to connect to Operion backend at ${API_BASE_URL}. Ensure the FastAPI server is running with: uvicorn main:app --port 8000`,
        `${API_BASE_URL}/api/investigations`,
      );
    }
    throw err;
  }
}

/** Poll status of an ongoing investigation job */
export async function getInvestigation(
  jobId: string,
): Promise<InvestigationJob> {
  try {
    const res = await fetch(`${API_BASE_URL}/api/investigations/${jobId}`, {
      method: "GET",
      cache: "no-store",
    });
    if (!res.ok) {
      const errBody = await res.text();
      throw new Error(`Failed to fetch job (${res.status}): ${errBody}`);
    }
    return await res.json();
  } catch (err: unknown) {
    if (err instanceof Error && err.name === "TypeError") {
      throw new BackendConnectionError(
        `Failed to connect to Operion backend at ${API_BASE_URL} while polling job ${jobId}.`,
        `${API_BASE_URL}/api/investigations/${jobId}`,
      );
    }
    throw err;
  }
}

/** Request human approval execution on an investigated target */
export async function approveInvestigation(
  jobId: string,
  approve: boolean = true,
): Promise<{ job_id: string; parent_job_id: string; status: string }> {
  try {
    const res = await fetch(
      `${API_BASE_URL}/api/investigations/${jobId}/approve`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ approve }),
      },
    );
    if (!res.ok) {
      const errBody = await res.text();
      throw new Error(`Failed to approve job (${res.status}): ${errBody}`);
    }
    return await res.json();
  } catch (err: unknown) {
    if (err instanceof Error && err.name === "TypeError") {
      throw new BackendConnectionError(
        `Failed to connect to Operion backend at ${API_BASE_URL} while requesting approval.`,
        `${API_BASE_URL}/api/investigations/${jobId}/approve`,
      );
    }
    throw err;
  }
}

/** List past investigation jobs */
export async function listInvestigations(): Promise<InvestigationSummary[]> {
  try {
    const res = await fetch(`${API_BASE_URL}/api/investigations`, {
      method: "GET",
      cache: "no-store",
    });
    if (!res.ok) {
      throw new Error(`Backend error (${res.status})`);
    }
    return await res.json();
  } catch (err: unknown) {
    if (err instanceof Error && err.name === "TypeError") {
      throw new BackendConnectionError(
        `Failed to connect to Operion backend at ${API_BASE_URL}.`,
        `${API_BASE_URL}/api/investigations`,
      );
    }
    throw err;
  }
}

/** Preserved adapter for backward compatibility */
export interface IncidentSource {
  mode: "demo" | "live";
  listIncidents(): Promise<Incident[]>;
}

export const incidentSource: IncidentSource = {
  mode: "demo",
  async listIncidents() {
    return demoIncidents;
  },
};
