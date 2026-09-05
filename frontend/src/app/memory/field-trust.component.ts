import { CommonModule } from '@angular/common';
import { Component, OnInit } from '@angular/core';
import { MatTooltipModule } from '@angular/material/tooltip';
import { DataService } from '../core/data.service';
import { FieldTrust } from '../core/models';

@Component({
  selector: 'app-field-trust',
  standalone: true,
  imports: [CommonModule, MatTooltipModule],
  templateUrl: './field-trust.component.html',
  styleUrl: './field-trust.component.scss',
})
export class FieldTrustComponent implements OnInit {
  fields: FieldTrust[] = [];

  constructor(private data: DataService) {}

  ngOnInit(): void {
    this.data.fieldTrust().subscribe((rows) => {
      this.fields = [...rows].sort((a, b) => a.trust - b.trust);
    });
  }
}
