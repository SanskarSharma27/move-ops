import { CommonModule } from '@angular/common';
import { Component, HostListener } from '@angular/core';
import { MatIconModule } from '@angular/material/icon';
import { MatTabsModule } from '@angular/material/tabs';
import { MatTooltipModule } from '@angular/material/tooltip';
import { ReplayClockComponent } from './replay/replay-clock.component';
import { IncidentListComponent } from './incidents/incident-list.component';
import { IncidentDetailComponent } from './incidents/incident-detail.component';
import { SuppressionLedgerComponent } from './suppression/suppression-ledger.component';
import { MemoryPanelComponent } from './memory/memory-panel.component';
import { ReportCardComponent } from './eval/report-card.component';
import { ReplayStoreService } from './core/replay-store.service';

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [
    CommonModule,
    MatIconModule,
    MatTabsModule,
    MatTooltipModule,
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
  state$ = this.store.state$;

  constructor(private store: ReplayStoreService) {}

  selectIncident(id: string): void {
    this.selectedIncidentId = id;
  }

  /**
   * Driving the replay from the keyboard during a live demo beats hunting for
   * a small button on a projector. Ignored while typing in a control.
   */
  @HostListener('document:keydown', ['$event'])
  handleShortcut(event: KeyboardEvent): void {
    const target = event.target as HTMLElement | null;
    if (target && ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName)) return;
    if (event.metaKey || event.ctrlKey || event.altKey) return;

    const state = this.store.current;
    switch (event.key) {
      case ' ':
        event.preventDefault();
        state.status === 'playing' ? this.store.pause() : this.store.play();
        break;
      case 'ArrowRight':
        event.preventDefault();
        this.store.step(1);
        break;
      case 'ArrowLeft':
        event.preventDefault();
        this.store.step(-1);
        break;
      default:
        break;
    }
  }
}
