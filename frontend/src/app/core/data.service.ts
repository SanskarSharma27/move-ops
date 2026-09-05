import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';
import { environment } from '../../environments/environment';
import { ApiService } from './api.service';
import { MockService } from './mock.service';
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
 * The only thing a component talks to. `environment.useMock` picks fixtures
 * or the live API underneath — nothing above this line ever knows which.
 */
@Injectable({ providedIn: 'root' })
export class DataService {
  private useMock = environment.useMock;

  constructor(private mock: MockService, private api: ApiService) {}

  // ---- replay ----

  replayState(): Observable<ReplayState | undefined> {
    return this.useMock ? this.mock.replayState$() : this.api.replayState$();
  }

  seek(day: string): Observable<ReplayState | undefined> {
    return this.useMock ? this.mock.seek(day) : this.api.seek(day);
  }

  play(): Observable<ReplayState | undefined> {
    return this.useMock ? this.mock.play() : this.api.play();
  }

  pause(): Observable<ReplayState | undefined> {
    return this.useMock ? this.mock.pause() : this.api.pause();
  }

  reset(): Observable<ReplayState | undefined> {
    return this.useMock ? this.mock.reset() : this.api.reset();
  }

  // ---- sense ----

  fieldTrust(): Observable<FieldTrust[]> {
    return this.useMock ? this.mock.fieldTrust() : this.api.fieldTrust();
  }

  signals(day?: string): Observable<Signal[]> {
    return this.useMock ? this.mock.signals(day) : this.api.signals(day);
  }

  baselines(): Observable<EntityBaseline[]> {
    return this.useMock ? this.mock.baselines() : this.api.baselines();
  }

  counterfactuals(): Observable<Counterfactual[]> {
    return this.useMock ? this.mock.counterfactuals() : this.api.counterfactuals();
  }

  // ---- reason ----

  incidents(day?: string): Observable<Incident[]> {
    return this.useMock ? this.mock.incidents(day) : this.api.incidents(day);
  }

  incident(id: string): Observable<Incident | undefined> {
    return this.useMock ? this.mock.incident(id) : this.api.incident(id);
  }

  suppressions(day?: string): Observable<Suppression[]> {
    return this.useMock ? this.mock.suppressions(day) : this.api.suppressions(day);
  }

  summary(day: string): Observable<DailySummary | undefined> {
    return this.useMock ? this.mock.summary(day) : this.api.summary(day);
  }

  whatif(incidentId: string, lever: string, param: string): Observable<WhatifResponse | undefined> {
    return this.useMock ? this.mock.whatif(incidentId, lever, param) : this.api.whatif(incidentId, lever, param);
  }

  // ---- memory ----

  cases(): Observable<CaseFile[]> {
    return this.useMock ? this.mock.cases() : this.api.cases();
  }

  case(id: string): Observable<CaseFile | undefined> {
    return this.useMock ? this.mock.case(id) : this.api.case(id);
  }

  predictions(): Observable<Prediction[]> {
    return this.useMock ? this.mock.predictions() : this.api.predictions();
  }

  playbook(): Observable<PlaybookEntry[]> {
    return this.useMock ? this.mock.playbook() : this.api.playbook();
  }

  reportCard(): Observable<ReportCard | undefined> {
    return this.useMock ? this.mock.reportCard() : this.api.reportCard();
  }

  eval(gate?: string): Observable<EvalResult[]> {
    return this.useMock ? this.mock.eval(gate) : this.api.eval(gate);
  }
}
