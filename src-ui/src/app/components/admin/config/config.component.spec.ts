import {
  ComponentFixture,
  TestBed,
  fakeAsync,
  tick,
} from '@angular/core/testing'

import { provideHttpClient, withInterceptorsFromDi } from '@angular/common/http'
import { provideHttpClientTesting } from '@angular/common/http/testing'
import { FormsModule, ReactiveFormsModule } from '@angular/forms'
import { BrowserModule } from '@angular/platform-browser'
import { NgbModule } from '@ng-bootstrap/ng-bootstrap'
import { NgSelectModule } from '@ng-select/ng-select'
import { NgxBootstrapIconsModule, allIcons } from 'ngx-bootstrap-icons'
import { of, throwError } from 'rxjs'
import { OutputTypeConfig } from 'src/app/data/paperless-config'
import { PaperlessTaskStatus } from 'src/app/data/paperless-task'
import { ConfigService } from 'src/app/services/config.service'
import { PermissionsService } from 'src/app/services/permissions.service'
import { SettingsService } from 'src/app/services/settings.service'
import { TasksService } from 'src/app/services/tasks.service'
import { ToastService } from 'src/app/services/toast.service'
import { FileComponent } from '../../common/input/file/file.component'
import { NumberComponent } from '../../common/input/number/number.component'
import { SelectComponent } from '../../common/input/select/select.component'
import { SwitchComponent } from '../../common/input/switch/switch.component'
import { TextComponent } from '../../common/input/text/text.component'
import { PageHeaderComponent } from '../../common/page-header/page-header.component'
import { ConfigComponent } from './config.component'

describe('ConfigComponent', () => {
  let component: ConfigComponent
  let fixture: ComponentFixture<ConfigComponent>
  let configService: ConfigService
  let tasksService: TasksService
  let toastService: ToastService
  let settingService: SettingsService
  let permissionsService: PermissionsService

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [
        BrowserModule,
        NgbModule,
        NgSelectModule,
        FormsModule,
        ReactiveFormsModule,
        NgxBootstrapIconsModule.pick(allIcons),
        ConfigComponent,
        TextComponent,
        SelectComponent,
        NumberComponent,
        SwitchComponent,
        FileComponent,
        PageHeaderComponent,
      ],
      providers: [
        provideHttpClient(withInterceptorsFromDi()),
        provideHttpClientTesting(),
      ],
    }).compileComponents()

    configService = TestBed.inject(ConfigService)
    tasksService = TestBed.inject(TasksService)
    toastService = TestBed.inject(ToastService)
    settingService = TestBed.inject(SettingsService)
    permissionsService = TestBed.inject(PermissionsService)
    permissionsService.initialize([], {
      id: 1,
      username: 'admin',
      is_superuser: true,
      is_staff: true,
      groups: [],
      user_permissions: [],
    } as any)
    jest.spyOn(tasksService, 'getByTaskName').mockReturnValue(of([]))
    fixture = TestBed.createComponent(ConfigComponent)
    component = fixture.componentInstance
    fixture.detectChanges()
  })

  it('should load config on init, show error if necessary', () => {
    const getSpy = jest.spyOn(configService, 'getConfig')
    const getStoragesSpy = jest.spyOn(configService, 'getS3Storages')
    const errorSpy = jest.spyOn(toastService, 'showError')
    getStoragesSpy.mockReturnValue(of([]))
    getSpy.mockReturnValueOnce(
      throwError(() => new Error('Error getting config'))
    )
    component.ngOnInit()
    expect(getSpy).toHaveBeenCalled()
    expect(errorSpy).toHaveBeenCalled()
    getSpy.mockReturnValueOnce(
      of({ output_type: OutputTypeConfig.PDF_A } as any)
    )
    component.ngOnInit()
    expect(component.initialConfig).toEqual({
      output_type: OutputTypeConfig.PDF_A,
    })
  })

  it('should save config, show error if necessary', () => {
    const saveSpy = jest.spyOn(configService, 'saveConfig')
    const errorSpy = jest.spyOn(toastService, 'showError')
    saveSpy.mockReturnValueOnce(
      throwError(() => new Error('Error saving config'))
    )
    component.saveConfig()
    expect(saveSpy).toHaveBeenCalled()
    expect(errorSpy).toHaveBeenCalled()
    saveSpy.mockReturnValueOnce(
      of({ output_type: OutputTypeConfig.PDF_A } as any)
    )
    component.saveConfig()
    expect(component.initialConfig).toEqual({
      output_type: OutputTypeConfig.PDF_A,
    })
  })

  it('should support discard changes', () => {
    component.initialConfig = { output_type: OutputTypeConfig.PDF_A2 } as any
    component.configForm.patchValue({ output_type: OutputTypeConfig.PDF_A })
    component.discardChanges()
    expect(component.configForm.get('output_type').value).toEqual(
      OutputTypeConfig.PDF_A2
    )
  })

  it('should update remote import connection values in config form', () => {
    component.onRemoteImportConnectionChanged({
      base_url: 'https://remote.example.com',
      api_token: 'secret-token',
    })

    expect(component.configForm.get('remote_import_base_url')?.value).toEqual(
      'https://remote.example.com'
    )
    expect(component.configForm.get('remote_import_api_token')?.value).toEqual(
      'secret-token'
    )

    component.onRemoteImportConnectionChanged({
      base_url: '',
      api_token: '',
    })

    expect(component.configForm.get('remote_import_base_url')?.value).toBeNull()
    expect(
      component.configForm.get('remote_import_api_token')?.value
    ).toBeNull()
  })

  it('should support JSON validation for e.g. user_args', () => {
    component.configForm.patchValue({ user_args: '{ foo bar }' })
    expect(component.errors['user_args']).toEqual('Invalid JSON')
    component.configForm.patchValue({ user_args: '{ "foo": "bar" }' })
    expect(component.errors['user_args']).toBeNull()
  })

  it('should upload file, show error if necessary', () => {
    const uploadSpy = jest.spyOn(configService, 'uploadFile')
    const errorSpy = jest.spyOn(toastService, 'showError')
    uploadSpy.mockReturnValueOnce(
      throwError(() => new Error('Error uploading file'))
    )
    component.uploadFile(new File([], 'test.png'), 'app_logo')
    expect(uploadSpy).toHaveBeenCalled()
    expect(errorSpy).toHaveBeenCalled()
    uploadSpy.mockReturnValueOnce(
      of({ app_logo: 'https://example.com/logo/test.png' } as any)
    )
    component.uploadFile(new File([], 'test.png'), 'app_logo')
    expect(component.initialConfig).toEqual({
      app_logo: 'https://example.com/logo/test.png',
    })
  })

  it('should refresh ui settings after save or upload', () => {
    const saveSpy = jest.spyOn(configService, 'saveConfig')
    const initSpy = jest.spyOn(settingService, 'initializeSettings')
    saveSpy.mockReturnValueOnce(
      of({ output_type: OutputTypeConfig.PDF_A } as any)
    )
    component.saveConfig()
    expect(initSpy).toHaveBeenCalled()

    const uploadSpy = jest.spyOn(configService, 'uploadFile')
    uploadSpy.mockReturnValueOnce(
      of({ app_logo: 'https://example.com/logo/test.png' } as any)
    )
    component.uploadFile(new File([], 'test.png'), 'app_logo')
    expect(initSpy).toHaveBeenCalled()
  })

  it('should reset option to null', () => {
    component.configForm.patchValue({ output_type: OutputTypeConfig.PDF_A })
    expect(component.isSet('output_type')).toBeTruthy()
    component.resetOption('output_type')
    expect(component.configForm.get('output_type').value).toBeNull()
    expect(component.isSet('output_type')).toBeFalsy()
    component.configForm.patchValue({ app_title: 'Test Title' })
    component.resetOption('app_title')
    expect(component.configForm.get('app_title').value).toBeNull()
    component.configForm.patchValue({ barcodes_enabled: true })
    component.resetOption('barcodes_enabled')
    expect(component.configForm.get('barcodes_enabled').value).toBeNull()
  })

  it('should start runtime data reset and poll for task status', fakeAsync(() => {
    jest.spyOn(window, 'confirm').mockReturnValue(true)
    jest
      .spyOn(configService, 'resetRuntimeData')
      .mockReturnValue(
        of({ detail: 'Runtime data reset started.', task_id: 'task-1' })
      )
    jest.spyOn(tasksService, 'getByTaskId').mockReturnValue(
      of([
        {
          task_id: 'task-1',
          status: PaperlessTaskStatus.Complete,
          task_name: 'reset_runtime_data',
          acknowledged: false,
          type: 'manual_task',
          date_created: new Date(),
          result: 'done',
        } as any,
      ])
    )

    component.configForm.patchValue({ id: 1 })
    component.resetRuntimeData()
    tick(0)

    expect(configService.resetRuntimeData).toHaveBeenCalledWith(1)
    expect(component.runtimeResetTask?.status).toEqual(
      PaperlessTaskStatus.Complete
    )
  }))

  it('should start demo crafts data generation and poll for task status', fakeAsync(() => {
    jest.spyOn(window, 'confirm').mockReturnValue(true)
    jest
      .spyOn(configService, 'seedDemoCraftsData')
      .mockReturnValue(
        of({ detail: 'Demo data generation started.', task_id: 'task-2' })
      )
    jest.spyOn(tasksService, 'getByTaskId').mockReturnValue(
      of([
        {
          task_id: 'task-2',
          status: PaperlessTaskStatus.Complete,
          task_name: 'create_demo_crafts_data',
          acknowledged: false,
          type: 'manual_task',
          date_created: new Date(),
          result: 'done',
        } as any,
      ])
    )

    component.configForm.patchValue({ id: 1 })
    component.seedDemoCraftsData()
    tick(0)

    expect(configService.seedDemoCraftsData).toHaveBeenCalledWith(1)
    expect(component.demoCraftsTask?.status).toEqual(
      PaperlessTaskStatus.Complete
    )
  }))

  it('should release runtime data reset lock', () => {
    jest.spyOn(window, 'confirm').mockReturnValue(true)
    jest
      .spyOn(configService, 'releaseRuntimeResetLock')
      .mockReturnValue(
        of({ detail: 'Runtime data reset lock released.', released_tasks: 1 })
      )

    component.configForm.patchValue({ id: 1 })
    component.runtimeResetTask = {
      task_id: 'task-1',
      status: PaperlessTaskStatus.Pending,
      task_name: 'reset_runtime_data',
      acknowledged: false,
      type: 'manual_task',
      date_created: new Date(),
    } as any

    component.releaseRuntimeResetLock()

    expect(configService.releaseRuntimeResetLock).toHaveBeenCalledWith(1)
    expect(component.runtimeResetTask).toBeNull()
  })
})
