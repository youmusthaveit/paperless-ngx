import tqdm
from django.core.management.base import BaseCommand
from django.core.management.base import CommandError

from documents.management.commands.mixins import ProgressBarMixin
from documents.models import Document
from documents.storage import backup_document_write_from_path
from paperless.config import DocumentBackupConfig


def _backup_document(document: Document) -> None:
    if document.source_exists():
        with document.local_source_path() as source_path:
            backup_document_write_from_path(
                "originals",
                document.source_name,
                source_path,
            )

    if document.archive_exists() and document.archive_name is not None:
        with document.local_archive_path() as archive_path:
            backup_document_write_from_path(
                "archive",
                document.archive_name,
                archive_path,
            )

    if document.thumbnail_exists():
        with document.local_thumbnail_path() as thumbnail_path:
            backup_document_write_from_path(
                "thumbnails",
                document.thumbnail_name,
                thumbnail_path,
            )


class Command(ProgressBarMixin, BaseCommand):
    help = "Backup existing originals, archive files and thumbnails to the configured S3 backup storage."

    def add_arguments(self, parser):
        parser.add_argument(
            "-d",
            "--document",
            default=None,
            type=int,
            required=False,
            help="Only backup the specified document ID.",
        )
        self.add_argument_progress_bar_mixin(parser)

    def handle(self, *args, **options):
        self.handle_progress_bar_mixin(**options)

        backup_config = DocumentBackupConfig()
        if not backup_config.is_configured:
            raise CommandError(
                "S3 document backup is not configured. Set PAPERLESS_DOCUMENTS_BACKUP_S3_BUCKET or configure it in the application settings.",
            )

        if options["document"]:
            documents = Document.objects.filter(pk=options["document"])
        else:
            documents = Document.objects.all()

        total = documents.count()
        if total == 0:
            self.stdout.write(self.style.WARNING("No documents found to backup."))
            return

        for document in tqdm.tqdm(
            documents.iterator(),
            total=total,
            disable=self.no_progress_bar,
        ):
            _backup_document(document)

        self.stdout.write(
            self.style.SUCCESS(
                f"Backed up {total} document(s) to S3 backup storage.",
            ),
        )
