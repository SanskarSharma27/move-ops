import { CommonModule } from '@angular/common';
import { Component } from '@angular/core';
import { MatTabsModule } from '@angular/material/tabs';
import { ReplayClockComponent } from './replay/replay-clock.component';
import { IncidentListComponent } from './incidents/incident-list.component';
import { IncidentDetailComponent } from './incidents/incident-detail.component';
import { SuppressionLedgerComponent } from './suppression/suppression-ledger.component';
import { MemoryPanelComponent } from './memory/memory-panel.component';
import { ReportCardComponent } from './eval/report-card.component';

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [
    CommonModule,
    MatTabsModule,
    ReplayClockComponent,
    IncidentListComponent,
    IncidentDetailComponent,
    SuppressionLedgerComponent,
    MemoryPanelComponent,
    ReportCardComponent,
  ],
  templateUrl: './app.component.html',
  styleUrl: './app.component.scss',
})
export class AppComponent {
  selectedIncidentId: string | null = null;

  selectIncident(id: string): void {
    this.selectedIncidentId = id;
  }
}
