import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable } from '@angular/core';
import { Observable, catchError, of } from 'rxjs';
import { environment } from '../../environments/environment';
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

/**
 * Talks to the live FastAPI backend. Endpoints under a service that has not been
 * built yet return 404 — every call here degrades to an empty value instead of
 * throwing, so a missing microservice never crashes the UI.
 */
@Injectable({ providedIn: 'root' })
export class ApiService {
  private base = environment.apiBase;

  constructor(private http: HttpClient) {}

  private empty<T>(fallback: T): Observable<T> {
    return of(fallback);
  }

  private get<T>(path: string, params?: Record<string, string | number | undefined>, fallback?: T): Observable<T> {
    let httpParams = new HttpParams();
    for (const [k, v] of Object.entries(params ?? {})) {
      if (v !== undefined && v !== null && v !== '') httpParams = httpParams.set(k, v);
    }
    return this.http
      .get<T>(`${this.base}${path}`, { params: httpParams })
      .pipe(catchError(() => this.empty(fallback as T)));
  }

  // ---- replay ----

  replayState$(): Observable<ReplayState | undefined> {
    return this.get<ReplayState>('/replay/state', undefined, undefined);
  }

  play(): Observable<ReplayState | undefined> {
    return this.http.post<ReplayState>(`${this.base}/replay/play`, {}).pipe(catchError(() => of(undefined)));
  }

  pause(): Observable<ReplayState | undefined> {
    return this.http.post<ReplayState>(`${this.base}/replay/pause`, {}).pipe(catchError(() => of(undefined)));
  }

  reset(): Observable<ReplayState | undefined> {
    return this.http.post<ReplayState>(`${this.base}/replay/reset`, {}).pipe(catchError(() => of(undefined)));
  }

  seek(day: string): Observable<ReplayState | undefined> {
    return this.http
      .post<ReplayState>(`${this.base}/replay/seek`, {}, { params: { day } })
      .pipe(catchError(() => of(undefined)));
  }

  // ---- sense ----

  fieldTrust(): Observable<FieldTrust[]> {
    return this.get<FieldTrust[]>('/sense/field-trust', undefined, []);
  }

  signals(day?: string): Observable<Signal[]> {
    return this.get<Signal[]>('/sense/signals', { date: day }, []);
  }

  baselines(entityType?: string, entityId?: string, metric?: string): Observable<EntityBaseline[]> {
    return this.get<EntityBaseline[]>(
      '/sense/baselines',
      { entity_type: entityType, entity_id: entityId, metric },
      []
    );
  }

  counterfactuals(entityType?: string, entityId?: string, lever?: string): Observable<Counterfactual[]> {
    return this.get<Counterfactual[]>(
      '/sense/counterfactual',
      { entity_type: entityType, entity_id: entityId, lever },
      []
    );
  }

  // ---- reason ----

  incidents(day?: string, status?: string, persona?: string): Observable<Incident[]> {
    return this.get<Incident[]>('/reason/incidents', { date: day, status, persona }, []);
  }

  incident(id: string): Observable<Incident | undefined> {
    return this.get<Incident | undefined>(`/reason/incidents/${id}`, undefined, undefined);
  }

  suppressions(day?: string, reasonCode?: string): Observable<Suppression[]> {
    return this.get<Suppression[]>('/reason/suppressions', { date: day, reason_code: reasonCode }, []);
  }

  summary(day: string): Observable<DailySummary | undefined> {
    return this.get<DailySummary>('/reason/summary', { date: day }, undefined);
  }

  whatif(incidentId: string, lever: string, param: string): Observable<WhatifResponse | undefined> {
    return this.http
      .post<WhatifResponse>(`${this.base}/reason/whatif`, { incident_id: incidentId, lever, param })
      .pipe(catchError(() => of(undefined)));
  }

  // ---- memory ----

  cases(entityId?: string, status?: string): Observable<CaseFile[]> {
    return this.get<CaseFile[]>('/memory/cases', { entity_id: entityId, status }, []);
  }

  case(id: string): Observable<CaseFile | undefined> {
    return this.get<CaseFile | undefined>(`/memory/cases/${id}`, undefined, undefined);
  }

  predictions(status?: string, caseId?: string): Observable<Prediction[]> {
    return this.get<Prediction[]>('/memory/predictions', { status, case_id: caseId }, []);
  }

  playbook(): Observable<PlaybookEntry[]> {
    return this.get<PlaybookEntry[]>('/memory/playbook', undefined, []);
  }

  reportCard(): Observable<ReportCard | undefined> {
    return this.get<ReportCard>('/memory/report-card', undefined, undefined);
  }

  eval(gate?: string): Observable<EvalResult[]> {
    return this.get<EvalResult[]>('/memory/eval', { gate }, []);
  }
}
