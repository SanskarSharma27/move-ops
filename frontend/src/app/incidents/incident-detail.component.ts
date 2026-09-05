import { CommonModule } from '@angular/common';
import { Component, Input, OnChanges, SimpleChanges } from '@angular/core';
import { MatChipsModule } from '@angular/material/chips';
import { MatExpansionModule } from '@angular/material/expansion';
import { MatIconModule } from '@angular/material/icon';
import { MatTooltipModule } from '@angular/material/tooltip';
import { WhatifComponent } from '../whatif/whatif.component';
import { DataService } from '../core/data.service';
import { Hypothesis, Incident } from '../core/models';

type ContextKey = keyof Incident['context'];

@Component({
  selector: 'app-incident-detail',
  standalone: true,
  imports: [CommonModule, MatChipsModule, MatExpansionModule, MatIconModule, MatTooltipModule, WhatifComponent],
  templateUrl: './incident-detail.component.html',
  styleUrl: './incident-detail.component.scss',
})
export class IncidentDetailComponent implements OnChanges {
  @Input() incidentId: string | null = null;

  incident: Incident | null = null;
  loading = false;

  contextBlocks: Array<{ key: ContextKey; icon: string; label: string }> = [
    { key: 'trend', icon: 'timeline', label: 'Trend' },
    { key: 'peer', icon: 'groups', label: 'Peer' },
    { key: 'threshold', icon: 'flag', label: 'Threshold' },
    { key: 'impact', icon: 'groups_2', label: 'Impact' },
  ];

  constructor(private data: DataService) {}

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['incidentId'] && this.incidentId) {
      this.loading = true;
      this.data.incident(this.incidentId).subscribe((inc) => {
        this.incident = inc ?? null;
        this.loading = false;
      });
    }
  }

  trendEntries(inc: Incident): Array<[string, number]> {
    return Object.entries(inc.context.trend.values);
  }

  verdictIcon(verdict: Hypothesis['verdict']): string {
    return verdict === 'refuted' ? 'close' : verdict === 'supported' ? 'check' : 'help';
  }

  refutedCount(inc: Incident): number {
    return (inc.hypotheses ?? []).filter((h) => h.verdict === 'refuted').length;
  }
}
