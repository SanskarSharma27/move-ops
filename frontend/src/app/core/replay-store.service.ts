import { Injectable, OnDestroy } from '@angular/core';
import { BehaviorSubject, Subscription, interval } from 'rxjs';
import { DataService } from './data.service';
import { ReplayState } from './models';

const FIRST_DAY = '2026-07-01';
const LAST_DAY = '2026-07-31';
const STEP_MS = 1500;

const INITIAL: ReplayState = {
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
  updated_at: '',
};

/**
 * Single shared clock. Every surface reads `state$` to know "as of what day"
 * to render; only the clock itself calls DataService to advance it.
 */
@Injectable({ providedIn: 'root' })
export class ReplayStoreService implements OnDestroy {
  private stateSubject = new BehaviorSubject<ReplayState>(INITIAL);
  readonly state$ = this.stateSubject.asObservable();
  private timerSub?: Subscription;

  constructor(private data: DataService) {
    this.data.replayState().subscribe((state) => {
      if (state) this.stateSubject.next(state);
    });
  }

  get current(): ReplayState {
    return this.stateSubject.value;
  }

  seek(day: string): void {
    this.data.seek(day).subscribe((state) => {
      if (state) this.stateSubject.next(state);
    });
  }

  play(): void {
    this.data.play().subscribe();
    this.stateSubject.next({ ...this.current, status: 'playing' });
    this.startTimer();
  }

  pause(): void {
    this.data.pause().subscribe();
    this.stateSubject.next({ ...this.current, status: 'paused' });
    this.stopTimer();
  }

  reset(): void {
    this.stopTimer();
    this.data.reset().subscribe((state) => {
      this.stateSubject.next(state ?? INITIAL);
    });
  }

  step(days: number): void {
    const next = this.addDays(this.current.current_day, days);
    if (next < this.current.first_day || next > this.current.last_day) return;
    this.seek(next);
  }

  private startTimer(): void {
    this.stopTimer();
    this.timerSub = interval(STEP_MS).subscribe(() => {
      const next = this.addDays(this.current.current_day, 1);
      if (next > this.current.last_day) {
        this.pause();
        return;
      }
      this.seek(next);
    });
  }

  private stopTimer(): void {
    this.timerSub?.unsubscribe();
    this.timerSub = undefined;
  }

  private addDays(day: string, n: number): string {
    const d = new Date(day + 'T00:00:00Z');
    d.setUTCDate(d.getUTCDate() + n);
    return d.toISOString().slice(0, 10);
  }

  ngOnDestroy(): void {
    this.stopTimer();
  }
}
