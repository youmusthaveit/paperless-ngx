export interface DocumentMetadataEntry {
  namespace?: string
  prefix?: string
  key?: string
  value?: string
}

export interface DocumentMetadata {
  original_checksum?: string

  archived_checksum?: string

  original_mime_type?: string

  media_filename?: string

  original_filename?: string

  has_archive_version?: boolean

  original_metadata?: DocumentMetadataEntry[] | null

  archive_metadata?: DocumentMetadataEntry[] | null

  lang?: string
}
