import { CommonModule } from '@angular/common';
import { Component, Input, OnChanges, SimpleChanges } from '@angular/core';
import { MatChipsModule } from '@angular/material/chips';
import { MatExpansionModule } from '@angular/material/expansion';
import { MatIconModule } from '@angular/material/icon';
import { WhatifComponent } from '../whatif/whatif.component';
import { DataService } from '../core/data.service';
import { Incident } from '../core/models';

@Component({
  selector: 'app-incident-detail',
  standalone: true,
  imports: [CommonModule, MatChipsModule, MatExpansionModule, MatIconModule, WhatifComponent],
  templateUrl: './incident-detail.component.html',
  styleUrl: './incident-detail.component.scss',
})
export class IncidentDetailComponent implements OnChanges {
  @Input() incidentId: string | null = null;

  incident: Incident | null = null;
  loading = false;

  contextOrder: Array<keyof Incident['context']> = ['trend', 'peer', 'threshold', 'impact'];

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
}
