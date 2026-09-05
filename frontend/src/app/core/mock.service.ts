import { Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable, map, forkJoin, of } from 'rxjs';
import {
  CaseFile,
  Counterfactual,
  DailySummary,
  EntityBaseline,
  EvalResult,
  FieldTrust,
  Incident,
  PlaybookEntry,
  Prediction,
  ReplayState,
  ReportCard,
  Signal,
  Suppression,
  WhatifResponse,
} from './models';

const FIRST_DAY = '2026-07-01';
const LAST_DAY = '2026-07-31';

/**
 * Reads contracts/fixtures/*.json (copied into src/assets/fixtures) and assembles
 * the composite endpoints client-side by joining on ids, so no component ever
 * knows it isn't talking to a real backend.
 */
@Injectable({ providedIn: 'root' })
export class MockService {
  private cache = new Map<string, Observable<any>>();
  private replayState: ReplayState = {
    id: 1,
    current_day: FIRST_DAY,
    first_day: FIRST_DAY,
    last_day: LAST_DAY,
    status: 'paused',
    days_done: 0,
    trips_seen: 0,
    signals_raised: 0,
    suppressed: 0,
    incidents_open: 0,
    updated_at: new Date().toISOString(),
  };

  constructor(private http: HttpClient) {}

  private load<T>(name: string): Observable<T[]> {
    if (!this.cache.has(name)) {
      this.cache.set(name, this.http.get<T[]>(`assets/fixtures/${name}.json`));
    }
    return this.cache.get(name)!;
  }

  private upToDate<T extends { as_of?: string; opened_on?: string; created_at?: string }>(
    rows: T[],
    day: string,
    dateField: 'as_of' | 'opened_on' = 'as_of'
  ): T[] {
    return rows.filter((r) => ((r as any)[dateField] as string) <= day);
  }

  // ---- replay ----

  replayState$(): Observable<ReplayState> {
    return of(this.replayState);
  }

  seek(day: string): Observable<ReplayState> {
    return this.recompute(day).pipe(map((state) => (this.replayState = state)));
  }

  play(): Observable<ReplayState> {
    this.replayState = { ...this.replayState, status: 'playing' };
    return of(this.replayState);
  }

  pause(): Observable<ReplayState> {
    this.replayState = { ...this.replayState, status: 'paused' };
    return of(this.replayState);
  }

  reset(): Observable<ReplayState> {
    return this.seek(FIRST_DAY);
  }

  // Fixtures carry no per-day trip volume, so trips_seen is a cosmetic estimate
  // at the documented ~9,700-weekday-trip average (docs/02-demo-cases.md).
  private recompute(day: string): Observable<ReplayState> {
    return forkJoin({
      signals: this.load<Signal>('signal'),
      suppressions: this.load<Suppression>('suppression'),
      incidents: this.load<Incident>('incident'),
    }).pipe(
      map(({ signals, suppressions, incidents }) => {
        const daysDone = this.daysBetween(FIRST_DAY, day);
        const openIncidents = this.upToDate(incidents, day, 'opened_on').filter(
          (i) => i.status !== 'closed'
        );
        return {
          ...this.replayState,
          current_day: day,
          days_done: daysDone,
          trips_seen: Math.max(0, daysDone) * 9700,
          signals_raised: this.upToDate(signals, day).length,
          suppressed: this.upToDate(suppressions, day).length,
          incidents_open: openIncidents.length,
          updated_at: new Date().toISOString(),
        };
      })
    );
  }

  private daysBetween(a: string, b: string): number {
    return Math.round((+new Date(b) - +new Date(a)) / 86400000);
  }

  // ---- sense ----

  fieldTrust(): Observable<FieldTrust[]> {
    return this.load<FieldTrust>('field_trust');
  }

  signals(day?: string): Observable<Signal[]> {
    return this.load<Signal>('signal').pipe(
      map((rows) => (day ? rows.filter((r) => r.as_of === day) : rows))
    );
  }

  signalsUpTo(day: string): Observable<Signal[]> {
    return this.load<Signal>('signal').pipe(map((rows) => this.upToDate(rows, day)));
  }

  baselines(): Observable<EntityBaseline[]> {
    return this.load<EntityBaseline>('entity_baseline');
  }

  counterfactuals(): Observable<Counterfactual[]> {
    return this.load<Counterfactual>('counterfactual');
  }

  // ---- reason ----

  incidents(day?: string): Observable<Incident[]> {
    return this.load<Incident>('incident').pipe(
      map((rows) => (day ? this.upToDate(rows, day, 'opened_on') : rows))
    );
  }

  incident(id: string): Observable<Incident | undefined> {
    return forkJoin({
      incidents: this.load<Incident>('incident'),
      hypotheses: this.load<any>('hypothesis'),
      signals: this.load<Signal>('signal'),
    }).pipe(
      map(({ incidents, hypotheses, signals }) => {
        const inc = incidents.find((i) => i.incident_id === id);
        if (!inc) return undefined;
        const hyps = hypotheses
          .filter((h) => h.incident_id === id)
          .sort((a, b) => a.rank - b.rank);
        const sigs = signals.filter((s) => inc.signal_ids.includes(s.signal_id));
        return { ...inc, hypotheses: hyps, signals: sigs };
      })
    );
  }

  suppressions(day?: string): Observable<Suppression[]> {
    return forkJoin({
      suppressions: this.load<Suppression>('suppression'),
      signals: this.load<Signal>('signal'),
    }).pipe(
      map(({ suppressions, signals }) => {
        const withSignal = suppressions.map((s) => ({
          ...s,
          signal: signals.find((sig) => sig.signal_id === s.signal_id),
        }));
        return day ? withSignal.filter((s) => s.as_of === day) : withSignal;
      })
    );
  }

  suppressionsUpTo(day: string): Observable<Suppression[]> {
    return this.suppressions().pipe(
      map((rows) => this.upToDate(rows, day))
    );
  }

  summary(day: string): Observable<DailySummary> {
    return forkJoin({
      signals: this.load<Signal>('signal'),
      suppressions: this.load<Suppression>('suppression'),
      incidents: this.load<Incident>('incident'),
    }).pipe(
      map(({ signals, suppressions, incidents }) => {
        const daySignals = signals.filter((s) => s.as_of === day);
        const daySuppressions = suppressions.filter((s) => s.as_of === day);
        const dayIncidents = incidents.filter((i) => i.opened_on === day);
        const windowSignals = this.upToDate(signals, day);
        const windowSuppressions = this.upToDate(suppressions, day);
        const windowIncidents = this.upToDate(incidents, day, 'opened_on');
        const byReason: Record<string, number> = {};
        for (const s of daySuppressions) byReason[s.reason_code] = (byReason[s.reason_code] ?? 0) + 1;
        return {
          date: day,
          raw_signals: daySignals.length,
          suppressed: daySuppressions.length,
          incidents: dayIncidents.length,
          by_reason: byReason,
          window: {
            raw_signals: windowSignals.length,
            suppressed: windowSuppressions.length,
            incidents: windowIncidents.length,
          },
        };
      })
    );
  }

  whatif(incidentId: string, lever: string, param: string): Observable<WhatifResponse | undefined> {
    return forkJoin({
      incidents: this.load<Incident>('incident'),
      counterfactuals: this.load<Counterfactual>('counterfactual'),
    }).pipe(
      map(({ incidents, counterfactuals }) => {
        const inc = incidents.find((i) => i.incident_id === incidentId);
        if (!inc) return undefined;
        const cf = counterfactuals.find(
          (c) => c.entity_id === inc.entity_id && c.lever === lever && c.param === param
        );
        if (!cf) return undefined;
        const direction = cf.delta > 0 ? 'improves' : cf.delta < 0 ? 'worsens slightly' : 'does not change';
        const narrative = `${cf.assumption} Projected ${cf.metric} moves from ${cf.baseline_value}% to ${cf.projected_value}% (${direction}).`;
        return { ...cf, narrative };
      })
    );
  }

  // ---- memory ----

  cases(): Observable<CaseFile[]> {
    return forkJoin({
      cases: this.load<CaseFile>('case_file'),
      predictions: this.load<Prediction>('prediction'),
    }).pipe(
      map(({ cases, predictions }) =>
        cases.map((c) => {
          const preds = predictions.filter((p) => p.case_id === c.case_id);
          return {
            ...c,
            predictions: preds,
            prediction_record: {
              confirmed: preds.filter((p) => p.outcome === 'confirmed').length,
              refuted: preds.filter((p) => p.outcome === 'refuted').length,
              pending: preds.filter((p) => p.outcome === null).length,
            },
          };
        })
      )
    );
  }

  case(id: string): Observable<CaseFile | undefined> {
    return this.cases().pipe(map((rows) => rows.find((c) => c.case_id === id)));
  }

  predictions(): Observable<Prediction[]> {
    return this.load<Prediction>('prediction');
  }

  playbook(): Observable<PlaybookEntry[]> {
    return this.load<PlaybookEntry>('playbook');
  }

  playbookUpTo(day: string): Observable<PlaybookEntry[]> {
    return this.playbook().pipe(
      map((rows) => rows.filter((r) => r.promoted_on <= day))
    );
  }

  reportCard(): Observable<ReportCard> {
    return this.load<EvalResult>('eval_result').pipe(
      map((rows) => {
        const byGate = (g: string) => rows.filter((r) => r.gate === g);
        const faithfulness = byGate('faithfulness')[0];
        const detection = byGate('detection');
        const precision = detection.find((d) => d.metric === 'precision');
        const recall = detection.find((d) => d.metric === 'recall');
        const lead = detection.find((d) => d.metric === 'median_lead_days');
        const trace = byGate('trace_schema')[0];
        const behaviour = byGate('behaviour')[0];
        return {
          run_id: faithfulness?.run_id ?? 'seed',
          generated_at: faithfulness?.computed_at ?? '',
          faithfulness: {
            unsourced_number_rate: faithfulness?.value ?? 0,
            statements_checked: (faithfulness?.detail as any)?.statements_checked ?? 0,
            numbers_extracted: (faithfulness?.detail as any)?.numbers_extracted ?? 0,
            passed: faithfulness?.passed ?? false,
          },
          detection: {
            precision: precision?.value ?? 0,
            recall: recall?.value ?? 0,
            median_lead_days: lead?.value ?? 0,
            passed: detection.every((d) => d.passed),
          },
          trace_schema: {
            complete_traces: trace?.value ?? 0,
            incidents: (trace?.detail as any)?.incidents ?? 0,
            complete: (trace?.detail as any)?.complete ?? 0,
            passed: trace?.passed ?? false,
          },
          behaviour: {
            probes_passed: (behaviour?.detail as any)?.total
              ? behaviour!.value
              : 0,
            probes_total: (behaviour?.detail as any)?.total ?? 0,
            failed: (behaviour?.detail as any)?.failed ?? [],
            passed: behaviour?.passed ?? false,
          },
        };
      })
    );
  }

  eval(gate?: string): Observable<EvalResult[]> {
    return this.load<EvalResult>('eval_result').pipe(
      map((rows) => (gate ? rows.filter((r) => r.gate === gate) : rows))
    );
  }
}
