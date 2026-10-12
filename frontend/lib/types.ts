export type EvidenceId = string;

export interface Evidence {
  id: EvidenceId;
  title: string;
  source: string;
  content: string;
  highlight: string;
  category?: string;
  severity?: string | null;
  reference?: string | null;
}

export interface RetrievedRunbook {
  rank: number;
  filename: string;
  similarity: number;
}

export interface KnowledgeDocument {
  sourceType: "runbook" | "kubernetes_docs";
  title: string;
  url?: string;
  section?: string;
  score?: number | null;
  availability: string;
  excerpt?: string;
  content?: string;
}

export interface Diagnosis {
  rootCause: string;
  confidence: number | null;
  summary: string;
  supportingEvidence: EvidenceId[];
  recommendedActions: string[];
}

export type RiskLevel = "low" | "medium" | "high";

export interface ResourceTarget {
  kind: string;
  name: string;
  namespace: string;
  container?: string;
}

export interface RemediationPlan {
  action: string;
  reason: string;
  evidence: EvidenceId[];
  safety: string;
  abstained: boolean;
  operation?: string;
  target?: ResourceTarget;
  riskLevel?: RiskLevel;
  requiresApproval?: boolean;
  parameters?: Record<string, unknown>;
  executableStatus?: "supported_and_allowed" | "known_but_not_executable_yet" | "denied" | "abstain";
  executableNow?: boolean;
}

export type AgentStepStatus = "complete" | "current" | "pending" | "blocked";

export interface AgentStep {
  id: string;
  label: string;
  status: AgentStepStatus;
  detail: string;
}

export interface AgentTraceStep {
  id: string;
  phase: "investigation" | "retrieval" | "diagnosis" | "remediation" | "safety_gate" | "human_approval" | "execution" | "verification";
  label: string;
  tool?: string;
  toolObservation?: string;
  readOnly?: boolean;
  status: AgentStepStatus;
  detail: string;
  timestamp?: string;
  evidenceProduced?: EvidenceId[];
}

export interface VerificationState {
  proposedState: string;
  executedState: "not_executed" | "executed" | "pending_live_runtime";
  verifiedState: "unverified" | "verified" | "independent_verification_pending";
  verificationStrategy: string[];
  summary: string;
}

export interface Incident {
  id: string;
  title: string;
  service: string;
  namespace: string;
  severity: "Critical" | "High";
  podStatus: "CrashLoopBackOff" | "Running" | string;
  ready: boolean;
  restarts: number | null;
  evidence: Evidence[];
  diagnosis: Diagnosis;
  runbooks: RetrievedRunbook[];
  remediation: RemediationPlan;
  knowledgeDocs?: KnowledgeDocument[];
  trace?: AgentTraceStep[];
  verification?: VerificationState;
}

export interface InvestigationTargetInput {
  namespace: string;
  service: string;
  deployment?: string;
  labelSelector?: string;
  requireService?: boolean;
  approve?: boolean;
}

export interface LiveInvestigationState {
  incident: Incident;
  health?: {
    pod_health: string;
    service_health: string;
    reason: string;
  } | null;
  diagnosis?: Diagnosis | null;
  knowledgeDocs: KnowledgeDocument[];
  knowledgeRetrievalStatus: "completed" | "empty" | "failed" | "not_started" | string;
  remediationProposal?: {
    abstain: boolean;
    abstainReason?: string | null;
    abstainReasonCode?: string | null;
    generationStatus?: string | null;
    fallbackUsed?: boolean;
    recommendedCandidateIndex?: number | null;
    candidates: {
      operation: string;
      target?: ResourceTarget | null;
      reason: string;
      evidenceIds: string[];
      knowledgeIds: string[];
      riskLevel: RiskLevel;
      confidence: number;
      verificationStrategy: string[];
    }[];
  } | null;
  proposalDecisions: {
    status: string;
    riskLevel: RiskLevel;
    reason: string;
    executableNow: boolean;
  }[];
  remediationPlan?: RemediationPlan | null;
  policyDecision?: {
    allowed: boolean;
    reason: string;
    normalizedParameters: Record<string, unknown>;
  } | null;
  approved: boolean;
  executionResult?: Record<string, unknown> | null;
  verificationResult?: Record<string, unknown> | null;
  resolved: boolean;
  trace: AgentTraceStep[];
  verification: VerificationState;
  hypotheses: {
    cause: string;
    confidence: number;
    supportingEvidenceIds: string[];
    missingEvidence: string[];
  }[];
  investigationComplete: boolean;
  investigationBlockedReason?: string | null;
}

export interface InvestigationJob {
  job_id: string;
  status: "queued" | "running" | "completed" | "failed";
  progress_step?: string;
  target: {
    namespace: string;
    service: string;
    deployment?: string | null;
    label_selector?: string | null;
    require_service?: boolean;
    approve?: boolean;
  };
  created_at: string;
  completed_at?: string | null;
  error?: string | null;
  state?: LiveInvestigationState | null;
  parent_job_id?: string | null;
}

export interface InvestigationSummary {
  job_id: string;
  status: "queued" | "running" | "completed" | "failed";
  progress_step?: string;
  target: {
    namespace: string;
    service: string;
  };
  created_at: string;
  completed_at?: string | null;
  error?: string | null;
  has_result: boolean;
}
