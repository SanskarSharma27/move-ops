import { CommonModule } from '@angular/common';
import { Component } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatTooltipModule } from '@angular/material/tooltip';
import { Observable, map } from 'rxjs';
import { ReplayStoreService } from '../core/replay-store.service';
import { ReplayState } from '../core/models';

interface Preset {
  label: string;
  day: string;
  hint: string;
}

@Component({
  selector: 'app-replay-clock',
  standalone: true,
  imports: [CommonModule, MatButtonModule, MatIconModule, MatTooltipModule],
  templateUrl: './replay-clock.component.html',
  styleUrl: './replay-clock.component.scss',
})
export class ReplayClockComponent {
  state$: Observable<ReplayState> = this.store.state$;
  progress$: Observable<number> = this.state$.pipe(map((s) => (s.days_done / 30) * 100));

  presets: Preset[] = [
    { label: '19 Jul', day: '2026-07-19', hint: 'Santa Clara fake improvement' },
    { label: '21 Jul', day: '2026-07-21', hint: 'Cedar Ridge site event' },
    { label: '29 Jul', day: '2026-07-29', hint: 'Memory reclassifies' },
    { label: '31 Jul', day: '2026-07-31', hint: 'Billing close and the memo' },
  ];

  constructor(private store: ReplayStoreService) {}

  toggle(state: ReplayState): void {
    if (state.status === 'playing') this.store.pause();
    else this.store.play();
  }

  step(days: number): void {
    this.store.step(days);
  }

  seek(day: string): void {
    this.store.seek(day);
  }

  reset(): void {
    this.store.reset();
  }

  dayOfMonth(day: string): string {
    return String(Number(day.slice(8, 10)));
  }

  weekday(day: string): string {
    return new Date(day + 'T00:00:00Z').toLocaleDateString('en-US', {
      weekday: 'long',
      timeZone: 'UTC',
    });
  }

  /** Share of raised signals that were explained away rather than escalated. */
  suppressionRate(state: ReplayState): number {
    if (!state.signals_raised) return 0;
    return Math.round((state.suppressed / state.signals_raised) * 100);
  }
}
