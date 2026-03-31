export interface RemoteImportNamedObject {
  id: number
  name: string
}

export interface RemoteImportCustomFieldPreview {
  field_id: number
  field_name: string
  data_type: string
  value: unknown
}

export interface RemoteImportDocumentPreview {
  id: number
  document_url?: string
  title: string
  created?: string
  original_file_name?: string
  archive_serial_number?: number
  correspondent?: RemoteImportNamedObject
  document_type?: RemoteImportNamedObject
  storage_path?: RemoteImportNamedObject
  tags: RemoteImportNamedObject[]
  custom_fields: RemoteImportCustomFieldPreview[]
}

export interface RemoteImportMappingState {
  total: number
  matched: number
  missing: Array<{ id: number; name: string; data_type?: string }>
}

export interface RemoteImportInspection {
  remote: {
    base_url: string
    app_title: string
    document_count: number
    correspondent_count: number
    tag_count: number
    document_type_count: number
    storage_path_count: number
    custom_field_count: number
  }
  mappings: {
    correspondents: RemoteImportMappingState
    tags: RemoteImportMappingState
    document_types: RemoteImportMappingState
    storage_paths: RemoteImportMappingState
    custom_fields: RemoteImportMappingState
  }
}

export interface RemoteImportDocumentPage {
  count: number
  next?: string
  previous?: string
  all: number[]
  results: RemoteImportDocumentPreview[]
}

export interface RemoteImportStartResponse {
  task_id: string
}
