export type EvidenceId = "E1" | "E2" | "E3" | "E4";
export interface Evidence {
  id: EvidenceId;
  title: string;
  source: string;
  content: string;
  highlight: string;
}
export interface RetrievedRunbook {
  rank: number;
  filename: string;
  similarity: number;
}
export interface Diagnosis {
  rootCause: string;
  confidence: number | null;
  summary: string;
  supportingEvidence: EvidenceId[];
  recommendedActions: string[];
}
export interface RemediationPlan {
  action: string;
  reason: string;
  evidence: EvidenceId[];
  safety: string;
  abstained: boolean;
}
export type AgentStepStatus = "complete" | "current" | "pending" | "blocked";
export interface AgentStep {
  id: string;
  label: string;
  status: AgentStepStatus;
  detail: string;
}
export interface Incident {
  id: string;
  title: string;
  service: string;
  namespace: string;
  severity: "Critical" | "High";
  podStatus: "CrashLoopBackOff" | "Running";
  ready: boolean;
  restarts: number | null;
  evidence: Evidence[];
  diagnosis: Diagnosis;
  runbooks: RetrievedRunbook[];
  remediation: RemediationPlan;
}
