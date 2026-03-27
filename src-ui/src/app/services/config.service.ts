import { HttpClient } from '@angular/common/http'
import { Injectable, inject } from '@angular/core'
import { Observable, first, map } from 'rxjs'
import { environment } from 'src/environments/environment'
import { PaperlessConfig } from '../data/paperless-config'

@Injectable({
  providedIn: 'root',
})
export class ConfigService {
  protected http = inject(HttpClient)

  protected baseUrl: string = environment.apiBaseUrl + 'config/'
  protected storageConfigKeys = [
    'documents_storage_type',
    'documents_storage_prefix',
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
    this.storageConfigKeys.forEach((key) => {
      if (config[key] !== undefined) payload[key] = config[key]
    })

    return this.http
      .post<{
        detail: string
      }>(`${this.baseUrl}${config.id}/test-s3-storage/`, payload)
      .pipe(first())
  }
}
