import { Injectable } from '@angular/core'
import { Observable } from 'rxjs'
import { DocumentType } from 'src/app/data/document-type'
import { AbstractNameFilterService } from './abstract-name-filter-service'

@Injectable({
  providedIn: 'root',
})
export class DocumentTypeService extends AbstractNameFilterService<DocumentType> {
  constructor() {
    super()
    this.resourceName = 'document_types'
  }

  applyXRechnungMappings(
    documentTypeId: number
  ): Observable<{ detail: string; updated_documents: number }> {
    return this.http.post<{ detail: string; updated_documents: number }>(
      this.getResourceUrl(documentTypeId, 'apply_xrechnung_mappings'),
      {}
    )
  }
}
