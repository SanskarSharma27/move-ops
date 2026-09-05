import { CommonModule } from '@angular/common';
import { Component, EventEmitter, Input, OnDestroy, OnInit, Output } from '@angular/core';
import { MatIconModule } from '@angular/material/icon';
import { Subject, combineLatest, takeUntil } from 'rxjs';
import { DataService } from '../core/data.service';
import { ReplayStoreService } from '../core/replay-store.service';
import { Incident } from '../core/models';

@Component({
  selector: 'app-incident-list',
  standalone: true,
  imports: [CommonModule, MatIconModule],
  templateUrl: './incident-list.component.html',
  styleUrl: './incident-list.component.scss',
})
export class IncidentListComponent implements OnInit, OnDestroy {
  @Input() selectedId: string | null = null;
  @Output() select = new EventEmitter<string>();

  incidents: Incident[] = [];
  private destroy$ = new Subject<void>();

  constructor(private data: DataService, private store: ReplayStoreService) {}

  ngOnInit(): void {
    combineLatest([this.data.incidents(), this.store.state$])
      .pipe(takeUntil(this.destroy$))
      .subscribe(([incidents, state]) => {
        const visible = incidents
          .filter((i) => i.opened_on <= state.current_day)
          .sort((a, b) => (a.opened_on < b.opened_on ? 1 : -1));
        this.incidents = visible;
        if (!this.selectedId && visible.length) {
          this.select.emit(visible[0].incident_id);
        }
      });
  }

  ngOnDestroy(): void {
    this.destroy$.next();
    this.destroy$.complete();
  }

  pick(id: string): void {
    this.select.emit(id);
  }

  jumpTo(day: string): void {
    this.store.seek(day);
  }

  trackByIncidentId(_index: number, inc: Incident): string {
    return inc.incident_id;
  }
}
