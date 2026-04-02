import { NgTemplateOutlet, SlicePipe } from '@angular/common'
import { Component, inject, OnDestroy, OnInit } from '@angular/core'
import { FormsModule, ReactiveFormsModule } from '@angular/forms'
import { Router } from '@angular/router'
import {
  NgbCollapseModule,
  NgbDropdownModule,
  NgbModal,
  NgbNavModule,
  NgbPaginationModule,
  NgbPopoverModule,
} from '@ng-bootstrap/ng-bootstrap'
import { NgxBootstrapIconsModule } from 'ngx-bootstrap-icons'
import {
  debounceTime,
  distinctUntilChanged,
  filter,
  first,
  Subject,
  Subscription,
  switchMap,
  takeUntil,
  timer,
} from 'rxjs'
import { PaperlessTask } from 'src/app/data/paperless-task'
import { IfPermissionsDirective } from 'src/app/directives/if-permissions.directive'
import { CustomDatePipe } from 'src/app/pipes/custom-date.pipe'
import { TasksService } from 'src/app/services/tasks.service'
import { ToastService } from 'src/app/services/toast.service'
import { ConfirmDialogComponent } from '../../common/confirm-dialog/confirm-dialog.component'
import { PageHeaderComponent } from '../../common/page-header/page-header.component'
import { LoadingComponentWithPermissions } from '../../loading-component/loading.component'

export enum TaskTab {
  Queued = 'queued',
  Started = 'started',
  Completed = 'completed',
  Failed = 'failed',
}

enum TaskFilterTargetID {
  Name,
  Result,
}

const FILTER_TARGETS = [
  { id: TaskFilterTargetID.Name, name: $localize`Name` },
  { id: TaskFilterTargetID.Result, name: $localize`Result` },
]

@Component({
  selector: 'pngx-tasks',
  templateUrl: './tasks.component.html',
  styleUrls: ['./tasks.component.scss'],
  imports: [
    PageHeaderComponent,
    IfPermissionsDirective,
    CustomDatePipe,
    SlicePipe,
    FormsModule,
    ReactiveFormsModule,
    NgTemplateOutlet,
    NgbCollapseModule,
    NgbDropdownModule,
    NgbNavModule,
    NgbPaginationModule,
    NgbPopoverModule,
    NgxBootstrapIconsModule,
  ],
})
export class TasksComponent
  extends LoadingComponentWithPermissions
  implements OnInit, OnDestroy
{
  tasksService = inject(TasksService)
  private modalService = inject(NgbModal)
  private readonly router = inject(Router)
  private readonly toastService = inject(ToastService)

  public activeTab: TaskTab = TaskTab.Failed
  public importActiveTab: TaskTab = TaskTab.Failed
  public runningTasks: PaperlessTask[] = []
  public selectedTasks: Set<number> = new Set()
  public selectedImportTasks: Set<number> = new Set()
  public selectedRunningTasks: Set<number> = new Set()
  public togggleAll: boolean = false
  public toggleAllImports: boolean = false
  public toggleAllRunningTasks: boolean = false
  public expandedTask: number
  public expandedImportTask: number
  public expandedRunningTask: number

  public pageSize: number = 25
  public page: number = 1
  public importPage: number = 1

  public autoRefreshEnabled: boolean = true

  private _filterText: string = ''
  private runningTasksPollingSubscription: Subscription | null = null
  get filterText() {
    return this._filterText
  }
  set filterText(value: string) {
    this.filterDebounce.next(value)
  }

  public filterTargetID: TaskFilterTargetID = TaskFilterTargetID.Name
  public get filterTargetName(): string {
    return this.filterTargets.find((t) => t.id == this.filterTargetID).name
  }
  private filterDebounce: Subject<string> = new Subject<string>()

  public get filterTargets(): Array<{ id: number; name: string }> {
    return [TaskTab.Failed, TaskTab.Completed].includes(this.activeTab)
      ? FILTER_TARGETS
      : FILTER_TARGETS.slice(0, 1)
  }

  get dismissButtonText(): string {
    return this.selectedTasks.size > 0
      ? $localize`Dismiss selected`
      : $localize`Dismiss all`
  }

  get dismissImportButtonText(): string {
    return this.selectedImportTasks.size > 0
      ? $localize`Dismiss selected`
      : $localize`Dismiss all`
  }

  get abortRunningButtonText(): string {
    return this.selectedRunningTasks.size > 0
      ? $localize`Abort selected`
      : $localize`Abort all`
  }

  ngOnInit() {
    this.tasksService.reload()
    this.loadRunningTasks()
    timer(5000, 5000)
      .pipe(
        filter(() => this.autoRefreshEnabled),
        takeUntil(this.unsubscribeNotifier)
      )
      .subscribe(() => {
        this.tasksService.reload()
        this.loadRunningTasks()
      })

    this.filterDebounce
      .pipe(
        takeUntil(this.unsubscribeNotifier),
        debounceTime(100),
        distinctUntilChanged(),
        filter((query) => !query.length || query.length > 2)
      )
      .subscribe((query) => (this._filterText = query))
  }

  ngOnDestroy() {
    super.ngOnDestroy()
    this.tasksService.cancelPending()
    this.runningTasksPollingSubscription?.unsubscribe()
  }

  dismissTask(task: PaperlessTask, taskGroup: 'file' | 'import' = 'file') {
    this.dismissTasks(task, taskGroup)
  }

  dismissTasks(
    task: PaperlessTask = undefined,
    taskGroup: 'file' | 'import' = 'file'
  ) {
    let tasks = task
      ? new Set([task.id])
      : new Set(this.getSelection(taskGroup).values())
    if (!task && tasks.size == 0)
      tasks = new Set(this.getAllTasks(taskGroup).map((t) => t.id))
    if (tasks.size > 1) {
      let modal = this.modalService.open(ConfirmDialogComponent, {
        backdrop: 'static',
      })
      modal.componentInstance.title = $localize`Confirm Dismiss All`
      modal.componentInstance.messageBold = $localize`Dismiss all ${tasks.size} tasks?`
      modal.componentInstance.btnClass = 'btn-warning'
      modal.componentInstance.btnCaption = $localize`Dismiss`
      modal.componentInstance.confirmClicked.pipe(first()).subscribe(() => {
        modal.componentInstance.buttonsEnabled = false
        modal.close()
        this.tasksService.dismissTasks(tasks).subscribe({
          error: (e) => {
            this.toastService.showError($localize`Error dismissing tasks`, e)
            modal.componentInstance.buttonsEnabled = true
          },
        })
        this.clearSelection(taskGroup)
      })
    } else {
      this.tasksService.dismissTasks(tasks).subscribe({
        error: (e) =>
          this.toastService.showError($localize`Error dismissing task`, e),
      })
      this.clearSelection(taskGroup)
    }
  }

  dismissAndGo(task: PaperlessTask) {
    this.dismissTask(task)
    this.router.navigate(['documents', task.related_document])
  }

  dismissImportAndGo(task: PaperlessTask) {
    this.dismissTask(task, 'import')
    this.router.navigate(['documents', task.related_document])
  }

  expandTask(task: PaperlessTask, taskGroup: 'file' | 'import' = 'file') {
    if (taskGroup === 'file') {
      this.expandedTask = this.expandedTask == task.id ? undefined : task.id
    } else {
      this.expandedImportTask =
        this.expandedImportTask == task.id ? undefined : task.id
    }
  }

  toggleSelected(task: PaperlessTask, taskGroup: 'file' | 'import' = 'file') {
    const selectedTasks = this.getSelection(taskGroup)
    selectedTasks.has(task.id)
      ? selectedTasks.delete(task.id)
      : selectedTasks.add(task.id)
  }

  toggleRunningSelected(task: PaperlessTask) {
    this.selectedRunningTasks.has(task.id)
      ? this.selectedRunningTasks.delete(task.id)
      : this.selectedRunningTasks.add(task.id)
    this.toggleAllRunningTasks =
      this.currentRunningTasks.length > 0 &&
      this.selectedRunningTasks.size === this.currentRunningTasks.length
  }

  get currentTasks(): PaperlessTask[] {
    let tasks: PaperlessTask[] = this.tasksForTab(this.activeTab, 'file')
    if (this._filterText.length) {
      tasks = tasks.filter((t) => {
        if (this.filterTargetID == TaskFilterTargetID.Name) {
          return t.task_file_name
            .toLowerCase()
            .includes(this._filterText.toLowerCase())
        } else if (this.filterTargetID == TaskFilterTargetID.Result) {
          return t.result.toLowerCase().includes(this._filterText.toLowerCase())
        }
      })
    }
    return tasks
  }

  get currentImportTasks(): PaperlessTask[] {
    return this.tasksForTab(this.importActiveTab, 'import')
  }

  get currentRunningTasks(): PaperlessTask[] {
    return this.runningTasks
  }

  toggleAll(event: PointerEvent, taskGroup: 'file' | 'import' = 'file') {
    if ((event.target as HTMLInputElement).checked) {
      if (taskGroup === 'file') {
        this.togggleAll = true
        this.selectedTasks = new Set(this.currentTasks.map((t) => t.id))
      } else {
        this.toggleAllImports = true
        this.selectedImportTasks = new Set(
          this.currentImportTasks.map((t) => t.id)
        )
      }
    } else {
      this.clearSelection(taskGroup)
    }
  }

  toggleAllRunning(event: PointerEvent) {
    if ((event.target as HTMLInputElement).checked) {
      this.toggleAllRunningTasks = true
      this.selectedRunningTasks = new Set(
        this.currentRunningTasks.map((t) => t.id)
      )
    } else {
      this.clearRunningSelection()
    }
  }

  clearSelection(taskGroup: 'file' | 'import' = 'file') {
    if (taskGroup === 'file') {
      this.togggleAll = false
      this.selectedTasks.clear()
    } else {
      this.toggleAllImports = false
      this.selectedImportTasks.clear()
    }
  }

  clearRunningSelection() {
    this.toggleAllRunningTasks = false
    this.selectedRunningTasks.clear()
  }

  duringTabChange(taskGroup: 'file' | 'import' = 'file') {
    if (taskGroup === 'file') {
      this.page = 1
    } else {
      this.importPage = 1
    }
  }

  beforeTabChange(taskGroup: 'file' | 'import' = 'file') {
    if (taskGroup === 'file') {
      this.resetFilter()
      this.filterTargetID = TaskFilterTargetID.Name
    }
  }

  get activeTabLocalized(): string {
    return this.localizeTab(this.activeTab)
  }

  get activeImportTabLocalized(): string {
    return this.localizeTab(this.importActiveTab)
  }

  private localizeTab(tab: TaskTab): string {
    switch (tab) {
      case TaskTab.Queued:
        return $localize`queued`
      case TaskTab.Started:
        return $localize`started`
      case TaskTab.Completed:
        return $localize`completed`
      case TaskTab.Failed:
        return $localize`failed`
    }
  }

  public resetFilter() {
    this._filterText = ''
  }

  public formatTaskName(taskName: string | null | undefined): string {
    return taskName ? taskName.split('_').join(' ') : $localize`Unknown`
  }

  filterInputKeyup(event: KeyboardEvent) {
    if (event.key == 'Enter') {
      this._filterText = (event.target as HTMLInputElement).value
    } else if (event.key === 'Escape') {
      this.resetFilter()
    }
  }

  private getSelection(taskGroup: 'file' | 'import'): Set<number> {
    return taskGroup === 'file' ? this.selectedTasks : this.selectedImportTasks
  }

  private getRunningSelection(): Set<number> {
    return this.selectedRunningTasks
  }

  private getAllTasks(taskGroup: 'file' | 'import'): PaperlessTask[] {
    return taskGroup === 'file'
      ? this.tasksService.allFileTasks
      : this.tasksService.allImportFileTasks
  }

  private tasksForTab(
    tab: TaskTab,
    taskGroup: 'file' | 'import'
  ): PaperlessTask[] {
    if (taskGroup === 'file') {
      switch (tab) {
        case TaskTab.Queued:
          return this.tasksService.queuedFileTasks
        case TaskTab.Started:
          return this.tasksService.startedFileTasks
        case TaskTab.Completed:
          return this.tasksService.completedFileTasks
        case TaskTab.Failed:
          return this.tasksService.failedFileTasks
      }
    }

    switch (tab) {
      case TaskTab.Queued:
        return this.tasksService.queuedImportFileTasks
      case TaskTab.Started:
        return this.tasksService.startedImportFileTasks
      case TaskTab.Completed:
        return this.tasksService.completedImportFileTasks
      case TaskTab.Failed:
        return this.tasksService.failedImportFileTasks
    }
  }

  private loadRunningTasks() {
    this.runningTasksPollingSubscription?.unsubscribe()
    this.runningTasksPollingSubscription = timer(0, 5000)
      .pipe(
        takeUntil(this.unsubscribeNotifier),
        switchMap(() => this.tasksService.getRunningTasks())
      )
      .subscribe({
        next: (tasks) => {
          this.runningTasks = tasks ?? []
          const activeSelection = new Set(
            this.runningTasks.map((task) => task.id)
          )
          this.selectedRunningTasks = new Set(
            [...this.selectedRunningTasks].filter((id) =>
              activeSelection.has(id)
            )
          )
          this.toggleAllRunningTasks =
            this.currentRunningTasks.length > 0 &&
            this.selectedRunningTasks.size === this.currentRunningTasks.length
        },
        error: () => {
          this.runningTasksPollingSubscription?.unsubscribe()
          this.runningTasksPollingSubscription = null
        },
      })
  }

  public abortRunningTasks(task?: PaperlessTask) {
    const tasks = task
      ? new Set([task.id])
      : new Set(this.getRunningSelection().values())
    if (!task && tasks.size == 0) {
      for (const runningTask of this.currentRunningTasks) {
        tasks.add(runningTask.id)
      }
    }
    if (tasks.size == 0) return

    const message =
      tasks.size > 1
        ? $localize`Abort all ${tasks.size} running tasks?`
        : $localize`Abort this running task?`
    if (!window.confirm(message)) {
      return
    }

    this.tasksService.abortTasks(tasks).subscribe({
      next: () => {
        this.clearRunningSelection()
        this.loadRunningTasks()
      },
      error: (e) =>
        this.toastService.showError($localize`Error aborting tasks`, e),
    })
  }
}
