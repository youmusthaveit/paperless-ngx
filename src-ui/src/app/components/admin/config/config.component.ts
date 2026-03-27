import { AsyncPipe } from '@angular/common'
import { Component, OnDestroy, OnInit, inject } from '@angular/core'
import {
  AbstractControl,
  FormControl,
  FormGroup,
  FormsModule,
  ReactiveFormsModule,
} from '@angular/forms'
import { NgbNavModule } from '@ng-bootstrap/ng-bootstrap'
import { DirtyComponent, dirtyCheck } from '@ngneat/dirty-check-forms'
import { NgxBootstrapIconsModule } from 'ngx-bootstrap-icons'
import {
  BehaviorSubject,
  Observable,
  Subscription,
  first,
  forkJoin,
  takeUntil,
} from 'rxjs'
import {
  ConfigCategory,
  ConfigOption,
  ConfigOptionType,
  PaperlessConfig,
  PaperlessConfigOptions,
} from 'src/app/data/paperless-config'
import { S3Storage, S3StorageExport } from 'src/app/data/s3-storage'
import { ConfigService } from 'src/app/services/config.service'
import { SettingsService } from 'src/app/services/settings.service'
import { ToastService } from 'src/app/services/toast.service'
import { FileComponent } from '../../common/input/file/file.component'
import { NumberComponent } from '../../common/input/number/number.component'
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

  public readonly ConfigOptionType = ConfigOptionType
  public readonly ConfigCategory = ConfigCategory

  // generated dynamically
  public configForm = new FormGroup({})

  public errors = {}
  public testingStorage = false
  public runningS3Transfer = false
  public savingS3Storage = false
  public s3Storages: S3Storage[] = []
  public expandedExportsStorageId: number | null = null
  public loadingExportsStorageId: number | null = null
  public s3StorageExports: Record<number, S3StorageExport[]> = {}
  public s3StorageSelectItems: Array<{ id: number; name: string }> = []
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

  initialConfig: PaperlessConfig
  store: BehaviorSubject<any>
  storeSub: Subscription
  isDirty$: Observable<boolean>

  constructor() {
    super()
    this.configForm.addControl('id', new FormControl())
    PaperlessConfigOptions.forEach((option) => {
      this.configForm.addControl(option.key, new FormControl())
    })
    this.editS3Storage()
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

  public isS3StorageSelected(): boolean {
    return (
      this.configForm.get('documents_storage_type')?.value === 's3' &&
      !!this.configForm.get('documents_s3_storage')?.value
    )
  }

  public isS3BackupConfigured(): boolean {
    return !!this.configForm.get('documents_backup_s3_storage')?.value
  }

  public editS3Storage(storage?: S3Storage) {
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

  public copyS3Storage(storage: S3Storage) {
    this.editS3Storage({
      ...storage,
      id: null,
      name: `${storage.name} (Copy)`,
      secret_access_key: null,
    })
    this.toastService.showInfo(
      $localize`S3 storage copied into the form. Please enter the secret access key before saving.`
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
          this.editS3Storage()
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
          if (this.s3StorageForm.get('id')?.value === storage.id) {
            this.editS3Storage()
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
          this.runningS3Transfer = false
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
          this.runningS3Transfer = false
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
