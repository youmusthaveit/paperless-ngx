import { ObjectWithId } from './object-with-id'
import { WorkflowAction } from './workflow-action'
import { WorkflowTrigger } from './workflow-trigger'

export interface Workflow extends ObjectWithId {
  name: string

  order: number

  enabled: boolean

  triggers: WorkflowTrigger[]

  actions: WorkflowAction[]
}

export interface ManualWorkflowSummary extends ObjectWithId {
  name: string
  order: number
}

export interface WorkflowRunStep extends ObjectWithId {
  order: number
  status: string
  started_at?: string
  finished_at?: string
  message?: string
  error?: string
  request_payload?: object
  response_payload?: object
  action_type?: number
}

export interface WorkflowRunHistory extends ObjectWithId {
  workflow_id: number
  workflow_name: string
  type?: number
  trigger_type_display?: string
  status: string
  status_display?: string
  run_at: string
  started_at?: string
  finished_at?: string
  current_step_order?: number
  message?: string
  error?: string
  started_by?: {
    id: number
    username: string
  }
  steps: WorkflowRunStep[]
}
