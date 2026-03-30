import { Component, OnInit, inject } from '@angular/core'
import {
  FormArray,
  FormControl,
  FormGroup,
  FormsModule,
  ReactiveFormsModule,
} from '@angular/forms'
import { NgxBootstrapIconsModule } from 'ngx-bootstrap-icons'
import { EditDialogComponent } from 'src/app/components/common/edit-dialog/edit-dialog.component'
import { CustomField } from 'src/app/data/custom-field'
import { DocumentType } from 'src/app/data/document-type'
import { DEFAULT_MATCHING_ALGORITHM } from 'src/app/data/matching-model'
import { IfOwnerDirective } from 'src/app/directives/if-owner.directive'
import { CustomFieldsService } from 'src/app/services/rest/custom-fields.service'
import { DocumentTypeService } from 'src/app/services/rest/document-type.service'
import { UserService } from 'src/app/services/rest/user.service'
import { SettingsService } from 'src/app/services/settings.service'
import { ToastService } from 'src/app/services/toast.service'
import { CheckComponent } from '../../input/check/check.component'
import { PermissionsFormComponent } from '../../input/permissions/permissions-form/permissions-form.component'
import { SelectComponent } from '../../input/select/select.component'
import { TextComponent } from '../../input/text/text.component'

const XRECHNUNG_FIELD_OPTIONS = [
  { id: 'profile', name: $localize`Profile` },
  { id: 'invoice_number', name: $localize`Invoice number` },
  { id: 'invoice_type_code', name: $localize`Invoice type code` },
  { id: 'issue_date', name: $localize`Issue date` },
  { id: 'due_amount', name: $localize`Due amount` },
  { id: 'grand_total', name: $localize`Grand total` },
  { id: 'tax_total', name: $localize`Tax total` },
  { id: 'currency', name: $localize`Currency` },
  { id: 'buyer_reference', name: $localize`Buyer reference` },
  { id: 'payment_reference', name: $localize`Payment reference` },
  { id: 'payment_terms', name: $localize`Payment terms` },
  { id: 'seller_name', name: $localize`Seller name` },
  { id: 'seller_identifier', name: $localize`Seller identifier` },
  { id: 'seller_tax_identifier', name: $localize`Seller tax identifier` },
  { id: 'seller_email', name: $localize`Seller email` },
  { id: 'buyer_name', name: $localize`Buyer name` },
  { id: 'buyer_identifier', name: $localize`Buyer identifier` },
  { id: 'buyer_tax_identifier', name: $localize`Buyer tax identifier` },
  { id: 'buyer_email', name: $localize`Buyer email` },
]

const XRECHNUNG_CORRESPONDENT_TARGET = 'correspondent'

@Component({
  selector: 'pngx-document-type-edit-dialog',
  templateUrl: './document-type-edit-dialog.component.html',
  styleUrls: ['./document-type-edit-dialog.component.scss'],
  imports: [
    CheckComponent,
    SelectComponent,
    PermissionsFormComponent,
    TextComponent,
    IfOwnerDirective,
    FormsModule,
    ReactiveFormsModule,
    NgxBootstrapIconsModule,
  ],
})
export class DocumentTypeEditDialogComponent
  extends EditDialogComponent<DocumentType>
  implements OnInit
{
  customFields: CustomField[] = []
  xrechnungFieldOptions = XRECHNUNG_FIELD_OPTIONS
  xrechnungMappingsExpanded = false
  private readonly documentTypeService = inject(DocumentTypeService)
  private readonly toastService = inject(ToastService)
  xrechnungActionRunning = false

  constructor() {
    super()
    this.service = this.documentTypeService
    this.userService = inject(UserService)
    this.settingsService = inject(SettingsService)
    inject(CustomFieldsService)
      .listAll()
      .subscribe((result) => (this.customFields = result.results))
  }

  ngOnInit(): void {
    super.ngOnInit()
    this.setXRechnungMappings(
      this.object?.xrechnung_custom_field_mappings ?? []
    )
  }

  getCreateTitle() {
    return $localize`Create new document type`
  }

  getEditTitle() {
    return $localize`Edit document type`
  }

  get xrechnungMappings(): FormArray {
    return this.objectForm.get('xrechnung_custom_field_mappings') as FormArray
  }

  get xrechnungTargetOptions() {
    return [
      { id: XRECHNUNG_CORRESPONDENT_TARGET, name: $localize`Correspondent` },
      ...this.customFields,
    ]
  }

  getForm(): FormGroup {
    return new FormGroup({
      name: new FormControl(''),
      matching_algorithm: new FormControl(DEFAULT_MATCHING_ALGORITHM),
      match: new FormControl(''),
      is_insensitive: new FormControl(true),
      custom_fields: new FormControl([]),
      enable_xrechnung_import: new FormControl(false),
      xrechnung_correspondent_field: new FormControl(null),
      xrechnung_custom_field_mappings: new FormArray([]),
      permissions_form: new FormControl(null),
    })
  }

  addXRechnungMapping() {
    this.xrechnungMappings.push(
      new FormGroup({
        custom_field: new FormControl<number | string | null>(null),
        source: new FormControl(null),
      })
    )
  }

  removeXRechnungMapping(index: number) {
    this.xrechnungMappings.removeAt(index)
  }

  applyXRechnungMappings() {
    if (!this.object?.id || this.xrechnungActionRunning) {
      return
    }

    this.xrechnungActionRunning = true
    this.documentTypeService.applyXRechnungMappings(this.object.id).subscribe({
      next: (result) => {
        this.xrechnungActionRunning = false
        this.toastService.showInfo(
          $localize`Applied E-Rechnung mappings to ${result.updated_documents}:count: document(s).`
        )
      },
      error: (error) => {
        this.xrechnungActionRunning = false
        this.toastService.showError(
          $localize`Error while applying E-Rechnung mappings.`,
          error
        )
      },
    })
  }

  protected override getFormValues(): any {
    const formValues = super.getFormValues()
    const rawMappings = formValues.xrechnung_custom_field_mappings ?? []

    const correspondentMapping = rawMappings.find(
      (mapping) => mapping?.custom_field === XRECHNUNG_CORRESPONDENT_TARGET
    )

    formValues.xrechnung_correspondent_field =
      correspondentMapping?.source ?? null
    formValues.xrechnung_custom_field_mappings = rawMappings.filter(
      (mapping) => typeof mapping?.custom_field === 'number'
    )

    return formValues
  }

  private setXRechnungMappings(
    mappings: DocumentType['xrechnung_custom_field_mappings']
  ) {
    this.xrechnungMappings.clear()
    if (this.object?.xrechnung_correspondent_field) {
      this.xrechnungMappings.push(
        new FormGroup({
          custom_field: new FormControl(XRECHNUNG_CORRESPONDENT_TARGET),
          source: new FormControl(this.object.xrechnung_correspondent_field),
        })
      )
    }
    mappings?.forEach((mapping) =>
      this.xrechnungMappings.push(
        new FormGroup({
          custom_field: new FormControl(mapping.custom_field ?? null),
          source: new FormControl(mapping.source ?? null),
        })
      )
    )
  }
}
