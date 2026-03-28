import { HttpEventType } from '@angular/common/http'
import { Injectable, inject } from '@angular/core'
import { Subscription, timer } from 'rxjs'
import { switchMap } from 'rxjs/operators'
import { PaperlessTaskStatus } from '../data/paperless-task'
import { DocumentService } from './rest/document.service'
import { TasksService } from './tasks.service'
import {
  FileStatusPhase,
  WebsocketStatusService,
} from './websocket-status.service'

@Injectable({
  providedIn: 'root',
})
export class UploadDocumentsService {
  private documentService = inject(DocumentService)
  private websocketStatusService = inject(WebsocketStatusService)
  private tasksService = inject(TasksService)

  private uploadSubscriptions: Record<string, Subscription> = {}
  private taskPollingSubscriptions: Record<string, Subscription> = {}

  public uploadFile(file: File) {
    let formData = new FormData()
    formData.append('document', file, file.name)
    formData.append('from_webui', 'true')
    let status = this.websocketStatusService.newFileUpload(file.name)

    status.message = $localize`Connecting...`

    this.uploadSubscriptions[file.name] = this.documentService
      .uploadDocument(formData)
      .subscribe({
        next: (event) => {
          if (event.type == HttpEventType.UploadProgress) {
            status.updateProgress(
              FileStatusPhase.UPLOADING,
              event.loaded,
              event.total
            )
            status.message = $localize`Uploading...`
          } else if (event.type == HttpEventType.Response) {
            status.taskId = event.body['task_id'] ?? event.body.toString()
            status.message = $localize`Upload complete, waiting...`
            this.startTaskPolling(status.taskId)
            this.stopUploadSubscription(file.name)
          }
        },
        error: (error) => {
          switch (error.status) {
            case 400: {
              this.websocketStatusService.fail(status, error.error.document)
              break
            }
            default: {
              this.websocketStatusService.fail(
                status,
                $localize`HTTP error: ${error.status} ${error.statusText}`
              )
              break
            }
          }
          this.stopUploadSubscription(file.name)
        },
      })
  }

  private stopUploadSubscription(fileName: string) {
    this.uploadSubscriptions[fileName]?.unsubscribe()
    delete this.uploadSubscriptions[fileName]
  }

  private startTaskPolling(taskId: string) {
    this.taskPollingSubscriptions[taskId]?.unsubscribe()
    this.taskPollingSubscriptions[taskId] = timer(1000, 2000)
      .pipe(switchMap(() => this.tasksService.getByTaskId(taskId)))
      .subscribe({
        next: (tasks) => {
          const task = tasks?.[0]
          if (!task) {
            return
          }

          if (task.status === PaperlessTaskStatus.Complete) {
            this.websocketStatusService.completeTask(
              taskId,
              task.related_document
            )
            this.stopTaskPolling(taskId)
          } else if (task.status === PaperlessTaskStatus.Failed) {
            this.websocketStatusService.failTask(
              taskId,
              task.result ?? $localize`Unknown error`
            )
            this.stopTaskPolling(taskId)
          }
        },
        error: () => {
          // Keep the upload status in place and retry on the next interval.
        },
      })
  }

  private stopTaskPolling(taskId: string) {
    this.taskPollingSubscriptions[taskId]?.unsubscribe()
    delete this.taskPollingSubscriptions[taskId]
  }
}
