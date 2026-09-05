import { CommonModule } from '@angular/common';
import { Component, OnDestroy, OnInit } from '@angular/core';
import { MatExpansionModule } from '@angular/material/expansion';
import { MatIconModule } from '@angular/material/icon';
import { Subject, combineLatest, takeUntil } from 'rxjs';
import { DataService } from '../core/data.service';
import { ReplayStoreService } from '../core/replay-store.service';
import { FieldTrustComponent } from './field-trust.component';
import { CaseFile, PlaybookEntry } from '../core/models';

@Component({
  selector: 'app-memory-panel',
  standalone: true,
  imports: [CommonModule, MatExpansionModule, MatIconModule, FieldTrustComponent],
  templateUrl: './memory-panel.component.html',
  styleUrl: './memory-panel.component.scss',
})
export class MemoryPanelComponent implements OnInit, OnDestroy {
  cases: CaseFile[] = [];
  playbook: PlaybookEntry[] = [];
  private destroy$ = new Subject<void>();

  constructor(private data: DataService, private store: ReplayStoreService) {}

  ngOnInit(): void {
    combineLatest([this.data.cases(), this.data.playbook(), this.store.state$])
      .pipe(takeUntil(this.destroy$))
      .subscribe(([cases, playbook, state]) => {
        this.cases = cases
          .filter((c) => c.opened_on <= state.current_day)
          .map((c) => {
            const predictions = (c.predictions ?? []).filter((p) => p.made_on <= state.current_day);
            return {
              ...c,
              predictions,
              prediction_record: {
                confirmed: predictions.filter((p) => p.outcome === 'confirmed').length,
                refuted: predictions.filter((p) => p.outcome === 'refuted').length,
                pending: predictions.filter((p) => p.outcome === null).length,
              },
            };
          })
          .sort((a, b) => (a.last_seen_on < b.last_seen_on ? 1 : -1));
        this.playbook = playbook
          .filter((p) => p.promoted_on <= state.current_day)
          .sort((a, b) => (a.promoted_on < b.promoted_on ? 1 : -1));
      });
  }

  ngOnDestroy(): void {
    this.destroy$.next();
    this.destroy$.complete();
  }

  trackByCaseId(_index: number, c: CaseFile): string {
    return c.case_id;
  }
}
