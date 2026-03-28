import { ObjectWithId } from './object-with-id'

export interface S3StorageExport {
  name: string
  size: number
  modified: string
}

export interface S3Storage extends ObjectWithId {
  name: string
  prefix: string
  bucket: string
  endpoint_url: string
  access_key_id: string
  secret_access_key: string
  region_name: string
  default_acl: string
  custom_domain: string
  url_protocol: string
  addressing_style: string
  querystring_auth: boolean
  use_ssl: boolean
}
