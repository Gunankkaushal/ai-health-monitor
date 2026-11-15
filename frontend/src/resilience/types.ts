export type Severity3 = "high" | "medium" | "low";

export interface Edge {
  id: string;
  key: string;
  agent_key: string;
  agent: string;
  tool: string;
  server: string | null;
  kind: "mcp.tool" | "model.inference";
  direction: "read" | "write";
  idempotent: boolean;
  connected: boolean;
  interception: string;
  response: string;
  timestamped: boolean;
  provenance: Record<string, string>;
  journeys: string[];
  critical: boolean;
  owner: string;
  stats?: { calls: number; errors: number; p95_ms: number | null };
  tested?: boolean;
  retry_policy?: string;
  risk_rank?: number;
}

export interface FaultParam {
  name: string;
  label: string;
  unit?: string;
  min?: number;
  max?: number;
  default: number | string;
  options?: string[];
}

export interface Fault {
  id: string;
  name: string;
  category: string;
  interception: string;
  can_target: string[];
  paths: string[];
  requires: string[];
  risk: "low" | "medium" | "high";
  simulates: string;
  mechanism: string;
  reversal: string;
  signals: string[];
  guardrails: string[];
  limitation: string;
  params: FaultParam[];
  enabled?: boolean;
  disabled_reason?: string | null;
  eligible_edges?: number;
  usage?: { experiments: number; journeys: number; last_run: string | null };
}

export interface Applicability {
  fault_id: string;
  name: string;
  risk: string;
  status: "eligible" | "not_applicable" | "unavailable" | "disabled" | "blocked" | "excluded";
  reason: string;
}

export interface SteadyCheck {
  metric: string;
  op: ">=" | "<=";
  threshold: number;
  abort: boolean;
}

export interface MetricMetaR {
  label: string;
  unit: string;
  kind: string;
}

export interface Stage {
  name: string;
  detail: string;
  edges: (Edge & {
    eligible_faults: string[];
    coverage: string;
    last_run_id: number | null;
    last_verdict: string | null;
    edge_score: number | null;
  })[];
}

export interface Journey {
  id: string;
  name: string;
  slo: string;
  owner: string;
  workload: string;
  stages: Stage[];
  critical_edges: string[];
  steady_state: SteadyCheck[];
  score: number | null;
  tested_edges: number;
  testable_edges: number;
  total_edges: number;
  edge_scores: Record<string, number>;
  last_tested: string | null;
  trend: { run_id: number; at: string | null; score: number; run_score: number }[];
  never_tested: number;
  never_tested_critical: number;
  write_edges: number;
}

export interface ApprovalStatus {
  required: string[];
  required_labels: string[];
  missing: string[];
  missing_labels: string[];
  change_ref_required: boolean;
  change_ref_ok: boolean;
  satisfied: boolean;
}

export interface ExperimentSpec {
  name: string;
  journey_id: string;
  hypothesis: string;
  edge_key: string;
  fault_id: string;
  params: Record<string, number | string>;
  share_pct: number;
  selection: "random" | "every_nth" | "burst";
  duration_s: number;
  patient_scope: number[];
  environment?: string;
  steady_state: SteadyCheck[];
  baseline_iterations: number;
  fault_iterations: number;
  recovery_iterations: number;
  change_ref: string;
}

export interface RunSummary {
  id: number;
  label: string;
  experiment_id: number | null;
  suite_run_id: number | null;
  name: string;
  journey_id: string;
  edge_key: string;
  fault_id: string;
  dry_run: boolean;
  status: string;
  phase: string;
  verdict: string | null;
  reason: string;
  score: number | null;
  requested_by: string;
  break_glass: string;
  created_at: string;
  started_at: string | null;
  ended_at: string | null;
}

export interface Experiment extends ExperimentSpec {
  id: number;
  environment: string;
  version: number;
  created_by: string;
  created_at: string;
  updated_at: string;
  archived: boolean;
  approval: ApprovalStatus;
  edge?: Edge;
  fault_name?: string;
  last_run?: RunSummary | null;
  approvals?: { id: number; actor: string; role: string; comment: string; created_at: string; valid: boolean; experiment_version: number }[];
  runs?: RunSummary[];
  fault?: Fault;
}

export interface Iteration {
  i: number;
  ok: boolean | null;
  error: string | null;
  latency_ms: number | null;
  note: string;
  metrics: Record<string, number | null>;
  patient_id: number;
  t_s: number;
  holds: boolean;
}

export interface Finding {
  id: number;
  run_id: number;
  journey_id: string;
  edge_key: string;
  fault_id: string;
  fault_name: string;
  kind: string;
  severity: Severity3;
  title: string;
  what_happened: string;
  guardrail: string;
  owner: string;
  status: "open" | "filed" | "resolved";
  reference: string;
  occurrences: number;
  created_at: string;
  updated_at: string;
}

export interface RunDetail extends RunSummary {
  spec: Experiment;
  events: { t_s: number; at: string; msg: string }[];
  results: {
    checks?: SteadyCheck[];
    iterations?: Record<"baseline" | "fault" | "recovery", Iteration[]>;
    baseline?: Record<string, number | null>;
    steady_state?: (SteadyCheck & { baseline: number | null; fault: number | null; recovery: number | null; held_during_fault: boolean | null })[];
    fault_stats?: {
      target_calls: number;
      injected: number;
      retries: number;
      amplification: number | null;
      contained: boolean;
      errors_beyond_target: Record<string, number>;
      baseline_calls_per_iteration: number;
      fault_calls_per_iteration: number;
    };
    cascade?: { node: string; effect: string; first_seen_s: number | null; hop: string }[];
    recovered?: boolean | null;
    recovery_time_s?: number | null;
    score_breakdown?: { steady_fraction: number; recovered: boolean; rolled_back: boolean; formula: string };
    cleaned_up_readings?: number;
  };
  findings: Finding[];
  edge: Edge | null;
  fault: Fault | null;
  journey: { id: string; name: string; slo: string };
}

export interface Suite {
  id: number;
  name: string;
  journey_id: string;
  experiment_ids: number[];
  gate_mode: "block" | "warn" | "report";
  gate_threshold: number;
  created_by: string;
  last_run: SuiteRun | null;
}

export interface SuiteRun {
  id: number;
  suite_id: number;
  status: string;
  gate: string | null;
  score: number | null;
  run_ids: number[];
  detail: string;
  requested_by: string;
  created_at: string;
  ended_at: string | null;
  runs?: RunSummary[];
}

export interface LabStatus {
  environment: string;
  policy_version: number;
  kill_switch: { engaged: boolean; by?: string; reason?: string; at?: string };
  guardrails: { retries: boolean; max_attempts: number; output_validation: boolean };
  active_runs: number[];
  model_mode: string;
  library_version: string;
  actor: { name: string; role: string; role_label: string; permissions: string[] };
}
