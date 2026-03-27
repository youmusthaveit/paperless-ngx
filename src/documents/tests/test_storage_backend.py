from __future__ import annotations

from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING
from unittest import mock

from django.core.files.base import File
from django.core.files.storage import Storage
from django.core.management import call_command
from django.test import TestCase
from django.test import override_settings
from django.utils import timezone

from documents import storage as document_storage
from documents.file_handling import generate_unique_filename
from documents.models import Document
from documents.models import WorkflowTrigger
from documents.signals import handlers as signal_handlers
from paperless.config import DocumentStorageConfig
from paperless.models import ApplicationConfiguration
from paperless.models import S3StorageConfiguration

if TYPE_CHECKING:
    from datetime import datetime


class FakeMemoryStorage(Storage):
    _files: dict[str, bytes] = {}
    _modified: dict[str, datetime] = {}

    @classmethod
    def clear(cls) -> None:
        cls._files.clear()
        cls._modified.clear()

    def _open(self, name, mode="rb"):
        return File(BytesIO(self._files[name]), name=name)

    def _save(self, name, content):
        self._files[name] = content.read()
        self._modified[name] = timezone.now()
        return name

    def exists(self, name):
        return name in self._files

    def delete(self, name):
        self._files.pop(name, None)
        self._modified.pop(name, None)

    def size(self, name):
        return len(self._files[name])

    def get_modified_time(self, name):
        return self._modified[name]

    def path(self, name):
        raise NotImplementedError


class FakeBackupStorage(FakeMemoryStorage):
    _files: dict[str, bytes] = {}
    _modified: dict[str, datetime] = {}


class RecordingStorage(Storage):
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def _open(self, name, mode="rb"):
        raise NotImplementedError

    def _save(self, name, content):
        raise NotImplementedError

    def exists(self, name):
        return False


class PrefixAwareStorage(Storage):
    _files_by_location: dict[str, dict[str, bytes]] = {}
    _modified_by_location: dict[str, dict[str, datetime]] = {}

    @classmethod
    def clear(cls) -> None:
        cls._files_by_location.clear()
        cls._modified_by_location.clear()

    def __init__(self, **kwargs):
        self.location = kwargs["location"]

    def _open(self, name, mode="rb"):
        return File(BytesIO(self._files_by_location[self.location][name]), name=name)

    def _save(self, name, content):
        self._files_by_location.setdefault(self.location, {})[name] = content.read()
        self._modified_by_location.setdefault(self.location, {})[name] = timezone.now()
        return name

    def exists(self, name):
        return name in self._files_by_location.get(self.location, {})

    def delete(self, name):
        self._files_by_location.get(self.location, {}).pop(name, None)
        self._modified_by_location.get(self.location, {}).pop(name, None)

    def size(self, name):
        return len(self._files_by_location[self.location][name])

    def get_modified_time(self, name):
        return self._modified_by_location[self.location][name]

    def path(self, name):
        raise NotImplementedError


class DocumentStorageBackendTestCase(TestCase):
    def tearDown(self) -> None:
        FakeMemoryStorage.clear()
        FakeBackupStorage.clear()
        PrefixAwareStorage.clear()
        document_storage._build_document_storage.cache_clear()
        document_storage._build_document_backup_storage.cache_clear()
        super().tearDown()

    @override_settings(
        DOCUMENTS_STORAGE_TYPE="local",
    )
    def test_remote_document_storage_roundtrip(self):
        document = Document.objects.create(
            title="Remote storage doc",
            mime_type="application/pdf",
            checksum="checksum-1",
            filename="remote/doc.pdf",
        )

        with mock.patch.object(
            document_storage,
            "_build_document_storage",
            return_value=FakeMemoryStorage(),
        ):
            document.source_write_bytes(b"original")
            self.assertTrue(document.source_exists())
            self.assertEqual(document.source_read_bytes(), b"original")
            self.assertEqual(document.source_size(), len(b"original"))

            with document.local_source_path(writeback=True) as source_path:
                self.assertEqual(source_path.read_bytes(), b"original")
                source_path.write_bytes(b"updated")

            self.assertEqual(document.source_read_bytes(), b"updated")

    @override_settings(
        DOCUMENTS_STORAGE_TYPE="local",
    )
    def test_generate_unique_filename_checks_non_local_storage(self):
        with mock.patch.object(
            document_storage,
            "_build_document_storage",
            return_value=FakeMemoryStorage(),
        ):
            document = Document.objects.create(
                title="Existing doc",
                mime_type="application/pdf",
                checksum="checksum-2",
                filename="0000001.pdf",
            )
            document.source_write_bytes(b"existing")
            FakeMemoryStorage._files["0000001_01.pdf"] = b"conflict"

            other = Document(
                pk=1,
                title="Other duplicate target",
                mime_type="application/pdf",
                checksum="checksum-3",
            )
            unique_name_other = generate_unique_filename(other)
            self.assertEqual(unique_name_other, Path("0000001_02.pdf"))

    def test_local_document_storage_path_roundtrip(self):
        with TemporaryDirectory() as temp_dir:
            with override_settings(
                ORIGINALS_DIR=Path(temp_dir),
                ARCHIVE_DIR=Path(temp_dir),
                THUMBNAIL_DIR=Path(temp_dir),
            ):
                document_storage._build_document_storage.cache_clear()
                document = Document.objects.create(
                    title="Local storage doc",
                    mime_type="application/pdf",
                    checksum="checksum-4",
                    filename="nested/doc.pdf",
                )

                document.source_write_bytes(b"local")
                self.assertTrue(document.source_path.is_file())
                self.assertEqual(document.source_read_bytes(), b"local")

    def test_local_file_is_preferred_over_s3_storage(self):
        with TemporaryDirectory() as temp_dir:
            with override_settings(
                ORIGINALS_DIR=Path(temp_dir),
                ARCHIVE_DIR=Path(temp_dir),
                THUMBNAIL_DIR=Path(temp_dir),
            ):
                local_path = Path(temp_dir) / "fallback.pdf"
                local_path.write_bytes(b"local-first")
                FakeMemoryStorage._files["fallback.pdf"] = b"s3-second"

                with mock.patch.object(
                    document_storage,
                    "_build_document_storage",
                    return_value=FakeMemoryStorage(),
                ):
                    self.assertTrue(
                        document_storage.document_exists("archive", "fallback.pdf"),
                    )
                    self.assertEqual(
                        document_storage.document_read_bytes("archive", "fallback.pdf"),
                        b"local-first",
                    )
                    self.assertEqual(
                        document_storage.document_size("archive", "fallback.pdf"),
                        len(b"local-first"),
                    )

    @override_settings(DOCUMENTS_STORAGE_PREFIX="documents")
    def test_document_read_falls_back_to_selected_s3_storage_when_local_missing(self):
        storage_config = S3StorageConfiguration.objects.create(
            name="Primary",
            prefix="pngx",
            bucket="paperless-selected",
            access_key_id="selected-access-key",
            secret_access_key="selected-secret-key",
        )
        config = ApplicationConfiguration.objects.first()
        config.documents_storage_type = "local"
        config.documents_s3_storage = storage_config
        config.save()
        PrefixAwareStorage._files_by_location["pngx/archive"] = {
            "remote.pdf": b"remote-content",
        }

        with mock.patch(
            "documents.storage.import_module",
            return_value=mock.Mock(S3Storage=PrefixAwareStorage),
        ):
            self.assertTrue(document_storage.document_exists("archive", "remote.pdf"))
            self.assertEqual(
                document_storage.document_read_bytes("archive", "remote.pdf"),
                b"remote-content",
            )

    @override_settings(DOCUMENTS_STORAGE_PREFIX="documents")
    def test_document_read_falls_back_to_legacy_s3_prefix(self):
        storage_config = S3StorageConfiguration.objects.create(
            name="Primary",
            prefix="pngx",
            bucket="paperless-selected",
            access_key_id="selected-access-key",
            secret_access_key="selected-secret-key",
        )
        config = ApplicationConfiguration.objects.first()
        config.documents_storage_type = "s3"
        config.documents_s3_storage = storage_config
        config.save()
        PrefixAwareStorage._files_by_location["documents/archive"] = {
            "legacy.pdf": b"legacy-content",
        }

        with mock.patch(
            "documents.storage.import_module",
            return_value=mock.Mock(S3Storage=PrefixAwareStorage),
        ):
            self.assertTrue(document_storage.document_exists("archive", "legacy.pdf"))
            self.assertEqual(
                document_storage.document_read_bytes("archive", "legacy.pdf"),
                b"legacy-content",
            )

    def test_document_move_prefers_local_file_over_s3_storage(self):
        with TemporaryDirectory() as temp_dir:
            with override_settings(
                ORIGINALS_DIR=Path(temp_dir),
                ARCHIVE_DIR=Path(temp_dir),
                THUMBNAIL_DIR=Path(temp_dir),
            ):
                source = Path(temp_dir) / "old.pdf"
                source.write_bytes(b"local-file")

                with mock.patch.object(
                    document_storage,
                    "_build_document_storage",
                    return_value=FakeMemoryStorage(),
                ):
                    document_storage.document_move("archive", "old.pdf", "new.pdf")

                self.assertFalse(source.exists())
                self.assertEqual(
                    (Path(temp_dir) / "new.pdf").read_bytes(),
                    b"local-file",
                )

    def test_app_config_can_build_s3_storage(self):
        config = ApplicationConfiguration.objects.first()
        config.documents_storage_type = "s3"
        config.documents_storage_prefix = "tenant-a/documents"
        config.documents_s3_bucket = "paperless-test"
        config.documents_s3_access_key_id = "access-key"
        config.documents_s3_secret_access_key = "secret-key"
        config.documents_s3_region_name = "eu-central-1"
        config.documents_s3_querystring_auth = True
        config.documents_s3_use_ssl = False
        config.save()

        with mock.patch(
            "documents.storage.import_module",
            return_value=mock.Mock(S3Storage=RecordingStorage),
        ):
            storage = document_storage.get_document_storage("archive")

        self.assertIsInstance(storage, RecordingStorage)
        self.assertEqual(storage.kwargs["bucket_name"], "paperless-test")
        self.assertEqual(
            storage.kwargs["location"],
            "tenant-a/documents/archive",
        )
        self.assertEqual(storage.kwargs["access_key"], "access-key")
        self.assertEqual(storage.kwargs["secret_key"], "secret-key")
        self.assertEqual(storage.kwargs["region_name"], "eu-central-1")
        self.assertEqual(storage.kwargs["querystring_auth"], True)
        self.assertEqual(storage.kwargs["use_ssl"], False)

    def test_selected_s3_storage_is_used_for_document_storage(self):
        storage_config = S3StorageConfiguration.objects.create(
            name="Primary",
            prefix="tenant-a/documents",
            bucket="paperless-selected",
            access_key_id="selected-access-key",
            secret_access_key="selected-secret-key",
            region_name="eu-central-1",
            querystring_auth=False,
            use_ssl=True,
        )
        config = ApplicationConfiguration.objects.first()
        config.documents_storage_type = "s3"
        config.documents_storage_prefix = "tenant-a/documents"
        config.documents_s3_storage = storage_config
        config.save()

        with mock.patch(
            "documents.storage.import_module",
            return_value=mock.Mock(S3Storage=RecordingStorage),
        ):
            storage = document_storage.get_document_storage("archive")

        self.assertIsInstance(storage, RecordingStorage)
        self.assertEqual(storage.kwargs["bucket_name"], "paperless-selected")
        self.assertEqual(storage.kwargs["access_key"], "selected-access-key")
        self.assertEqual(storage.kwargs["secret_key"], "selected-secret-key")
        self.assertEqual(storage.kwargs["querystring_auth"], False)
        self.assertEqual(storage.kwargs["use_ssl"], True)

    def test_app_config_can_build_s3_backup_storage(self):
        config = ApplicationConfiguration.objects.first()
        config.documents_backup_prefix = "tenant-a/backup"
        config.documents_backup_s3_bucket = "paperless-backup"
        config.documents_backup_s3_access_key_id = "backup-access-key"
        config.documents_backup_s3_secret_access_key = "backup-secret-key"
        config.documents_backup_s3_region_name = "eu-central-1"
        config.documents_backup_s3_querystring_auth = False
        config.documents_backup_s3_use_ssl = True
        config.save()

        with mock.patch(
            "documents.storage.import_module",
            return_value=mock.Mock(S3Storage=RecordingStorage),
        ):
            storage = document_storage.get_document_backup_storage("archive")

        self.assertIsInstance(storage, RecordingStorage)
        self.assertEqual(storage.kwargs["bucket_name"], "paperless-backup")
        self.assertEqual(
            storage.kwargs["location"],
            "tenant-a/backup/archive",
        )
        self.assertEqual(storage.kwargs["access_key"], "backup-access-key")
        self.assertEqual(storage.kwargs["secret_key"], "backup-secret-key")
        self.assertEqual(storage.kwargs["region_name"], "eu-central-1")
        self.assertEqual(storage.kwargs["querystring_auth"], False)
        self.assertEqual(storage.kwargs["use_ssl"], True)

    def test_selected_s3_storage_is_used_for_document_backup_storage(self):
        storage_config = S3StorageConfiguration.objects.create(
            name="Backup",
            prefix="tenant-a/backup",
            bucket="paperless-selected-backup",
            access_key_id="selected-backup-access-key",
            secret_access_key="selected-backup-secret-key",
            region_name="eu-central-1",
            querystring_auth=True,
            use_ssl=False,
        )
        config = ApplicationConfiguration.objects.first()
        config.documents_backup_prefix = "tenant-a/backup"
        config.documents_backup_s3_storage = storage_config
        config.save()

        with mock.patch(
            "documents.storage.import_module",
            return_value=mock.Mock(S3Storage=RecordingStorage),
        ):
            storage = document_storage.get_document_backup_storage("archive")

        self.assertIsInstance(storage, RecordingStorage)
        self.assertEqual(
            storage.kwargs["bucket_name"],
            "paperless-selected-backup",
        )
        self.assertEqual(
            storage.kwargs["access_key"],
            "selected-backup-access-key",
        )
        self.assertEqual(
            storage.kwargs["secret_key"],
            "selected-backup-secret-key",
        )
        self.assertEqual(storage.kwargs["querystring_auth"], True)
        self.assertEqual(storage.kwargs["use_ssl"], False)

    def test_document_storage_config_supports_overrides(self):
        config = ApplicationConfiguration.objects.first()
        config.documents_storage_type = "local"
        config.documents_storage_prefix = "default-documents"
        config.save()

        resolved = DocumentStorageConfig(
            overrides={
                "documents_storage_type": "s3",
                "documents_storage_prefix": "override-documents",
                "documents_s3_bucket": "paperless-test",
            },
        )

        self.assertEqual(resolved.storage_type, "s3")
        self.assertEqual(resolved.prefix, "override-documents")
        self.assertEqual(resolved.s3_bucket, "paperless-test")

    def test_document_backup_config_supports_overrides(self):
        resolved = document_storage.DocumentBackupConfig(
            overrides={
                "documents_backup_prefix": "override-backup",
                "documents_backup_s3_bucket": "paperless-backup",
            },
        )

        self.assertEqual(resolved.prefix, "override-backup")
        self.assertEqual(resolved.s3_bucket, "paperless-backup")
        self.assertTrue(resolved.is_configured)

    def test_test_document_storage_connection_requires_s3(self):
        with self.assertRaisesMessage(
            Exception,
            "The document storage backend must be set to s3 to run this test.",
        ):
            document_storage.test_document_storage_connection(
                {
                    "documents_storage_type": "local",
                },
            )

    def test_test_document_storage_connection_writes_probe_file(self):
        recording_storage = FakeMemoryStorage()
        with mock.patch.object(
            document_storage,
            "get_document_storage",
            return_value=recording_storage,
        ):
            document_storage.test_document_storage_connection(
                {
                    "documents_storage_type": "s3",
                    "documents_s3_bucket": "paperless-test",
                },
            )

        self.assertEqual(FakeMemoryStorage._files, {})

    def test_test_document_backup_storage_connection_requires_bucket(self):
        with self.assertRaisesMessage(
            Exception,
            "The document backup storage requires PAPERLESS_DOCUMENTS_BACKUP_S3_BUCKET or an application configuration value.",
        ):
            document_storage.test_document_backup_storage_connection({})

    def test_test_document_backup_storage_connection_writes_probe_file(self):
        with mock.patch.object(
            document_storage,
            "test_s3_connection",
        ) as test_s3_connection_mock:
            document_storage.test_document_backup_storage_connection(
                {
                    "documents_backup_prefix": "paperless-backup",
                    "documents_backup_s3_bucket": "paperless-backup",
                },
            )

        test_s3_connection_mock.assert_called_once_with(
            prefix="paperless-backup",
            s3_bucket="paperless-backup",
            s3_endpoint_url=None,
            s3_access_key_id=None,
            s3_secret_access_key=None,
            s3_region_name=None,
            s3_default_acl=None,
            s3_custom_domain=None,
            s3_url_protocol="https:",
            s3_addressing_style=None,
            s3_querystring_auth=False,
            s3_use_ssl=True,
        )

    def test_document_write_from_path_copies_to_backup_storage(self):
        document = Document.objects.create(
            title="Backed up doc",
            mime_type="application/pdf",
            checksum="checksum-6",
            filename="backup/doc.pdf",
        )

        primary_storage = FakeMemoryStorage()
        backup_storage = FakeBackupStorage()
        with (
            mock.patch.object(
                document_storage,
                "get_document_storage",
                return_value=primary_storage,
            ),
            mock.patch.object(
                document_storage,
                "get_document_backup_storage",
                return_value=backup_storage,
            ),
        ):
            document.source_write_bytes(b"backup-me")

        self.assertEqual(primary_storage._files["backup/doc.pdf"], b"backup-me")
        self.assertEqual(backup_storage._files["backup/doc.pdf"], b"backup-me")

    def test_document_backup_command_syncs_existing_document(self):
        document = Document.objects.create(
            title="Command backup doc",
            mime_type="application/pdf",
            checksum="checksum-7",
            filename="command/doc.pdf",
        )

        primary_storage = FakeMemoryStorage()
        backup_storage = FakeBackupStorage()

        with mock.patch.object(
            document_storage,
            "_build_document_storage",
            return_value=primary_storage,
        ):
            document.source_write_bytes(b"command-backup")

        config = ApplicationConfiguration.objects.first()
        config.documents_backup_s3_bucket = "paperless-backup"
        config.save()
        backup_storage.clear()

        with (
            mock.patch.object(
                document_storage,
                "_build_document_storage",
                return_value=primary_storage,
            ),
            mock.patch.object(
                document_storage,
                "get_document_backup_storage",
                return_value=backup_storage,
            ),
        ):
            call_command("document_backup", "--no-progress-bar")

        self.assertEqual(
            backup_storage._files["command/doc.pdf"],
            b"command-backup",
        )

    def test_run_workflows_uses_local_source_path_for_remote_documents(self):
        document = Document.objects.create(
            title="Workflow remote doc",
            mime_type="application/pdf",
            checksum="checksum-5",
            filename="remote/workflow.pdf",
        )

        with mock.patch.object(
            document_storage,
            "_build_document_storage",
            return_value=FakeMemoryStorage(),
        ):
            document.source_write_bytes(b"workflow")

            with mock.patch.object(
                signal_handlers,
                "get_workflows_for_trigger",
                return_value=[],
            ):
                result = signal_handlers.run_workflows(
                    trigger_type=WorkflowTrigger.WorkflowTriggerType.DOCUMENT_UPDATED,
                    document=document,
                )

        self.assertIsNone(result)
