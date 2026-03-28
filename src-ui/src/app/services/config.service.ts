import { HttpClient, HttpResponse } from '@angular/common/http'
import { Injectable, inject } from '@angular/core'
import { Observable, first, map } from 'rxjs'
import { environment } from 'src/environments/environment'
import { PaperlessConfig } from '../data/paperless-config'
import { S3Storage, S3StorageExport } from '../data/s3-storage'

@Injectable({
  providedIn: 'root',
})
export class ConfigService {
  protected http = inject(HttpClient)

  protected baseUrl: string = environment.apiBaseUrl + 'config/'
  protected s3StorageUrl: string = environment.apiBaseUrl + 's3_storages/'
  protected storageConfigKeys = [
    'documents_storage_type',
    'documents_storage_prefix',
    'documents_s3_storage',
    'documents_s3_bucket',
    'documents_s3_endpoint_url',
    'documents_s3_access_key_id',
    'documents_s3_secret_access_key',
    'documents_s3_region_name',
    'documents_s3_default_acl',
    'documents_s3_custom_domain',
    'documents_s3_url_protocol',
    'documents_s3_addressing_style',
    'documents_s3_querystring_auth',
    'documents_s3_use_ssl',
    'documents_backup_prefix',
    'documents_backup_s3_storage',
    'documents_backup_s3_bucket',
    'documents_backup_s3_endpoint_url',
    'documents_backup_s3_access_key_id',
    'documents_backup_s3_secret_access_key',
    'documents_backup_s3_region_name',
    'documents_backup_s3_default_acl',
    'documents_backup_s3_custom_domain',
    'documents_backup_s3_url_protocol',
    'documents_backup_s3_addressing_style',
    'documents_backup_s3_querystring_auth',
    'documents_backup_s3_use_ssl',
  ]

  getConfig(): Observable<PaperlessConfig> {
    return this.http.get<[PaperlessConfig]>(this.baseUrl).pipe(
      first(),
      map((configs) => configs[0])
    )
  }

  saveConfig(config: PaperlessConfig): Observable<PaperlessConfig> {
    // dont pass string
    if (typeof config.app_logo === 'string') delete config.app_logo
    return this.http
      .patch<PaperlessConfig>(`${this.baseUrl}${config.id}/`, config)
      .pipe(first())
  }

  uploadFile(
    file: File,
    configID: number,
    configKey: string
  ): Observable<PaperlessConfig> {
    let formData = new FormData()
    formData.append(configKey, file, file.name)
    return this.http
      .patch<PaperlessConfig>(`${this.baseUrl}${configID}/`, formData)
      .pipe(first())
  }

  testS3Storage(
    config: Partial<PaperlessConfig>
  ): Observable<{ detail: string }> {
    let payload = {}
    this.storageConfigKeys
      .filter((key) => !key.startsWith('documents_backup_'))
      .forEach((key) => {
        if (config[key] !== undefined) payload[key] = config[key]
      })

    return this.http
      .post<{
        detail: string
      }>(`${this.baseUrl}${config.id}/test-s3-storage/`, payload)
      .pipe(first())
  }

  testS3BackupStorage(
    config: Partial<PaperlessConfig>
  ): Observable<{ detail: string }> {
    let payload = {}
    this.storageConfigKeys
      .filter((key) => key.startsWith('documents_backup_'))
      .forEach((key) => {
        if (config[key] !== undefined) payload[key] = config[key]
      })

    return this.http
      .post<{
        detail: string
      }>(`${this.baseUrl}${config.id}/test-s3-backup-storage/`, payload)
      .pipe(first())
  }

  getS3Storages(): Observable<S3Storage[]> {
    return this.http.get<S3Storage[]>(this.s3StorageUrl).pipe(first())
  }

  saveS3Storage(storage: Partial<S3Storage>): Observable<S3Storage> {
    if (storage.id) {
      return this.http
        .patch<S3Storage>(`${this.s3StorageUrl}${storage.id}/`, storage)
        .pipe(first())
    }

    return this.http.post<S3Storage>(this.s3StorageUrl, storage).pipe(first())
  }

  deleteS3Storage(id: number): Observable<object> {
    return this.http.delete(`${this.s3StorageUrl}${id}/`).pipe(first())
  }

  testS3StorageConfiguration(
    storage: Partial<S3Storage>
  ): Observable<{ detail: string }> {
    return this.http
      .post<{
        detail: string
      }>(`${this.s3StorageUrl}${storage.id}/test-connection/`, storage)
      .pipe(first())
  }

  exportToS3Storage(
    storage: Partial<S3Storage>
  ): Observable<{ detail: string; task_id: string }> {
    return this.http
      .post<{
        detail: string
        task_id: string
      }>(`${this.s3StorageUrl}${storage.id}/export/`, {})
      .pipe(first())
  }

  importFromS3Storage(
    storage: Partial<S3Storage>,
    exportName: string
  ): Observable<{ detail: string; task_id: string }> {
    return this.http
      .post<{
        detail: string
        task_id: string
      }>(`${this.s3StorageUrl}${storage.id}/import/`, {
        export_name: exportName,
      })
      .pipe(first())
  }

  getS3StorageExports(storageId: number): Observable<S3StorageExport[]> {
    return this.http
      .get<S3StorageExport[]>(`${this.s3StorageUrl}${storageId}/exports/`)
      .pipe(first())
  }

  downloadS3StorageExport(
    storageId: number,
    exportName: string
  ): Observable<HttpResponse<Blob>> {
    return this.http
      .post(
        `${this.s3StorageUrl}${storageId}/download-export/`,
        {
          export_name: exportName,
        },
        {
          observe: 'response',
          responseType: 'blob',
        }
      )
      .pipe(first())
  }

  deleteS3StorageExport(
    storageId: number,
    exportName: string
  ): Observable<object> {
    return this.http
      .post(`${this.s3StorageUrl}${storageId}/delete-export/`, {
        export_name: exportName,
      })
      .pipe(first())
  }
}
