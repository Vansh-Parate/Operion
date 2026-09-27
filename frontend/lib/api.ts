import { demoIncidents } from "./demo-incidents";
import type { Incident } from "./types";
/** Replace this adapter with validated FastAPI responses when live mode is implemented. */
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
// Live integration also needs server-side approval/execution endpoints and a
// LangGraph event stream. A browser approval alone must never authorize execution.
