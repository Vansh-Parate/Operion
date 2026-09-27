"use client";
import { useEffect, useState } from "react";
import {
  Activity,
  ArrowDown,
  ArrowRight,
  Box,
  Check,
  CheckCheck,
  ChevronDown,
  Circle,
  Database,
  FileCode2,
  FileText,
  GitBranch,
  Layers3,
  LockKeyhole,
  Network,
  Search,
  ShieldCheck,
  Terminal,
  X,
} from "lucide-react";
import type { AgentStep, EvidenceId, Incident } from "@/lib/types";
type Approval = "review" | "received" | "planned" | "awaiting" | "rejected";
function Badge({
  children,
  tone = "neutral",
}: {
  children: React.ReactNode;
  tone?: "neutral" | "green" | "red" | "amber" | "blue";
}) {
  return <span className={`badge ${tone}`}>{children}</span>;
}
function EvidenceRefs({ ids }: { ids: EvidenceId[] }) {
  return (
    <span className="evidence-refs">
      {ids.map((id) => (
        <a key={id} href={`#evidence-${id}`} title={`View evidence ${id}`}>
          {id}
        </a>
      ))}
    </span>
  );
}
function PanelHeading({
  icon,
  title,
  detail,
}: {
  icon: React.ReactNode;
  title: string;
  detail?: React.ReactNode;
}) {
  return (
    <div className="panel-heading">
      <div>
        {icon}
        <h2>{title}</h2>
      </div>
      {detail}
    </div>
  );
}
export function IncidentConsole({ incidents }: { incidents: Incident[] }) {
  const [selected, setSelected] = useState("INC-002");
  const [approval, setApproval] = useState<Approval>("review");
  const incident =
    incidents.find((item) => item.id === selected) ?? incidents[0];
  useEffect(() => {
    if (approval !== "received" && approval !== "planned") return;
    const timer = setTimeout(
      () => setApproval(approval === "received" ? "planned" : "awaiting"),
      1000,
    );
    return () => clearTimeout(timer);
  }, [approval]);
  const approved = ["received", "planned", "awaiting"].includes(approval);
  const steps: AgentStep[] = [
    {
      id: "collect",
      label: "Collect Evidence",
      status: "complete",
      detail: "Pod, events, logs, config",
    },
    {
      id: "retrieve",
      label: "Retrieve Runbooks",
      status: "complete",
      detail: "MiniLM → Qdrant",
    },
    {
      id: "diagnose",
      label: "Diagnose",
      status: "complete",
      detail: "Evidence-grounded result",
    },
    {
      id: "plan",
      label: "Plan Remediation",
      status: incident.remediation.abstained ? "blocked" : "complete",
      detail: incident.remediation.abstained
        ? "Abstained · insufficient evidence"
        : "Proposal available",
    },
    {
      id: "approve",
      label: "Human Approval",
      status:
        incident.remediation.abstained || approval === "rejected"
          ? "blocked"
          : approved
            ? "complete"
            : "current",
      detail: incident.remediation.abstained
        ? "Safety gate closed"
        : approval === "rejected"
          ? "Proposal rejected"
          : approved
            ? "Demo approval received"
            : "Waiting for review",
    },
    {
      id: "execute",
      label: "Execute",
      status: "pending",
      detail: "Live integration required",
    },
    {
      id: "verify",
      label: "Verify Recovery",
      status: "pending",
      detail: "No recovery verified",
    },
  ];
  return (
    <div className="console-shell">
      <header className="topbar">
        <a href="#" className="brand" aria-label="Operion console">
          <div className="brand-mark">
            <Layers3 size={23} />
          </div>
          <div>
            <strong>OPERION</strong>
            <span>Agentic Production Incident Investigator</span>
          </div>
        </a>
        <div className="header-badges">
          <Badge>Demo Cluster</Badge>
          <span className="cluster-status">
            <span className="status-dot" /> Kubernetes{" "}
            <span className="muted">snapshot</span>
          </span>
          <Badge tone="green">
            <ShieldCheck size={12} /> Evidence Grounded
          </Badge>
        </div>
      </header>
      <main>
        <div className="page-heading">
          <div>
            <div className="eyebrow">OPERATIONS / INCIDENT INVESTIGATION</div>
            <h1>
              Incident workspace<span className="heading-dot">.</span>
            </h1>
            <p>From cluster evidence to a reviewable remediation plan.</p>
          </div>
          <Badge tone="amber">
            <Circle size={8} fill="currentColor" /> Demo Mode
          </Badge>
        </div>
        <nav className="incident-selector" aria-label="Demo incident scenarios">
          {incidents.map((item) => (
            <button
              key={item.id}
              type="button"
              aria-pressed={selected === item.id}
              className={`incident-tab ${selected === item.id ? "active" : ""}`}
              onClick={() => {
                setSelected(item.id);
                setApproval("review");
              }}
            >
              <span className="tab-meta">
                <span>{item.id}</span>
                <span
                  className={`tiny-dot ${item.severity === "Critical" ? "red-dot" : "amber-dot"}`}
                />
              </span>
              <strong>{item.title}</strong>
              <span className="tab-bottom">
                {item.podStatus}
                <ArrowRight size={13} />
              </span>
            </button>
          ))}
        </nav>
        <section className="overview panel" aria-labelledby="overview-title">
          <div className="overview-title">
            <div className="incident-icon">
              <Activity size={22} />
            </div>
            <div>
              <div className="inline-meta">
                <span className="mono muted">{incident.id}</span>
                <Badge
                  tone={incident.severity === "Critical" ? "red" : "amber"}
                >
                  {incident.severity}
                </Badge>
              </div>
              <h2 id="overview-title">{incident.title}</h2>
            </div>
          </div>
          <div className="overview-values">
            {[
              ["Service", incident.service],
              ["Namespace", incident.namespace],
              ["Pod status", incident.podStatus],
              ["Readiness", incident.ready ? "1/1 · Ready" : "0/1 · Not ready"],
              ["Restarts", incident.restarts ?? "Not provided"],
            ].map(([label, value]) => (
              <div key={label}>
                <span className="field-label">{label}</span>
                <span
                  className={`mono ${label === "Readiness" ? (incident.ready ? "text-green" : "text-amber") : label === "Pod status" && value === "Running" ? "text-green" : ""}`}
                >
                  {value}
                </span>
              </div>
            ))}
          </div>
        </section>
        <div className="workspace-grid">
          <div className="left-column">
            <section className="panel evidence-panel">
              <PanelHeading
                icon={<Terminal size={17} />}
                title="Cluster evidence"
                detail={<span className="mono muted">4 sources</span>}
              />
              <div className="evidence-list">
                {incident.evidence.map((item, index) => (
                  <details
                    className="evidence-card"
                    key={`${incident.id}-${item.id}`}
                    id={`evidence-${item.id}`}
                    open={index === 0 || index === 2}
                  >
                    <summary>
                      <span className="evidence-id">{item.id}</span>
                      <span className="evidence-label">
                        <strong>{item.title}</strong>
                        <span>{item.highlight}</span>
                      </span>
                      <ChevronDown size={15} className="chevron" />
                    </summary>
                    <div className="evidence-content">
                      <span className="source-label">{item.source}</span>
                      <pre>{item.content}</pre>
                    </div>
                  </details>
                ))}
              </div>
              <div className="panel-footnote">
                <ShieldCheck size={13} /> Representative excerpts from
                controlled demo scenarios.
              </div>
            </section>
            <section className="panel retrieval-panel">
              <PanelHeading
                icon={<Search size={17} />}
                title="Runbook retrieval"
                detail={<Badge tone="blue">RAG</Badge>}
              />
              <div className="retrieval-tech">
                <Database size={14} />
                <span>MiniLM Embeddings</span>
                <ArrowRight size={13} />
                <span>Qdrant</span>
              </div>
              <div className="runbook-table">
                <div className="table-labels">
                  <span>RANK / RUNBOOK</span>
                  <span>SIMILARITY</span>
                </div>
                {incident.runbooks.map((book) => (
                  <div className="runbook-row" key={book.filename}>
                    <span className="rank">#{book.rank}</span>
                    <FileText size={15} />
                    <span className="mono runbook-name">{book.filename}</span>
                    <strong className="mono text-green">
                      {book.similarity.toFixed(4)}
                    </strong>
                  </div>
                ))}
              </div>
              <p className="panel-footnote">
                Benchmark Top-1 result. Additional ranks were not supplied.
              </p>
            </section>
          </div>
          <div className="right-column">
            <section className="panel diagnosis-panel">
              <PanelHeading
                icon={<ShieldCheck size={17} />}
                title="Evidence-Grounded Diagnosis"
                detail={<Badge tone="green">Grounded</Badge>}
              />
              <div className="diagnosis-body">
                <div className="diagnosis-labels">
                  <span className="field-label">ROOT CAUSE</span>
                  <span className="confidence">
                    {incident.diagnosis.confidence === null
                      ? "Confidence not reported"
                      : `${(incident.diagnosis.confidence * 100).toFixed(0)}% confidence`}
                  </span>
                </div>
                <h3 className="root-cause mono">
                  {incident.diagnosis.rootCause}
                </h3>
                <p>{incident.diagnosis.summary}</p>
                <div className="support-row">
                  <span className="field-label">Supporting evidence</span>
                  <EvidenceRefs ids={incident.diagnosis.supportingEvidence} />
                </div>
                <div className="recommendations">
                  <h4>Recommended actions</h4>
                  {incident.diagnosis.recommendedActions.map(
                    (action, index) => (
                      <div key={action}>
                        <span className="action-number">{index + 1}</span>
                        <p>{action}</p>
                      </div>
                    ),
                  )}
                </div>
              </div>
            </section>
            <section
              className={`panel remediation-panel ${incident.remediation.abstained ? "abstained" : ""}`}
            >
              <PanelHeading
                icon={<GitBranch size={17} />}
                title="Agent remediation"
                detail={<Badge tone="amber">Demo Mode</Badge>}
              />
              <div className="remediation-body">
                <div className="field-label">
                  {incident.remediation.abstained
                    ? "SAFETY DECISION"
                    : "PROPOSED ACTION"}
                </div>
                <h3>{incident.remediation.action}</h3>
                <p>{incident.remediation.reason}</p>
                <div className="support-row">
                  <span className="field-label">Evidence</span>
                  <EvidenceRefs ids={incident.remediation.evidence} />
                </div>
                <div className="safety-notice">
                  <LockKeyhole size={16} />
                  <span>{incident.remediation.safety}</span>
                </div>
                {!incident.remediation.abstained && (
                  <>
                    <div className="approval-buttons">
                      <button
                        className="button secondary"
                        disabled={approval !== "review"}
                        onClick={() => setApproval("rejected")}
                      >
                        <X size={15} /> Reject
                      </button>
                      <button
                        className="button primary"
                        disabled={approval !== "review"}
                        onClick={() => setApproval("received")}
                      >
                        <Check size={16} /> Approve Remediation
                      </button>
                    </div>
                    <div
                      className="approval-status"
                      role="status"
                      aria-live="polite"
                    >
                      {approval === "review" ? (
                        "Demo approval only. No cluster changes will be made."
                      ) : approval === "rejected" ? (
                        "Proposal rejected. No remediation will be executed."
                      ) : (
                        <div className="simulation">
                          <span className="field-label">
                            SIMULATED WORKFLOW
                          </span>
                          {[
                            "Approval Received",
                            "Remediation Planned",
                            "Awaiting Live Agent Integration",
                          ].map((label, index) => {
                            const activeIndex =
                              approval === "received"
                                ? 0
                                : approval === "planned"
                                  ? 1
                                  : 2;
                            return (
                              <span
                                className={
                                  index <= activeIndex
                                    ? "simulation-active"
                                    : "muted"
                                }
                                key={label}
                              >
                                {index <= activeIndex ? (
                                  <Check size={12} />
                                ) : (
                                  <Circle size={10} />
                                )}
                                {label}
                              </span>
                            );
                          })}
                          <span className="muted">
                            Execution and recovery verification have not
                            occurred.
                          </span>
                        </div>
                      )}
                    </div>
                  </>
                )}
              </div>
            </section>
          </div>
        </div>
        <section className="panel trace-panel">
          <PanelHeading
            icon={<GitBranch size={17} />}
            title="Agent workflow"
            detail={
              <span className="muted text-xs">
                Demo trace · future LangGraph state
              </span>
            }
          />
          <ol className="workflow">
            {steps.map((step, index) => (
              <li key={step.id} className={`step ${step.status}`}>
                <div className="step-rail">
                  <span className="step-node">
                    {step.status === "complete" ? (
                      <Check size={15} />
                    ) : step.status === "blocked" ? (
                      <LockKeyhole size={13} />
                    ) : (
                      <span>{index + 1}</span>
                    )}
                  </span>
                  {index < steps.length - 1 && <span className="step-line" />}
                </div>
                <strong>{step.label}</strong>
                <span>{step.detail}</span>
              </li>
            ))}
          </ol>
        </section>
        <section className="benchmark" aria-labelledby="benchmark-title">
          <div className="benchmark-intro">
            <div className="eyebrow">
              <CheckCheck size={14} /> CONTROLLED BENCHMARK
            </div>
            <h2 id="benchmark-title">Small scope. Reproducible results.</h2>
            <p>
              Measured on four reproducible controlled Kubernetes failure
              scenarios.
            </p>
          </div>
          <div className="benchmark-stats">
            {[
              ["4", "Kubernetes incident scenarios"],
              ["100%", "Deterministic root-cause accuracy"],
              ["100%", "LLM + RAG root-cause accuracy"],
              ["100%", "Top-1 runbook retrieval accuracy"],
            ].map(([value, label]) => (
              <div key={label}>
                <strong>{value}</strong>
                <span>{label}</span>
              </div>
            ))}
          </div>
        </section>
        <section className="panel architecture">
          <PanelHeading
            icon={<Network size={17} />}
            title="System architecture"
            detail={
              <span className="muted text-xs">
                Evidence → decision → safety
              </span>
            }
          />
          <div className="architecture-pipeline">
            {[
              "Kubernetes",
              "Evidence Collection",
              "Evidence Processing",
              "RAG / Qdrant",
              "LLM Diagnosis",
              "Remediation Planner",
              "Safety Gate",
              "Recovery Verification",
            ].map((label, index) => (
              <div
                className={`architecture-item ${index > 4 ? "in-progress" : ""}`}
                key={label}
              >
                <div>
                  {index === 0 ? (
                    <Box size={17} />
                  ) : index === 3 ? (
                    <Database size={17} />
                  ) : index === 6 ? (
                    <ShieldCheck size={17} />
                  ) : (
                    <FileCode2 size={17} />
                  )}
                  <span>{label}</span>
                </div>
                {index < 7 && (
                  <ArrowRight className="pipeline-arrow" size={14} />
                )}
              </div>
            ))}
          </div>
          <div className="architecture-note">
            <span className="tiny-dot amber-dot" /> Remediation planner, safety
            gate & recovery verification: Agentic workflow – in progress
          </div>
        </section>
        <footer>
          <span>
            <Layers3 size={14} /> OPERION{" "}
            <span className="muted">/ Evidence before action.</span>
          </span>
          <span>Controlled demo · no live cluster connection</span>
          <a href="#" aria-label="Back to top">
            Back to top <ArrowDown size={12} className="rotate-180" />
          </a>
        </footer>
      </main>
    </div>
  );
}
