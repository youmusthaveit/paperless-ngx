import shutil
import zipfile
from datetime import UTC
from datetime import timedelta
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.test import TestCase
from django.utils import timezone

from documents import tasks
from documents.models import Correspondent
from documents.models import Document
from documents.models import DocumentType
from documents.models import PaperlessTask
from documents.models import Tag
from documents.sanity_checker import SanityCheckFailedException
from documents.sanity_checker import SanityCheckMessages
from documents.tests.test_classifier import dummy_preprocess
from documents.tests.utils import DirectoriesMixin
from documents.tests.utils import FileSystemAssertsMixin
from paperless.models import ApplicationConfiguration
from paperless.models import S3StorageConfiguration


class TestIndexReindex(DirectoriesMixin, TestCase):
    def test_index_reindex(self):
        Document.objects.create(
            title="test",
            content="my document",
            checksum="wow",
            added=timezone.now(),
            created=timezone.now(),
            modified=timezone.now(),
        )

        tasks.index_reindex()

    def test_index_optimize(self):
        Document.objects.create(
            title="test",
            content="my document",
            checksum="wow",
            added=timezone.now(),
            created=timezone.now(),
            modified=timezone.now(),
        )

        tasks.index_optimize()


class TestClassifier(DirectoriesMixin, FileSystemAssertsMixin, TestCase):
    @mock.patch("documents.tasks.load_classifier")
    def test_train_classifier_no_auto_matching(self, load_classifier):
        tasks.train_classifier()
        load_classifier.assert_not_called()

    @mock.patch("documents.tasks.load_classifier")
    def test_train_classifier_with_auto_tag(self, load_classifier):
        load_classifier.return_value = None
        Tag.objects.create(matching_algorithm=Tag.MATCH_AUTO, name="test")
        tasks.train_classifier()
        load_classifier.assert_called_once()
        self.assertIsNotFile(settings.MODEL_FILE)

    @mock.patch("documents.tasks.load_classifier")
    def test_train_classifier_with_auto_type(self, load_classifier):
        load_classifier.return_value = None
        DocumentType.objects.create(matching_algorithm=Tag.MATCH_AUTO, name="test")
        tasks.train_classifier()
        load_classifier.assert_called_once()
        self.assertIsNotFile(settings.MODEL_FILE)

    @mock.patch("documents.tasks.load_classifier")
    def test_train_classifier_with_auto_correspondent(self, load_classifier):
        load_classifier.return_value = None
        Correspondent.objects.create(matching_algorithm=Tag.MATCH_AUTO, name="test")
        tasks.train_classifier()
        load_classifier.assert_called_once()
        self.assertIsNotFile(settings.MODEL_FILE)

    def test_train_classifier(self):
        c = Correspondent.objects.create(matching_algorithm=Tag.MATCH_AUTO, name="test")
        doc = Document.objects.create(correspondent=c, content="test", title="test")
        self.assertIsNotFile(settings.MODEL_FILE)

        with mock.patch(
            "documents.classifier.DocumentClassifier.preprocess_content",
        ) as pre_proc_mock:
            pre_proc_mock.side_effect = dummy_preprocess

            tasks.train_classifier()
            self.assertIsFile(settings.MODEL_FILE)
            mtime = Path(settings.MODEL_FILE).stat().st_mtime

            tasks.train_classifier()
            self.assertIsFile(settings.MODEL_FILE)
            mtime2 = Path(settings.MODEL_FILE).stat().st_mtime
            self.assertEqual(mtime, mtime2)

            doc.content = "test2"
            doc.save()
            tasks.train_classifier()
            self.assertIsFile(settings.MODEL_FILE)
            mtime3 = Path(settings.MODEL_FILE).stat().st_mtime
            self.assertNotEqual(mtime2, mtime3)


class TestSanityCheck(DirectoriesMixin, TestCase):
    @mock.patch("documents.tasks.sanity_checker.check_sanity")
    def test_sanity_check_success(self, m):
        m.return_value = SanityCheckMessages()
        self.assertEqual(tasks.sanity_check(), "No issues detected.")
        m.assert_called_once()

    @mock.patch("documents.tasks.sanity_checker.check_sanity")
    def test_sanity_check_error(self, m):
        messages = SanityCheckMessages()
        messages.error(None, "Some error")
        m.return_value = messages
        self.assertRaises(SanityCheckFailedException, tasks.sanity_check)
        m.assert_called_once()

    @mock.patch("documents.tasks.sanity_checker.check_sanity")
    def test_sanity_check_error_no_raise(self, m):
        messages = SanityCheckMessages()
        messages.error(None, "Some error")
        m.return_value = messages
        # No exception should be raised
        result = tasks.sanity_check(raise_on_error=False)
        self.assertEqual(
            result,
            "Sanity check exited with errors. See log.",
        )
        m.assert_called_once()

    @mock.patch("documents.tasks.sanity_checker.check_sanity")
    def test_sanity_check_warning(self, m):
        messages = SanityCheckMessages()
        messages.warning(None, "Some warning")
        m.return_value = messages
        self.assertEqual(
            tasks.sanity_check(),
            "Sanity check exited with warnings. See log.",
        )
        m.assert_called_once()

    @mock.patch("documents.tasks.sanity_checker.check_sanity")
    def test_sanity_check_info(self, m):
        messages = SanityCheckMessages()
        messages.info(None, "Some info")
        m.return_value = messages
        self.assertEqual(
            tasks.sanity_check(),
            "Sanity check exited with infos. See log.",
        )
        m.assert_called_once()


class TestBulkUpdate(DirectoriesMixin, TestCase):
    def test_bulk_update_documents(self):
        doc1 = Document.objects.create(
            title="test",
            content="my document",
            checksum="wow",
            added=timezone.now(),
            created=timezone.now(),
            modified=timezone.now(),
        )

        tasks.bulk_update_documents([doc1.pk])


class TestManualS3TransferTasks(DirectoriesMixin, TestCase):
    @mock.patch("documents.tasks.timezone.now")
    def test_build_manual_s3_export_filename_includes_subseconds(self, now_mock):
        now_mock.return_value = timezone.datetime(
            2026,
            3,
            27,
            12,
            0,
            0,
            123456,
            tzinfo=UTC,
        )

        export_name = tasks.build_manual_s3_export_filename()

        self.assertEqual(
            export_name,
            "paperless-manual-export-20260327T120000123456Z.zip",
        )

    @mock.patch("documents.tasks.timezone.now")
    def test_build_automatic_s3_export_path_uses_date_folder(self, now_mock):
        now_mock.return_value = timezone.datetime(
            2026,
            3,
            27,
            12,
            0,
            0,
            123456,
            tzinfo=UTC,
        )

        export_path = tasks.build_automatic_s3_export_path()

        self.assertEqual(
            export_path,
            "2026-03-27/paperless-automatic-export-20260327T120000123456Z.zip",
        )

    @mock.patch("documents.tasks.call_command")
    @mock.patch(
        "documents.tasks.build_manual_s3_export_filename",
        return_value="paperless-manual-export-20260327T120000123456Z.zip",
    )
    def test_export_documents_to_s3_storage_uses_folder_structure(
        self,
        _build_export_filename,
        call_command_mock,
    ):
        storage_config = S3StorageConfiguration(name="Backup Storage")
        storage = mock.Mock()

        def export_side_effect(command_name, export_dir, **kwargs):
            self.assertEqual(command_name, "document_exporter")
            export_path = (
                Path(export_dir) / "paperless-manual-export-20260327T120000123456Z.zip"
            )
            with zipfile.ZipFile(export_path, "w") as zip_file:
                zip_file.writestr("originals/customer-a/document.pdf", b"test")

        call_command_mock.side_effect = export_side_effect

        with mock.patch(
            "documents.tasks._get_manual_s3_transfer_storage",
            return_value=(storage_config, storage),
        ):
            tasks.export_documents_to_s3_storage(1)

        call_command_mock.assert_called_once_with(
            "document_exporter",
            mock.ANY,
            zip=True,
            zip_name="paperless-manual-export-20260327T120000123456Z",
            use_filename_format=True,
            use_folder_prefix=True,
            no_progress_bar=True,
        )
        storage.save.assert_called_once()

    @mock.patch("documents.tasks.call_command")
    @mock.patch(
        "documents.tasks.build_automatic_s3_export_path",
        return_value="2026-03-27/paperless-automatic-export-20260327T120000123456Z.zip",
    )
    def test_export_documents_to_automatic_s3_storage_uses_date_folder(
        self,
        _build_export_path,
        call_command_mock,
    ):
        storage_config = S3StorageConfiguration(name="Backup Storage")
        storage = mock.Mock()

        def export_side_effect(command_name, export_dir, **kwargs):
            self.assertEqual(command_name, "document_exporter")
            export_path = (
                Path(export_dir)
                / "paperless-automatic-export-20260327T120000123456Z.zip"
            )
            with zipfile.ZipFile(export_path, "w") as zip_file:
                zip_file.writestr("originals/customer-a/document.pdf", b"test")

        call_command_mock.side_effect = export_side_effect

        with mock.patch(
            "documents.tasks._get_automatic_s3_transfer_storage",
            return_value=(storage_config, storage),
        ):
            tasks._export_documents_to_automatic_s3_storage(1)

        call_command_mock.assert_called_once_with(
            "document_exporter",
            mock.ANY,
            zip=True,
            zip_name="paperless-automatic-export-20260327T120000123456Z",
            use_filename_format=True,
            use_folder_prefix=True,
            no_progress_bar=True,
        )
        storage.save.assert_called_once()
        self.assertEqual(
            storage.save.call_args.args[0],
            "2026-03-27/paperless-automatic-export-20260327T120000123456Z.zip",
        )

    @mock.patch("documents.tasks.call_command")
    def test_import_documents_from_s3_storage_imports_selected_export(
        self,
        call_command_mock,
    ):
        storage_config = S3StorageConfiguration(name="Backup Storage")
        storage = mock.Mock()
        export_name = "paperless-manual-export-20260327T120000Z.zip"
        storage.exists.return_value = True
        dummy_zip = self.dirs.scratch_dir / "dummy.zip"
        with zipfile.ZipFile(dummy_zip, "w") as zip_file:
            zip_file.writestr("originals/customer-a/document.pdf", b"test")

        storage.open.return_value = dummy_zip.open("rb")
        self.addCleanup(lambda: dummy_zip.unlink(missing_ok=True))

        with mock.patch(
            "documents.tasks._get_s3_transfer_storages",
            return_value=(storage_config, [("manual-transfer", storage)]),
        ):
            tasks.import_documents_from_s3_storage(1, export_name, owner_id=42)

        call_command_mock.assert_called_once_with(
            "document_importer",
            mock.ANY,
            no_progress_bar=True,
            task_owner_id=42,
        )

    @mock.patch("documents.tasks.call_command")
    def test_import_documents_from_s3_storage_imports_automatic_export(
        self,
        call_command_mock,
    ):
        storage = mock.Mock()
        storage.exists.return_value = True
        export_name = "2026-03-27/paperless-automatic-export-20260327T120000123456Z.zip"
        dummy_zip = self.dirs.scratch_dir / "dummy-automatic.zip"
        with zipfile.ZipFile(dummy_zip, "w") as zip_file:
            zip_file.writestr("originals/customer-a/document.pdf", b"test")

        storage.open.return_value = dummy_zip.open("rb")
        self.addCleanup(lambda: dummy_zip.unlink(missing_ok=True))

        with mock.patch(
            "documents.tasks._get_s3_transfer_storages",
            return_value=(
                S3StorageConfiguration(name="Backup Storage"),
                [
                    (
                        "manual-transfer",
                        mock.Mock(exists=mock.Mock(return_value=False)),
                    ),
                    ("automatic-transfer", storage),
                ],
            ),
        ):
            tasks.import_documents_from_s3_storage(1, export_name, owner_id=42)

        call_command_mock.assert_called_once_with(
            "document_importer",
            mock.ANY,
            no_progress_bar=True,
            task_owner_id=42,
        )

    def test_scheduled_s3_backup_exports_not_due(self):
        storage = S3StorageConfiguration.objects.create(
            name="Backup Storage",
            bucket="bucket",
        )
        ApplicationConfiguration.objects.update_or_create(
            pk=1,
            defaults={
                "documents_backup_schedule_jobs": [
                    {
                        "name": "Nightly",
                        "enabled": True,
                        "storage": storage.pk,
                        "frequency_days": 1,
                        "hour": 23,
                        "minute": 59,
                        "retain_count": 3,
                        "last_run": None,
                    },
                ],
            },
        )

        result = tasks.run_scheduled_s3_backup_exports()

        self.assertEqual(result, tasks.AUTOMATIC_S3_BACKUP_CHECK_RESULT_NOT_DUE)
        self.assertEqual(
            PaperlessTask.objects.filter(
                task_name=PaperlessTask.TaskName.SCHEDULED_BACKUP_S3_STORAGE,
            ).count(),
            0,
        )

    @mock.patch("documents.tasks._rotate_automatic_s3_exports")
    @mock.patch("documents.tasks._export_documents_to_automatic_s3_storage")
    @mock.patch("documents.tasks.timezone.now")
    def test_scheduled_s3_backup_exports_runs_and_rotates(
        self,
        now_mock,
        export_mock,
        rotate_mock,
    ):
        now_mock.return_value = timezone.datetime(
            2026,
            3,
            27,
            2,
            0,
            0,
            tzinfo=UTC,
        )
        storage = S3StorageConfiguration.objects.create(
            name="Backup Storage",
            bucket="bucket",
        )
        config, _ = ApplicationConfiguration.objects.update_or_create(
            pk=1,
            defaults={
                "documents_backup_schedule_jobs": [
                    {
                        "name": "Nightly",
                        "enabled": True,
                        "storage": storage.pk,
                        "frequency_days": 1,
                        "hour": 2,
                        "minute": 0,
                        "retain_count": 2,
                        "last_run": None,
                    },
                ],
            },
        )
        export_mock.return_value = "Successfully exported documents."
        rotate_mock.return_value = ["old-export.zip"]

        result = tasks.run_scheduled_s3_backup_exports()

        self.assertIn("Successfully exported documents.", result)
        self.assertIn("old-export.zip", result)
        export_mock.assert_called_once_with(storage.pk)
        rotate_mock.assert_called_once_with(storage.pk, 2)
        config.refresh_from_db()
        self.assertEqual(
            config.documents_backup_schedule_jobs[0]["last_run"],
            now_mock.return_value.isoformat(),
        )
        task = PaperlessTask.objects.get(
            task_name=PaperlessTask.TaskName.SCHEDULED_BACKUP_S3_STORAGE,
        )
        self.assertEqual(task.status, "SUCCESS")

    @mock.patch("documents.tasks._rotate_automatic_s3_exports")
    @mock.patch("documents.tasks._export_documents_to_automatic_s3_storage")
    @mock.patch("documents.tasks.timezone.now")
    def test_scheduled_s3_backup_exports_runs_multiple_jobs(
        self,
        now_mock,
        export_mock,
        rotate_mock,
    ):
        now_mock.return_value = timezone.datetime(
            2026,
            3,
            27,
            2,
            30,
            0,
            tzinfo=UTC,
        )
        nightly_storage = S3StorageConfiguration.objects.create(
            name="Nightly Storage",
            bucket="nightly",
        )
        weekly_storage = S3StorageConfiguration.objects.create(
            name="Weekly Storage",
            bucket="weekly",
        )
        ApplicationConfiguration.objects.update_or_create(
            pk=1,
            defaults={
                "documents_backup_schedule_jobs": [
                    {
                        "name": "Nightly",
                        "enabled": True,
                        "storage": nightly_storage.pk,
                        "frequency_days": 1,
                        "hour": 2,
                        "minute": 0,
                        "retain_count": 3,
                        "last_run": None,
                    },
                    {
                        "name": "Weekly",
                        "enabled": True,
                        "storage": weekly_storage.pk,
                        "frequency_days": 7,
                        "hour": 2,
                        "minute": 30,
                        "retain_count": 4,
                        "last_run": None,
                    },
                ],
            },
        )
        export_mock.side_effect = [
            "Nightly export complete.",
            "Weekly export complete.",
        ]
        rotate_mock.side_effect = [[], ["old-weekly.zip"]]

        result = tasks.run_scheduled_s3_backup_exports()

        self.assertIn("Nightly export complete.", result)
        self.assertIn("Weekly export complete.", result)
        self.assertIn("old-weekly.zip", result)
        self.assertEqual(export_mock.call_count, 2)
        self.assertEqual(rotate_mock.call_count, 2)

    def test_rotate_automatic_s3_exports_recurses_date_folders(self):
        storage = mock.Mock()
        storage.listdir.side_effect = [
            (["2026-03-27", "2026-03-26"], []),
            (
                [],
                [
                    "paperless-automatic-export-20260327T120000123456Z.zip",
                    "paperless-automatic-export-20260327T110000123456Z.zip",
                ],
            ),
            (
                [],
                [
                    "paperless-automatic-export-20260326T120000123456Z.zip",
                ],
            ),
        ]

        with mock.patch(
            "documents.tasks._get_automatic_s3_transfer_storage",
            return_value=(S3StorageConfiguration(name="Backup Storage"), storage),
        ):
            deleted_exports = tasks._rotate_automatic_s3_exports(1, 2)

        self.assertEqual(
            deleted_exports,
            ["2026-03-26/paperless-automatic-export-20260326T120000123456Z.zip"],
        )
        storage.delete.assert_called_once_with(
            "2026-03-26/paperless-automatic-export-20260326T120000123456Z.zip",
        )


class TestEmptyTrashTask(DirectoriesMixin, FileSystemAssertsMixin, TestCase):
    """
    GIVEN:
        - Existing document in trash
    WHEN:
        - Empty trash task is called without doc_ids
    THEN:
        - Document is only deleted if it has been in trash for more than delay (default 30 days)
    """

    def test_empty_trash(self):
        doc = Document.objects.create(
            title="test",
            content="my document",
            checksum="wow",
            added=timezone.now(),
            created=timezone.now(),
            modified=timezone.now(),
        )

        doc.delete()
        self.assertEqual(Document.global_objects.count(), 1)
        self.assertEqual(Document.objects.count(), 0)
        tasks.empty_trash()
        self.assertEqual(Document.global_objects.count(), 1)

        doc.deleted_at = timezone.now() - timedelta(days=31)
        doc.save()

        tasks.empty_trash()
        self.assertEqual(Document.global_objects.count(), 0)


class TestUpdateContent(DirectoriesMixin, TestCase):
    def test_update_content_maybe_archive_file(self):
        """
        GIVEN:
            - Existing document with archive file
        WHEN:
            - Update content task is called
        THEN:
            - Document is reprocessed, content and checksum are updated
        """
        sample1 = self.dirs.scratch_dir / "sample.pdf"
        shutil.copy(
            Path(__file__).parent
            / "samples"
            / "documents"
            / "originals"
            / "0000001.pdf",
            sample1,
        )
        sample1_archive = self.dirs.archive_dir / "sample_archive.pdf"
        shutil.copy(
            Path(__file__).parent
            / "samples"
            / "documents"
            / "originals"
            / "0000001.pdf",
            sample1_archive,
        )
        doc = Document.objects.create(
            title="test",
            content="my document",
            checksum="wow",
            archive_checksum="wow",
            filename=sample1,
            mime_type="application/pdf",
            archive_filename=sample1_archive,
        )

        tasks.update_document_content_maybe_archive_file(doc.pk)
        self.assertNotEqual(Document.objects.get(pk=doc.pk).content, "test")
        self.assertNotEqual(Document.objects.get(pk=doc.pk).archive_checksum, "wow")

    def test_update_content_maybe_archive_file_no_archive(self):
        """
        GIVEN:
            - Existing document without archive file
        WHEN:
            - Update content task is called
        THEN:
            - Document is reprocessed, content is updated
        """
        sample1 = self.dirs.scratch_dir / "sample.pdf"
        shutil.copy(
            Path(__file__).parent
            / "samples"
            / "documents"
            / "originals"
            / "0000001.pdf",
            sample1,
        )
        doc = Document.objects.create(
            title="test",
            content="my document",
            checksum="wow",
            filename=sample1,
            mime_type="application/pdf",
        )

        tasks.update_document_content_maybe_archive_file(doc.pk)
        self.assertNotEqual(Document.objects.get(pk=doc.pk).content, "test")
