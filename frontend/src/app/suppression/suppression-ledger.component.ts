import { CommonModule } from '@angular/common';
import { Component, OnDestroy, OnInit } from '@angular/core';
import { MatExpansionModule } from '@angular/material/expansion';
import { Subject, combineLatest, takeUntil } from 'rxjs';
import { DataService } from '../core/data.service';
import { ReplayStoreService } from '../core/replay-store.service';
import { DailySummary, ReasonCode, Suppression } from '../core/models';

interface ReasonGroup {
  reason_code: ReasonCode;
  label: string;
  count: number;
  rows: Suppression[];
}

const REASON_LABELS: Record<ReasonCode, string> = {
  composition: 'mix changed, not performance',
  small_sample: 'too few trips to be signal',
  child_of_parent: 'folded into a bigger event',
  known_pattern: 'seen before — reclassified, not dismissed',
  target_moved: 'improvement rejected',
};

const REASON_ORDER: ReasonCode[] = [
  'composition',
  'small_sample',
  'child_of_parent',
  'known_pattern',
  'target_moved',
];

@Component({
  selector: 'app-suppression-ledger',
  standalone: true,
  imports: [CommonModule, MatExpansionModule],
  templateUrl: './suppression-ledger.component.html',
  styleUrl: './suppression-ledger.component.scss',
})
export class SuppressionLedgerComponent implements OnInit, OnDestroy {
  summary: DailySummary | null = null;
  groups: ReasonGroup[] = [];
  private destroy$ = new Subject<void>();

  constructor(private data: DataService, private store: ReplayStoreService) {}

  ngOnInit(): void {
    this.store.state$
      .pipe(takeUntil(this.destroy$))
      .subscribe((state) => {
        combineLatest([this.data.summary(state.current_day), this.data.suppressions()])
          .pipe(takeUntil(this.destroy$))
          .subscribe(([summary, suppressions]) => {
            this.summary = summary ?? null;
            const upToDate = suppressions.filter((s) => s.as_of <= state.current_day);
            this.groups = REASON_ORDER.map((code) => ({
              reason_code: code,
              label: REASON_LABELS[code],
              count: upToDate.filter((s) => s.reason_code === code).length,
              rows: upToDate.filter((s) => s.reason_code === code),
            })).filter((g) => g.count > 0);
          });
      });
  }

  ngOnDestroy(): void {
    this.destroy$.next();
    this.destroy$.complete();
  }

  trackByReason(_index: number, group: ReasonGroup): ReasonCode {
    return group.reason_code;
  }

  trackBySuppressionId(_index: number, row: Suppression): string {
    return row.suppression_id;
  }
}
