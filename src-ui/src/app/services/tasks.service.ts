import { HttpClient } from '@angular/common/http'
import { Injectable, inject } from '@angular/core'
import { Observable, Subject } from 'rxjs'
import { first, takeUntil, tap } from 'rxjs/operators'
import {
  PaperlessTask,
  PaperlessTaskName,
  PaperlessTaskStatus,
} from 'src/app/data/paperless-task'
import { environment } from 'src/environments/environment'

@Injectable({
  providedIn: 'root',
})
export class TasksService {
  private http = inject(HttpClient)

  private baseUrl: string = environment.apiBaseUrl
  private endpoint: string = 'tasks'

  public loading: boolean

  private fileTasks: PaperlessTask[] = []
  private importFileTasks: PaperlessTask[] = []

  private unsubscribeNotifer: Subject<any> = new Subject()

  public get total(): number {
    return this.fileTasks.length
  }

  public get allFileTasks(): PaperlessTask[] {
    return this.fileTasks.slice(0)
  }

  public get totalImportTasks(): number {
    return this.importFileTasks.length
  }

  public get allImportFileTasks(): PaperlessTask[] {
    return this.importFileTasks.slice(0)
  }

  public get queuedFileTasks(): PaperlessTask[] {
    return this.fileTasks.filter((t) => t.status == PaperlessTaskStatus.Pending)
  }

  public get startedFileTasks(): PaperlessTask[] {
    return this.fileTasks.filter((t) => t.status == PaperlessTaskStatus.Started)
  }

  public get completedFileTasks(): PaperlessTask[] {
    return this.fileTasks.filter(
      (t) => t.status == PaperlessTaskStatus.Complete
    )
  }

  public get failedFileTasks(): PaperlessTask[] {
    return this.fileTasks.filter((t) => t.status == PaperlessTaskStatus.Failed)
  }

  public get queuedImportFileTasks(): PaperlessTask[] {
    return this.importFileTasks.filter(
      (t) => t.status == PaperlessTaskStatus.Pending
    )
  }

  public get startedImportFileTasks(): PaperlessTask[] {
    return this.importFileTasks.filter(
      (t) => t.status == PaperlessTaskStatus.Started
    )
  }

  public get completedImportFileTasks(): PaperlessTask[] {
    return this.importFileTasks.filter(
      (t) => t.status == PaperlessTaskStatus.Complete
    )
  }

  public get failedImportFileTasks(): PaperlessTask[] {
    return this.importFileTasks.filter(
      (t) => t.status == PaperlessTaskStatus.Failed
    )
  }

  public reload() {
    if (this.loading) return
    this.loading = true

    this.http
      .get<PaperlessTask[]>(
        `${this.baseUrl}${this.endpoint}/?acknowledged=false`
      )
      .pipe(takeUntil(this.unsubscribeNotifer), first())
      .subscribe((r) => {
        this.fileTasks = r.filter(
          (t) => t.task_name == PaperlessTaskName.ConsumeFile
        )
        this.importFileTasks = r.filter(
          (t) => t.task_name == PaperlessTaskName.ImportFile
        )
        this.loading = false
      })
  }

  public getByTaskId(taskId: string): Observable<PaperlessTask[]> {
    return this.http
      .get<
        PaperlessTask[]
      >(`${this.baseUrl}${this.endpoint}/?task_id=${encodeURIComponent(taskId)}`)
      .pipe(first(), takeUntil(this.unsubscribeNotifer))
  }

  public getByTaskName(
    taskName: PaperlessTaskName
  ): Observable<PaperlessTask[]> {
    return this.http
      .get<
        PaperlessTask[]
      >(`${this.baseUrl}${this.endpoint}/?task_name=${encodeURIComponent(taskName)}`)
      .pipe(first(), takeUntil(this.unsubscribeNotifer))
  }

  public getRunningTasks(): Observable<PaperlessTask[]> {
    return this.http
      .get<PaperlessTask[]>(`${this.baseUrl}${this.endpoint}/?running=true`)
      .pipe(first(), takeUntil(this.unsubscribeNotifer))
  }

  public dismissTasks(task_ids: Set<number>) {
    return this.http
      .post(`${this.baseUrl}tasks/acknowledge/`, {
        tasks: [...task_ids],
      })
      .pipe(
        first(),
        takeUntil(this.unsubscribeNotifer),
        tap(() => {
          this.reload()
        })
      )
  }

  public resetTasks(task_ids: Set<number>) {
    return this.http
      .post(`${this.baseUrl}tasks/reset/`, {
        tasks: [...task_ids],
      })
      .pipe(
        first(),
        takeUntil(this.unsubscribeNotifer),
        tap(() => {
          this.reload()
        })
      )
  }

  public cancelPending(): void {
    this.unsubscribeNotifer.next(true)
  }

  public run(taskName: PaperlessTaskName): Observable<any> {
    return this.http.post<any>(
      `${environment.apiBaseUrl}${this.endpoint}/run/`,
      {
        task_name: taskName,
      }
    )
  }
}
