export type MetricKey = "heart_rate" | "blood_pressure" | "temperature" | "spo2" | "weight" | "sleep";
export type Severity = "info" | "warning" | "critical";

export interface MetricMeta {
  key: MetricKey;
  label: string;
  unit: string;
  accepted_units: string[];
  min: number;
  max: number;
}

export interface AgentInfo {
  key: string;
  name: string;
  responsibility: string;
  allowed_tools: string[];
  instructions: string;
}

export interface AppConfig {
  mode: "groq" | "demo";
  demo_mode_setting: boolean;
  groq_key_configured: boolean;
  model: string;
  reason: string;
  max_tool_iterations: number;
  metrics: MetricMeta[];
  agents: AgentInfo[];
  disclaimer: string;
}

export interface Patient {
  id: number;
  name: string;
  age: number;
  sex: string;
  height_cm: number | null;
  notes: string;
  is_synthetic: boolean;
  created_at: string;
  open_alerts?: number;
  critical_alerts?: number;
}

export interface Reading {
  id: number;
  patient_id: number;
  metric: MetricKey;
  label: string;
  value: number;
  value2: number | null;
  unit: string;
  display: string;
  recorded_at: string;
  source: "manual" | "simulator" | "seed" | "experiment";
}

export interface Alert {
  id: number;
  patient_id: number;
  reading_id: number | null;
  rule_id: string;
  metric: MetricKey;
  severity: Severity;
  message: string;
  explanation: string;
  observed: string;
  threshold: string;
  source: string;
  ai_explanation: string | null;
  acknowledged: boolean;
  recorded_at: string;
  origin: "rule";
  is_new?: boolean;
}

export interface PipelineResult {
  run_id: string;
  retrieved_via_mcp: Reading[];
  readings_evaluated: number;
  new_alerts: Alert[];
  rules_version: string;
  warnings?: string[];
}

export interface AddReadingsResponse {
  readings: Reading[];
  pipeline: PipelineResult | null;
  pipeline_error: string | null;
  scenario?: string;
  synthetic?: boolean;
}

export interface McpTool {
  name: string;
  description: string;
  input_schema: Record<string, unknown>;
}

export interface McpServer {
  key: string;
  name: string;
  description: string;
  transport: string;
  command: string;
  status: "starting" | "connected" | "error" | "stopped";
  error: string | null;
  protocol_version: string | null;
  connected_at: string | null;
  last_heartbeat: string | null;
  restarts: number;
  calls: number;
  failures: number;
  tools: McpTool[];
}

export interface McpStatus {
  servers: McpServer[];
  connected: number;
  total_tools: number;
}

export interface Activity {
  id: number;
  run_id: string;
  patient_id: number | null;
  agent: string;
  tool: string | null;
  server: string | null;
  status: "ok" | "error" | "rejected" | "info";
  duration_ms: number | null;
  detail: string;
  created_at: string;
}

export interface AgentResult {
  agent: string;
  text: string;
  mode: string;
  tool_calls: { tool: string; server: string | null; status: string }[];
  error: string | null;
}

export interface ChatResponse {
  run_id: string;
  mode: string;
  mode_label: string;
  routed_to: string[];
  answer: string;
  agents: AgentResult[];
  safety_note: boolean;
}

export interface Trend {
  metric: MetricKey;
  label: string;
  unit: string;
  count: number;
  latest?: number;
  mean?: number;
  min?: number;
  max?: number;
  direction: string;
  slope_per_day?: number;
}

export interface SummaryResponse {
  run_id: string;
  mode: string;
  mode_label: string;
  ai_narrative: string | null;
  error?: string | null;
  summary: {
    patient_name: string;
    days: number;
    markdown: string;
    alert_counts: Record<Severity, number>;
    open_alerts: number;
    trends: Trend[];
  };
}

export interface RuleCondition {
  field: "value" | "value2";
  op: string;
  threshold: number;
}

export interface Rule {
  id: string;
  group: string;
  metric: MetricKey;
  match: string;
  conditions: RuleCondition[];
  severity: Severity;
  message: string;
  explanation: string;
  source: string;
  limitations: string;
}

export interface RulesConfig {
  version: string;
  population: string;
  disclaimer: string;
  rules: Rule[];
  trend_rules: (Omit<Rule, "conditions" | "match"> & { days: number; threshold: number; type: string })[];
}
