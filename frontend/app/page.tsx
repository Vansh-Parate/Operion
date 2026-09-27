import { IncidentConsole } from "@/components/incident-console";
import { incidentSource } from "@/lib/api";
export default async function Page() {
  const incidents = await incidentSource.listIncidents();
  return <IncidentConsole incidents={incidents} />;
}
