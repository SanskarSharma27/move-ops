import { CommonModule } from '@angular/common';
import { Component, OnInit } from '@angular/core';
import { DataService } from '../core/data.service';
import { ReportCard } from '../core/models';

@Component({
  selector: 'app-report-card',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './report-card.component.html',
  styleUrl: './report-card.component.scss',
})
export class ReportCardComponent implements OnInit {
  card: ReportCard | null = null;
  expanded: string | null = null;

  constructor(private data: DataService) {}

  ngOnInit(): void {
    this.data.reportCard().subscribe((card) => (this.card = card ?? null));
  }

  toggle(key: string): void {
    this.expanded = this.expanded === key ? null : key;
  }
}
