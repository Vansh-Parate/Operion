"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Activity,
  AlertCircle,
  AlertTriangle,
  ArrowRight,
  Box,
  Check,
  CheckCheck,
  ChevronDown,
  Circle,
  Clock,
  Copy,
  Database,
  ExternalLink,
  FileCode2,
  FileText,
  GitBranch,
  History,
  Layers3,
  Loader2,
  Lock,
  LockKeyhole,
  Play,
  RotateCcw,
  Search,
  Server,
  ShieldAlert,
  ShieldCheck,
  Terminal,
  Wifi,
  WifiOff,
  X,
} from "lucide-react";
import type {
  EvidenceId,
  Incident,
  InvestigationJob,
  InvestigationSummary,
  InvestigationTargetInput,
  LiveInvestigationState,
  RiskLevel,
} from "@/lib/types";
import {
  approveInvestigation,
  checkBackendHealth,
  getInvestigation,
  listInvestigations,
  startInvestigation,
} from "@/lib/api";

type NavSection = "overview" | "incidents" | "investigation" | "knowledge" | "safety";

function Badge({
  children,
  tone = "neutral",
  className = "",
}: {
  children: React.ReactNode;
  tone?: "neutral" | "mint" | "rose" | "amber" | "cyan";
  className?: string;
}) {
  return (
    <span className={`badge ${tone !== "neutral" ? tone : ""} ${className}`.trim()}>
      {children}
    </span>
  );
}

function RiskBadge({ level }: { level?: RiskLevel }) {
  if (!level) return null;
  const toneMap: Record<RiskLevel, "mint" | "amber" | "rose"> = {
    low: "mint",
    medium: "amber",
    high: "rose",
  };
  return (
    <Badge tone={toneMap[level]}>
      {level.toUpperCase()} RISK
    </Badge>
  );
}

export function IncidentConsole({ incidents: demoIncidents }: { incidents: Incident[] }) {
  // Mode selection: "live" vs "demo"
  const [consoleMode, setConsoleMode] = useState<"live" | "demo">("live");

  // Navigation
  const [activeSection, setActiveSection] = useState<NavSection>("overview");

  // Backend Health
  const [backendHealth, setBackendHealth] = useState<{ ok: boolean; status: string; checked: boolean }>({
    ok: false,
    status: "checking",
    checked: false,
  });

  // Target Input for Live Mode
  const [targetInput, setTargetInput] = useState<InvestigationTargetInput>({
    namespace: "operion-sandbox",
    service: "payment-service",
    deployment: "payment-service",
    labelSelector: "app=payment-service",
    requireService: false,
  });

  // Active Live Investigation Job
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [activeJob, setActiveJob] = useState<InvestigationJob | null>(null);
  const [isStartingJob, setIsStartingJob] = useState(false);
  const [isApproving, setIsApproving] = useState(false);
  const [liveError, setLiveError] = useState<string | null>(null);

  // Investigation History
  const [jobHistory, setJobHistory] = useState<InvestigationSummary[]>([]);

  // Demo Mode state
  const [selectedDemoId, setSelectedDemoId] = useState<string>("INC-002");
  const [demoApproval, setDemoApproval] = useState<"review" | "received" | "planned" | "awaiting" | "rejected">("review");
  const [demoSearchQuery, setDemoSearchQuery] = useState("");
  const [demoSeverityFilter, setDemoSeverityFilter] = useState<"ALL" | "Critical" | "High">("ALL");

  // Expandable UI States
  const [expandedEvidence, setExpandedEvidence] = useState<Record<string, boolean>>({
    E1: true,
    E3: true,
  });
  const [expandedTrace, setExpandedTrace] = useState<Record<string, boolean>>({
    "step-1": true,
    "step-3": true,
  });
  const [copiedId, setCopiedId] = useState<string | null>(null);

  // Check backend health periodically or on mount
  const verifyBackend = useCallback(async () => {
    const res = await checkBackendHealth();
    setBackendHealth({ ok: res.ok, status: res.status, checked: true });
    return res.ok;
  }, []);

  const refreshHistory = useCallback(async () => {
    try {
      const items = await listInvestigations();
      setJobHistory(items);
    } catch {
      // Ignored if offline
    }
  }, []);

  useEffect(() => {
    let ignore = false;
    checkBackendHealth().then((res) => {
      if (!ignore) {
        setBackendHealth({ ok: res.ok, status: res.status, checked: true });
        if (res.ok) {
          listInvestigations().then((items) => {
            if (!ignore) setJobHistory(items);
          }).catch(() => {});
        }
      }
    });
    return () => {
      ignore = true;
    };
  }, []);

  // Demo approval progression timer
  useEffect(() => {
    if (demoApproval !== "received" && demoApproval !== "planned") return;
    const timer = setTimeout(() => {
      setDemoApproval((prev) => (prev === "received" ? "planned" : "awaiting"));
    }, 1200);
    return () => clearTimeout(timer);
  }, [demoApproval]);

  // Poll active live job
  useEffect(() => {
    if (!activeJobId) return;

    let isMounted = true;
    const pollInterval = setInterval(async () => {
      try {
        const job = await getInvestigation(activeJobId);
        if (!isMounted) return;
        setActiveJob(job);

        if (job.status === "completed" || job.status === "failed") {
          clearInterval(pollInterval);
          refreshHistory();
        }
      } catch (err: unknown) {
        if (!isMounted) return;
        setLiveError(err instanceof Error ? err.message : String(err));
        clearInterval(pollInterval);
      }
    }, 1500);

    return () => {
      isMounted = false;
      clearInterval(pollInterval);
    };
  }, [activeJobId, refreshHistory]);

  // Handle Starting a Real Investigation
  const handleStartLiveInvestigation = async () => {
    setIsStartingJob(true);
    setLiveError(null);
    try {
      const res = await startInvestigation(targetInput);
      setActiveJobId(res.job_id);
      setActiveJob({
        job_id: res.job_id,
        status: "queued",
        progress_step: "queued",
        target: {
          namespace: targetInput.namespace,
          service: targetInput.service,
          deployment: targetInput.deployment || null,
          label_selector: targetInput.labelSelector || null,
          require_service: !!targetInput.requireService,
        },
        created_at: new Date().toISOString(),
      });
      // Default open first evidence items
      setExpandedEvidence({ E1: true, E2: true });
    } catch (err: unknown) {
      setLiveError(err instanceof Error ? err.message : String(err));
    } finally {
      setIsStartingJob(false);
    }
  };

  // Handle Live Human Approval
  const handleApproveLiveRemediation = async () => {
    if (!activeJobId) return;
    setIsApproving(true);
    setLiveError(null);
    try {
      const res = await approveInvestigation(activeJobId, true);
      setActiveJobId(res.job_id);
      setActiveJob({
        job_id: res.job_id,
        status: "queued",
        progress_step: "queued",
        target: {
          namespace: targetInput.namespace,
          service: targetInput.service,
          deployment: targetInput.deployment || null,
          label_selector: targetInput.labelSelector || null,
          require_service: !!targetInput.requireService,
          approve: true,
        },
        parent_job_id: activeJobId,
        created_at: new Date().toISOString(),
      });
    } catch (err: unknown) {
      setLiveError(err instanceof Error ? err.message : String(err));
    } finally {
      setIsApproving(false);
    }
  };

  // Load a job from history
  const handleSelectHistoryJob = async (jobId: string) => {
    setLiveError(null);
    try {
      const job = await getInvestigation(jobId);
      setActiveJobId(job.job_id);
      setActiveJob(job);
      if (job.target) {
        setTargetInput({
          namespace: job.target.namespace,
          service: job.target.service,
          deployment: job.target.deployment || undefined,
          labelSelector: job.target.label_selector || undefined,
          requireService: job.target.require_service || false,
        });
      }
    } catch (err: unknown) {
      setLiveError(err instanceof Error ? err.message : String(err));
    }
  };

  // Active Incident Data depending on Mode
  const liveState: LiveInvestigationState | null = activeJob?.state ?? null;

  const currentIncident: Incident = useMemo(() => {
    if (consoleMode === "live") {
      if (liveState?.incident) {
        return {
          ...liveState.incident,
          evidence: liveState.incident.evidence || [],
          runbooks: liveState.incident.runbooks || [],
          diagnosis: liveState.diagnosis || liveState.incident.diagnosis || {
            rootCause: "Awaiting Diagnosis",
            confidence: null,
            summary: "Diagnosis pending.",
            supportingEvidence: [],
            recommendedActions: [],
          },
          remediation: liveState.remediationPlan || liveState.incident.remediation || {
            action: "none",
            reason: "No remediation planned.",
            evidence: [],
            safety: "Human approval required",
            abstained: false,
          },
          knowledgeDocs: liveState.knowledgeDocs || liveState.incident.knowledgeDocs || [],
          trace: liveState.trace || liveState.incident.trace || [],
          verification: liveState.verification || liveState.incident.verification || {
            proposedState: "None",
            executedState: "not_executed",
            verifiedState: "unverified",
            verificationStrategy: [],
            summary: "No verification yet.",
          },
        };
      }
      // Empty shell for Live Mode before job completion
      return {
        id: activeJob ? `LIVE-${activeJob.job_id.slice(-6).toUpperCase()}` : "LIVE-TARGET",
        title: activeJob
          ? `Live Investigation: ${activeJob.target.service}`
          : `Target: ${targetInput.service}`,
        service: activeJob?.target.service || targetInput.service,
        namespace: activeJob?.target.namespace || targetInput.namespace,
        severity: "High",
        podStatus: "Running",
        ready: true,
        restarts: null,
        evidence: [],
        diagnosis: {
          rootCause: "Awaiting Live Investigation",
          confidence: null,
          summary: "Submit a cluster workload target to begin live evidence collection and diagnosis.",
          supportingEvidence: [],
          recommendedActions: [],
        },
        runbooks: [],
        remediation: {
          action: "No remediation requested yet",
          reason: "Submit an investigation target to trigger the remediation planner.",
          evidence: [],
          safety: "Human approval required",
          abstained: false,
        },
      };
    }
    // Demo Mode
    return demoIncidents.find((item) => item.id === selectedDemoId) ?? demoIncidents[0];
  }, [consoleMode, liveState, activeJob, targetInput, demoIncidents, selectedDemoId]);

  const toggleEvidence = (id: string) => {
    setExpandedEvidence((prev) => ({ ...prev, [id]: !prev[id] }));
  };

  const toggleTrace = (id: string) => {
    setExpandedTrace((prev) => ({ ...prev, [id]: !prev[id] }));
  };

  const handleJumpToEvidence = (id: EvidenceId) => {
    setExpandedEvidence((prev) => ({ ...prev, [id]: true }));
    const element = document.getElementById(`evidence-${id}`);
    if (element) {
      element.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  };

  const handleCopy = (text: string, id: string) => {
    if (navigator?.clipboard) {
      navigator.clipboard.writeText(text);
      setCopiedId(id);
      setTimeout(() => setCopiedId(null), 1800);
    }
  };

  // Demo filtered incidents
  const filteredDemoIncidents = useMemo(() => {
    return demoIncidents.filter((item) => {
      const matchesSeverity = demoSeverityFilter === "ALL" || item.severity === demoSeverityFilter;
      const q = demoSearchQuery.trim().toLowerCase();
      const matchesSearch =
        !q ||
        item.id.toLowerCase().includes(q) ||
        item.title.toLowerCase().includes(q) ||
        item.diagnosis.rootCause.toLowerCase().includes(q) ||
        item.service.toLowerCase().includes(q);
      return matchesSeverity && matchesSearch;
    });
  }, [demoIncidents, demoSeverityFilter, demoSearchQuery]);

  // Derived trace steps
  const traceSteps = useMemo(() => {
    if (consoleMode === "live" && liveState?.trace) {
      return liveState.trace;
    }
    return currentIncident.trace ?? [];
  }, [consoleMode, liveState, currentIncident]);

  return (
    <div className="app-shell">
      {/* 1. COMPACT SIDEBAR */}
      <aside className="app-sidebar" aria-label="Console Navigation">
        <div className="sidebar-header">
          <div className="brand-icon-box">
            <Layers3 size={20} />
          </div>
          <div>
            <div className="brand-title">OPERION</div>
            <div className="brand-subtitle">AI Production SRE Console</div>
          </div>
        </div>

        <nav className="sidebar-nav">
          <div className="nav-section-label">Navigation</div>

          <button
            type="button"
            className={`sidebar-nav-item ${activeSection === "overview" ? "active" : ""}`}
            onClick={() => setActiveSection("overview")}
          >
            <div className="nav-left">
              <Activity size={15} />
              <span>Overview</span>
            </div>
          </button>

          <button
            type="button"
            className={`sidebar-nav-item ${activeSection === "incidents" ? "active" : ""}`}
            onClick={() => setActiveSection("incidents")}
          >
            <div className="nav-left">
              <Server size={15} />
              <span>{consoleMode === "live" ? "Workload Target" : "Incidents"}</span>
            </div>
            <span className="nav-badge">
              {consoleMode === "live" ? (jobHistory.length > 0 ? jobHistory.length : "Live") : demoIncidents.length}
            </span>
          </button>

          <button
            type="button"
            className={`sidebar-nav-item ${activeSection === "investigation" ? "active" : ""}`}
            onClick={() => setActiveSection("investigation")}
          >
            <div className="nav-left">
              <Terminal size={15} />
              <span>Investigation</span>
            </div>
            <span className="nav-badge">
              {consoleMode === "live"
                ? `${currentIncident.evidence.length} Ev.`
                : "E1-E4"}
            </span>
          </button>

          <button
            type="button"
            className={`sidebar-nav-item ${activeSection === "knowledge" ? "active" : ""}`}
            onClick={() => setActiveSection("knowledge")}
          >
            <div className="nav-left">
              <Database size={15} />
              <span>Knowledge (RAG)</span>
            </div>
            <Badge tone="cyan" className="text-[9px] py-0 px-1">
              {consoleMode === "live"
                ? (liveState?.knowledgeRetrievalStatus === "failed" ? "Failed" : "RAG")
                : "Qdrant"}
            </Badge>
          </button>

          <button
            type="button"
            className={`sidebar-nav-item ${activeSection === "safety" ? "active" : ""}`}
            onClick={() => setActiveSection("safety")}
          >
            <div className="nav-left">
              <ShieldCheck size={15} />
              <span>Safety & Audit</span>
            </div>
            <Badge
              tone={
                consoleMode === "live"
                  ? (liveState?.remediationProposal?.abstain ? "rose" : "amber")
                  : (currentIncident.remediation.abstained ? "rose" : "amber")
              }
              className="text-[9px] py-0 px-1"
            >
              {consoleMode === "live"
                ? (liveState?.remediationProposal?.abstain ? "Abstained" : "Guarded")
                : (currentIncident.remediation.abstained ? "Gate Closed" : "Armed")}
            </Badge>
          </button>
        </nav>

        <div className="sidebar-footer">
          <div className="env-card">
            <div className="env-header">
              <div className="flex items-center gap-2">
                <span className={`env-status-dot ${backendHealth.ok ? "bg-mint" : "bg-rose-500"}`} />
                <span className="env-title">
                  {consoleMode === "live" ? "Cluster Backend" : "Demo Sandbox"}
                </span>
              </div>
              <Badge tone={backendHealth.ok ? "mint" : "rose"}>
                {backendHealth.ok ? "Online" : "Offline"}
              </Badge>
            </div>
            <div className="env-meta">
              Target: <span className="mono">{targetInput.namespace}</span>
            </div>
            <div className="env-meta mt-1 text-[9px] text-gray-500 flex items-center justify-between">
              <span>{consoleMode === "live" ? "Live FastAPI Agent" : "Deterministic Fixture"}</span>
              <button
                type="button"
                className="hover:text-mint"
                onClick={verifyBackend}
                title="Verify backend connection"
              >
                <RotateCcw size={10} />
              </button>
            </div>
          </div>
        </div>
      </aside>

      {/* 2. MAIN APPLICATION CONTENT */}
      <div className="app-main">
        {/* STICKY CONSOLE HEADER WITH MODE SWITCHER */}
        <header className="console-header">
          <div className="header-left">
            <div className="incident-pill">
              <span className="mono text-mint font-semibold">{currentIncident.id}</span>
              <span className="text-gray-400">|</span>
              <span className="truncate max-w-[180px] sm:max-w-[280px]">
                {currentIncident.title}
              </span>
            </div>

            <div className="workload-target hidden md:flex">
              <Box size={14} className="text-gray-400" />
              <span className="mono">{currentIncident.service}</span>
              <span className="text-gray-600">/</span>
              <span className="mono text-gray-400">{currentIncident.namespace}</span>
            </div>
          </div>

          <div className="header-right">
            {/* Live Mode vs Demo Mode Selector */}
            <div className="flex items-center bg-[#0d121c] p-1 rounded-lg border border-[#27374d] text-xs">
              <button
                type="button"
                className={`px-3 py-1 rounded-md flex items-center gap-1.5 font-medium transition-all ${
                  consoleMode === "live"
                    ? "bg-mint text-[#071311] shadow-sm font-semibold"
                    : "text-gray-400 hover:text-white"
                }`}
                onClick={() => setConsoleMode("live")}
              >
                <Wifi size={13} />
                <span>Live Mode</span>
              </button>
              <button
                type="button"
                className={`px-3 py-1 rounded-md flex items-center gap-1.5 font-medium transition-all ${
                  consoleMode === "demo"
                    ? "bg-amber text-[#261502] shadow-sm font-semibold"
                    : "text-gray-400 hover:text-white"
                }`}
                onClick={() => setConsoleMode("demo")}
              >
                <Layers3 size={13} />
                <span>Demo Mode</span>
              </button>
            </div>

            <Badge tone={consoleMode === "live" ? "mint" : "amber"}>
              <Circle size={7} fill="currentColor" className="animate-pulse" />
              {consoleMode === "live" ? "Live Cluster" : "Demo Fixtures"}
            </Badge>
          </div>
        </header>

        {/* WORKSPACE CONTAINER */}
        <main className="workspace-container">
          {/* BACKEND DISCONNECTION BANNER (WHEN IN LIVE MODE BUT BACKEND DOWN) */}
          {consoleMode === "live" && !backendHealth.ok && backendHealth.checked && (
            <div className="p-4 bg-rose-500/10 border border-rose-500/30 rounded-lg flex items-start justify-between gap-4 text-xs text-rose-300">
              <div className="flex items-start gap-3">
                <WifiOff size={18} className="text-rose-400 flex-shrink-0 mt-0.5" />
                <div>
                  <strong className="text-white block text-sm mb-1">
                    Operion FastAPI Backend Unreachable
                  </strong>
                  <p className="leading-relaxed">
                    Live Mode requires the local backend server. Please run:
                  </p>
                  <pre className="mt-2 p-2 bg-[#060910] border border-[#27374d] rounded mono text-[11px] text-gray-300">
                    cd backend &amp;&amp; source ../.venv-wsl/bin/activate &amp;&amp; uvicorn main:app --port 8000
                  </pre>
                  <p className="mt-2 text-gray-400 text-[11px]">
                    No fake operational data is substituted in Live Mode. You can switch to <strong>Demo Mode</strong> above to inspect the controlled benchmark scenarios.
                  </p>
                </div>
              </div>
              <button
                type="button"
                className="btn btn-secondary flex-shrink-0"
                onClick={verifyBackend}
              >
                <RotateCcw size={12} /> Check Again
              </button>
            </div>
          )}

          {/* LIVE ERROR MESSAGE IF ANY */}
          {liveError && (
            <div className="p-3 bg-rose-500/10 border border-rose-500/30 rounded-lg text-xs text-rose-300 flex items-center justify-between">
              <div className="flex items-center gap-2">
                <AlertCircle size={15} />
                <span>{liveError}</span>
              </div>
              <button
                type="button"
                onClick={() => setLiveError(null)}
                className="hover:text-white"
              >
                <X size={14} />
              </button>
            </div>
          )}

          {/* 3. TARGET SUBMISSION & ACTIVE JOB (LIVE MODE) OR INCIDENT QUEUE (DEMO MODE) */}
          {consoleMode === "live" ? (
            <section id="incidents" className="panel" aria-labelledby="live-target-heading">
              <div className="panel-header">
                <div className="panel-header-left">
                  <Play size={16} className="text-mint" />
                  <h2 id="live-target-heading" className="panel-header-title">
                    Live Cluster Investigation Target
                  </h2>
                </div>
                <div className="flex items-center gap-2">
                  <Badge tone="mint">Real Kubernetes Probes</Badge>
                  {activeJob && (
                    <Badge tone={activeJob.status === "completed" ? "mint" : activeJob.status === "failed" ? "rose" : "amber"}>
                      Job: {activeJob.status.toUpperCase()}
                    </Badge>
                  )}
                </div>
              </div>

              <div className="p-5 space-y-4">
                {/* Target Submission Form */}
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3 text-xs">
                  <div>
                    <label className="block text-[10px] uppercase font-semibold text-gray-400 mb-1">
                      Namespace
                    </label>
                    <input
                      type="text"
                      className="w-full bg-[#0a0e17] border border-[#1c2738] rounded px-3 py-1.5 text-gray-200 mono text-xs focus:border-mint outline-none"
                      value={targetInput.namespace}
                      onChange={(e) => setTargetInput({ ...targetInput, namespace: e.target.value })}
                      placeholder="e.g. operion-sandbox"
                    />
                  </div>

                  <div>
                    <label className="block text-[10px] uppercase font-semibold text-gray-400 mb-1">
                      Service / Target
                    </label>
                    <input
                      type="text"
                      className="w-full bg-[#0a0e17] border border-[#1c2738] rounded px-3 py-1.5 text-gray-200 mono text-xs focus:border-mint outline-none"
                      value={targetInput.service}
                      onChange={(e) => setTargetInput({ ...targetInput, service: e.target.value })}
                      placeholder="e.g. payment-service"
                    />
                  </div>

                  <div>
                    <label className="block text-[10px] uppercase font-semibold text-gray-400 mb-1">
                      Deployment Name (optional)
                    </label>
                    <input
                      type="text"
                      className="w-full bg-[#0a0e17] border border-[#1c2738] rounded px-3 py-1.5 text-gray-200 mono text-xs focus:border-mint outline-none"
                      value={targetInput.deployment || ""}
                      onChange={(e) => setTargetInput({ ...targetInput, deployment: e.target.value || undefined })}
                      placeholder="defaults to service"
                    />
                  </div>

                  <div>
                    <label className="block text-[10px] uppercase font-semibold text-gray-400 mb-1">
                      Label Selector (optional)
                    </label>
                    <input
                      type="text"
                      className="w-full bg-[#0a0e17] border border-[#1c2738] rounded px-3 py-1.5 text-gray-200 mono text-xs focus:border-mint outline-none"
                      value={targetInput.labelSelector || ""}
                      onChange={(e) => setTargetInput({ ...targetInput, labelSelector: e.target.value || undefined })}
                      placeholder="defaults to app=<service>"
                    />
                  </div>
                </div>

                <div className="flex flex-wrap items-center justify-between gap-3 pt-2 border-t border-[#1c2738]">
                  <div className="flex items-center gap-4 text-xs text-gray-400">
                    <label className="flex items-center gap-2 cursor-pointer select-none">
                      <input
                        type="checkbox"
                        checked={targetInput.requireService || false}
                        onChange={(e) => setTargetInput({ ...targetInput, requireService: e.target.checked })}
                        className="rounded border-[#1c2738] text-mint focus:ring-0"
                      />
                      <span>Require Kubernetes Service routing</span>
                    </label>

                    {/* Workload presets */}
                    <div className="hidden sm:flex items-center gap-2">
                      <span className="text-[10px] uppercase text-gray-500">Presets:</span>
                      <button
                        type="button"
                        className="px-2 py-0.5 rounded bg-[#162030] hover:text-white text-[11px] mono transition-colors"
                        onClick={() =>
                          setTargetInput({
                            namespace: "operion-sandbox",
                            service: "payment-service",
                            deployment: "payment-service",
                            labelSelector: "app=payment-service",
                            requireService: false,
                          })
                        }
                      >
                        payment-service
                      </button>
                      <button
                        type="button"
                        className="px-2 py-0.5 rounded bg-[#162030] hover:text-white text-[11px] mono transition-colors"
                        onClick={() =>
                          setTargetInput({
                            namespace: "operion-sandbox",
                            service: "selector-demo",
                            deployment: "selector-demo",
                            labelSelector: "app=selector-demo",
                            requireService: true,
                          })
                        }
                      >
                        selector-demo
                      </button>
                    </div>
                  </div>

                  <button
                    type="button"
                    className="btn btn-primary"
                    disabled={isStartingJob || !targetInput.namespace || !targetInput.service || !backendHealth.ok}
                    onClick={handleStartLiveInvestigation}
                  >
                    {isStartingJob ? (
                      <>
                        <Loader2 size={13} className="animate-spin" /> Starting...
                      </>
                    ) : (
                      <>
                        <Play size={13} /> Run Live Investigation
                      </>
                    )}
                  </button>
                </div>

                {/* Active Live Progress Rail */}
                {activeJob && (
                  <div className="p-3.5 bg-[#070a12] border border-[#1c2738] rounded-lg mt-3 space-y-2">
                    <div className="flex items-center justify-between text-xs">
                      <div className="flex items-center gap-2">
                        {activeJob.status === "running" || activeJob.status === "queued" ? (
                          <Loader2 size={14} className="animate-spin text-mint" />
                        ) : activeJob.status === "completed" ? (
                          <Check size={14} className="text-mint" />
                        ) : (
                          <AlertTriangle size={14} className="text-rose-400" />
                        )}
                        <span className="font-semibold text-gray-200">
                          {activeJob.status === "running"
                            ? `Active Step: ${activeJob.progress_step || "executing"}...`
                            : activeJob.status === "completed"
                            ? "Investigation Complete"
                            : "Investigation Failed"}
                        </span>
                      </div>
                      <span className="mono text-[10px] text-gray-500">ID: {activeJob.job_id}</span>
                    </div>

                    {activeJob.error && (
                      <div className="text-[11px] text-rose-400 mono p-2 bg-rose-500/10 rounded">
                        Error: {activeJob.error}
                      </div>
                    )}

                    {/* Progress timeline nodes */}
                    <div className="flex items-center gap-1.5 overflow-x-auto py-1 text-[10px] text-gray-400">
                      {[
                        "collect_evidence",
                        "generate_hypotheses",
                        "retrieve_knowledge",
                        "diagnose",
                        "generate_remediation_proposal",
                        "validate_policy",
                        "completed",
                      ].map((step, idx) => {
                        const isDone =
                          activeJob.status === "completed" ||
                          (activeJob.progress_step && idx < 6 && step === activeJob.progress_step);
                        const isCurrent = activeJob.status === "running" && step === activeJob.progress_step;
                        return (
                          <div key={step} className="flex items-center gap-1.5 flex-shrink-0">
                            <span
                              className={`px-2 py-0.5 rounded mono ${
                                isCurrent
                                  ? "bg-mint text-[#071311] font-semibold"
                                  : isDone
                                  ? "bg-[#162030] text-mint"
                                  : "bg-[#0f141f] text-gray-600"
                              }`}
                            >
                              {step.replace("_", " ")}
                            </span>
                            {idx < 6 && <ArrowRight size={10} className="text-gray-700" />}
                          </div>
                        );
                      })}
                    </div>
                  </div>
                )}

                {/* History Drawer */}
                {jobHistory.length > 0 && (
                  <div className="pt-2 border-t border-[#1c2738]">
                    <div className="flex items-center justify-between text-xs text-gray-400 mb-2">
                      <div className="flex items-center gap-1.5">
                        <History size={13} />
                        <span>Investigation History ({jobHistory.length})</span>
                      </div>
                      <button
                        type="button"
                        onClick={refreshHistory}
                        className="hover:text-white text-[10px] flex items-center gap-1"
                      >
                        <RotateCcw size={10} /> Refresh
                      </button>
                    </div>

                    <div className="flex items-center gap-2 overflow-x-auto pb-1">
                      {jobHistory.slice(0, 6).map((j) => (
                        <button
                          key={j.job_id}
                          type="button"
                          className={`p-2 bg-[#0e1420] border rounded text-left flex-shrink-0 min-w-[170px] hover:border-mint transition-all ${
                            activeJobId === j.job_id ? "border-mint shadow-sm" : "border-[#1c2738]"
                          }`}
                          onClick={() => handleSelectHistoryJob(j.job_id)}
                        >
                          <div className="flex justify-between items-center text-[10px]">
                            <span className="mono font-semibold text-gray-300">
                              {j.target?.service || "unknown"}
                            </span>
                            <Badge tone={j.status === "completed" ? "mint" : j.status === "failed" ? "rose" : "amber"}>
                              {j.status}
                            </Badge>
                          </div>
                          <div className="text-[9px] text-gray-500 mt-1 flex items-center gap-1 mono">
                            <Clock size={9} />
                            <span>{new Date(j.created_at).toLocaleTimeString()}</span>
                          </div>
                        </button>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </section>
          ) : (
            /* DEMO INCIDENT QUEUE */
            <section id="incidents" aria-labelledby="demo-queue-heading">
              <div className="queue-section-header">
                <div className="flex items-center gap-3">
                  <h2 id="demo-queue-heading" className="queue-title">Demo Incident Queue</h2>
                  <span className="text-xs text-gray-400">({filteredDemoIncidents.length} scenarios)</span>
                </div>

                <div className="flex items-center gap-3">
                  <div className="hidden sm:flex items-center bg-[#121824] p-1 rounded border border-[#1c2738] text-[11px]">
                    {(["ALL", "Critical", "High"] as const).map((sev) => (
                      <button
                        key={sev}
                        type="button"
                        className={`px-2.5 py-1 rounded transition-colors ${
                          demoSeverityFilter === sev ? "bg-[#1f2b3e] text-white font-medium" : "text-gray-400 hover:text-white"
                        }`}
                        onClick={() => setDemoSeverityFilter(sev)}
                      >
                        {sev}
                      </button>
                    ))}
                  </div>

                  <div className="queue-search-bar">
                    <Search size={13} className="text-gray-400" />
                    <input
                      type="text"
                      placeholder="Filter scenarios or causes..."
                      className="queue-search-input"
                      value={demoSearchQuery}
                      onChange={(e) => setDemoSearchQuery(e.target.value)}
                    />
                    {demoSearchQuery && (
                      <button
                        type="button"
                        onClick={() => setDemoSearchQuery("")}
                        className="text-gray-400 hover:text-white"
                      >
                        <X size={12} />
                      </button>
                    )}
                  </div>
                </div>
              </div>

              {filteredDemoIncidents.length > 0 ? (
                <div className="incident-queue-grid">
                  {filteredDemoIncidents.map((item) => {
                    const isSelected = item.id === currentIncident.id;
                    return (
                      <button
                        key={item.id}
                        type="button"
                        className={`incident-queue-card ${isSelected ? "selected" : ""}`}
                        onClick={() => {
                          setSelectedDemoId(item.id);
                          setDemoApproval("review");
                        }}
                        aria-pressed={isSelected}
                      >
                        <div>
                          <div className="queue-card-top">
                            <span className="mono font-semibold text-gray-300">{item.id}</span>
                            <Badge tone={item.severity === "Critical" ? "rose" : "amber"}>
                              {item.severity}
                            </Badge>
                          </div>
                          <div className="queue-card-title">{item.title}</div>
                          <div className="text-[10px] text-gray-400 truncate mono">
                            {item.diagnosis.rootCause}
                          </div>
                        </div>

                        <div className="queue-card-bottom">
                          <span className="mono">
                            {item.podStatus === "CrashLoopBackOff" ? (
                              <span className="text-rose-400">CrashLoopBackOff</span>
                            ) : (
                              <span className="text-amber-400">{item.podStatus}</span>
                            )}
                          </span>
                          <div className="flex items-center gap-1 text-mint">
                            <span>Inspect</span>
                            <ArrowRight size={11} />
                          </div>
                        </div>
                      </button>
                    );
                  })}
                </div>
              ) : (
                <div className="panel p-8 text-center text-gray-400">
                  <AlertTriangle size={24} className="mx-auto mb-2 text-amber" />
                  <p className="text-sm font-medium text-gray-200">No scenarios match &quot;{demoSearchQuery}&quot;</p>
                  <button
                    type="button"
                    className="btn btn-secondary mt-4"
                    onClick={() => {
                      setDemoSearchQuery("");
                      setDemoSeverityFilter("ALL");
                    }}
                  >
                    <RotateCcw size={12} /> Reset Filters
                  </button>
                </div>
              )}
            </section>
          )}

          {/* ACTIVE INCIDENT EXECUTIVE SUMMARY BAR */}
          <section className="incident-summary-bar" aria-label="Incident Overview">
            <div className="incident-summary-left">
              <div className="incident-avatar">
                <Activity size={22} className={currentIncident.severity === "Critical" ? "text-rose-400" : "text-amber-400"} />
              </div>
              <div className="incident-title-block">
                <div className="incident-meta-chips">
                  <span className="mono text-mint font-semibold">{currentIncident.id}</span>
                  <Badge tone={currentIncident.severity === "Critical" ? "rose" : "amber"}>
                    {currentIncident.severity}
                  </Badge>
                  <span className="text-gray-500">·</span>
                  <span className="text-gray-400 text-xs">{currentIncident.service}</span>
                </div>
                <h2>{currentIncident.title}</h2>
              </div>
            </div>

            <div className="metric-strip">
              <div className="metric-strip-item">
                <span className="metric-strip-label">Workload</span>
                <span className="metric-strip-value mono text-gray-200">{currentIncident.service}</span>
              </div>
              <div className="metric-strip-item">
                <span className="metric-strip-label">Namespace</span>
                <span className="metric-strip-value mono text-gray-200">{currentIncident.namespace}</span>
              </div>
              <div className="metric-strip-item">
                <span className="metric-strip-label">Pod Status</span>
                <span className={`metric-strip-value mono ${currentIncident.podStatus === "Running" ? "text-mint" : "text-rose-400"}`}>
                  {currentIncident.podStatus}
                </span>
              </div>
              <div className="metric-strip-item">
                <span className="metric-strip-label">Readiness</span>
                <span className={`metric-strip-value mono ${currentIncident.ready ? "text-mint" : "text-amber-400"}`}>
                  {currentIncident.ready ? "1/1 · Ready" : "0/1 · Not Ready"}
                </span>
              </div>
              <div className="metric-strip-item">
                <span className="metric-strip-label">Restarts</span>
                <span className="metric-strip-value mono text-gray-300">
                  {currentIncident.restarts ?? "None"}
                </span>
              </div>
            </div>
          </section>

          {/* TWO-COLUMN INVESTIGATION WORKSPACE */}
          <div className="workspace-columns">
            {/* LEFT COLUMN: EVIDENCE TIMELINE */}
            <div className="workspace-column">
              {/* CLUSTER EVIDENCE TIMELINE */}
              <section id="investigation" className="panel" aria-labelledby="evidence-heading">
                <div className="panel-header">
                  <div className="panel-header-left">
                    <Terminal size={16} className="panel-header-icon" />
                    <h3 id="evidence-heading" className="panel-header-title">
                      Cluster Evidence Timeline
                    </h3>
                  </div>
                  <div className="flex items-center gap-2">
                    <button
                      type="button"
                      className="text-[10px] text-gray-400 hover:text-white px-2 py-1 rounded bg-[#162030] transition-colors"
                      onClick={() => {
                        const all: Record<string, boolean> = {};
                        (currentIncident.evidence || []).forEach((e) => (all[e.id] = true));
                        setExpandedEvidence(all);
                      }}
                    >
                      Expand All
                    </button>
                    <button
                      type="button"
                      className="text-[10px] text-gray-400 hover:text-white px-2 py-1 rounded bg-[#162030] transition-colors"
                      onClick={() => setExpandedEvidence({})}
                    >
                      Collapse All
                    </button>
                    <Badge tone="cyan">{(currentIncident.evidence || []).length} Evidence Items</Badge>
                  </div>
                </div>

                <div className="evidence-timeline-header">
                  <span>Artifact Pipeline: Pod Snapshot → Event Stream → Log Excerpt → Spec</span>
                  <span className="mono">
                    {consoleMode === "live" ? "Live Cluster Probes" : "Benchmark Snapshots"}
                  </span>
                </div>

                <div className="evidence-items-container">
                  {(currentIncident.evidence || []).length > 0 ? (
                    (currentIncident.evidence || []).map((item) => {
                      const isOpen = !!expandedEvidence[item.id];
                      return (
                        <div
                          key={`${currentIncident.id}-${item.id}`}
                          id={`evidence-${item.id}`}
                          className={`evidence-card ${isOpen ? "active-target" : ""}`}
                        >
                          <summary
                            onClick={(e) => {
                              e.preventDefault();
                              toggleEvidence(item.id);
                            }}
                          >
                            <span className="evidence-id-tag">{item.id}</span>
                            <div className="evidence-title-group">
                              <strong>{item.title}</strong>
                              <span className="evidence-highlight-snippet">{item.highlight}</span>
                            </div>
                            <div className="flex items-center gap-2">
                              <span className="text-[10px] text-gray-500 hidden sm:inline">{item.source}</span>
                              <ChevronDown
                                size={15}
                                className={`text-gray-400 transition-transform ${isOpen ? "rotate-180" : ""}`}
                              />
                            </div>
                          </summary>

                          {isOpen && (
                            <div className="evidence-body">
                              <div className="evidence-source-banner">
                                <span className="mono text-[10px] text-gray-400">
                                  Source: {item.source}
                                </span>
                                <button
                                  type="button"
                                  className="flex items-center gap-1 text-[10px] text-gray-400 hover:text-mint transition-colors"
                                  onClick={() => handleCopy(item.content, item.id)}
                                  title="Copy evidence to clipboard"
                                >
                                  <Copy size={11} />
                                  {copiedId === item.id ? "Copied!" : "Copy"}
                                </button>
                              </div>
                              <pre className="terminal-output mono">{item.content}</pre>
                            </div>
                          )}
                        </div>
                      );
                    })
                  ) : (
                    <div className="p-8 text-center text-gray-400 text-xs">
                      {consoleMode === "live"
                        ? "No evidence collected yet. Start an investigation above to query the Kubernetes cluster."
                        : "No evidence available."}
                    </div>
                  )}
                </div>

                <div className="p-3 bg-[#0a0e17] border-t border-[#1c2738] text-[10px] text-gray-500 flex items-center gap-2">
                  <ShieldCheck size={13} className="text-mint" />
                  <span>
                    {consoleMode === "live"
                      ? "Real observations captured live via Kubernetes client (read-only tools)."
                      : "Representative excerpts captured during controlled benchmark runs."}
                  </span>
                </div>
              </section>

              {/* KNOWLEDGE PANEL (RAG & DOCS) */}
              <section id="knowledge" className="panel" aria-labelledby="knowledge-heading">
                <div className="panel-header">
                  <div className="panel-header-left">
                    <Database size={16} className="panel-header-icon" />
                    <h3 id="knowledge-heading" className="panel-header-title">
                      Knowledge Retrieval & Runbooks
                    </h3>
                  </div>
                  <Badge tone="cyan">
                    {consoleMode === "live"
                      ? (liveState?.knowledgeRetrievalStatus === "failed" ? "Failed" : "RAG Corpus")
                      : "RAG · Qdrant"}
                  </Badge>
                </div>

                <div className="knowledge-pipeline-info">
                  <div className="flex items-center gap-2">
                    <span className="mono">Embedding: MiniLM-L6-v2</span>
                    <span>→</span>
                    <span className="mono">Vector DB: Qdrant</span>
                  </div>
                  <span className="text-[10px] text-gray-400">
                    Status: {consoleMode === "live" ? (liveState?.knowledgeRetrievalStatus || "Ready") : "Active"}
                  </span>
                </div>

                {/* Honest Retrieval Failure Banner */}
                {consoleMode === "live" && liveState?.knowledgeRetrievalStatus === "failed" && (
                  <div className="m-4 p-3 bg-amber-500/10 border border-amber-500/30 rounded text-xs text-amber-300 flex items-start gap-2">
                    <AlertTriangle size={15} className="flex-shrink-0 mt-0.5" />
                    <div>
                      <strong>Embedding Initialization Unavailable:</strong> Vector search timed out or Qdrant was unreachable.
                      No runbooks were retrieved for this investigation. Fallback to official Kubernetes documentation was applied.
                    </div>
                  </div>
                )}

                <div className="knowledge-docs-list">
                  {/* Retrieved Runbooks */}
                  {(currentIncident.runbooks || []).map((book) => (
                    <div key={book.filename} className="knowledge-doc-card">
                      <div className="doc-card-header">
                        <div className="doc-title">
                          <FileText size={14} className="text-mint" />
                          <span className="mono">{book.filename}</span>
                        </div>
                        <Badge tone="mint">
                          Similarity: {book.similarity.toFixed(4)}
                        </Badge>
                      </div>
                      <div className="doc-excerpt">
                        Matched via dense vector retrieval against curated SRE runbook corpus.
                      </div>
                    </div>
                  ))}

                  {/* Official Kubernetes Documentation Sources */}
                  {(consoleMode === "live" ? liveState?.knowledgeDocs || [] : currentIncident.knowledgeDocs || []).map((doc, idx) => (
                    <div key={doc.title + idx} className="knowledge-doc-card">
                      <div className="doc-card-header">
                        <div className="doc-title">
                          {doc.sourceType === "kubernetes_docs" ? (
                            <Box size={14} className="text-cyan" />
                          ) : (
                            <FileCode2 size={14} className="text-mint" />
                          )}
                          <span>{doc.title}</span>
                        </div>
                        {doc.url ? (
                          <a
                            href={doc.url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="flex items-center gap-1 text-[10px] text-cyan hover:underline"
                          >
                            <span>Official Docs</span>
                            <ExternalLink size={10} />
                          </a>
                        ) : (
                          <span className="text-[10px] text-gray-500">{doc.availability}</span>
                        )}
                      </div>
                      {doc.section && (
                        <div className="text-[10px] text-gray-400 mono">
                          Section: {doc.section}
                        </div>
                      )}
                      {doc.excerpt && (
                        <div className="doc-excerpt border-t border-[#1c2738] pt-2 mt-2">
                          &ldquo;{doc.excerpt}&rdquo;
                        </div>
                      )}
                    </div>
                  ))}

                  {(currentIncident.runbooks || []).length === 0 &&
                    (consoleMode === "live" ? (liveState?.knowledgeDocs || []).length === 0 : (currentIncident.knowledgeDocs || []).length === 0) && (
                      <div className="text-center py-6 text-gray-500 text-xs">
                        No runbooks or documentation retrieved.
                      </div>
                    )}
                </div>

                <div className="p-3 bg-[#0a0e17] border-t border-[#1c2738] text-[10px] text-gray-500">
                  <p>
                    <strong>Honest Retrieval Disclosure:</strong> In Live Mode, runbook availability reflects real vector search and embedding status.
                    Failures are reported truthfully rather than simulated.
                  </p>
                </div>
              </section>
            </div>

            {/* RIGHT COLUMN: DIAGNOSIS, REMEDIATION & VERIFICATION */}
            <div className="workspace-column">
              {/* DIAGNOSIS CARD */}
              <section className="panel" aria-labelledby="diagnosis-heading">
                <div className="panel-header">
                  <div className="panel-header-left">
                    <ShieldCheck size={16} className="panel-header-icon" />
                    <h3 id="diagnosis-heading" className="panel-header-title">
                      Evidence-Grounded Diagnosis
                    </h3>
                  </div>
                  <Badge tone={currentIncident.diagnosis.rootCause === "no_active_incident" ? "mint" : "amber"}>
                    {currentIncident.diagnosis.rootCause === "no_active_incident" ? "Healthy" : "Grounded"}
                  </Badge>
                </div>

                <div className="diagnosis-content">
                  <div className="diagnosis-root-cause-box">
                    <div className="flex justify-between items-center mb-1">
                      <span className="root-cause-heading">Inferred Root Cause</span>
                      <span className="mono text-[10px] text-mint">
                        {currentIncident.diagnosis.confidence === null
                          ? "Confidence not reported"
                          : `${(currentIncident.diagnosis.confidence * 100).toFixed(0)}% confidence`}
                      </span>
                    </div>
                    <div className="root-cause-title mono">{currentIncident.diagnosis.rootCause}</div>
                  </div>

                  <p className="diagnosis-summary-text">{currentIncident.diagnosis.summary}</p>

                  <div className="supporting-evidence-row">
                    <span>Supporting Evidence:</span>
                    <div className="flex items-center gap-1.5 flex-wrap">
                      {(currentIncident.diagnosis?.supportingEvidence || []).length > 0 ? (
                        (currentIncident.diagnosis?.supportingEvidence || []).map((eid) => (
                          <button
                            key={eid}
                            type="button"
                            className="evidence-ref-button"
                            onClick={() => handleJumpToEvidence(eid.split(":")[0])}
                            title={`Jump to evidence ${eid}`}
                          >
                            {eid}
                          </button>
                        ))
                      ) : (
                        <span className="text-gray-500 text-xs">No supporting IDs cited</span>
                      )}
                    </div>
                  </div>

                  {(currentIncident.diagnosis?.recommendedActions || []).length > 0 && (
                    <div className="recommended-actions-block">
                      <h4 className="recommended-actions-heading">Recommended Operator Actions</h4>
                      {(currentIncident.diagnosis?.recommendedActions || []).map((action, idx) => (
                        <div key={action} className="action-item">
                          <span className="action-index-badge">{idx + 1}</span>
                          <span>{action}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </section>

              {/* REMEDIATION PANEL */}
              <section id="safety" className="panel" aria-labelledby="remediation-heading">
                <div className="panel-header">
                  <div className="panel-header-left">
                    <GitBranch size={16} className="panel-header-icon" />
                    <h3 id="remediation-heading" className="panel-header-title">
                      Remediation Plan & Safety Gate
                    </h3>
                  </div>
                  <Badge
                    tone={
                      consoleMode === "live"
                        ? (liveState?.remediationProposal?.abstain ? "rose" : "amber")
                        : (currentIncident.remediation.abstained ? "rose" : "amber")
                    }
                  >
                    {consoleMode === "live"
                      ? (liveState?.remediationProposal?.abstain ? "Abstained" : "Policy Gated")
                      : (currentIncident.remediation.abstained ? "Abstained" : "Approval Gated")}
                  </Badge>
                </div>

                <div className="remediation-content">
                  {/* Technical Operation Specs */}
                  <div className="remediation-props-table">
                    <div className="remediation-prop-item">
                      <span className="remediation-prop-label">Operation</span>
                      <span className="remediation-prop-value text-mint">
                        {currentIncident.remediation.operation ||
                          liveState?.remediationProposal?.candidates?.[0]?.operation ||
                          "none"}
                      </span>
                    </div>

                    <div className="remediation-prop-item">
                      <span className="remediation-prop-label">Target Resource</span>
                      <span className="remediation-prop-value text-gray-300">
                        {currentIncident.remediation.target
                          ? `${currentIncident.remediation.target.kind}/${currentIncident.remediation.target.name}`
                          : `${currentIncident.service} (${currentIncident.namespace})`}
                      </span>
                    </div>

                    <div className="remediation-prop-item">
                      <span className="remediation-prop-label">Risk Evaluation</span>
                      <div>
                        <RiskBadge
                          level={
                            currentIncident.remediation.riskLevel ||
                            liveState?.remediationProposal?.candidates?.[0]?.riskLevel
                          }
                        />
                      </div>
                    </div>

                    <div className="remediation-prop-item">
                      <span className="remediation-prop-label">Execution Guardrail</span>
                      <span className="text-[10px] text-amber mono">
                        {consoleMode === "live" ? "Allowlist Enforced" : "Non-Executable (Demo)"}
                      </span>
                    </div>
                  </div>

                  <div>
                    <h4 className="text-xs font-semibold text-gray-200 mb-1">
                      {currentIncident.remediation.action}
                    </h4>
                    <p className="text-xs text-gray-400 leading-relaxed">
                      {currentIncident.remediation.reason}
                    </p>
                  </div>

                  {/* Safety Policy Notice */}
                  {(consoleMode === "live"
                    ? liveState?.remediationProposal?.abstain
                    : currentIncident.remediation.abstained) ? (
                    <div className="safety-gate-banner abstained">
                      <LockKeyhole size={16} className="flex-shrink-0 mt-0.5" />
                      <div>
                        <strong>Safety Gate Closed:</strong>{" "}
                        {liveState?.remediationProposal?.abstainReason || currentIncident.remediation.safety}.
                        Ungrounded mutations are prevented.
                      </div>
                    </div>
                  ) : (
                    <div className="safety-gate-banner guarded">
                      <Lock size={15} className="flex-shrink-0 mt-0.5" />
                      <div>
                        <strong>Human Approval Required:</strong> {currentIncident.remediation.safety}.
                        Mutation cannot execute without operator sign-off. Only allowlisted payment env patches may execute.
                      </div>
                    </div>
                  )}

                  {/* Human-in-the-Loop Actions */}
                  {consoleMode === "live" ? (
                    liveState?.remediationProposal && !liveState.remediationProposal.abstain ? (
                      <div>
                        <div className="remediation-actions-row">
                          <button
                            type="button"
                            className="btn btn-primary"
                            disabled={isApproving || liveState.approved}
                            onClick={handleApproveLiveRemediation}
                          >
                            {isApproving ? (
                              <>
                                <Loader2 size={13} className="animate-spin" /> Authorizing...
                              </>
                            ) : liveState.approved ? (
                              <>
                                <Check size={13} /> Approved by Operator
                              </>
                            ) : (
                              <>
                                <Check size={13} /> Approve &amp; Execute Allowed Remediation
                              </>
                            )}
                          </button>
                        </div>

                        {liveState.executionResult && (
                          <div className="mt-3 p-3 bg-[#070a12] border border-[#1c2738] rounded text-xs space-y-1">
                            <span className="font-semibold text-gray-300 block">Execution Result:</span>
                            <pre className="terminal-output mono text-[10px]">
                              {JSON.stringify(liveState.executionResult, null, 2)}
                            </pre>
                          </div>
                        )}
                      </div>
                    ) : null
                  ) : (
                    !currentIncident.remediation.abstained && (
                      <div>
                        <div className="remediation-actions-row">
                          <button
                            type="button"
                            className="btn btn-secondary"
                            disabled={demoApproval !== "review"}
                            onClick={() => setDemoApproval("rejected")}
                          >
                            <X size={14} /> Reject Proposal
                          </button>

                          <button
                            type="button"
                            className="btn btn-primary"
                            disabled={demoApproval !== "review"}
                            onClick={() => setDemoApproval("received")}
                          >
                            <Check size={14} /> Approve Remediation (Demo)
                          </button>
                        </div>

                        <div className="p-3 bg-[#070a12] border border-[#1c2738] rounded text-xs space-y-1.5 mt-3">
                          <div className="text-[10px] font-semibold text-gray-500 uppercase tracking-wider">
                            Demo Simulation Status
                          </div>
                          {demoApproval === "review" ? (
                            <div className="text-gray-400 text-[11px]">
                              Awaiting operator decision. In demo mode, approvals are simulated.
                            </div>
                          ) : demoApproval === "rejected" ? (
                            <div className="text-rose-400 text-[11px] flex items-center gap-1.5">
                              <X size={12} />
                              Proposal rejected. No cluster changes scheduled.
                            </div>
                          ) : (
                            <div className="space-y-1 text-mint text-[11px]">
                              <div className="flex items-center gap-1.5">
                                <Check size={12} />
                                <span>Approval Received (Simulated)</span>
                              </div>
                              <span className="text-[10px] text-gray-500 block">
                                Real cluster writes are disabled in demo mode.
                              </span>
                            </div>
                          )}
                        </div>
                      </div>
                    )
                  )}
                </div>
              </section>

              {/* INDEPENDENT VERIFICATION SECTION */}
              <section className="panel" aria-labelledby="verification-heading">
                <div className="panel-header">
                  <div className="panel-header-left">
                    <ShieldAlert size={16} className="panel-header-icon" />
                    <h3 id="verification-heading" className="panel-header-title">
                      Independent Verification &amp; States
                    </h3>
                  </div>
                  <Badge tone="cyan">Multi-Tier State</Badge>
                </div>

                <div className="verification-tiers">
                  <div className="verification-tier-card">
                    <span className="verification-tier-label">1. Proposed State</span>
                    <div className="verification-tier-value text-mint">
                      {consoleMode === "live"
                        ? liveState?.verification?.proposedState || "No mutation proposed"
                        : currentIncident.verification?.proposedState || "Proposal formulated"}
                    </div>
                  </div>

                  <div className="verification-tier-card">
                    <span className="verification-tier-label">2. Executed State</span>
                    <div className="verification-tier-value text-amber">
                      {consoleMode === "live"
                        ? liveState?.verification?.executedState === "executed"
                          ? "Executed"
                          : "Not Executed"
                        : "Not Executed"}
                    </div>
                    <span className="text-[9px] text-gray-500 mono">
                      {consoleMode === "live" ? "Backend Policy Gated" : "Demo Guardrail Active"}
                    </span>
                  </div>

                  <div className="verification-tier-card">
                    <span className="verification-tier-label">3. Verified State</span>
                    <div className="verification-tier-value text-gray-400">
                      {consoleMode === "live"
                        ? liveState?.verification?.verifiedState === "verified"
                          ? "Verified Recovered"
                          : "Unverified"
                        : "Unverified"}
                    </div>
                    <span className="text-[9px] text-gray-500 mono">
                      {consoleMode === "live" ? "Cluster Re-Query Node" : "Awaiting Live Runtime"}
                    </span>
                  </div>
                </div>

                {/* Verification Strategy */}
                {(consoleMode === "live" ? liveState?.verification : currentIncident.verification) && (
                  <div className="verification-strategy-box">
                    <div className="font-semibold text-gray-300 mb-1.5 flex items-center gap-1.5">
                      <ShieldCheck size={13} className="text-mint" />
                      <span>Planned Verification Criteria</span>
                    </div>
                    <ul className="space-y-1 text-gray-400 text-[11px] list-disc list-inside">
                      {(consoleMode === "live"
                        ? liveState?.verification?.verificationStrategy || []
                        : currentIncident.verification?.verificationStrategy || []
                      ).map((strat) => (
                        <li key={strat}>{strat}</li>
                      ))}
                    </ul>
                  </div>
                )}

                <div className="p-3 bg-[#0a0e17] border-t border-[#1c2738] text-[10px] text-gray-500">
                  Independent verification requires re-querying the Kubernetes API through an isolated verification node.
                  Success is never reported without actual verification.
                </div>
              </section>
            </div>
          </div>

          {/* 7. AGENT TRACE & READ-ONLY TOOL OBSERVATIONS */}
          <section className="panel" aria-labelledby="trace-heading">
            <div className="panel-header">
              <div className="panel-header-left">
                <Terminal size={16} className="panel-header-icon" />
                <h3 id="trace-heading" className="panel-header-title">
                  Agent Investigation Trace &amp; Observations
                </h3>
              </div>
              <Badge tone="mint">Read-Only Tool Execution</Badge>
            </div>

            <div className="trace-timeline">
              {traceSteps.length > 0 ? (
                traceSteps.map((step, idx) => {
                  const isStepOpen = !!expandedTrace[step.id];
                  return (
                    <div key={step.id} className="trace-step-item">
                      <div
                        className="trace-step-summary"
                        onClick={() => toggleTrace(step.id)}
                      >
                        <div className="trace-step-left">
                          <div
                            className={`trace-node-circle ${
                              step.status === "complete"
                                ? "complete"
                                : step.status === "blocked"
                                ? "blocked"
                                : step.status === "current"
                                ? "current"
                                : ""
                            }`}
                          >
                            {step.status === "complete" ? (
                              <Check size={11} />
                            ) : step.status === "blocked" ? (
                              <Lock size={10} />
                            ) : (
                              idx + 1
                            )}
                          </div>

                          <div>
                            <div className="font-semibold text-gray-200 flex items-center gap-2">
                              <span>{step.label}</span>
                              {step.tool && (
                                <span className="mono text-[10px] text-cyan bg-[#162030] px-1.5 py-0.5 rounded border border-[#26344b]">
                                  tool:{step.tool}
                                </span>
                              )}
                              {step.readOnly && (
                                <span className="text-[9px] text-gray-400 mono">read-only</span>
                              )}
                            </div>
                            <div className="text-[10px] text-gray-400 mt-0.5">{step.detail}</div>
                          </div>
                        </div>

                        <div className="flex items-center gap-2">
                          {step.evidenceProduced && (
                            <div className="flex items-center gap-1">
                              {step.evidenceProduced.map((eid) => (
                                <span key={eid} className="evidence-id-tag">
                                  {eid}
                                </span>
                              ))}
                            </div>
                          )}
                          <ChevronDown
                            size={14}
                            className={`text-gray-400 transition-transform ${isStepOpen ? "rotate-180" : ""}`}
                          />
                        </div>
                      </div>

                      {isStepOpen && step.toolObservation && (
                        <div className="trace-observation-details">
                          <div className="text-[10px] text-gray-400 mb-1 font-semibold uppercase tracking-wider flex items-center gap-1">
                            <Terminal size={11} className="text-mint" />
                            <span>Tool Observation Output</span>
                          </div>
                          <pre className="terminal-output mono">{step.toolObservation}</pre>
                        </div>
                      )}
                    </div>
                  );
                })
              ) : (
                <div className="p-6 text-center text-gray-500 text-xs">
                  {consoleMode === "live"
                    ? "Investigation trace will populate as the LangGraph agent executes read-only tool steps."
                    : "No trace steps available."}
                </div>
              )}
            </div>

            <div className="p-3 bg-[#0a0e17] border-t border-[#1c2738] text-[10px] text-gray-500">
              Investigation steps represent execution logs from the multi-step LangGraph agent state graph. All investigation probes are strictly read-only.
            </div>
          </section>

          {/* CONTROLLED BENCHMARK & SYSTEM ARCHITECTURE */}
          <section className="benchmark-container" aria-labelledby="benchmark-heading">
            <div className="benchmark-left">
              <Badge tone="mint" className="mb-2">
                <CheckCheck size={12} /> Controlled Evaluation
              </Badge>
              <h3 id="benchmark-heading" className="text-base font-bold text-white tracking-tight">
                Reproducible Failure Scenarios
              </h3>
              <p className="text-xs text-gray-400 mt-1.5 leading-relaxed">
                Evaluated against four controlled, deterministic Kubernetes failure environments in local testbeds.
              </p>
            </div>

            <div className="benchmark-stats-grid">
              <div className="benchmark-stat-card">
                <div className="benchmark-stat-number">4</div>
                <div className="benchmark-stat-label">Reproducible Scenarios</div>
              </div>
              <div className="benchmark-stat-card">
                <div className="benchmark-stat-number">100%</div>
                <div className="benchmark-stat-label">Deterministic Root-Cause Accuracy</div>
              </div>
              <div className="benchmark-stat-card">
                <div className="benchmark-stat-number">100%</div>
                <div className="benchmark-stat-label">LLM + RAG Diagnosis Grounding</div>
              </div>
              <div className="benchmark-stat-card">
                <div className="benchmark-stat-number">100%</div>
                <div className="benchmark-stat-label">Top-1 Runbook Retrieval Accuracy</div>
              </div>
            </div>
          </section>

          {/* SYSTEM ARCHITECTURE PIPELINE */}
          <section className="panel" aria-labelledby="arch-heading">
            <div className="panel-header">
              <div className="panel-header-left">
                <Layers3 size={16} className="panel-header-icon" />
                <h3 id="arch-heading" className="panel-header-title">
                  System Architecture: Evidence → Diagnosis → Safety Gate
                </h3>
              </div>
              <Badge tone="cyan">Full Loop Architecture</Badge>
            </div>

            <div className="architecture-flow">
              {[
                { label: "Kubernetes Cluster", icon: <Box size={14} />, status: "done" },
                { label: "Evidence Collection", icon: <Terminal size={14} />, status: "done" },
                { label: "Evidence Gate", icon: <ShieldCheck size={14} />, status: "done" },
                { label: "RAG / Qdrant", icon: <Database size={14} />, status: "done" },
                { label: "LLM Diagnosis", icon: <Activity size={14} />, status: "done" },
                { label: "Remediation Generator", icon: <GitBranch size={14} />, status: "done" },
                { label: "Safety Gate Policy", icon: <Lock size={14} />, status: "done" },
                { label: "Recovery Verification", icon: <ShieldAlert size={14} />, status: "in-progress" },
              ].map((step, idx) => (
                <div key={step.label} className="flex items-center gap-2">
                  <div className={`architecture-step-node ${step.status === "in-progress" ? "in-progress" : ""}`}>
                    <div className="text-gray-300">{step.icon}</div>
                    <span className="font-medium text-gray-200">{step.label}</span>
                    <span className="text-[8px] mono text-gray-400">
                      {step.status === "done" ? "Operational" : "Agentic Loop"}
                    </span>
                  </div>
                  {idx < 7 && <ArrowRight size={12} className="text-gray-600 flex-shrink-0" />}
                </div>
              ))}
            </div>

            <div className="p-3 bg-[#0a0e17] border-t border-[#1c2738] text-[10px] text-gray-500">
              Operion enforces strict evidence gating before any diagnosis is synthesized or remediation is proposed.
            </div>
          </section>

          {/* FOOTER */}
          <footer className="console-footer">
            <div className="flex items-center gap-2">
              <Layers3 size={14} className="text-mint" />
              <strong className="text-gray-200 tracking-wider">OPERION</strong>
              <span className="text-gray-500">/ Evidence before action.</span>
            </div>
            <div>
              {consoleMode === "live"
                ? "Live Mode · Real Kubernetes Client & LangGraph Engine"
                : "Controlled Benchmark Demo · No Live Cluster Modification"}
            </div>
            <a href="#" className="text-gray-400 hover:text-white transition-colors">
              Back to top ↑
            </a>
          </footer>
        </main>
      </div>
    </div>
  );
}
