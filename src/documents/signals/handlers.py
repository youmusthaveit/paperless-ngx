from __future__ import annotations

import logging
import shutil
from contextlib import nullcontext
from pathlib import Path
from typing import TYPE_CHECKING

from celery import shared_task
from celery import states
from celery.signals import before_task_publish
from celery.signals import task_failure
from celery.signals import task_postrun
from celery.signals import task_prerun
from celery.signals import worker_process_init
from django.conf import settings
from django.contrib.auth.models import Group
from django.contrib.auth.models import User
from django.db import DatabaseError
from django.db import close_old_connections
from django.db import connections
from django.db import models
from django.db.models import Q
from django.dispatch import receiver
from django.utils import timezone
from filelock import FileLock
from guardian.shortcuts import get_groups_with_perms
from guardian.shortcuts import get_users_with_perms

from documents import matching
from documents.caching import clear_document_caches
from documents.file_handling import delete_empty_directories
from documents.file_handling import generate_filename
from documents.file_handling import generate_unique_filename
from documents.models import ApprovalRequest
from documents.models import CustomField
from documents.models import CustomFieldInstance
from documents.models import Document
from documents.models import MatchingModel
from documents.models import PaperlessTask
from documents.models import SavedView
from documents.models import Tag
from documents.models import UiSettings
from documents.models import Workflow
from documents.models import WorkflowAction
from documents.models import WorkflowRun
from documents.models import WorkflowRunStep
from documents.models import WorkflowTrigger
from documents.permissions import get_objects_for_user_owner_aware
from documents.plugins.helpers import DocumentsStatusManager
from documents.storage import _local_document_path
from documents.storage import document_checksum_matches
from documents.storage import document_delete
from documents.storage import document_exists
from documents.storage import document_move
from documents.storage import document_storage_is_local
from documents.templating.utils import convert_format_str_to_template_format
from documents.workflows.actions import build_workflow_action_context
from documents.workflows.actions import execute_approval_action
from documents.workflows.actions import execute_email_action
from documents.workflows.actions import execute_move_to_trash_action
from documents.workflows.actions import execute_password_removal_action
from documents.workflows.actions import execute_webhook_action
from documents.workflows.mutations import apply_assignment_to_document
from documents.workflows.mutations import apply_assignment_to_overrides
from documents.workflows.mutations import apply_removal_to_document
from documents.workflows.mutations import apply_removal_to_overrides
from documents.workflows.utils import get_workflows_for_trigger

if TYPE_CHECKING:
    from documents.classifier import DocumentClassifier
    from documents.data_models import ConsumableDocument
    from documents.data_models import DocumentMetadataOverrides

logger = logging.getLogger("paperless.handlers")


def _finalize_workflow_run(workflow_run: WorkflowRun) -> None:
    steps = list(workflow_run.steps.all())
    if not steps:
        workflow_run.status = WorkflowRun.WorkflowRunStatus.SUCCESS
        workflow_run.finished_at = timezone.now()
        workflow_run.current_step_order = None
        workflow_run.message = "Workflow completed"
        workflow_run.error = ""
    elif any(
        step.status == WorkflowRunStep.WorkflowRunStepStatus.FAILED for step in steps
    ):
        failed_step = next(
            step
            for step in steps
            if step.status == WorkflowRunStep.WorkflowRunStepStatus.FAILED
        )
        workflow_run.status = WorkflowRun.WorkflowRunStatus.FAILED
        workflow_run.finished_at = failed_step.finished_at or timezone.now()
        workflow_run.current_step_order = failed_step.order
        workflow_run.message = failed_step.message or "Workflow failed"
        workflow_run.error = failed_step.error
    elif any(
        step.status == WorkflowRunStep.WorkflowRunStepStatus.WAITING_APPROVAL
        for step in steps
    ):
        waiting_step = next(
            step
            for step in steps
            if step.status == WorkflowRunStep.WorkflowRunStepStatus.WAITING_APPROVAL
        )
        workflow_run.status = WorkflowRun.WorkflowRunStatus.WAITING_APPROVAL
        workflow_run.finished_at = None
        workflow_run.current_step_order = waiting_step.order
        workflow_run.message = waiting_step.message or "Waiting for approval"
        workflow_run.error = ""
    elif any(
        step.status in [WorkflowRunStep.WorkflowRunStepStatus.RUNNING] for step in steps
    ):
        running_step = next(
            step
            for step in steps
            if step.status == WorkflowRunStep.WorkflowRunStepStatus.RUNNING
        )
        workflow_run.status = WorkflowRun.WorkflowRunStatus.RUNNING
        workflow_run.finished_at = None
        workflow_run.current_step_order = running_step.order
        workflow_run.message = running_step.message or "Workflow is running"
        workflow_run.error = ""
    else:
        final_step = steps[-1]
        workflow_run.status = WorkflowRun.WorkflowRunStatus.SUCCESS
        workflow_run.finished_at = final_step.finished_at or timezone.now()
        workflow_run.current_step_order = None
        workflow_run.message = final_step.message or "Workflow completed"
        workflow_run.error = ""

    workflow_run.save(
        update_fields=[
            "status",
            "finished_at",
            "current_step_order",
            "message",
            "error",
        ],
    )


def _mark_workflow_step_failure(
    workflow_run: WorkflowRun,
    step: WorkflowRunStep,
    *,
    message: str,
    error: str,
) -> None:
    step.status = WorkflowRunStep.WorkflowRunStepStatus.FAILED
    step.finished_at = timezone.now()
    step.error = error
    step.message = message
    step.save(
        update_fields=[
            "status",
            "finished_at",
            "error",
            "message",
        ],
    )
    _finalize_workflow_run(workflow_run)


def _save_document_workflow_mutations(
    document: Document,
    doc_tag_ids: list[int],
) -> None:
    document.title = document.title[:128]
    document.save(
        update_fields=[
            "title",
            "correspondent",
            "document_type",
            "storage_path",
            "owner",
            "modified",
        ],
    )
    document.tags.set(doc_tag_ids)


def _execute_workflow_actions(
    workflow_run: WorkflowRun,
    workflow: Workflow,
    trigger_type: WorkflowTrigger.WorkflowTriggerType,
    document: Document | ConsumableDocument,
    *,
    logging_group,
    use_overrides: bool,
    overrides: DocumentMetadataOverrides | None,
    original_file: Path | None,
    started_by: User | None,
    doc_tag_ids: list[int] | None = None,
    start_after_action_id: int | None = None,
) -> bool:
    resume_started = start_after_action_id is None
    for action in workflow.actions.order_by("order", "pk"):
        if not resume_started:
            if action.id == start_after_action_id:
                resume_started = True
            continue

        if action.id == start_after_action_id:
            continue

        message = f"Applying {action} from {workflow}"
        if not use_overrides:
            logger.info(message, extra={"group": logging_group})

        step = WorkflowRunStep.objects.create(
            workflow_run=workflow_run,
            action=action,
            order=action.order,
            status=WorkflowRunStep.WorkflowRunStepStatus.RUNNING,
            started_at=timezone.now(),
            message=message,
        )
        workflow_run.current_step_order = action.order
        workflow_run.message = message
        workflow_run.save(update_fields=["current_step_order", "message"])

        if action.type == WorkflowAction.WorkflowActionType.ASSIGNMENT:
            try:
                if use_overrides and overrides:
                    apply_assignment_to_overrides(action, overrides)
                else:
                    apply_assignment_to_document(
                        action,
                        document,
                        doc_tag_ids,
                        logging_group,
                    )
                step.status = WorkflowRunStep.WorkflowRunStepStatus.SUCCESS
                step.finished_at = timezone.now()
                step.message = "Assignment applied"
                step.save(update_fields=["status", "finished_at", "message"])
            except Exception as exc:
                _mark_workflow_step_failure(
                    workflow_run,
                    step,
                    message="Assignment failed",
                    error=str(exc),
                )
                raise
        elif action.type == WorkflowAction.WorkflowActionType.REMOVAL:
            try:
                if use_overrides and overrides:
                    apply_removal_to_overrides(action, overrides)
                else:
                    apply_removal_to_document(action, document, doc_tag_ids)
                step.status = WorkflowRunStep.WorkflowRunStepStatus.SUCCESS
                step.finished_at = timezone.now()
                step.message = "Removal applied"
                step.save(update_fields=["status", "finished_at", "message"])
            except Exception as exc:
                _mark_workflow_step_failure(
                    workflow_run,
                    step,
                    message="Removal failed",
                    error=str(exc),
                )
                raise
        elif action.type == WorkflowAction.WorkflowActionType.EMAIL:
            context = build_workflow_action_context(document, overrides)
            result = execute_email_action(
                action,
                document,
                context,
                logging_group,
                original_file,
                trigger_type,
            )
            step.status = (
                WorkflowRunStep.WorkflowRunStepStatus.SUCCESS
                if result.status == "success"
                else WorkflowRunStep.WorkflowRunStepStatus.FAILED
            )
            step.finished_at = timezone.now()
            step.message = result.message or "Email action completed"
            step.error = result.error
            step.response_payload = result.response_payload
            step.save(
                update_fields=[
                    "status",
                    "finished_at",
                    "message",
                    "error",
                    "response_payload",
                ],
            )
        elif action.type == WorkflowAction.WorkflowActionType.WEBHOOK:
            context = build_workflow_action_context(document, overrides)
            result = execute_webhook_action(
                action,
                document,
                context,
                logging_group,
                original_file,
                run_step_id=step.pk,
            )
            step.message = result.message or "Webhook queued"
            step.error = result.error
            step.request_payload = result.request_payload
            if result.status == "running":
                step.save(
                    update_fields=[
                        "message",
                        "error",
                        "request_payload",
                    ],
                )
            else:
                step.status = WorkflowRunStep.WorkflowRunStepStatus.FAILED
                step.finished_at = timezone.now()
                step.save(
                    update_fields=[
                        "status",
                        "finished_at",
                        "message",
                        "error",
                        "request_payload",
                    ],
                )
        elif action.type == WorkflowAction.WorkflowActionType.PASSWORD_REMOVAL:
            try:
                execute_password_removal_action(
                    action,
                    document,
                    logging_group,
                )
                step.status = WorkflowRunStep.WorkflowRunStepStatus.SUCCESS
                step.finished_at = timezone.now()
                step.message = "Password removal action completed"
                step.save(update_fields=["status", "finished_at", "message"])
            except Exception as exc:
                _mark_workflow_step_failure(
                    workflow_run,
                    step,
                    message="Password removal failed",
                    error=str(exc),
                )
                raise
        elif action.type == WorkflowAction.WorkflowActionType.MOVE_TO_TRASH:
            try:
                execute_move_to_trash_action(
                    action,
                    document,
                    logging_group,
                )
                step.status = WorkflowRunStep.WorkflowRunStepStatus.SUCCESS
                step.finished_at = timezone.now()
                step.message = "Document moved to trash"
                step.save(update_fields=["status", "finished_at", "message"])
            except Exception as exc:
                _mark_workflow_step_failure(
                    workflow_run,
                    step,
                    message="Move to trash failed",
                    error=str(exc),
                )
                raise
        elif action.type == WorkflowAction.WorkflowActionType.APPROVAL:
            result = execute_approval_action(
                action,
                document,
                workflow_run,
                step,
                started_by,
                logging_group,
            )
            if result.status == "waiting_approval":
                step.status = WorkflowRunStep.WorkflowRunStepStatus.WAITING_APPROVAL
                step.message = result.message
                step.request_payload = result.request_payload
                step.save(
                    update_fields=[
                        "status",
                        "message",
                        "request_payload",
                    ],
                )
                workflow_run.status = WorkflowRun.WorkflowRunStatus.WAITING_APPROVAL
                workflow_run.message = result.message
                workflow_run.save(update_fields=["status", "message"])
                if not use_overrides and isinstance(document, Document):
                    _save_document_workflow_mutations(document, doc_tag_ids)
                return True

            _mark_workflow_step_failure(
                workflow_run,
                step,
                message="Approval request failed",
                error=result.error or "Approval request could not be created",
            )
            raise ValueError(result.error or "Approval request could not be created")

    if not use_overrides and isinstance(document, Document):
        _save_document_workflow_mutations(document, doc_tag_ids)
    return False


def continue_workflow_run(
    workflow_run: WorkflowRun,
    *,
    decided_by: User | None = None,
    logging_group=None,
) -> None:
    if workflow_run.document_id is None:
        return

    workflow = get_workflows_for_trigger(
        workflow_run.type,
        workflow_run.workflow,
    ).first()
    if workflow is None:
        return

    workflow_run.document.refresh_from_db()
    doc_tag_ids = list(workflow_run.document.tags.values_list("pk", flat=True))
    approval_step = (
        workflow_run.steps.filter(
            status=WorkflowRunStep.WorkflowRunStepStatus.SUCCESS,
            approval_request__status=ApprovalRequest.ApprovalStatus.APPROVED,
        )
        .select_related("action")
        .order_by("-finished_at", "-pk")
        .first()
    )
    workflow_run.status = WorkflowRun.WorkflowRunStatus.RUNNING
    workflow_run.message = "Workflow resumed"
    workflow_run.error = ""
    workflow_run.finished_at = None
    workflow_run.save(
        update_fields=["status", "message", "error", "finished_at"],
    )
    _execute_workflow_actions(
        workflow_run,
        workflow,
        workflow_run.type,
        workflow_run.document,
        logging_group=logging_group,
        use_overrides=False,
        overrides=None,
        original_file=None,
        started_by=decided_by,
        doc_tag_ids=doc_tag_ids,
        start_after_action_id=approval_step.action_id if approval_step else None,
    )
    _finalize_workflow_run(workflow_run)


def add_inbox_tags(sender, document: Document, logging_group=None, **kwargs):
    if document.owner is not None:
        tags = get_objects_for_user_owner_aware(
            document.owner,
            "documents.view_tag",
            Tag,
        )
    else:
        tags = Tag.objects.all()
    inbox_tags = tags.filter(is_inbox_tag=True)
    document.add_nested_tags(inbox_tags)


def add_or_update_document_in_llm_index(
    sender,
    document: Document,
    logging_group=None,
    **kwargs,
) -> None:
    from documents.tasks import update_document_in_llm_index
    from paperless.config import AIConfig

    ai_config = AIConfig()
    if not ai_config.llm_index_enabled:
        return

    update_document_in_llm_index.delay(document)


def send_websocket_document_updated(
    sender,
    document: Document,
    logging_group=None,
    **kwargs,
) -> None:
    users_can_view = list(
        get_users_with_perms(
            document,
            only_with_perms_in=["view_document"],
            with_group_users=False,
        ).values_list("pk", flat=True),
    )
    groups_can_view = list(
        get_groups_with_perms(
            document,
            only_with_perms_in=["view_document"],
        ).values_list("pk", flat=True),
    )

    with DocumentsStatusManager() as status_mgr:
        status_mgr.send_document_updated(
            document_id=document.pk,
            modified=document.modified.isoformat(),
            owner_id=document.owner_id,
            users_can_view=users_can_view,
            groups_can_view=groups_can_view,
        )


def _suggestion_printer(
    stdout,
    style_func,
    suggestion_type: str,
    document: Document,
    selected: MatchingModel,
    base_url: str | None = None,
):
    """
    Smaller helper to reduce duplication when just outputting suggestions to the console
    """
    doc_str = str(document)
    if base_url is not None:
        stdout.write(style_func.SUCCESS(doc_str))
        stdout.write(style_func.SUCCESS(f"{base_url}/documents/{document.pk}"))
    else:
        stdout.write(style_func.SUCCESS(f"{doc_str} [{document.pk}]"))
    stdout.write(f"Suggest {suggestion_type}: {selected}")


def set_correspondent(
    sender,
    document: Document,
    *,
    logging_group=None,
    classifier: DocumentClassifier | None = None,
    replace=False,
    use_first=True,
    suggest=False,
    base_url=None,
    stdout=None,
    style_func=None,
    **kwargs,
):
    if document.correspondent and not replace:
        return

    potential_correspondents = matching.match_correspondents(document, classifier)

    potential_count = len(potential_correspondents)
    selected = potential_correspondents[0] if potential_correspondents else None
    if potential_count > 1:
        if use_first:
            logger.debug(
                f"Detected {potential_count} potential correspondents, "
                f"so we've opted for {selected}",
                extra={"group": logging_group},
            )
        else:
            logger.debug(
                f"Detected {potential_count} potential correspondents, "
                f"not assigning any correspondent",
                extra={"group": logging_group},
            )
            return

    if selected or replace:
        if suggest:
            _suggestion_printer(
                stdout,
                style_func,
                "correspondent",
                document,
                selected,
                base_url,
            )
        else:
            logger.info(
                f"Assigning correspondent {selected} to {document}",
                extra={"group": logging_group},
            )

            document.correspondent = selected
            document.save(update_fields=("correspondent",))


def set_document_type(
    sender,
    document: Document,
    *,
    logging_group=None,
    classifier: DocumentClassifier | None = None,
    replace=False,
    use_first=True,
    suggest=False,
    base_url=None,
    stdout=None,
    style_func=None,
    **kwargs,
):
    if document.document_type and not replace:
        return

    potential_document_type = matching.match_document_types(document, classifier)

    potential_count = len(potential_document_type)
    selected = potential_document_type[0] if potential_document_type else None

    if potential_count > 1:
        if use_first:
            logger.info(
                f"Detected {potential_count} potential document types, "
                f"so we've opted for {selected}",
                extra={"group": logging_group},
            )
        else:
            logger.info(
                f"Detected {potential_count} potential document types, "
                f"not assigning any document type",
                extra={"group": logging_group},
            )
            return

    if selected or replace:
        if suggest:
            _suggestion_printer(
                stdout,
                style_func,
                "document type",
                document,
                selected,
                base_url,
            )
        else:
            logger.info(
                f"Assigning document type {selected} to {document}",
                extra={"group": logging_group},
            )

            document.document_type = selected
            document.save(update_fields=("document_type",))


def set_tags(
    sender,
    document: Document,
    *,
    logging_group=None,
    classifier: DocumentClassifier | None = None,
    replace=False,
    suggest=False,
    base_url=None,
    stdout=None,
    style_func=None,
    **kwargs,
):
    if replace:
        Document.tags.through.objects.filter(document=document).exclude(
            Q(tag__is_inbox_tag=True),
        ).exclude(
            Q(tag__match="") & ~Q(tag__matching_algorithm=Tag.MATCH_AUTO),
        ).delete()

    current_tags = set(document.tags.all())

    matched_tags = matching.match_tags(document, classifier)

    relevant_tags = set(matched_tags) - current_tags

    if suggest:
        extra_tags = current_tags - set(matched_tags)
        extra_tags = [
            t for t in extra_tags if t.matching_algorithm == MatchingModel.MATCH_AUTO
        ]
        if not relevant_tags and not extra_tags:
            return
        doc_str = style_func.SUCCESS(str(document))
        if base_url:
            stdout.write(doc_str)
            stdout.write(f"{base_url}/documents/{document.pk}")
        else:
            stdout.write(doc_str + style_func.SUCCESS(f" [{document.pk}]"))
        if relevant_tags:
            stdout.write("Suggest tags: " + ", ".join([t.name for t in relevant_tags]))
        if extra_tags:
            stdout.write("Extra tags: " + ", ".join([t.name for t in extra_tags]))
    else:
        if not relevant_tags:
            return

        message = 'Tagging "{}" with "{}"'
        logger.info(
            message.format(document, ", ".join([t.name for t in relevant_tags])),
            extra={"group": logging_group},
        )

        document.add_nested_tags(relevant_tags)


def set_storage_path(
    sender,
    document: Document,
    *,
    logging_group=None,
    classifier: DocumentClassifier | None = None,
    replace=False,
    use_first=True,
    suggest=False,
    base_url=None,
    stdout=None,
    style_func=None,
    **kwargs,
):
    if document.storage_path and not replace:
        return

    potential_storage_path = matching.match_storage_paths(
        document,
        classifier,
    )

    potential_count = len(potential_storage_path)
    selected = potential_storage_path[0] if potential_storage_path else None

    if potential_count > 1:
        if use_first:
            logger.info(
                f"Detected {potential_count} potential storage paths, "
                f"so we've opted for {selected}",
                extra={"group": logging_group},
            )
        else:
            logger.info(
                f"Detected {potential_count} potential storage paths, "
                f"not assigning any storage directory",
                extra={"group": logging_group},
            )
            return

    if selected or replace:
        if suggest:
            _suggestion_printer(
                stdout,
                style_func,
                "storage directory",
                document,
                selected,
                base_url,
            )
        else:
            logger.info(
                f"Assigning storage path {selected} to {document}",
                extra={"group": logging_group},
            )

            document.storage_path = selected
            document.save(update_fields=("storage_path",))


# see empty_trash in documents/tasks.py for signal handling
def cleanup_document_deletion(sender, instance, **kwargs):
    with FileLock(settings.MEDIA_LOCK):
        if settings.EMPTY_TRASH_DIR:
            # Find a non-conflicting filename in case a document with the same
            # name was moved to trash earlier
            counter = 0
            old_filename = Path(instance.source_name).name
            old_filebase = Path(old_filename).stem
            old_fileext = Path(old_filename).suffix

            while True:
                new_file_path = settings.EMPTY_TRASH_DIR / (
                    old_filebase + (f"_{counter:02}" if counter else "") + old_fileext
                )

                if new_file_path.exists():
                    counter += 1
                else:
                    break

            if document_storage_is_local("originals"):
                logger.debug(
                    f"Moving {instance.source_name} to trash at {new_file_path}",
                )
                try:
                    shutil.move(
                        _local_document_path("originals", instance.source_name),
                        new_file_path,
                    )
                except OSError as e:
                    logger.error(
                        f"Failed to move {instance.source_name} to trash at "
                        f"{new_file_path}: {e}. Skipping cleanup!",
                    )
                    return
                except NotImplementedError:
                    logger.warning(
                        "Skipping trash move because the originals storage backend "
                        "does not expose local paths.",
                    )
                    return
            else:
                logger.warning(
                    "Skipping EMPTY_TRASH_DIR for non-local document storage backend.",
                )

        files = [
            ("archive", instance.archive_name),
            ("thumbnails", instance.thumbnail_name),
        ]
        if not settings.EMPTY_TRASH_DIR:
            files.append(("originals", instance.source_name))

        for kind, name in files:
            if not name:
                continue
            if document_exists(kind, name):
                document_delete(kind, name)
                logger.debug(f'Deleted "{kind}" file {name}.')
            else:
                logger.warning(
                    f'Expected "{kind}" file {name} to exist, but it did not',
                )

        delete_empty_directories(
            settings.ORIGINALS_DIR / Path(instance.source_name).parent,
            root=settings.ORIGINALS_DIR,
        )

        if instance.has_archive_version and instance.archive_name is not None:
            delete_empty_directories(
                settings.ARCHIVE_DIR / Path(instance.archive_name).parent,
                root=settings.ARCHIVE_DIR,
            )


class CannotMoveFilesException(Exception):
    pass


def _path_matches_checksum(kind: str, name: str, checksum: str | None) -> bool:
    return document_checksum_matches(kind, name, checksum)


def _filename_template_uses_custom_fields(doc: Document) -> bool:
    template = None
    if doc.storage_path is not None:
        template = doc.storage_path.path
    elif settings.FILENAME_FORMAT is not None:
        template = convert_format_str_to_template_format(settings.FILENAME_FORMAT)

    if not template:
        return False

    return "custom_fields" in template


# should be disabled in /src/documents/management/commands/document_importer.py handle
@receiver(models.signals.post_save, sender=CustomFieldInstance, weak=False)
@receiver(models.signals.m2m_changed, sender=Document.tags.through, weak=False)
@receiver(models.signals.post_save, sender=Document, weak=False)
def update_filename_and_move_files(
    sender,
    instance: Document | CustomFieldInstance,
    **kwargs,
):
    if isinstance(instance, CustomFieldInstance):
        if not _filename_template_uses_custom_fields(instance.document):
            return
        instance = instance.document

    def validate_move(
        instance,
        *,
        kind: str,
        old_name: str,
        new_name: str,
        old_path: Path | None,
        new_path: Path | None,
        root: Path,
    ):
        if (
            document_storage_is_local(kind)
            and new_path is not None
            and not new_path.is_relative_to(root)
        ):
            msg = (
                f"Document {instance!s}: Refusing to move file outside root {root}: "
                f"{new_path}."
            )
            logger.warning(msg)
            raise CannotMoveFilesException(msg)

        if not document_exists(kind, old_name):
            # Can't do anything if the old file does not exist anymore.
            msg = f'Document {instance!s}: File "{old_name}" does not exist.'
            logger.fatal(msg)
            raise CannotMoveFilesException(msg)

        if document_exists(kind, new_name):
            # Can't do anything if the new file already exists. Skip updating file.
            msg = f'Document {instance!s}: Cannot rename file since target path "{new_name}" already exists.'
            logger.warning(msg)
            raise CannotMoveFilesException(msg)

    if not instance.filename:
        # Can't update the filename if there is no filename to begin with
        # This happens when the consumer creates a new document.
        # The document is modified and saved multiple times, and only after
        # everything is done (i.e., the generated filename is final),
        # filename will be set to the location where the consumer has put
        # the file.
        #
        # This will in turn cause this logic to move the file where it belongs.
        return

    def _safe_local_path(kind: str) -> Path | None:
        if kind == "originals":
            local_path = _local_document_path("originals", str(instance.filename))
            if local_path.exists():
                return local_path
        elif kind == "archive" and instance.has_archive_version:
            local_path = _local_document_path("archive", str(instance.archive_filename))
            if local_path.exists():
                return local_path
        return None

    with FileLock(settings.MEDIA_LOCK):
        try:
            # If this was waiting for the lock, the filename or archive_filename
            # of this document may have been updated.  This happens if multiple updates
            # get queued from the UI for the same document
            # So freshen up the data before doing anything
            instance.refresh_from_db()

            old_filename = instance.filename
            old_source_path = _safe_local_path("originals")
            move_original = False
            original_already_moved = False

            old_archive_filename = instance.archive_filename
            old_archive_path = _safe_local_path("archive")
            move_archive = False
            archive_already_moved = False

            candidate_filename = generate_filename(instance)
            if len(str(candidate_filename)) > Document.MAX_STORED_FILENAME_LENGTH:
                msg = (
                    f"Document {instance!s}: Generated filename exceeds db path "
                    f"limit ({len(str(candidate_filename))} > "
                    f"{Document.MAX_STORED_FILENAME_LENGTH}): {candidate_filename!s}"
                )
                logger.warning(msg)
                raise CannotMoveFilesException(msg)

            if candidate_filename == Path(old_filename):
                new_filename = Path(old_filename)
            elif document_exists(
                "originals",
                str(candidate_filename),
            ) and candidate_filename != Path(old_filename):
                if not document_exists(
                    "originals",
                    old_filename,
                ) and _path_matches_checksum(
                    "originals",
                    str(candidate_filename),
                    instance.checksum,
                ):
                    new_filename = candidate_filename
                    original_already_moved = True
                else:
                    # Only fall back to unique search when there is an actual conflict
                    new_filename = generate_unique_filename(instance)
            else:
                new_filename = candidate_filename

            # Need to convert to string to be able to save it to the db
            instance.filename = str(new_filename)
            move_original = (
                old_filename != instance.filename and not original_already_moved
            )

            if instance.has_archive_version:
                archive_candidate = generate_filename(instance, archive_filename=True)
                if len(str(archive_candidate)) > Document.MAX_STORED_FILENAME_LENGTH:
                    msg = (
                        f"Document {instance!s}: Generated archive filename exceeds "
                        f"db path limit ({len(str(archive_candidate))} > "
                        f"{Document.MAX_STORED_FILENAME_LENGTH}): {archive_candidate!s}"
                    )
                    logger.warning(msg)
                    raise CannotMoveFilesException(msg)
                if archive_candidate == Path(old_archive_filename):
                    new_archive_filename = Path(old_archive_filename)
                elif document_exists(
                    "archive",
                    str(archive_candidate),
                ) and archive_candidate != Path(old_archive_filename):
                    if not document_exists(
                        "archive",
                        str(old_archive_filename),
                    ) and _path_matches_checksum(
                        "archive",
                        str(archive_candidate),
                        instance.archive_checksum,
                    ):
                        new_archive_filename = archive_candidate
                        archive_already_moved = True
                    else:
                        new_archive_filename = generate_unique_filename(
                            instance,
                            archive_filename=True,
                        )
                else:
                    new_archive_filename = archive_candidate

                instance.archive_filename = str(new_archive_filename)

                move_archive = (
                    old_archive_filename != instance.archive_filename
                    and not archive_already_moved
                )
            else:
                move_archive = False

            if not move_original and not move_archive:
                updates = {"modified": timezone.now()}
                if old_filename != instance.filename:
                    updates["filename"] = instance.filename
                if old_archive_filename != instance.archive_filename:
                    updates["archive_filename"] = instance.archive_filename

                # Don't save() here to prevent infinite recursion.
                Document.objects.filter(pk=instance.pk).update(**updates)
                return

            if move_original:
                validate_move(
                    instance,
                    kind="originals",
                    old_name=str(old_filename),
                    new_name=str(instance.filename),
                    old_path=old_source_path,
                    new_path=_safe_local_path("originals"),
                    root=settings.ORIGINALS_DIR,
                )
                document_move("originals", str(old_filename), str(instance.filename))

            if move_archive:
                validate_move(
                    instance,
                    kind="archive",
                    old_name=str(old_archive_filename),
                    new_name=str(instance.archive_filename),
                    old_path=old_archive_path,
                    new_path=_safe_local_path("archive"),
                    root=settings.ARCHIVE_DIR,
                )
                document_move(
                    "archive",
                    str(old_archive_filename),
                    str(instance.archive_filename),
                )

            # Don't save() here to prevent infinite recursion.
            Document.global_objects.filter(pk=instance.pk).update(
                filename=instance.filename,
                archive_filename=instance.archive_filename,
                modified=timezone.now(),
            )
            # Clear any caching for this document.  Slightly overkill, but not terrible
            clear_document_caches(instance.pk)

        except (OSError, DatabaseError, CannotMoveFilesException) as e:
            logger.warning(f"Exception during file handling: {e}")
            # This happens when either:
            #  - moving the files failed due to file system errors
            #  - saving to the database failed due to database errors
            # In both cases, we need to revert to the original state.

            # Try to move files to their original location.
            try:
                if move_original and document_exists(
                    "originals",
                    str(instance.filename),
                ):
                    logger.info("Restoring previous original path")
                    document_move(
                        "originals",
                        str(instance.filename),
                        str(old_filename),
                    )

                if move_archive and document_exists(
                    "archive",
                    str(instance.archive_filename),
                ):
                    logger.info("Restoring previous archive path")
                    document_move(
                        "archive",
                        str(instance.archive_filename),
                        str(old_archive_filename),
                    )

            except Exception:
                # This is fine, since:
                # A: if we managed to move source from A to B, we will also
                #  manage to move it from B to A. If not, we have a serious
                #  issue that's going to get caught by the santiy checker.
                #  All files remain in place and will never be overwritten,
                #  so this is not the end of the world.
                # B: if moving the original file failed, nothing has changed
                #  anyway.
                pass

            # restore old values on the instance
            instance.filename = old_filename
            instance.archive_filename = old_archive_filename

        # finally, remove any empty sub folders. This will do nothing if
        # something has failed above.
        if not document_exists("originals", str(old_filename)):
            delete_empty_directories(
                settings.ORIGINALS_DIR / Path(str(old_filename)).parent,
                root=settings.ORIGINALS_DIR,
            )

        if (
            instance.has_archive_version
            and old_archive_filename is not None
            and not document_exists("archive", str(old_archive_filename))
        ):
            delete_empty_directories(
                settings.ARCHIVE_DIR / Path(str(old_archive_filename)).parent,
                root=settings.ARCHIVE_DIR,
            )


@shared_task
def process_cf_select_update(custom_field: CustomField):
    """
    Update documents tied to a select custom field:

    1. 'Select' custom field instances get their end-user value (e.g. in file names) from the select_options in extra_data,
    which is contained in the custom field itself. So when the field is changed, we (may) need to update the file names
    of all documents that have this custom field.
    2. If a 'Select' field option was removed, we need to nullify the custom field instances that have the option.
    """
    select_options = {
        option["id"]: option["label"]
        for option in custom_field.extra_data.get("select_options", [])
    }

    # Clear select values that no longer exist
    custom_field.fields.exclude(
        value_select__in=select_options.keys(),
    ).update(value_select=None)

    for cf_instance in custom_field.fields.select_related("document").iterator():
        # Update the filename and move files if necessary
        update_filename_and_move_files(CustomFieldInstance, cf_instance)


# should be disabled in /src/documents/management/commands/document_importer.py handle
@receiver(models.signals.post_save, sender=CustomField)
def check_paths_and_prune_custom_fields(sender, instance: CustomField, **kwargs):
    """
    When a custom field is updated, check if we need to update any documents. Done async to avoid slowing down the save operation.
    """
    if (
        instance.data_type == CustomField.FieldDataType.SELECT
        and instance.fields.count() > 0
        and instance.extra_data
    ):  # Only select fields, for now
        process_cf_select_update.delay(instance)


@receiver(models.signals.post_delete, sender=CustomField)
def cleanup_custom_field_deletion(sender, instance: CustomField, **kwargs):
    """
    When a custom field is deleted, ensure no saved views reference it.
    """
    field_identifier = SavedView.DisplayFields.CUSTOM_FIELD % instance.pk
    # remove field from display_fields of all saved views
    for view in SavedView.objects.filter(display_fields__isnull=False).distinct():
        if field_identifier in view.display_fields:
            logger.debug(
                f"Removing custom field {instance} from view {view}",
            )
            view.display_fields.remove(field_identifier)
            view.save()

    # remove from sort_field of all saved views
    views_with_sort_updated = SavedView.objects.filter(
        sort_field=field_identifier,
    ).update(
        sort_field=SavedView.DisplayFields.CREATED,
    )
    if views_with_sort_updated > 0:
        logger.debug(
            f"Removing custom field {instance} from sort field of {views_with_sort_updated} views",
        )


@receiver(models.signals.post_delete, sender=User)
@receiver(models.signals.post_delete, sender=Group)
def cleanup_user_deletion(sender, instance: User | Group, **kwargs):
    """
    When a user or group is deleted, remove non-cascading references.
    At the moment, just the default permission settings in UiSettings.
    """
    # Remove the user permission settings e.g.
    #   DEFAULT_PERMS_OWNER: 'general-settings:permissions:default-owner',
    #   DEFAULT_PERMS_VIEW_USERS: 'general-settings:permissions:default-view-users',
    #   DEFAULT_PERMS_VIEW_GROUPS: 'general-settings:permissions:default-view-groups',
    #   DEFAULT_PERMS_EDIT_USERS: 'general-settings:permissions:default-edit-users',
    #   DEFAULT_PERMS_EDIT_GROUPS: 'general-settings:permissions:default-edit-groups',
    for ui_settings in UiSettings.objects.all():
        try:
            permissions = ui_settings.settings.get("permissions", {})
            updated = False
            if isinstance(instance, User):
                if permissions.get("default_owner") == instance.pk:
                    permissions["default_owner"] = None
                    updated = True
                if instance.pk in permissions.get("default_view_users", []):
                    permissions["default_view_users"].remove(instance.pk)
                    updated = True
                if instance.pk in permissions.get("default_change_users", []):
                    permissions["default_change_users"].remove(instance.pk)
                    updated = True
            elif isinstance(instance, Group):
                if instance.pk in permissions.get("default_view_groups", []):
                    permissions["default_view_groups"].remove(instance.pk)
                    updated = True
                if instance.pk in permissions.get("default_change_groups", []):
                    permissions["default_change_groups"].remove(instance.pk)
                    updated = True
            if updated:
                ui_settings.settings["permissions"] = permissions
                ui_settings.save(update_fields=["settings"])
        except Exception as e:
            logger.error(
                f"Error while cleaning up user {instance.pk} ({instance.username}) from ui_settings: {e}"
                if isinstance(instance, User)
                else f"Error while cleaning up group {instance.pk} ({instance.name}) from ui_settings: {e}",
            )


def add_to_index(sender, document, **kwargs):
    from documents import index

    index.add_or_update_document(document)


def run_workflows_added(
    sender,
    document: Document,
    logging_group=None,
    original_file=None,
    **kwargs,
):
    run_workflows(
        trigger_type=WorkflowTrigger.WorkflowTriggerType.DOCUMENT_ADDED,
        document=document,
        logging_group=logging_group,
        overrides=None,
        original_file=original_file,
    )


def run_workflows_updated(sender, document: Document, logging_group=None, **kwargs):
    run_workflows(
        trigger_type=WorkflowTrigger.WorkflowTriggerType.DOCUMENT_UPDATED,
        document=document,
        logging_group=logging_group,
    )


def run_workflows(
    trigger_type: WorkflowTrigger.WorkflowTriggerType,
    document: Document | ConsumableDocument,
    workflow_to_run: Workflow | None = None,
    logging_group=None,
    overrides: DocumentMetadataOverrides | None = None,
    original_file: Path | None = None,
    started_by: User | None = None,
) -> tuple[DocumentMetadataOverrides, str] | None:
    """
    Execute workflows matching a document for the given trigger. When `overrides` is provided
    (consumption flow), actions mutate that object and the function returns `(overrides, messages)`.
    Otherwise actions mutate the actual document and return nothing.

    Attachments for email/webhook actions use `original_file` when given, otherwise fall back to
    `document.source_path` (Document) or `document.original_file` (ConsumableDocument).

    Passing `workflow_to_run` skips the workflow query (currently only used by scheduled runs).
    """

    use_overrides = overrides is not None
    original_file_context = nullcontext(original_file)
    if original_file is None:
        original_file_context = (
            document.local_source_path()
            if not use_overrides
            else nullcontext(document.original_file)
        )

    with original_file_context as resolved_original_file:
        original_file = resolved_original_file
        messages = []

        workflows = get_workflows_for_trigger(trigger_type, workflow_to_run)

        for workflow in workflows:
            if not use_overrides:
                # This can be called from bulk_update_documents, which may be running multiple times
                # Refresh this so the matching data is fresh and instance fields are re-freshed
                # Otherwise, this instance might be behind and overwrite the work another process did
                document.refresh_from_db()
                doc_tag_ids = list(document.tags.values_list("pk", flat=True))

            if matching.document_matches_workflow(document, workflow, trigger_type):
                workflow_run = WorkflowRun.objects.create(
                    workflow=workflow,
                    type=trigger_type,
                    document=document if not use_overrides else None,
                    run_at=timezone.now(),
                    started_at=timezone.now(),
                    status=WorkflowRun.WorkflowRunStatus.RUNNING,
                    started_by=started_by,
                    message="Workflow started",
                )
                if use_overrides:
                    messages.append(f"Running {workflow}")

                workflow_paused = _execute_workflow_actions(
                    workflow_run,
                    workflow,
                    trigger_type,
                    document,
                    logging_group=logging_group,
                    use_overrides=use_overrides,
                    overrides=overrides,
                    original_file=original_file,
                    started_by=started_by,
                    doc_tag_ids=doc_tag_ids if not use_overrides else None,
                )

                if not workflow_paused:
                    _finalize_workflow_run(workflow_run)

        if use_overrides:
            return overrides, "\n".join(messages)


@before_task_publish.connect
def before_task_publish_handler(sender=None, headers=None, body=None, **kwargs):
    """
    Creates the PaperlessTask object in a pending state.  This is sent before
    the task reaches the broker, but before it begins executing on a worker.

    https://docs.celeryq.dev/en/stable/userguide/signals.html#before-task-publish

    https://docs.celeryq.dev/en/stable/internals/protocol.html#version-2

    """
    if "task" not in headers or headers["task"] != "documents.tasks.consume_file":
        # Assumption: this is only ever a v2 message
        return

    try:
        close_old_connections()

        task_args = body[0]
        input_doc, overrides = task_args

        task_file_name = input_doc.original_file.name
        user_id = overrides.owner_id if overrides else None

        PaperlessTask.objects.create(
            type=PaperlessTask.TaskType.AUTO,
            task_id=headers["id"],
            status=states.PENDING,
            task_file_name=task_file_name,
            task_name=PaperlessTask.TaskName.CONSUME_FILE,
            result=None,
            date_created=timezone.now(),
            date_started=None,
            date_done=None,
            owner_id=user_id,
        )
    except Exception:  # pragma: no cover
        # Don't let an exception in the signal handlers prevent
        # a document from being consumed.
        logger.exception("Creating PaperlessTask failed")


@task_prerun.connect
def task_prerun_handler(sender=None, task_id=None, task=None, **kwargs):
    """

    Updates the PaperlessTask to be started.  Sent before the task begins execution
    on a worker.

    https://docs.celeryq.dev/en/stable/userguide/signals.html#task-prerun
    """
    try:
        close_old_connections()
        task_instance = PaperlessTask.objects.filter(task_id=task_id).first()

        if task_instance is not None:
            task_instance.status = states.STARTED
            task_instance.date_started = timezone.now()
            task_instance.save()
    except Exception:  # pragma: no cover
        # Don't let an exception in the signal handlers prevent
        # a document from being consumed.
        logger.exception("Setting PaperlessTask started failed")


@task_postrun.connect
def task_postrun_handler(
    sender=None,
    task_id=None,
    task=None,
    retval=None,
    state=None,
    **kwargs,
):
    """
    Updates the result of the PaperlessTask.

    https://docs.celeryq.dev/en/stable/userguide/signals.html#task-postrun
    """
    try:
        close_old_connections()
        task_instance = PaperlessTask.objects.filter(task_id=task_id).first()

        if task_instance is not None:
            task_instance.status = state
            task_instance.result = retval
            task_instance.date_done = timezone.now()
            task_instance.save()
    except Exception:  # pragma: no cover
        # Don't let an exception in the signal handlers prevent
        # a document from being consumed.
        logger.exception("Updating PaperlessTask failed")


@task_failure.connect
def task_failure_handler(
    sender=None,
    task_id=None,
    exception=None,
    args=None,
    traceback=None,
    **kwargs,
):
    """
    Updates the result of a failed PaperlessTask.

    https://docs.celeryq.dev/en/stable/userguide/signals.html#task-failure
    """
    try:
        close_old_connections()
        task_instance = PaperlessTask.objects.filter(task_id=task_id).first()

        if task_instance is not None and task_instance.result is None:
            task_instance.status = states.FAILURE
            task_instance.result = traceback
            task_instance.date_done = timezone.now()
            task_instance.save()
    except Exception:  # pragma: no cover
        logger.exception("Updating PaperlessTask failed")


@worker_process_init.connect
def close_connection_pool_on_worker_init(**kwargs):
    """
    Close the DB connection pool for each Celery child process after it starts.

    This is necessary because the parent process parse the Django configuration,
    initializes connection pools then forks.

    Closing these pools after forking ensures child processes have a valid connection.
    """
    for conn in connections.all(initialized_only=True):
        if conn.alias == "default" and hasattr(conn, "pool") and conn.pool:
            conn.close_pool()
