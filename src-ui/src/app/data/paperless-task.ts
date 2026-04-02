import { Document } from './document'
import { ObjectWithId } from './object-with-id'

export enum PaperlessTaskType {
  Auto = 'auto_task',
  ScheduledTask = 'scheduled_task',
  ManualTask = 'manual_task',
}

export enum PaperlessTaskName {
  ConsumeFile = 'consume_file',
  ImportFile = 'import_file',
  TrainClassifier = 'train_classifier',
  SanityCheck = 'check_sanity',
  IndexOptimize = 'index_optimize',
  LLMIndexUpdate = 'llmindex_update',
  ResetRuntimeData = 'reset_runtime_data',
  CreateDemoCraftsData = 'create_demo_crafts_data',
  EmptyTrash = 'empty_trash',
  ExportS3Storage = 'export_s3_storage',
  ImportS3Storage = 'import_s3_storage',
}

export enum PaperlessTaskStatus {
  Pending = 'PENDING',
  Started = 'STARTED',
  Complete = 'SUCCESS',
  Failed = 'FAILURE',
}

export interface PaperlessTask extends ObjectWithId {
  type: PaperlessTaskType

  status: PaperlessTaskStatus

  acknowledged: boolean

  task_id: string

  task_file_name: string

  task_name: PaperlessTaskName

  date_created: Date

  date_done?: Date

  result?: string

  related_document?: number

  duplicate_documents?: Document[]

  owner?: number
}
