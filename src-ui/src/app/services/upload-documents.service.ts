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

interface UploadDocumentResponse {
  task_id?: string
  task_ids?: string[]
}

function isUploadDocumentResponse(
  value: unknown
): value is UploadDocumentResponse {
  return typeof value === 'object' && value !== null
}

@Injectable({
  providedIn: 'root',
})
export class UploadDocumentsService {
  private documentService = inject(DocumentService)
  private websocketStatusService = inject(WebsocketStatusService)
  private tasksService = inject(TasksService)

  private uploadSubscriptions: Record<string, Subscription> = {}
  private taskPollingSubscriptions: Record<string, Subscription> = {}
  private taskPollingGroups: Record<
    string,
    { pendingTaskIds: Set<string>; statusId: string }
  > = {}
  private taskPollingGroupByTaskId: Record<string, string> = {}

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
            const responseBody = event.body as UploadDocumentResponse | string
            const taskIds = isUploadDocumentResponse(responseBody)
              ? Array.isArray(responseBody.task_ids)
                ? responseBody.task_ids
                : [responseBody.task_id ?? responseBody.toString()]
              : [responseBody?.toString()]

            status.taskId = taskIds[0]
            status.message = $localize`Upload complete, waiting...`
            this.startTaskPolling(taskIds)
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

  private startTaskPolling(taskIds: string | string[]) {
    const normalizedTaskIds = (
      Array.isArray(taskIds) ? taskIds : [taskIds]
    ).filter(Boolean)
    if (normalizedTaskIds.length === 0) {
      return
    }

    const statusId = normalizedTaskIds[0]
    this.taskPollingGroups[statusId] = {
      pendingTaskIds: new Set(normalizedTaskIds),
      statusId,
    }

    normalizedTaskIds.forEach((taskId) => {
      this.taskPollingGroupByTaskId[taskId] = statusId
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
              this.handleTaskPollingSuccess(taskId, task.related_document)
              this.stopTaskPolling(taskId)
            } else if (task.status === PaperlessTaskStatus.Failed) {
              this.handleTaskPollingFailure(
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
    })
  }

  private handleTaskPollingSuccess(taskId: string, documentId?: number) {
    const statusId = this.taskPollingGroupByTaskId[taskId]
    const group = statusId ? this.taskPollingGroups[statusId] : null

    if (!group) {
      this.websocketStatusService.completeTask(taskId, documentId)
      return
    }

    group.pendingTaskIds.delete(taskId)
    if (group.pendingTaskIds.size === 0) {
      this.websocketStatusService.completeTask(group.statusId, documentId)
      delete this.taskPollingGroups[group.statusId]
    }
    delete this.taskPollingGroupByTaskId[taskId]
  }

  private handleTaskPollingFailure(taskId: string, message: string) {
    const statusId = this.taskPollingGroupByTaskId[taskId]
    const group = statusId ? this.taskPollingGroups[statusId] : null

    if (!group) {
      this.websocketStatusService.failTask(taskId, message)
      return
    }

    this.websocketStatusService.failTask(group.statusId, message)
    group.pendingTaskIds.forEach((pendingTaskId) => {
      if (pendingTaskId !== taskId) {
        this.stopTaskPolling(pendingTaskId)
      }
      delete this.taskPollingGroupByTaskId[pendingTaskId]
    })
    delete this.taskPollingGroups[group.statusId]
  }

  private stopTaskPolling(taskId: string) {
    this.taskPollingSubscriptions[taskId]?.unsubscribe()
    delete this.taskPollingSubscriptions[taskId]
  }
}
