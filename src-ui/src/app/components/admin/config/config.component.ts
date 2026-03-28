import { AsyncPipe } from '@angular/common'
import { HttpResponse } from '@angular/common/http'
import {
  Component,
  OnDestroy,
  OnInit,
  TemplateRef,
  inject,
} from '@angular/core'
import {
  AbstractControl,
  FormArray,
  FormControl,
  FormGroup,
  FormsModule,
  ReactiveFormsModule,
} from '@angular/forms'
import { NgbModal, NgbModalRef, NgbNavModule } from '@ng-bootstrap/ng-bootstrap'
import { DirtyComponent, dirtyCheck } from '@ngneat/dirty-check-forms'
import { saveAs } from 'file-saver'
import { NgxBootstrapIconsModule } from 'ngx-bootstrap-icons'
import {
  BehaviorSubject,
  Observable,
  Subscription,
  first,
  forkJoin,
  switchMap,
  takeUntil,
  timer,
} from 'rxjs'
import {
  BackupScheduleJob,
  ConfigCategory,
  ConfigOption,
  ConfigOptionType,
  PaperlessConfig,
  PaperlessConfigOptions,
} from 'src/app/data/paperless-config'
import {
  PaperlessTask,
  PaperlessTaskName,
  PaperlessTaskStatus,
  PaperlessTaskType,
} from 'src/app/data/paperless-task'
import { S3Storage, S3StorageExport } from 'src/app/data/s3-storage'
import { ConfigService } from 'src/app/services/config.service'
import { SettingsService } from 'src/app/services/settings.service'
import { TasksService } from 'src/app/services/tasks.service'
import { ToastService } from 'src/app/services/toast.service'
import { FileComponent } from '../../common/input/file/file.component'
import { NumberComponent } from '../../common/input/number/number.component'
import { PasswordComponent } from '../../common/input/password/password.component'
import { SelectComponent } from '../../common/input/select/select.component'
import { SwitchComponent } from '../../common/input/switch/switch.component'
import { TextComponent } from '../../common/input/text/text.component'
import { PageHeaderComponent } from '../../common/page-header/page-header.component'
import { LoadingComponentWithPermissions } from '../../loading-component/loading.component'

@Component({
  selector: 'pngx-config',
  templateUrl: './config.component.html',
  styleUrl: './config.component.scss',
  imports: [
    PageHeaderComponent,
    SelectComponent,
    SwitchComponent,
    TextComponent,
    NumberComponent,
    FileComponent,
    PasswordComponent,
    AsyncPipe,
    NgbNavModule,
    FormsModule,
    ReactiveFormsModule,
    NgxBootstrapIconsModule,
  ],
})
export class ConfigComponent
  extends LoadingComponentWithPermissions
  implements OnInit, OnDestroy, DirtyComponent
{
  private configService = inject(ConfigService)
  private toastService = inject(ToastService)
  private settingsService = inject(SettingsService)
  private tasksService = inject(TasksService)
  private modalService = inject(NgbModal)

  public readonly ConfigOptionType = ConfigOptionType
  public readonly ConfigCategory = ConfigCategory

  // generated dynamically
  public configForm = new FormGroup({})

  public errors = {}
  public testingStorage = false
  public runningS3Transfer = false
  public downloadingS3ExportKey: string | null = null
  public deletingS3ExportKey: string | null = null
  public savingS3Storage = false
  public s3Storages: S3Storage[] = []
  public expandedExportsStorageId: number | null = null
  public loadingExportsStorageId: number | null = null
  public s3StorageExports: Record<number, S3StorageExport[]> = {}
  public s3TransferTasks: Record<number, PaperlessTask> = {}
  public s3StorageSelectItems: Array<{ id: number; name: string }> = []
  public backupJobsForm = new FormArray<FormGroup>([])
  private s3StorageModalRef: NgbModalRef | null = null
  private s3TransferPollingSubscriptions: Record<number, Subscription> = {}
  public s3StorageForm = new FormGroup({
    id: new FormControl<number | null>(null),
    name: new FormControl<string | null>(null),
    prefix: new FormControl<string | null>(null),
    bucket: new FormControl<string | null>(null),
    endpoint_url: new FormControl<string | null>(null),
    access_key_id: new FormControl<string | null>(null),
    secret_access_key: new FormControl<string | null>(null),
    region_name: new FormControl<string | null>(null),
    default_acl: new FormControl<string | null>(null),
    custom_domain: new FormControl<string | null>(null),
    url_protocol: new FormControl<string | null>(null),
    addressing_style: new FormControl<string | null>(null),
    querystring_auth: new FormControl<boolean | null>(null),
    use_ssl: new FormControl<boolean | null>(true),
  })

  get optionCategories(): string[] {
    return Object.values(ConfigCategory)
  }

  getCategoryOptions(category: string): ConfigOption[] {
    return PaperlessConfigOptions.filter((o) => o.category === category)
  }

  getPrimaryStorageOptions(): ConfigOption[] {
    return this.getCategoryOptions(ConfigCategory.Storage)
  }

  getBackupOptions(): ConfigOption[] {
    return this.getCategoryOptions(ConfigCategory.Backup)
  }

  initialConfig: PaperlessConfig
  store: BehaviorSubject<any>
  storeSub: Subscription
  isDirty$: Observable<boolean>

  constructor() {
    super()
    this.configForm.addControl('id', new FormControl())
    this.configForm.addControl(
      'documents_backup_schedule_jobs',
      new FormControl<BackupScheduleJob[]>([])
    )
    PaperlessConfigOptions.forEach((option) => {
      this.configForm.addControl(option.key, new FormControl())
    })
    this.resetS3StorageForm()
    this.backupJobsForm.valueChanges
      .pipe(takeUntil(this.unsubscribeNotifier))
      .subscribe((jobs) => {
        this.setBackupScheduleJobsValue(jobs as BackupScheduleJob[])
      })
  }

  ngOnInit(): void {
    forkJoin({
      config: this.configService.getConfig(),
      s3Storages: this.configService.getS3Storages(),
    })
      .pipe(takeUntil(this.unsubscribeNotifier))
      .subscribe({
        next: ({ config, s3Storages }) => {
          this.loading = false
          this.s3Storages = s3Storages
          this.refreshS3StorageChoices()
          this.initialize(config)
        },
        error: (e) => {
          this.loading = false
          this.toastService.showError($localize`Error retrieving config`, e)
        },
      })

    // validate JSON inputs
    PaperlessConfigOptions.filter(
      (o) => o.type === ConfigOptionType.JSON
    ).forEach((option) => {
      this.configForm
        .get(option.key)
        .addValidators((control: AbstractControl) => {
          if (!control.value || control.value.toString().length === 0)
            return null
          try {
            JSON.parse(control.value)
          } catch (e) {
            return [
              {
                user_args: e,
              },
            ]
          }
          return null
        })
      this.configForm.get(option.key).statusChanges.subscribe((status) => {
        this.errors[option.key] =
          status === 'INVALID' ? $localize`Invalid JSON` : null
      })
      this.configForm.get(option.key).updateValueAndValidity()
    })
  }

  ngOnDestroy(): void {
    Object.values(this.s3TransferPollingSubscriptions).forEach((subscription) =>
      subscription.unsubscribe()
    )
    this.unsubscribeNotifier.next(true)
    this.unsubscribeNotifier.complete()
  }

  private initialize(config: PaperlessConfig) {
    if (!this.store) {
      this.store = new BehaviorSubject(config)

      this.store
        .asObservable()
        .pipe(takeUntil(this.unsubscribeNotifier))
        .subscribe((state) => {
          this.configForm.patchValue(state, { emitEvent: false })
        })

      this.isDirty$ = dirtyCheck(this.configForm, this.store.asObservable())
    }
    this.configForm.patchValue(config)
    this.setBackupJobs(config.documents_backup_schedule_jobs ?? [])

    this.initialConfig = config
  }

  getDocsUrl(key: string) {
    return `https://docs.paperless-ngx.com/configuration/#${key}`
  }

  private refreshS3StorageChoices() {
    this.s3StorageSelectItems = this.s3Storages.map((storage) => ({
      id: storage.id,
      name: storage.name,
    }))

    PaperlessConfigOptions.forEach((option) => {
      if (
        option.key === 'documents_s3_storage' ||
        option.key === 'documents_backup_s3_storage'
      ) {
        option.choices = this.s3StorageSelectItems
      }
    })
  }

  public saveConfig() {
    this.loading = true
    this.configService
      .saveConfig(this.configForm.value as PaperlessConfig)
      .pipe(takeUntil(this.unsubscribeNotifier), first())
      .subscribe({
        next: (config) => {
          this.loading = false
          this.initialize(config)
          this.store.next(config)
          this.settingsService.initializeSettings().subscribe()
          this.toastService.showInfo($localize`Configuration updated`)
        },
        error: (e) => {
          this.loading = false
          this.toastService.showError(
            $localize`An error occurred updating configuration`,
            e
          )
        },
      })
  }

  public discardChanges() {
    this.configForm.reset(this.initialConfig)
    this.setBackupJobs(this.initialConfig?.documents_backup_schedule_jobs ?? [])
  }

  public uploadFile(file: File, key: string) {
    this.loading = true
    this.configService
      .uploadFile(file, this.configForm.value['id'], key)
      .pipe(takeUntil(this.unsubscribeNotifier), first())
      .subscribe({
        next: (config) => {
          this.loading = false
          this.initialize(config)
          this.store.next(config)
          this.settingsService.initializeSettings().subscribe()
          this.toastService.showInfo($localize`File successfully updated`)
        },
        error: (e) => {
          this.loading = false
          this.toastService.showError(
            $localize`An error occurred uploading file`,
            e
          )
        },
      })
  }

  public isSet(key: string): boolean {
    return this.configForm.get(key).value != null
  }

  public resetOption(key: string) {
    this.configForm.get(key).setValue(null)
  }

  get backupJobControls(): FormGroup[] {
    return this.backupJobsForm.controls as FormGroup[]
  }

  private createBackupJobForm(job?: Partial<BackupScheduleJob>): FormGroup {
    return new FormGroup({
      name: new FormControl(job?.name ?? ''),
      enabled: new FormControl(job?.enabled ?? true),
      storage: new FormControl<number | null>(job?.storage ?? null),
      frequency_days: new FormControl<number | null>(job?.frequency_days ?? 1),
      hour: new FormControl<number | null>(job?.hour ?? 2),
      minute: new FormControl<number | null>(job?.minute ?? 0),
      retain_count: new FormControl<number | null>(job?.retain_count ?? 7),
      last_run: new FormControl<string | null>(job?.last_run ?? null),
    })
  }

  private setBackupJobs(jobs: BackupScheduleJob[]) {
    while (this.backupJobsForm.length > 0) {
      this.backupJobsForm.removeAt(0, { emitEvent: false })
    }
    jobs.forEach((job) => {
      this.backupJobsForm.push(this.createBackupJobForm(job))
    })
    this.setBackupScheduleJobsValue(jobs, false)
  }

  public addBackupJob() {
    let nextIndex = this.backupJobsForm.length + 1
    this.backupJobsForm.push(
      this.createBackupJobForm({
        name: $localize`Backup Job ${nextIndex}`,
      })
    )
  }

  public removeBackupJob(index: number) {
    this.backupJobsForm.removeAt(index)
  }

  public formatBackupLastRun(value: string | null | undefined): string {
    if (!value) return $localize`Never`
    return new Date(value).toLocaleString()
  }

  public formatS3ExportSizeMb(size: number | null | undefined): string {
    if (size == null || Number.isNaN(size)) return '?'
    return `${(size / (1024 * 1024)).toFixed(1)} MB`
  }

  private setBackupScheduleJobsValue(
    jobs: BackupScheduleJob[],
    emitEvent = true
  ) {
    ;(this.configForm.controls as Record<string, FormControl<any>>)[
      'documents_backup_schedule_jobs'
    ]?.setValue(jobs, { emitEvent })
  }

  public isS3StorageSelected(): boolean {
    return (
      this.configForm.get('documents_storage_type')?.value === 's3' &&
      !!this.configForm.get('documents_s3_storage')?.value
    )
  }

  public isS3BackupConfigured(): boolean {
    return !!this.configForm.get('documents_backup_s3_storage')?.value
  }

  private resetS3StorageForm(storage?: S3Storage) {
    this.s3StorageForm.reset(
      storage ?? {
        id: null,
        name: null,
        prefix: null,
        bucket: null,
        endpoint_url: null,
        access_key_id: null,
        secret_access_key: null,
        region_name: null,
        default_acl: null,
        custom_domain: null,
        url_protocol: 'https:',
        addressing_style: null,
        querystring_auth: null,
        use_ssl: true,
      }
    )
  }

  public openS3StorageDialog(
    content: TemplateRef<unknown>,
    storage?: S3Storage
  ) {
    this.resetS3StorageForm(storage)
    this.s3StorageModalRef?.dismiss()
    this.s3StorageModalRef = this.modalService.open(content, {
      backdrop: 'static',
      size: 'lg',
    })
    this.s3StorageModalRef.result.finally(() => {
      this.s3StorageModalRef = null
    })
  }

  public copyS3Storage(content: TemplateRef<unknown>, storage: S3Storage) {
    this.resetS3StorageForm({
      ...storage,
      id: null,
      name: `${storage.name} (Copy)`,
      secret_access_key: null,
    })
    this.s3StorageModalRef?.dismiss()
    this.s3StorageModalRef = this.modalService.open(content, {
      backdrop: 'static',
      size: 'lg',
    })
    this.s3StorageModalRef.result.finally(() => {
      this.s3StorageModalRef = null
    })
    this.toastService.showInfo(
      $localize`S3 storage copied into the form. Please enter the secret access key before saving.`
    )
  }

  public clearS3StorageForm() {
    this.resetS3StorageForm()
  }

  public isS3StorageFormValid(): boolean {
    return !!(
      this.s3StorageForm.get('name')?.value &&
      this.s3StorageForm.get('bucket')?.value &&
      this.s3StorageForm.get('endpoint_url')?.value &&
      this.s3StorageForm.get('access_key_id')?.value &&
      this.s3StorageForm.get('secret_access_key')?.value
    )
  }

  public saveS3Storage() {
    this.savingS3Storage = true
    this.configService
      .saveS3Storage(this.s3StorageForm.value as Partial<S3Storage>)
      .pipe(takeUntil(this.unsubscribeNotifier), first())
      .subscribe({
        next: (storage) => {
          this.savingS3Storage = false
          let existingIndex = this.s3Storages.findIndex(
            (s) => s.id === storage.id
          )
          if (existingIndex >= 0) {
            let updated = [...this.s3Storages]
            updated[existingIndex] = storage
            this.s3Storages = updated.sort((a, b) =>
              a.name.localeCompare(b.name)
            )
          } else {
            this.s3Storages = [...this.s3Storages, storage].sort((a, b) =>
              a.name.localeCompare(b.name)
            )
          }
          this.refreshS3StorageChoices()
          this.resetS3StorageForm()
          this.s3StorageModalRef?.close()
          this.toastService.showInfo($localize`S3 storage saved`)
        },
        error: (e) => {
          this.savingS3Storage = false
          this.toastService.showError(
            $localize`An error occurred saving the S3 storage`,
            e
          )
        },
      })
  }

  public deleteS3Storage(storage: S3Storage) {
    if (!window.confirm($localize`Delete the S3 storage "${storage.name}"?`)) {
      return
    }

    this.configService
      .deleteS3Storage(storage.id)
      .pipe(takeUntil(this.unsubscribeNotifier), first())
      .subscribe({
        next: () => {
          this.s3Storages = this.s3Storages.filter((s) => s.id !== storage.id)
          this.refreshS3StorageChoices()
          if (
            this.configForm.get('documents_s3_storage')?.value === storage.id
          ) {
            this.configForm.get('documents_s3_storage')?.setValue(null as never)
          }
          if (
            this.configForm.get('documents_backup_s3_storage')?.value ===
            storage.id
          ) {
            this.configForm
              .get('documents_backup_s3_storage')
              ?.setValue(null as never)
          }
          this.backupJobControls.forEach((job) => {
            if (job.get('storage')?.value === storage.id) {
              job.get('storage')?.setValue(null)
            }
          })
          if (this.s3StorageForm.get('id')?.value === storage.id) {
            this.resetS3StorageForm()
          }
          this.toastService.showInfo($localize`S3 storage deleted`)
        },
        error: (e) => {
          this.toastService.showError(
            $localize`An error occurred deleting the S3 storage`,
            e
          )
        },
      })
  }

  public testNamedS3Storage() {
    this.testingStorage = true
    this.configService
      .testS3StorageConfiguration(
        this.s3StorageForm.value as Partial<S3Storage>
      )
      .pipe(takeUntil(this.unsubscribeNotifier), first())
      .subscribe({
        next: (response) => {
          this.testingStorage = false
          this.toastService.showInfo(response.detail)
        },
        error: (e) => {
          this.testingStorage = false
          this.toastService.showError(
            $localize`An error occurred testing the S3 storage`,
            e
          )
        },
      })
  }

  public exportNamedS3Storage(storage: S3Storage) {
    if (
      !window.confirm(
        $localize`Export all documents manually to the S3 storage "${storage.name}"?`
      )
    ) {
      return
    }

    this.runningS3Transfer = true
    this.configService
      .exportToS3Storage(storage)
      .pipe(takeUntil(this.unsubscribeNotifier), first())
      .subscribe({
        next: (response) => {
          this.s3TransferTasks[storage.id] = {
            id: 0,
            type: PaperlessTaskType.ManualTask,
            status: PaperlessTaskStatus.Pending,
            acknowledged: false,
            task_id: response.task_id,
            task_file_name: storage.name,
            task_name: PaperlessTaskName.ExportS3Storage,
            date_created: new Date(),
          }
          this.watchS3TransferTask(storage, response.task_id, true)
          this.loadS3StorageExports(storage, true)
          this.toastService.showInfo(response.detail)
        },
        error: (e) => {
          this.runningS3Transfer = false
          this.toastService.showError(
            $localize`An error occurred starting the S3 export`,
            e
          )
        },
      })
  }

  public importNamedS3Storage(storage: S3Storage, exportName: string) {
    if (
      !window.confirm(
        $localize`Import the export "${exportName}" from the S3 storage "${storage.name}"?`
      )
    ) {
      return
    }

    this.runningS3Transfer = true
    this.configService
      .importFromS3Storage(storage, exportName)
      .pipe(takeUntil(this.unsubscribeNotifier), first())
      .subscribe({
        next: (response) => {
          this.s3TransferTasks[storage.id] = {
            id: 0,
            type: PaperlessTaskType.ManualTask,
            status: PaperlessTaskStatus.Pending,
            acknowledged: false,
            task_id: response.task_id,
            task_file_name: storage.name,
            task_name: PaperlessTaskName.ImportS3Storage,
            date_created: new Date(),
          }
          this.watchS3TransferTask(storage, response.task_id, false)
          this.toastService.showInfo(response.detail)
        },
        error: (e) => {
          this.runningS3Transfer = false
          this.toastService.showError(
            $localize`An error occurred starting the S3 import`,
            e
          )
        },
      })
  }

  public downloadNamedS3Export(storage: S3Storage, exportName: string) {
    const downloadKey = `${storage.id}:${exportName}`
    this.downloadingS3ExportKey = downloadKey

    this.configService
      .downloadS3StorageExport(storage.id, exportName)
      .pipe(takeUntil(this.unsubscribeNotifier), first())
      .subscribe({
        next: (response) => {
          saveAs(
            response.body,
            this.getDownloadFilename(response) ?? exportName
          )
          this.downloadingS3ExportKey = null
        },
        error: (e) => {
          this.downloadingS3ExportKey = null
          this.toastService.showError(
            $localize`An error occurred downloading the S3 export`,
            e
          )
        },
      })
  }

  public deleteNamedS3Export(storage: S3Storage, exportName: string) {
    if (
      !window.confirm(
        $localize`Delete the export "${exportName}" from the S3 storage "${storage.name}"?`
      )
    ) {
      return
    }

    const deleteKey = `${storage.id}:${exportName}`
    this.deletingS3ExportKey = deleteKey

    this.configService
      .deleteS3StorageExport(storage.id, exportName)
      .pipe(takeUntil(this.unsubscribeNotifier), first())
      .subscribe({
        next: () => {
          this.deletingS3ExportKey = null
          this.s3StorageExports[storage.id] = (
            this.s3StorageExports[storage.id] ?? []
          ).filter((item) => item.name !== exportName)
          this.toastService.showInfo($localize`Export deleted`)
        },
        error: (e) => {
          this.deletingS3ExportKey = null
          this.toastService.showError(
            $localize`An error occurred deleting the export`,
            e
          )
        },
      })
  }

  public getS3TransferStatusText(storageId: number): string | null {
    const task = this.s3TransferTasks[storageId]
    if (!task) {
      return null
    }

    switch (task.status) {
      case PaperlessTaskStatus.Pending:
        return $localize`Status: queued`
      case PaperlessTaskStatus.Started:
        return $localize`Status: running`
      case PaperlessTaskStatus.Complete:
        return $localize`Status: completed`
      case PaperlessTaskStatus.Failed:
        return $localize`Status: failed`
      default:
        return $localize`Status: unknown`
    }
  }

  public isS3TransferActive(storageId: number): boolean {
    const task = this.s3TransferTasks[storageId]
    return (
      task?.status === PaperlessTaskStatus.Pending ||
      task?.status === PaperlessTaskStatus.Started
    )
  }

  private watchS3TransferTask(
    storage: S3Storage,
    taskId: string,
    refreshExportsOnSuccess: boolean
  ) {
    this.s3TransferPollingSubscriptions[storage.id]?.unsubscribe()
    this.runningS3Transfer = true

    this.s3TransferPollingSubscriptions[storage.id] = timer(0, 2000)
      .pipe(
        takeUntil(this.unsubscribeNotifier),
        switchMap(() => this.tasksService.getByTaskId(taskId))
      )
      .subscribe({
        next: (tasks) => {
          const task = tasks?.[0]
          if (!task) {
            return
          }

          this.s3TransferTasks[storage.id] = task

          if (
            task.status === PaperlessTaskStatus.Complete ||
            task.status === PaperlessTaskStatus.Failed
          ) {
            this.runningS3Transfer = false
            this.s3TransferPollingSubscriptions[storage.id]?.unsubscribe()
            delete this.s3TransferPollingSubscriptions[storage.id]
            if (
              refreshExportsOnSuccess &&
              task.status === PaperlessTaskStatus.Complete
            ) {
              this.loadS3StorageExports(storage, true)
            }
          }
        },
        error: () => {
          this.runningS3Transfer = false
          this.s3TransferPollingSubscriptions[storage.id]?.unsubscribe()
          delete this.s3TransferPollingSubscriptions[storage.id]
        },
      })
  }

  private getDownloadFilename(response: HttpResponse<Blob>): string | null {
    const disposition = response.headers.get('content-disposition')
    if (!disposition) {
      return null
    }

    const encodedFilenameMatch = disposition.match(/filename\*=UTF-8''([^;]+)/i)
    if (encodedFilenameMatch?.[1]) {
      return decodeURIComponent(encodedFilenameMatch[1])
    }

    const filenameMatch = disposition.match(/filename=\"?([^\";]+)\"?/i)
    return filenameMatch?.[1] ?? null
  }

  public toggleS3StorageExports(storage: S3Storage) {
    if (this.expandedExportsStorageId === storage.id) {
      this.expandedExportsStorageId = null
      return
    }

    this.expandedExportsStorageId = storage.id
    this.loadS3StorageExports(storage)
  }

  public loadS3StorageExports(
    storage: S3Storage,
    forceRefresh: boolean = false
  ) {
    if (!forceRefresh && this.s3StorageExports[storage.id]) {
      return
    }

    this.loadingExportsStorageId = storage.id
    this.configService
      .getS3StorageExports(storage.id)
      .pipe(takeUntil(this.unsubscribeNotifier), first())
      .subscribe({
        next: (exports) => {
          this.loadingExportsStorageId = null
          this.s3StorageExports[storage.id] = exports
        },
        error: (e) => {
          this.loadingExportsStorageId = null
          this.toastService.showError(
            $localize`An error occurred loading S3 exports`,
            e
          )
        },
      })
  }

  public formatS3ExportDate(value: string): string {
    return new Date(value).toLocaleString()
  }

  public testS3Storage() {
    this.testingStorage = true
    this.configService
      .testS3Storage(this.configForm.value as Partial<PaperlessConfig>)
      .pipe(takeUntil(this.unsubscribeNotifier), first())
      .subscribe({
        next: (response) => {
          this.testingStorage = false
          this.toastService.showInfo(response.detail)
        },
        error: (e) => {
          this.testingStorage = false
          this.toastService.showError(
            $localize`An error occurred testing S3 storage`,
            e
          )
        },
      })
  }

  public testS3BackupStorage() {
    this.testingStorage = true
    this.configService
      .testS3BackupStorage(this.configForm.value as Partial<PaperlessConfig>)
      .pipe(takeUntil(this.unsubscribeNotifier), first())
      .subscribe({
        next: (response) => {
          this.testingStorage = false
          this.toastService.showInfo(response.detail)
        },
        error: (e) => {
          this.testingStorage = false
          this.toastService.showError(
            $localize`An error occurred testing S3 backup storage`,
            e
          )
        },
      })
  }
}
