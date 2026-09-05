// Mirrors contracts/api.md and contracts/fixtures/*.json shapes exactly.

export type EntityType = 'vendor' | 'office' | 'business_unit' | 'contract' | 'shift';
export type Metric = 'ota15' | 'ack_minutes' | 'noshow_pct' | 'seat_util' | 'cost_per_trip' | 'sev1_count';
export type Detector =
  | 'punctuality_drop'
  | 'metric_integrity'
  | 'alert_ack_sla'
  | 'safety_cluster'
  | 'escort_breach'
  | 'noshow_spike'
  | 'billing_anomaly'
  | 'vendor_chronic';
export type Severity = 'critical' | 'high' | 'medium' | 'low';
export type Direction = 'worse' | 'better';
export type ReasonCode = 'composition' | 'small_sample' | 'child_of_parent' | 'known_pattern' | 'target_moved';
export type HypothesisVerdict = 'supported' | 'refuted' | 'inconclusive';
export type FieldTrustVerdict = 'trusted' | 'degraded' | 'quarantined';
export type IncidentStatus = 'open' | 'recurring' | 'structural' | 'closed';
export type CaseStatus = 'open' | 'recurring' | 'structural' | 'resolved';
export type PredictionOutcome = 'confirmed' | 'refuted' | 'unverifiable';
export type Confidence = 'exact' | 'estimated' | 'weak';
export type Persona = 'transport_manager' | 'transport_head' | 'line_manager';
export type Gate = 'faithfulness' | 'detection' | 'trace_schema' | 'behaviour';

export interface Evidence {
  claim: string;
  value: number;
  unit: string;
  source: string;
}

export interface ReplayState {
  id: number;
  current_day: string;
  first_day: string;
  last_day: string;
  status: 'playing' | 'paused';
  days_done: number;
  trips_seen: number;
  signals_raised: number;
  suppressed: number;
  incidents_open: number;
  updated_at: string;
}

export interface FieldTrust {
  table_name: string;
  column_name: string;
  verdict: FieldTrustVerdict;
  trust: number;
  test_name: string;
  evidence: string;
  computed_on: string;
}

export interface Signal {
  signal_id: string;
  as_of: string;
  detector: Detector;
  severity: Severity;
  entity_type: EntityType;
  entity_id: string;
  parent_id: string | null;
  metric: Metric;
  value: number;
  baseline: number;
  z: number;
  n: number;
  direction: Direction;
  headline: string;
  evidence: Evidence[];
  created_at: string;
}

export interface EntityBaseline {
  as_of: string;
  entity_type: EntityType;
  entity_id: string;
  parent_id: string | null;
  metric: Metric;
  value: number;
  n: number;
  baseline_mean: number;
  baseline_sd: number;
  baseline_n: number;
  z: number;
  peer_group: string;
  peer_median: number;
  peer_pctile: number;
  slope_28d: number;
}

export interface Counterfactual {
  as_of: string;
  entity_type: EntityType;
  entity_id: string;
  lever: string;
  param: string;
  metric: Metric;
  baseline_value: number;
  projected_value: number;
  delta: number;
  n: number;
  assumption: string;
  confidence: Confidence;
}

export interface WhatifResponse extends Counterfactual {
  narrative: string;
}

export interface ContextBlock {
  statement: string;
  [key: string]: unknown;
}

export interface IncidentContext {
  trend: ContextBlock & { values: Record<string, number>; unit: string };
  peer: ContextBlock & { peer_group: string; peer_median: number; pctile: number; unit?: string };
  threshold: ContextBlock & { target: number; actual: number; unit: string };
  impact: ContextBlock & { value: number; unit: string; secondary?: { value: number; unit: string } };
}

export interface Recommendation {
  action: string;
  owner: string;
  due: string;
  expected_effect: string;
  confidence: Confidence;
  assumption: string;
}

export interface Hypothesis {
  hypothesis_id: string;
  incident_id: string;
  name: string;
  statement: string;
  verdict: HypothesisVerdict;
  test_sql: string;
  result: Record<string, unknown>;
  reasoning: string;
  rank: number;
}

export interface Incident {
  incident_id: string;
  opened_on: string;
  status: IncidentStatus;
  severity: Severity;
  entity_type: EntityType;
  entity_id: string;
  detector: Detector;
  headline: string;
  narrative: string;
  context: IncidentContext;
  signal_ids: string[];
  recommendation: Recommendation;
  persona: Persona;
  created_at: string;
  hypotheses?: Hypothesis[];
  signals?: Signal[];
}

export interface Suppression {
  suppression_id: string;
  as_of: string;
  signal_id: string;
  reason_code: ReasonCode;
  explanation: string;
  evidence: Evidence[];
  parent_incident_id: string | null;
  created_at: string;
  signal?: Signal;
}

export interface ReasonSummary {
  [reason: string]: number;
}

export interface DailySummary {
  date: string;
  raw_signals: number;
  suppressed: number;
  incidents: number;
  by_reason: ReasonSummary;
  window: { raw_signals: number; suppressed: number; incidents: number };
}

export interface Prediction {
  prediction_id: string;
  case_id: string;
  made_on: string;
  verify_on: string;
  statement: string;
  metric: Metric;
  entity_type: EntityType;
  entity_id: string;
  predicate: 'lt' | 'gt';
  threshold: number;
  threshold_hi: number | null;
  outcome: PredictionOutcome | null;
  observed: number | null;
  verified_on: string | null;
}

export interface PredictionRecord {
  confirmed: number;
  refuted: number;
  pending: number;
}

export interface CaseFile {
  case_id: string;
  signature: string;
  entity_type: EntityType;
  entity_id: string;
  opened_on: string;
  last_seen_on: string;
  occurrences: number;
  status: CaseStatus;
  incident_ids: string[];
  diagnosis: string;
  created_at: string;
  updated_at: string;
  predictions?: Prediction[];
  prediction_record?: PredictionRecord;
}

export interface PlaybookEntry {
  playbook_id: string;
  signature: string;
  action: string;
  n_cases: number;
  n_confirmed: number;
  confidence: number;
  evidence: { case_ids: string[]; prediction_ids: string[]; note: string };
  promoted_on: string;
  updated_on: string;
}

export interface GateDetail {
  [key: string]: unknown;
}

export interface EvalResult {
  run_id: string;
  gate: Gate;
  metric: string;
  value: number;
  passed: boolean;
  detail: GateDetail;
  computed_at: string;
}

export interface ReportCard {
  run_id: string;
  generated_at: string;
  faithfulness: { unsourced_number_rate: number; statements_checked: number; numbers_extracted: number; passed: boolean };
  detection: { precision: number; recall: number; median_lead_days: number; passed: boolean };
  trace_schema: { complete_traces: number; incidents: number; complete: number; passed: boolean };
  behaviour: { probes_passed: number; probes_total: number; failed: string[]; passed: boolean };
}
