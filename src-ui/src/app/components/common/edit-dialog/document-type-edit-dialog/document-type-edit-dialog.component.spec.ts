import { provideHttpClient, withInterceptorsFromDi } from '@angular/common/http'
import { provideHttpClientTesting } from '@angular/common/http/testing'
import { ComponentFixture, TestBed } from '@angular/core/testing'
import { FormsModule, ReactiveFormsModule } from '@angular/forms'
import { NgbActiveModal, NgbModule } from '@ng-bootstrap/ng-bootstrap'
import { NgSelectModule } from '@ng-select/ng-select'
import { IfOwnerDirective } from 'src/app/directives/if-owner.directive'
import { IfPermissionsDirective } from 'src/app/directives/if-permissions.directive'
import { SettingsService } from 'src/app/services/settings.service'
import { PermissionsFormComponent } from '../../input/permissions/permissions-form/permissions-form.component'
import { SelectComponent } from '../../input/select/select.component'
import { TextComponent } from '../../input/text/text.component'
import { EditDialogMode } from '../edit-dialog.component'
import { DocumentTypeEditDialogComponent } from './document-type-edit-dialog.component'

describe('DocumentTypeEditDialogComponent', () => {
  let component: DocumentTypeEditDialogComponent
  let settingsService: SettingsService
  let fixture: ComponentFixture<DocumentTypeEditDialogComponent>

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
})
