import { provideHttpClient, withInterceptorsFromDi } from '@angular/common/http'
import { provideHttpClientTesting } from '@angular/common/http/testing'
import { ComponentFixture, TestBed } from '@angular/core/testing'
import { FormsModule, ReactiveFormsModule } from '@angular/forms'
import { NgbActiveModal, NgbModule } from '@ng-bootstrap/ng-bootstrap'
import { NgSelectModule } from '@ng-select/ng-select'
import { of, throwError } from 'rxjs'
import { IfOwnerDirective } from 'src/app/directives/if-owner.directive'
import { IfPermissionsDirective } from 'src/app/directives/if-permissions.directive'
import { DocumentTypeService } from 'src/app/services/rest/document-type.service'
import { SettingsService } from 'src/app/services/settings.service'
import { ToastService } from 'src/app/services/toast.service'
import { PermissionsFormComponent } from '../../input/permissions/permissions-form/permissions-form.component'
import { SelectComponent } from '../../input/select/select.component'
import { TextComponent } from '../../input/text/text.component'
import { EditDialogMode } from '../edit-dialog.component'
import { DocumentTypeEditDialogComponent } from './document-type-edit-dialog.component'

describe('DocumentTypeEditDialogComponent', () => {
  let component: DocumentTypeEditDialogComponent
  let settingsService: SettingsService
  let fixture: ComponentFixture<DocumentTypeEditDialogComponent>
  let documentTypeService: DocumentTypeService
  let toastService: ToastService

  beforeEach(async () => {
    TestBed.configureTestingModule({
      imports: [
        FormsModule,
        ReactiveFormsModule,
        NgSelectModule,
        NgbModule,
        DocumentTypeEditDialogComponent,
        IfPermissionsDirective,
        IfOwnerDirective,
        SelectComponent,
        TextComponent,
        PermissionsFormComponent,
      ],
      providers: [
        NgbActiveModal,
        provideHttpClient(withInterceptorsFromDi()),
        provideHttpClientTesting(),
      ],
    }).compileComponents()

    fixture = TestBed.createComponent(DocumentTypeEditDialogComponent)
    documentTypeService = TestBed.inject(DocumentTypeService)
    toastService = TestBed.inject(ToastService)
    settingsService = TestBed.inject(SettingsService)
    settingsService.currentUser = { id: 99, username: 'user99' }
    component = fixture.componentInstance

    fixture.detectChanges()
  })

  it('should support create and edit modes', () => {
    component.dialogMode = EditDialogMode.CREATE
    const createTitleSpy = jest.spyOn(component, 'getCreateTitle')
    const editTitleSpy = jest.spyOn(component, 'getEditTitle')
    fixture.detectChanges()
    expect(createTitleSpy).toHaveBeenCalled()
    expect(editTitleSpy).not.toHaveBeenCalled()
    component.dialogMode = EditDialogMode.EDIT
    fixture.detectChanges()
    expect(editTitleSpy).toHaveBeenCalled()
  })

  it('should include custom fields in the form', () => {
    expect(component.objectForm.get('custom_fields')).toBeTruthy()
    expect(component.objectForm.get('custom_fields').value).toEqual([])
  })

  it('should include xrechnung settings in the form', () => {
    expect(component.objectForm.get('enable_xrechnung_import')).toBeTruthy()
    expect(
      component.objectForm.get('xrechnung_correspondent_field')
    ).toBeTruthy()
    expect(
      component.objectForm.get('xrechnung_custom_field_mappings')
    ).toBeTruthy()
  })

  it('should add and remove xrechnung mappings', () => {
    component.addXRechnungMapping()
    expect(component.xrechnungMappings.length).toBe(1)

    component.removeXRechnungMapping(0)
    expect(component.xrechnungMappings.length).toBe(0)
  })

  it('should apply xrechnung mappings for an existing document type', () => {
    component.object = {
      id: 12,
      name: 'XRechnung',
      permissions: {},
    } as any
    component.dialogMode = EditDialogMode.EDIT
    fixture.detectChanges()

    jest.spyOn(documentTypeService, 'applyXRechnungMappings').mockReturnValue(
      of({
        detail: 'Applied XRechnung mappings to 1 document(s).',
        updated_documents: 1,
      })
    )
    const toastSpy = jest.spyOn(toastService, 'showInfo')

    component.applyXRechnungMappings()

    expect(toastSpy).toHaveBeenCalledWith(
      'Applied XRechnung mappings to 1 document(s).'
    )
    expect(component.xrechnungActionRunning).toBe(false)
  })

  it('should show an error toast when applying xrechnung mappings fails', () => {
    component.object = {
      id: 12,
      name: 'XRechnung',
      permissions: {},
    } as any
    component.dialogMode = EditDialogMode.EDIT
    fixture.detectChanges()

    jest
      .spyOn(documentTypeService, 'applyXRechnungMappings')
      .mockReturnValue(throwError(() => new Error('boom')))
    const toastSpy = jest.spyOn(toastService, 'showError')

    component.applyXRechnungMappings()

    expect(toastSpy).toHaveBeenCalled()
    expect(component.xrechnungActionRunning).toBe(false)
  })
})
