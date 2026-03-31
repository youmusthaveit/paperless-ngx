import { NgClass } from '@angular/common'
import { Component, inject, Input, OnDestroy, OnInit } from '@angular/core'
import {
  FormControl,
  FormGroup,
  FormsModule,
  ReactiveFormsModule,
} from '@angular/forms'
import { NgbPaginationModule } from '@ng-bootstrap/ng-bootstrap'
import { NgxBootstrapIconsModule } from 'ngx-bootstrap-icons'
import { Subject, switchMap, takeUntil, timer } from 'rxjs'
import { PaperlessConfig } from 'src/app/data/paperless-config'
import { PaperlessTask, PaperlessTaskStatus } from 'src/app/data/paperless-task'
import {
  RemoteImportDocumentPage,
  RemoteImportDocumentPreview,
  RemoteImportInspection,
} from 'src/app/data/remote-import'
import { ConfigService } from 'src/app/services/config.service'
import {
  RemoteImportBrowsePayload,
  RemoteImportService,
  RemoteImportStartPayload,
} from 'src/app/services/remote-import.service'
import { TasksService } from 'src/app/services/tasks.service'
import { ToastService } from 'src/app/services/toast.service'
import { PageHeaderComponent } from '../../common/page-header/page-header.component'
import { LoadingComponentWithPermissions } from '../../loading-component/loading.component'

@Component({
  selector: 'pngx-remote-import',
  standalone: true,
  templateUrl: './remote-import.component.html',
  styleUrl: './remote-import.component.scss',
  imports: [
    PageHeaderComponent,
    FormsModule,
    ReactiveFormsModule,
    NgbPaginationModule,
    NgxBootstrapIconsModule,
    NgClass,
  ],
})
export class RemoteImportComponent
  extends LoadingComponentWithPermissions
  implements OnInit, OnDestroy
{
  private readonly configService = inject(ConfigService)
  private readonly remoteImportService = inject(RemoteImportService)
  private readonly toastService = inject(ToastService)
  private readonly tasksService = inject(TasksService)

  @Input() embedded = false
  @Input() configIdOverride: number | null = null
  @Input() initialBaseUrl: string | null = null
  @Input() initialApiToken: string | null = null

  configId: number | null = null
  inspection: RemoteImportInspection | null = null
  documentsPage: RemoteImportDocumentPage | null = null
  selection = new Set<number>()
  activeTask: PaperlessTask | null = null
  testingConnection = false
  loadingDocuments = false
  startingImport = false
  savingDefaults = false

  page = 1
  pageSize = 25
  private activeImportTaskId: string | null = null
  private readonly refreshDocuments$ = new Subject<void>()
  private readonly stopTaskPolling$ = new Subject<void>()
  private loadedConfig: PaperlessConfig | null = null

  form = new FormGroup({
    base_url: new FormControl<string>(''),
    api_token: new FormControl<string>(''),
    query: new FormControl<string>(''),
    create_missing_items: new FormControl<boolean>(true, { nonNullable: true }),
    import_notes: new FormControl<boolean>(true, { nonNullable: true }),
  })

  ngOnInit(): void {
    if (this.configIdOverride !== null) {
      this.configId = this.configIdOverride
      this.form.patchValue({
        base_url: this.initialBaseUrl ?? '',
        api_token: this.initialApiToken ?? '',
      })
      this.loading = false
      return
    }

    this.configService
      .getConfig()
      .pipe(takeUntil(this.unsubscribeNotifier))
      .subscribe({
        next: (config) => {
          this.loadedConfig = config
          this.configId = config.id
          this.form.patchValue({
            base_url: config.remote_import_base_url ?? '',
            api_token: config.remote_import_api_token ?? '',
          })
          this.loading = false
        },
        error: (error) => {
          this.loading = false
          this.toastService.showError(
            $localize`Error retrieving configuration`,
            error
          )
        },
      })

    this.refreshDocuments$
      .pipe(
        takeUntil(this.unsubscribeNotifier),
        switchMap(() =>
          this.remoteImportService.browseDocuments(
            this.configId!,
            this.buildBrowsePayload()
          )
        )
      )
      .subscribe({
        next: (page) => {
          this.documentsPage = page
          this.loadingDocuments = false
        },
        error: (error) => {
          this.loadingDocuments = false
          this.toastService.showError(
            $localize`Error loading remote documents`,
            error
          )
        },
      })
  }

  override ngOnDestroy(): void {
    this.stopTaskPolling$.next()
    this.stopTaskPolling$.complete()
    super.ngOnDestroy()
  }

  get currentResults(): RemoteImportDocumentPreview[] {
    return this.documentsPage?.results ?? []
  }

  get selectedCount(): number {
    return this.selection.size
  }

  get canLoadDocuments(): boolean {
    return !!this.inspection && !this.loadingDocuments
  }

  get allCurrentPageSelected(): boolean {
    return (
      this.currentResults.length > 0 &&
      this.currentResults.every((doc) => this.selection.has(doc.id))
    )
  }

  inspectRemote(): void {
    if (!this.configId) return

    this.testingConnection = true
    this.inspection = null
    this.documentsPage = null
    this.selection.clear()
    this.page = 1

    this.remoteImportService
      .inspect(this.configId, {
        base_url: this.form.value.base_url ?? '',
        api_token: this.form.value.api_token ?? '',
      })
      .pipe(takeUntil(this.unsubscribeNotifier))
      .subscribe({
        next: (inspection) => {
          this.inspection = inspection
          this.testingConnection = false
          this.loadDocuments()
        },
        error: (error) => {
          this.testingConnection = false
          this.toastService.showError(
            $localize`Error connecting to remote instance`,
            error
          )
        },
      })
  }

  loadDocuments(): void {
    if (!this.configId) return
    this.loadingDocuments = true
    this.refreshDocuments$.next()
  }

  onPageChange(page: number): void {
    this.page = page
    this.loadDocuments()
  }

  toggleSelection(documentId: number): void {
    if (this.selection.has(documentId)) {
      this.selection.delete(documentId)
    } else {
      this.selection.add(documentId)
    }
  }

  toggleCurrentPageSelection(): void {
    if (this.allCurrentPageSelected) {
      this.currentResults.forEach((doc) => this.selection.delete(doc.id))
    } else {
      this.currentResults.forEach((doc) => this.selection.add(doc.id))
    }
  }

  clearSelection(): void {
    this.selection.clear()
  }

  startSelectedImport(): void {
    this.startImport({
      ...this.buildStartPayload(),
      selected_document_ids: [...this.selection],
      import_all: false,
    })
  }

  startImportAll(): void {
    this.startImport({
      ...this.buildStartPayload(),
      selected_document_ids: [],
      import_all: true,
    })
  }

  saveConnectionDefaults(): void {
    if (!this.configId || !this.loadedConfig) return

    this.savingDefaults = true
    this.configService
      .saveConfig({
        ...this.loadedConfig,
        remote_import_base_url: this.form.value.base_url ?? null,
        remote_import_api_token: this.form.value.api_token ?? null,
      } as PaperlessConfig)
      .pipe(takeUntil(this.unsubscribeNotifier))
      .subscribe({
        next: (config) => {
          this.loadedConfig = config
          this.savingDefaults = false
          this.form.patchValue({
            base_url: config.remote_import_base_url ?? '',
          })
          this.toastService.showInfo($localize`Remote import connection saved`)
        },
        error: (error) => {
          this.savingDefaults = false
          this.toastService.showError(
            $localize`Error saving remote import connection`,
            error
          )
        },
      })
  }

  private startImport(payload: RemoteImportStartPayload): void {
    if (!this.configId) return

    this.startingImport = true
    this.remoteImportService
      .startImport(this.configId, payload)
      .pipe(takeUntil(this.unsubscribeNotifier))
      .subscribe({
        next: ({ task_id }) => {
          this.startingImport = false
          this.activeImportTaskId = task_id
          this.tasksService.reload()
          this.pollImportTask()
        },
        error: (error) => {
          this.startingImport = false
          this.toastService.showError(
            $localize`Error starting remote import`,
            error
          )
        },
      })
  }

  private pollImportTask(): void {
    if (!this.activeImportTaskId) return

    this.stopTaskPolling$.next()
    timer(0, 2000)
      .pipe(
        takeUntil(this.stopTaskPolling$),
        takeUntil(this.unsubscribeNotifier),
        switchMap(() => this.tasksService.getByTaskId(this.activeImportTaskId!))
      )
      .subscribe({
        next: (tasks) => {
          const task = tasks?.[0]
          if (!task) return
          this.activeTask = task

          if (
            task.status === PaperlessTaskStatus.Complete ||
            task.status === PaperlessTaskStatus.Failed
          ) {
            this.tasksService.reload()
            this.activeImportTaskId = null
            this.stopTaskPolling$.next()
          }
        },
        error: (error) => {
          this.toastService.showError(
            $localize`Error polling import task`,
            error
          )
        },
      })
  }

  private buildBrowsePayload(): RemoteImportBrowsePayload {
    return {
      base_url: this.form.value.base_url ?? '',
      api_token: this.form.value.api_token ?? '',
      query: this.form.value.query ?? '',
      page: this.page,
      page_size: this.pageSize,
    }
  }

  private buildStartPayload(): RemoteImportStartPayload {
    return {
      base_url: this.form.value.base_url ?? '',
      api_token: this.form.value.api_token ?? '',
      query: this.form.value.query ?? '',
      create_missing_items: this.form.controls.create_missing_items.value,
      import_notes: this.form.controls.import_notes.value,
    }
  }

  trackByDocumentId(_: number, document: RemoteImportDocumentPreview): number {
    return document.id
  }
}
