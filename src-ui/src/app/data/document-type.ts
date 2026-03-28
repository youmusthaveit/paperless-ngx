import { MatchingModel } from './matching-model'

export interface XRechnungCustomFieldMapping {
  custom_field: number
  source: string
}

export interface DocumentType extends MatchingModel {
  custom_fields?: number[]
  enable_xrechnung_import?: boolean
  xrechnung_correspondent_field?: string | null
  xrechnung_custom_field_mappings?: XRechnungCustomFieldMapping[]
}
