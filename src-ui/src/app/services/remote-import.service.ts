import { HttpClient } from '@angular/common/http'
import { inject, Injectable } from '@angular/core'
import { first, Observable } from 'rxjs'
import { environment } from 'src/environments/environment'
import {
  RemoteImportDocumentPage,
  RemoteImportInspection,
  RemoteImportStartResponse,
} from '../data/remote-import'

export interface RemoteImportConnectionPayload {
  base_url: string
  api_token: string
}

export interface RemoteImportBrowsePayload extends RemoteImportConnectionPayload {
  query?: string
  page?: number
  page_size?: number
}

export interface RemoteImportStartPayload extends RemoteImportConnectionPayload {
  query?: string
  selected_document_ids?: number[]
  import_all?: boolean
  create_missing_items?: boolean
  import_notes?: boolean
}

@Injectable({
  providedIn: 'root',
})
export class RemoteImportService {
  private http = inject(HttpClient)
  private baseUrl = `${environment.apiBaseUrl}config/`

  inspect(
    configId: number,
    payload: RemoteImportConnectionPayload
  ): Observable<RemoteImportInspection> {
    return this.http
      .post<RemoteImportInspection>(
        `${this.baseUrl}${configId}/remote-import-inspect/`,
        payload
      )
      .pipe(first())
  }

  browseDocuments(
    configId: number,
    payload: RemoteImportBrowsePayload
  ): Observable<RemoteImportDocumentPage> {
    return this.http
      .post<RemoteImportDocumentPage>(
        `${this.baseUrl}${configId}/remote-import-documents/`,
        payload
      )
      .pipe(first())
  }

  startImport(
    configId: number,
    payload: RemoteImportStartPayload
  ): Observable<RemoteImportStartResponse> {
    return this.http
      .post<RemoteImportStartResponse>(
        `${this.baseUrl}${configId}/remote-import-start/`,
        payload
      )
      .pipe(first())
  }
}
