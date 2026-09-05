import { CommonModule } from '@angular/common';
import { Component, Input, OnChanges, SimpleChanges } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatIconModule } from '@angular/material/icon';
import { MatSelectModule } from '@angular/material/select';
import { DataService } from '../core/data.service';
import { Counterfactual, Incident, WhatifResponse } from '../core/models';

interface LeverOption {
  key: string;
  lever: string;
  param: string;
  label: string;
}

@Component({
  selector: 'app-whatif',
  standalone: true,
  imports: [CommonModule, FormsModule, MatButtonModule, MatFormFieldModule, MatIconModule, MatSelectModule],
  templateUrl: './whatif.component.html',
  styleUrl: './whatif.component.scss',
})
export class WhatifComponent implements OnChanges {
  @Input() incident: Incident | null = null;

  options: LeverOption[] = [];
  selectedKey: string | null = null;
  result: WhatifResponse | null = null;
  asking = false;

  constructor(private data: DataService) {}

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['incident'] && this.incident) {
      this.result = null;
      this.selectedKey = null;
      const inc = this.incident;
      const candidateIds =
        inc.entity_type === 'office' ? [inc.entity_id, inc.entity_id.split(' / ')[0]] : [inc.entity_id];
      this.data.counterfactuals().subscribe((rows) => {
        this.options = rows
          .filter((c) => candidateIds.includes(c.entity_id))
          .map((c) => this.toOption(c));
      });
    }
  }

  private toOption(c: Counterfactual): LeverOption {
    return {
      key: `${c.lever}::${c.param}`,
      lever: c.lever,
      param: c.param,
      label: `${c.lever} → ${c.param}`,
    };
  }

  ask(): void {
    if (!this.incident || !this.selectedKey) return;
    const opt = this.options.find((o) => o.key === this.selectedKey);
    if (!opt) return;
    this.asking = true;
    this.result = null;
    this.data.whatif(this.incident.incident_id, opt.lever, opt.param).subscribe((res) => {
      this.result = res ?? null;
      this.asking = false;
    });
  }
}
