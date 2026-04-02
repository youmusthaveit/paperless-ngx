import json
import tempfile
import uuid
from datetime import timedelta
from io import BytesIO
from pathlib import Path
from unittest import mock

from celery import states
from django.conf import settings
from django.contrib.auth.models import Permission
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from documents.demo_data import _render_html_to_pdf_bytes
from documents.demo_data import seed_handwerksbetrieb_demo_data
from documents.models import Correspondent
from documents.models import CustomField
from documents.models import CustomFieldInstance
from documents.models import Document
from documents.models import DocumentType
from documents.models import PaperlessTask
from documents.models import StoragePath
from documents.models import Tag
from documents.models import Workflow
from documents.models import WorkflowAction
from documents.models import WorkflowTrigger
from documents.tasks import reset_runtime_data
from documents.tests.utils import DirectoriesMixin
from paperless.models import ApplicationConfiguration
from paperless.models import ColorConvertChoices
from paperless.models import S3StorageConfiguration
from paperless.remote_import import RemoteImportService


class TestApiAppConfig(DirectoriesMixin, APITestCase):
    ENDPOINT = "/api/config/"

    def setUp(self) -> None:
        super().setUp()

        self.user = User.objects.create_superuser(username="temp_admin")
        self.client.force_authenticate(user=self.user)

    def test_api_get_config(self):
        """
        GIVEN:
            - API request to get app config
        WHEN:
            - API is called
        THEN:
            - Existing config
        """
        response = self.client.get(self.ENDPOINT, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.maxDiff = None

        self.assertDictEqual(
            response.data[0],
            {
                "id": 1,
                "output_type": None,
                "pages": None,
                "language": None,
                "mode": None,
                "skip_archive_file": None,
                "image_dpi": None,
                "unpaper_clean": None,
                "deskew": None,
                "rotate_pages": None,
                "rotate_pages_threshold": None,
                "max_image_pixels": None,
                "color_conversion_strategy": None,
                "user_args": None,
                "app_title": None,
                "remote_import_base_url": None,
                "remote_import_api_token": None,
                "app_logo": None,
                "documents_storage_type": None,
                "documents_storage_prefix": None,
                "documents_s3_storage": None,
                "documents_s3_bucket": None,
                "documents_s3_endpoint_url": None,
                "documents_s3_access_key_id": None,
                "documents_s3_secret_access_key": None,
                "documents_s3_region_name": None,
                "documents_s3_default_acl": None,
                "documents_s3_custom_domain": None,
                "documents_s3_url_protocol": None,
                "documents_s3_addressing_style": None,
                "documents_s3_querystring_auth": None,
                "documents_s3_use_ssl": None,
                "documents_backup_prefix": None,
                "documents_backup_s3_storage": None,
                "documents_backup_s3_bucket": None,
                "documents_backup_s3_endpoint_url": None,
                "documents_backup_s3_access_key_id": None,
                "documents_backup_s3_secret_access_key": None,
                "documents_backup_s3_region_name": None,
                "documents_backup_s3_default_acl": None,
                "documents_backup_s3_custom_domain": None,
                "documents_backup_s3_url_protocol": None,
                "documents_backup_s3_addressing_style": None,
                "documents_backup_s3_querystring_auth": None,
                "documents_backup_s3_use_ssl": None,
                "documents_backup_schedule_enabled": None,
                "documents_backup_schedule_storage": None,
                "documents_backup_schedule_frequency_days": None,
                "documents_backup_schedule_hour": None,
                "documents_backup_schedule_minute": None,
                "documents_backup_schedule_retain_count": None,
                "documents_backup_schedule_last_run": None,
                "documents_backup_schedule_jobs": [],
                "barcodes_enabled": None,
                "barcode_enable_tiff_support": None,
                "barcode_string": None,
                "barcode_retain_split_pages": None,
                "barcode_enable_asn": None,
                "barcode_asn_prefix": None,
                "barcode_upscale": None,
                "barcode_dpi": None,
                "barcode_max_pages": None,
                "barcode_enable_tag": None,
                "barcode_tag_mapping": None,
            },
        )

    def test_api_get_ui_settings_with_config(self):
        """
        GIVEN:
            - Existing config with app_title, app_logo specified
        WHEN:
            - API to retrieve uisettings is called
        THEN:
            - app_title and app_logo are included
        """
        config = ApplicationConfiguration.objects.first()
        config.app_title = "Fancy New Title"
        config.app_logo = "/logo/example.jpg"
        config.save()
        response = self.client.get("/api/ui_settings/", format="json")
        self.assertDictEqual(
            response.data["settings"],
            {
                "app_title": config.app_title,
                "app_logo": config.app_logo,
            }
            | response.data["settings"],
        )

    def test_api_update_config(self):
        """
        GIVEN:
            - API request to update app config
        WHEN:
            - API is called
        THEN:
            - Correct HTTP response
            - Config is updated
        """
        response = self.client.patch(
            f"{self.ENDPOINT}1/",
            json.dumps(
                {
                    "color_conversion_strategy": ColorConvertChoices.RGB,
                },
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        config = ApplicationConfiguration.objects.first()
        self.assertEqual(config.color_conversion_strategy, ColorConvertChoices.RGB)

    def test_api_update_document_storage_config(self):
        selected_storage = S3StorageConfiguration.objects.create(
            name="Primary",
            prefix="tenant-a/documents",
            bucket="selected-primary",
        )
        response = self.client.patch(
            f"{self.ENDPOINT}1/",
            json.dumps(
                {
                    "documents_storage_type": "s3",
                    "documents_storage_prefix": "tenant-a/documents",
                    "documents_s3_storage": selected_storage.pk,
                    "documents_s3_bucket": "paperless-test",
                    "documents_s3_access_key_id": "access-key",
                    "documents_s3_secret_access_key": "secret-key",
                    "documents_s3_querystring_auth": True,
                    "documents_s3_use_ssl": False,
                },
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        config = ApplicationConfiguration.objects.first()
        self.assertEqual(config.documents_storage_type, "s3")
        self.assertEqual(config.documents_storage_prefix, "tenant-a/documents")
        self.assertEqual(config.documents_s3_storage, selected_storage)
        self.assertEqual(config.documents_s3_bucket, "paperless-test")
        self.assertEqual(config.documents_s3_access_key_id, "access-key")
        self.assertEqual(config.documents_s3_secret_access_key, "secret-key")
        self.assertEqual(config.documents_s3_querystring_auth, True)
        self.assertEqual(config.documents_s3_use_ssl, False)

        response = self.client.get(self.ENDPOINT, format="json")
        self.assertNotEqual(
            response.data[0]["documents_s3_secret_access_key"],
            "secret-key",
        )

    def test_api_update_document_storage_secret_keeps_existing_masked_value(self):
        config = ApplicationConfiguration.objects.first()
        config.documents_s3_secret_access_key = "keep-me"
        config.save()

        response = self.client.patch(
            f"{self.ENDPOINT}1/",
            json.dumps(
                {
                    "documents_s3_secret_access_key": "**********",
                },
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        config.refresh_from_db()
        self.assertEqual(config.documents_s3_secret_access_key, "keep-me")

    def test_api_update_remote_import_credentials(self):
        response = self.client.patch(
            f"{self.ENDPOINT}1/",
            json.dumps(
                {
                    "remote_import_base_url": "https://remote.example.com",
                    "remote_import_api_token": "very-secret-token",
                },
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        config = ApplicationConfiguration.objects.first()
        self.assertEqual(
            config.remote_import_base_url,
            "https://remote.example.com",
        )
        self.assertEqual(
            config.remote_import_api_token,
            "very-secret-token",
        )

        response = self.client.get(self.ENDPOINT, format="json")
        self.assertNotEqual(
            response.data[0]["remote_import_api_token"],
            "very-secret-token",
        )

    def test_api_update_remote_import_token_keeps_existing_masked_value(self):
        config = ApplicationConfiguration.objects.first()
        config.remote_import_api_token = "keep-remote-token"
        config.save()

        response = self.client.patch(
            f"{self.ENDPOINT}1/",
            json.dumps(
                {
                    "remote_import_api_token": "**********",
                },
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        config.refresh_from_db()
        self.assertEqual(config.remote_import_api_token, "keep-remote-token")

    def test_api_update_document_backup_config(self):
        selected_storage = S3StorageConfiguration.objects.create(
            name="Backup",
            prefix="paperless/backup",
            bucket="selected-backup",
        )
        response = self.client.patch(
            f"{self.ENDPOINT}1/",
            json.dumps(
                {
                    "documents_backup_prefix": "paperless/backup",
                    "documents_backup_s3_storage": selected_storage.pk,
                    "documents_backup_s3_bucket": "paperless-backup",
                    "documents_backup_s3_access_key_id": "backup-access-key",
                    "documents_backup_s3_secret_access_key": "backup-secret-key",
                    "documents_backup_s3_querystring_auth": False,
                    "documents_backup_s3_use_ssl": True,
                },
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        config = ApplicationConfiguration.objects.first()
        self.assertEqual(config.documents_backup_prefix, "paperless/backup")
        self.assertEqual(config.documents_backup_s3_storage, selected_storage)
        self.assertEqual(config.documents_backup_s3_bucket, "paperless-backup")
        self.assertEqual(
            config.documents_backup_s3_access_key_id,
            "backup-access-key",
        )
        self.assertEqual(
            config.documents_backup_s3_secret_access_key,
            "backup-secret-key",
        )
        self.assertEqual(config.documents_backup_s3_querystring_auth, False)
        self.assertEqual(config.documents_backup_s3_use_ssl, True)

        response = self.client.get(self.ENDPOINT, format="json")
        self.assertNotEqual(
            response.data[0]["documents_backup_s3_secret_access_key"],
            "backup-secret-key",
        )

    def test_api_update_document_backup_secret_keeps_existing_masked_value(self):
        config = ApplicationConfiguration.objects.first()
        config.documents_backup_s3_secret_access_key = "keep-backup-secret"
        config.save()

        response = self.client.patch(
            f"{self.ENDPOINT}1/",
            json.dumps(
                {
                    "documents_backup_s3_secret_access_key": "**********",
                },
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        config.refresh_from_db()
        self.assertEqual(
            config.documents_backup_s3_secret_access_key,
            "keep-backup-secret",
        )

    def test_api_update_automatic_backup_jobs(self):
        selected_storage = S3StorageConfiguration.objects.create(
            name="Scheduled Backup",
            bucket="scheduled-bucket",
        )
        response = self.client.patch(
            f"{self.ENDPOINT}1/",
            json.dumps(
                {
                    "documents_backup_schedule_jobs": [
                        {
                            "name": "Nightly",
                            "enabled": True,
                            "storage": selected_storage.pk,
                            "frequency_days": 1,
                            "hour": 2,
                            "minute": 15,
                            "retain_count": 7,
                            "last_run": None,
                        },
                        {
                            "name": "Weekly",
                            "enabled": False,
                            "storage": selected_storage.pk,
                            "frequency_days": 7,
                            "hour": 5,
                            "minute": 0,
                            "retain_count": 4,
                            "last_run": None,
                        },
                    ],
                },
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        config = ApplicationConfiguration.objects.first()
        self.assertEqual(
            config.documents_backup_schedule_jobs,
            [
                {
                    "name": "Nightly",
                    "enabled": True,
                    "storage": selected_storage.pk,
                    "frequency_days": 1,
                    "hour": 2,
                    "minute": 15,
                    "retain_count": 7,
                    "last_run": None,
                },
                {
                    "name": "Weekly",
                    "enabled": False,
                    "storage": selected_storage.pk,
                    "frequency_days": 7,
                    "hour": 5,
                    "minute": 0,
                    "retain_count": 4,
                    "last_run": None,
                },
            ],
        )

    @mock.patch("paperless.views.test_document_storage_connection")
    def test_api_test_s3_storage(self, test_storage_mock):
        selected_storage = S3StorageConfiguration.objects.create(
            name="Primary",
            prefix="tenant-a/documents",
            bucket="selected-primary",
        )
        response = self.client.post(
            f"{self.ENDPOINT}1/test-s3-storage/",
            json.dumps(
                {
                    "documents_storage_type": "s3",
                    "documents_s3_storage": selected_storage.pk,
                    "documents_s3_bucket": "paperless-test",
                    "documents_s3_secret_access_key": "secret-key",
                },
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        test_storage_mock.assert_called_once_with(
            {
                "documents_storage_type": "s3",
                "documents_s3_storage": selected_storage,
                "documents_s3_bucket": "paperless-test",
                "documents_s3_secret_access_key": "secret-key",
            },
        )

    @mock.patch("paperless.views.test_document_storage_connection")
    def test_api_test_s3_storage_uses_existing_masked_secret(self, test_storage_mock):
        config = ApplicationConfiguration.objects.first()
        config.documents_s3_secret_access_key = "keep-me"
        config.save()

        response = self.client.post(
            f"{self.ENDPOINT}1/test-s3-storage/",
            json.dumps(
                {
                    "documents_storage_type": "s3",
                    "documents_s3_secret_access_key": "**********",
                },
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        test_storage_mock.assert_called_once_with(
            {
                "documents_storage_type": "s3",
                "documents_s3_secret_access_key": "keep-me",
            },
        )

    @mock.patch("paperless.views.test_document_backup_storage_connection")
    def test_api_test_s3_backup_storage(self, test_storage_mock):
        selected_storage = S3StorageConfiguration.objects.create(
            name="Backup",
            prefix="paperless/backup",
            bucket="selected-backup",
        )
        response = self.client.post(
            f"{self.ENDPOINT}1/test-s3-backup-storage/",
            json.dumps(
                {
                    "documents_backup_s3_storage": selected_storage.pk,
                    "documents_backup_s3_bucket": "paperless-backup",
                    "documents_backup_s3_secret_access_key": "backup-secret-key",
                },
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        test_storage_mock.assert_called_once_with(
            {
                "documents_backup_s3_storage": selected_storage,
                "documents_backup_s3_bucket": "paperless-backup",
                "documents_backup_s3_secret_access_key": "backup-secret-key",
            },
        )

    @mock.patch("paperless.views.test_document_backup_storage_connection")
    def test_api_test_s3_backup_storage_uses_existing_masked_secret(
        self,
        test_storage_mock,
    ):
        config = ApplicationConfiguration.objects.first()
        config.documents_backup_s3_secret_access_key = "keep-backup-secret"
        config.save()

        response = self.client.post(
            f"{self.ENDPOINT}1/test-s3-backup-storage/",
            json.dumps(
                {
                    "documents_backup_s3_secret_access_key": "**********",
                },
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        test_storage_mock.assert_called_once_with(
            {
                "documents_backup_s3_secret_access_key": "keep-backup-secret",
            },
        )

    def test_api_update_config_empty_fields(self):
        """
        GIVEN:
            - API request to update app config with empty string for user_args JSONField and language field
        WHEN:
            - API is called
        THEN:
            - Correct HTTP response
            - user_args is set to None
        """
        response = self.client.patch(
            f"{self.ENDPOINT}1/",
            json.dumps(
                {
                    "user_args": "",
                    "language": "",
                    "barcode_tag_mapping": "",
                },
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        config = ApplicationConfiguration.objects.first()
        self.assertEqual(config.user_args, None)
        self.assertEqual(config.language, None)
        self.assertEqual(config.barcode_tag_mapping, None)

    def test_api_replace_app_logo(self):
        """
        GIVEN:
            - Existing config with app_logo specified
        WHEN:
            - API to replace app_logo is called
        THEN:
            - old app_logo file is deleted
        """
        admin = User.objects.create_superuser(username="admin")
        self.client.force_login(user=admin)
        response = self.client.get("/logo/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

        self.client.patch(
            f"{self.ENDPOINT}1/",
            {
                "app_logo": SimpleUploadedFile(
                    name="simple.jpg",
                    content=(
                        Path(__file__).parent / "samples" / "simple.jpg"
                    ).read_bytes(),
                    content_type="image/jpeg",
                ),
            },
        )

        # Logo exists at /logo/simple.jpg
        response = self.client.get("/logo/simple.jpg")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("image/jpeg", response["Content-Type"])

        config = ApplicationConfiguration.objects.first()
        old_logo = config.app_logo
        self.assertTrue(Path(old_logo.path).exists())
        self.client.patch(
            f"{self.ENDPOINT}1/",
            {
                "app_logo": SimpleUploadedFile(
                    name="simple.png",
                    content=(
                        Path(__file__).parent / "samples" / "simple.png"
                    ).read_bytes(),
                    content_type="image/png",
                ),
            },
        )
        self.assertFalse(Path(old_logo.path).exists())

    def test_api_rejects_malicious_svg_logo(self):
        """
        GIVEN:
            - An SVG logo containing a <script> tag
        WHEN:
            - Uploaded via PATCH to app config
        THEN:
            - SVG is rejected with 400
        """
        malicious_svg = b"""<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">
                            <text x="10" y="20">Hello</text>
                            <script>alert('XSS')</script>
                            </svg>
                        """

        svg_file = BytesIO(malicious_svg)
        svg_file.name = "malicious_script.svg"

        response = self.client.patch(
            f"{self.ENDPOINT}1/",
            {"app_logo": svg_file},
            format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("disallowed svg tag", str(response.data).lower())

    def test_api_rejects_malicious_svg_with_style_javascript(self):
        """
        GIVEN:
            - An SVG logo containing javascript: in style attribute
        WHEN:
            - Uploaded via PATCH to app config
        THEN:
            - SVG is rejected with 400
        """

        malicious_svg = b"""<?xml version="1.0" encoding="UTF-8"?>
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
        <rect width="100" height="100" style="background: url(javascript:alert('XSS'));" fill="red"/>
    </svg>"""

        svg_file = BytesIO(malicious_svg)
        svg_file.name = "malicious_style.svg"

        response = self.client.patch(
            f"{self.ENDPOINT}1/",
            {"app_logo": svg_file},
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn(
            "disallowed pattern in style attribute",
            str(response.data).lower(),
        )
        self.assertIn("style", str(response.data).lower())

    def test_api_rejects_svg_with_style_expression(self):
        """
        GIVEN:
            - An SVG logo containing CSS expression() in style
        WHEN:
            - Uploaded via PATCH to app config
        THEN:
            - SVG is rejected with 400
        """

        malicious_svg = b"""<?xml version="1.0" encoding="UTF-8"?>
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
        <rect width="100" height="100" style="width: expression(alert('XSS'));" fill="blue"/>
    </svg>"""

        svg_file = BytesIO(malicious_svg)
        svg_file.name = "expression_style.svg"

        response = self.client.patch(
            f"{self.ENDPOINT}1/",
            {"app_logo": svg_file},
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("disallowed", str(response.data).lower())

    def test_api_rejects_svg_with_style_cdata_javascript(self):
        """
        GIVEN:
            - An SVG logo with javascript: hidden in a CDATA style block
        WHEN:
            - Uploaded via PATCH to app config
        THEN:
            - SVG is rejected with 400
        """

        malicious_svg = b"""<?xml version="1.0" encoding="UTF-8"?>
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
        <style><![CDATA[
            rect { background: url("javascript:alert('XSS')"); }
        ]]></style>
        <rect width="100" height="100" fill="purple"/>
    </svg>"""

        svg_file = BytesIO(malicious_svg)
        svg_file.name = "cdata_style.svg"

        response = self.client.patch(
            f"{self.ENDPOINT}1/",
            {"app_logo": svg_file},
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("disallowed", str(response.data).lower())

    def test_api_rejects_svg_with_style_import(self):
        """
        GIVEN:
            - An SVG logo containing @import in style
        WHEN:
            - Uploaded via PATCH to app config
        THEN:
            - SVG is rejected with 400
        """

        malicious_svg = b"""<?xml version="1.0" encoding="UTF-8"?>
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
        <rect width="100" height="100" style="@import url('http://evil.com/malicious.css');" fill="green"/>
    </svg>"""

        svg_file = BytesIO(malicious_svg)
        svg_file.name = "import_style.svg"

        response = self.client.patch(
            f"{self.ENDPOINT}1/",
            {"app_logo": svg_file},
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("disallowed", str(response.data).lower())

    def test_api_accepts_valid_svg_with_safe_style(self):
        """
        GIVEN:
            - A valid SVG logo with safe style attributes
        WHEN:
            - Uploaded via PATCH to app config
        THEN:
            - SVG is accepted with 200
        """

        safe_svg = b"""<?xml version="1.0" encoding="UTF-8"?>
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
        <rect width="100" height="100" style="fill: #ff6b6b; stroke: #333; stroke-width: 2;"/>
        <circle cx="50" cy="50" r="30" style="fill: white; opacity: 0.8;"/>
    </svg>"""

        svg_file = BytesIO(safe_svg)
        svg_file.name = "safe_logo.svg"

        response = self.client.patch(
            f"{self.ENDPOINT}1/",
            {"app_logo": svg_file},
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_api_accepts_valid_svg_with_safe_style_tag(self):
        """
        GIVEN:
            - A valid SVG logo with an embedded <style> tag
        WHEN:
            - Uploaded to app config
        THEN:
            - SVG is accepted with 200
        """

        safe_svg = b"""<?xml version="1.0" encoding="UTF-8"?>
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
        <style>
            rect { fill: #ff6b6b; stroke: #333; stroke-width: 2; }
            circle { fill: white; opacity: 0.8; }
        </style>
        <rect width="100" height="100"/>
        <circle cx="50" cy="50" r="30"/>
    </svg>"""

        svg_file = BytesIO(safe_svg)
        svg_file.name = "safe_logo_with_style.svg"

        response = self.client.patch(
            f"{self.ENDPOINT}1/",
            {"app_logo": svg_file},
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_api_rejects_svg_with_disallowed_attribute(self):
        """
        GIVEN:
            - An SVG with a disallowed attribute (onclick)
        WHEN:
            - Uploaded via PATCH to app config
        THEN:
            - SVG is rejected with 400
        """

        malicious_svg = b"""<?xml version="1.0" encoding="UTF-8"?>
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
        <rect width="100" height="100" fill="red" onclick="alert('XSS')"/>
    </svg>"""

        svg_file = BytesIO(malicious_svg)
        svg_file.name = "onclick_attribute.svg"

        response = self.client.patch(
            f"{self.ENDPOINT}1/",
            {"app_logo": svg_file},
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("disallowed", str(response.data).lower())
        self.assertIn("attribute", str(response.data).lower())

    def test_api_rejects_svg_with_disallowed_tag(self):
        """
        GIVEN:
            - An SVG with a disallowed tag (script)
        WHEN:
            - Uploaded via PATCH to app config
        THEN:
            - SVG is rejected with 400
        """

        malicious_svg = b"""<?xml version="1.0" encoding="UTF-8"?>
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
        <script>alert('XSS')</script>
        <rect width="100" height="100" fill="blue"/>
    </svg>"""

        svg_file = BytesIO(malicious_svg)
        svg_file.name = "script_tag.svg"

        response = self.client.patch(
            f"{self.ENDPOINT}1/",
            {"app_logo": svg_file},
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("disallowed", str(response.data).lower())
        self.assertIn("tag", str(response.data).lower())

    def test_api_rejects_svg_with_javascript_href(self):
        """
        GIVEN:
            - An SVG with javascript: in href attribute
        WHEN:
            - Uploaded via PATCH to app config
        THEN:
            - SVG is rejected with 400
        """
        malicious_svg = b"""<?xml version="1.0" encoding="UTF-8"?>
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
        <defs>
            <rect id="a" width="10" height="10" />
        </defs>
        <use href="javascript:alert('XSS')" />
    </svg>"""

        svg_file = BytesIO(malicious_svg)
        svg_file.name = "javascript_href.svg"

        response = self.client.patch(
            f"{self.ENDPOINT}1/",
            {"app_logo": svg_file},
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("disallowed", str(response.data).lower())
        self.assertIn("javascript", str(response.data).lower())

    def test_api_rejects_svg_with_javascript_xlink_href(self):
        """
        GIVEN:
            - An SVG with javascript: in xlink:href attribute
        WHEN:
            - Uploaded via PATCH to app config
        THEN:
            - SVG is rejected with 400
        """
        malicious_svg = b"""<?xml version="1.0" encoding="UTF-8"?>
    <svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 100 100">
        <use xlink:href="javascript:alert('XSS')" />
    </svg>"""

        svg_file = BytesIO(malicious_svg)
        svg_file.name = "javascript_xlink_href.svg"

        response = self.client.patch(
            f"{self.ENDPOINT}1/",
            {"app_logo": svg_file},
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("disallowed", str(response.data).lower())
        self.assertIn("javascript", str(response.data).lower())

    def test_api_rejects_svg_with_data_text_html_href(self):
        """
        GIVEN:
            - An SVG with data:text/html in href attribute
        WHEN:
            - Uploaded via PATCH to app config
        THEN:
            - SVG is rejected with 400
        """
        malicious_svg = b"""<?xml version="1.0" encoding="UTF-8"?>
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
            <defs>
                <rect id="r" width="100" height="100" fill="purple"/>
            </defs>
            <use href="javascript:alert(1)" />
        </svg>"""

        svg_file = BytesIO(malicious_svg)
        svg_file.name = "data_html_href.svg"

        response = self.client.patch(
            f"{self.ENDPOINT}1/",
            {"app_logo": svg_file},
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        # This will now catch "Disallowed URI scheme"
        self.assertIn("disallowed", str(response.data).lower())

    def test_api_rejects_svg_with_unknown_namespace_attribute(self):
        """
        GIVEN:
            - An SVG with an attribute in an unknown/custom namespace
        WHEN:
            - Uploaded via PATCH to app config
        THEN:
            - SVG is rejected with 400
            - Error message identifies the namespaced attribute as disallowed
        """

        # Define a custom namespace "my:hack" and try to use it
        malicious_svg = b"""<?xml version="1.0" encoding="UTF-8"?>
    <svg xmlns="http://www.w3.org/2000/svg"
         xmlns:hack="http://example.com/hack"
         viewBox="0 0 100 100">
        <rect width="100" height="100" hack:fill="red" />
    </svg>"""

        svg_file = BytesIO(malicious_svg)
        svg_file.name = "unknown_namespace.svg"

        response = self.client.patch(
            f"{self.ENDPOINT}1/",
            {"app_logo": svg_file},
            format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        # The error message should show the full Clark notation (curly braces)
        # because the validator's 'else' block kept the raw lxml name.
        error_msg = str(response.data).lower()
        self.assertIn("disallowed svg attribute", error_msg)
        self.assertIn("{http://example.com/hack}fill", error_msg)

    def test_api_rejects_svg_with_external_http_href(self) -> None:
        """
        GIVEN:
            - An SVG with an external URI (http://) in a safe tag's href attribute.
        WHEN:
            - Uploaded via PATCH to app config
        THEN:
            - SVG is rejected with 400 because http:// is not a safe_prefix.
        """
        from io import BytesIO

        # http:// is not in dangerous_schemes, but it is not in safe_prefixes.
        malicious_svg = b"""<?xml version="1.0" encoding="UTF-8"?>
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
        <use href="http://evil.com/logo.svg" />
    </svg>"""

        svg_file = BytesIO(malicious_svg)
        svg_file.name = "external_http_href.svg"

        response = self.client.patch(
            f"{self.ENDPOINT}1/",
            {"app_logo": svg_file},
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        # Check for the error message raised by the safe_prefixes check
        self.assertIn("uri scheme not allowed", str(response.data).lower())

    def test_create_not_allowed(self):
        """
        GIVEN:
            - API request to create a new app config
        WHEN:
            - API is called
        THEN:
            - Correct HTTP response
            - No new config is created
        """
        response = self.client.post(
            self.ENDPOINT,
            json.dumps(
                {
                    "output_type": "pdf",
                },
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        self.assertEqual(ApplicationConfiguration.objects.count(), 1)


class TestApiS3StorageConfig(DirectoriesMixin, APITestCase):
    ENDPOINT = "/api/s3_storages/"

    def setUp(self) -> None:
        super().setUp()

        user = User.objects.create_superuser(username="temp_admin_storage")
        self.client.force_authenticate(user=user)

    def test_api_create_s3_storage(self):
        response = self.client.post(
            self.ENDPOINT,
            json.dumps(
                {
                    "name": "Primary storage",
                    "prefix": "documents",
                    "bucket": "paperless-primary",
                    "endpoint_url": "https://s3.example.com",
                    "access_key_id": "access-key",
                    "secret_access_key": "secret-key",
                    "querystring_auth": True,
                    "use_ssl": False,
                },
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        storage = S3StorageConfiguration.objects.get(name="Primary storage")
        self.assertEqual(storage.prefix, "documents")
        self.assertEqual(storage.bucket, "paperless-primary")
        self.assertEqual(storage.secret_access_key, "secret-key")

        list_response = self.client.get(self.ENDPOINT, format="json")
        self.assertEqual(list_response.status_code, status.HTTP_200_OK)
        self.assertNotEqual(list_response.data[0]["secret_access_key"], "secret-key")

    def test_api_create_s3_storage_with_optional_prefix(self):
        response = self.client.post(
            self.ENDPOINT,
            json.dumps(
                {
                    "name": "No prefix storage",
                    "prefix": "",
                    "bucket": "paperless-primary",
                    "endpoint_url": "https://s3.example.com",
                    "access_key_id": "access-key",
                    "secret_access_key": "secret-key",
                },
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        storage = S3StorageConfiguration.objects.get(name="No prefix storage")
        self.assertIsNone(storage.prefix)

    @mock.patch("paperless.views.test_s3_connection")
    def test_api_test_named_s3_storage(self, test_s3_connection_mock):
        storage = S3StorageConfiguration.objects.create(
            name="Backup storage",
            prefix="backup",
            bucket="paperless-backup",
            endpoint_url="https://s3.example.com",
            access_key_id="backup-access",
            secret_access_key="backup-secret",
        )

        response = self.client.post(
            f"{self.ENDPOINT}{storage.pk}/test-connection/",
            json.dumps(
                {
                    "secret_access_key": "**********",
                },
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        test_s3_connection_mock.assert_called_once_with(
            prefix="backup",
            s3_bucket="paperless-backup",
            s3_endpoint_url="https://s3.example.com",
            s3_access_key_id="backup-access",
            s3_secret_access_key="backup-secret",
            s3_region_name=None,
            s3_default_acl=None,
            s3_custom_domain=None,
            s3_url_protocol="https:",
            s3_addressing_style=None,
            s3_querystring_auth=False,
            s3_use_ssl=True,
        )

    @mock.patch("paperless.views.export_documents_to_s3_storage.delay")
    def test_api_export_named_s3_storage(self, export_delay_mock):
        task_id = str(uuid.uuid4())
        export_delay_mock.return_value = mock.Mock(id=task_id)
        storage = S3StorageConfiguration.objects.create(
            name="Primary storage",
            prefix="documents",
            bucket="paperless-primary",
            endpoint_url="https://s3.example.com",
            access_key_id="access-key",
            secret_access_key="secret-key",
        )

        response = self.client.post(
            f"{self.ENDPOINT}{storage.pk}/export/",
            content_type="application/json",
        )

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        export_delay_mock.assert_called_once_with(storage.pk)
        task = PaperlessTask.objects.get(task_id=task_id)
        self.assertEqual(task.task_name, PaperlessTask.TaskName.EXPORT_S3_STORAGE)
        self.assertEqual(task.status, states.PENDING)
        self.assertEqual(task.task_file_name, storage.name)

    @mock.patch(
        "paperless.views.build_manual_s3_export_filename",
        return_value="paperless-manual-export-20260327T120000Z.zip",
    )
    @mock.patch("paperless.views.call_command")
    def test_api_download_manual_export(self, call_command_mock, _filename_mock):
        def create_export(command_name, export_dir, **kwargs):
            self.assertEqual(command_name, "document_exporter")
            export_path = (
                Path(export_dir) / "paperless-manual-export-20260327T120000Z.zip"
            )
            export_path.write_bytes(b"zip-content")

        call_command_mock.side_effect = create_export

        config = ApplicationConfiguration.objects.first()
        response = self.client.post(
            f"/api/config/{config.pk}/download-export/",
            content_type="application/json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response["Content-Type"], "application/zip")
        self.assertIn(
            'filename="paperless-manual-export-20260327T120000Z.zip"',
            response["Content-Disposition"],
        )
        self.assertEqual(b"".join(response.streaming_content), b"zip-content")

    def test_api_delete_named_s3_export(self):
        storage = S3StorageConfiguration.objects.create(
            name="Backup storage",
            prefix="backup",
            bucket="paperless-backup",
            endpoint_url="https://s3.example.com",
            access_key_id="backup-access",
            secret_access_key="backup-secret",
        )
        storage_backend = mock.Mock()
        storage_backend.exists.return_value = True

        with mock.patch(
            "paperless.views.get_s3_configuration_storage",
            return_value=storage_backend,
        ):
            response = self.client.post(
                f"{self.ENDPOINT}{storage.pk}/delete-export/",
                json.dumps(
                    {
                        "export_name": "paperless-manual-export-20260327T120000Z.zip",
                    },
                ),
                content_type="application/json",
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data["detail"],
            'Successfully deleted export "paperless-manual-export-20260327T120000Z.zip".',
        )
        storage_backend.delete.assert_called_once_with(
            "paperless-manual-export-20260327T120000Z.zip",
        )

    @mock.patch("paperless.views.import_documents_from_s3_storage.delay")
    def test_api_import_named_s3_storage(self, import_delay_mock):
        task_id = str(uuid.uuid4())
        import_delay_mock.return_value = mock.Mock(id=task_id)
        storage = S3StorageConfiguration.objects.create(
            name="Backup storage",
            prefix="documents",
            bucket="paperless-primary",
            endpoint_url="https://s3.example.com",
            access_key_id="access-key",
            secret_access_key="secret-key",
        )

        response = self.client.post(
            f"{self.ENDPOINT}{storage.pk}/import/",
            json.dumps({"export_name": "paperless-manual-export-20260327T120000Z.zip"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        import_delay_mock.assert_called_once_with(
            storage.pk,
            "paperless-manual-export-20260327T120000Z.zip",
        )
        task = PaperlessTask.objects.get(task_id=task_id)
        self.assertEqual(task.task_name, PaperlessTask.TaskName.IMPORT_S3_STORAGE)
        self.assertEqual(task.status, states.PENDING)
        self.assertEqual(task.task_file_name, storage.name)

    def test_api_delete_named_s3_export_requires_export_name(self):
        storage = S3StorageConfiguration.objects.create(
            name="Backup storage",
            prefix="documents",
            bucket="paperless-primary",
            endpoint_url="https://s3.example.com",
            access_key_id="access-key",
            secret_access_key="secret-key",
        )

        response = self.client.post(
            f"{self.ENDPOINT}{storage.pk}/delete-export/",
            json.dumps({}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_api_import_named_s3_storage_requires_export_name(self):
        storage = S3StorageConfiguration.objects.create(
            name="Backup storage",
            prefix="documents",
            bucket="paperless-primary",
            endpoint_url="https://s3.example.com",
            access_key_id="access-key",
            secret_access_key="secret-key",
        )

        response = self.client.post(
            f"{self.ENDPOINT}{storage.pk}/import/",
            json.dumps({}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_api_list_named_s3_exports(self):
        modified = "2026-03-27T12:34:56Z"
        storage = S3StorageConfiguration.objects.create(
            name="Backup storage",
            prefix="backup",
            bucket="paperless-backup",
            endpoint_url="https://s3.example.com",
            access_key_id="backup-access",
            secret_access_key="backup-secret",
        )
        storage_backend = mock.Mock()
        storage_backend.listdir.return_value = (
            [],
            [
                "paperless-manual-export-20260327T120000Z.zip",
                "ignore.txt",
                "paperless-manual-export-20260326T120000Z.zip",
            ],
        )
        storage_backend.size.side_effect = [456, 123]
        storage_backend.get_modified_time.side_effect = [modified, modified]

        with mock.patch(
            "paperless.views.S3StorageConfigurationViewSet._get_export_storage_candidates",
            return_value=[("manual-transfer", storage_backend)],
        ):
            response = self.client.get(f"{self.ENDPOINT}{storage.pk}/exports/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 2)
        self.assertEqual(
            response.data[0]["name"],
            "paperless-manual-export-20260327T120000Z.zip",
        )
        self.assertEqual(response.data[0]["size"], 456)

    def test_api_list_named_s3_exports_includes_automatic_exports(self):
        modified = "2026-03-27T12:34:56Z"
        storage = S3StorageConfiguration.objects.create(
            name="Backup storage",
            prefix="backup",
            bucket="paperless-backup",
            endpoint_url="https://s3.example.com",
            access_key_id="backup-access",
            secret_access_key="backup-secret",
        )
        manual_storage_backend = mock.Mock()
        manual_storage_backend.listdir.return_value = (
            [],
            ["paperless-manual-export-20260327T120000Z.zip"],
        )
        manual_storage_backend.size.return_value = 123
        manual_storage_backend.get_modified_time.return_value = modified

        automatic_storage_backend = mock.Mock()
        automatic_storage_backend.listdir.side_effect = [
            (["2026-03-27"], []),
            ([], ["paperless-automatic-export-20260327T120000Z.zip"]),
        ]
        automatic_storage_backend.size.return_value = 456
        automatic_storage_backend.get_modified_time.return_value = modified

        with mock.patch(
            "paperless.views.S3StorageConfigurationViewSet._get_export_storage_candidates",
            return_value=[
                ("manual-transfer", manual_storage_backend),
                ("automatic-transfer", automatic_storage_backend),
            ],
        ):
            response = self.client.get(f"{self.ENDPOINT}{storage.pk}/exports/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 2)
        self.assertEqual(
            response.data[0]["name"],
            "paperless-manual-export-20260327T120000Z.zip",
        )
        self.assertEqual(
            response.data[1]["name"],
            "2026-03-27/paperless-automatic-export-20260327T120000Z.zip",
        )

    def test_api_download_named_s3_export(self):
        storage = S3StorageConfiguration.objects.create(
            name="Backup storage",
            prefix="backup",
            bucket="paperless-backup",
            endpoint_url="https://s3.example.com",
            access_key_id="backup-access",
            secret_access_key="backup-secret",
        )
        storage_backend = mock.Mock()
        storage_backend.exists.return_value = True
        storage_backend.open.return_value = BytesIO(b"zip-content")

        with mock.patch(
            "paperless.views.get_s3_configuration_storage",
            return_value=storage_backend,
        ):
            response = self.client.post(
                f"{self.ENDPOINT}{storage.pk}/download-export/",
                json.dumps(
                    {
                        "export_name": "paperless-manual-export-20260327T120000Z.zip",
                    },
                ),
                content_type="application/json",
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response["Content-Type"], "application/zip")
        self.assertIn(
            'filename="paperless-manual-export-20260327T120000Z.zip"',
            response["Content-Disposition"],
        )
        self.assertEqual(b"".join(response.streaming_content), b"zip-content")

    def test_api_download_automatic_s3_export(self):
        storage = S3StorageConfiguration.objects.create(
            name="Backup storage",
            prefix="backup",
            bucket="paperless-backup",
            endpoint_url="https://s3.example.com",
            access_key_id="backup-access",
            secret_access_key="backup-secret",
        )
        storage_backend = mock.Mock()
        storage_backend.exists.return_value = True
        storage_backend.open.return_value = BytesIO(b"zip-content")

        with mock.patch(
            "paperless.views.S3StorageConfigurationViewSet._get_storage_backend_for_export",
            return_value=storage_backend,
        ):
            response = self.client.post(
                f"{self.ENDPOINT}{storage.pk}/download-export/",
                json.dumps(
                    {
                        "export_name": (
                            "2026-03-27/paperless-automatic-export-20260327T120000Z.zip"
                        ),
                    },
                ),
                content_type="application/json",
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response["Content-Type"], "application/zip")
        self.assertIn(
            'filename="paperless-automatic-export-20260327T120000Z.zip"',
            response["Content-Disposition"],
        )
        self.assertEqual(b"".join(response.streaming_content), b"zip-content")

    def test_api_remote_import_inspect(self):
        with mock.patch(
            "paperless.views.RemoteImportService.inspect",
            return_value={
                "remote": {
                    "base_url": "https://remote.example.com/api/",
                    "app_title": "Remote",
                    "document_count": 12,
                    "correspondent_count": 2,
                    "tag_count": 3,
                    "document_type_count": 4,
                    "storage_path_count": 1,
                    "custom_field_count": 5,
                },
                "mappings": {
                    "correspondents": {"total": 2, "matched": 1, "missing": []},
                    "tags": {"total": 3, "matched": 3, "missing": []},
                    "document_types": {"total": 4, "matched": 4, "missing": []},
                    "storage_paths": {"total": 1, "matched": 1, "missing": []},
                    "custom_fields": {"total": 5, "matched": 4, "missing": []},
                },
            },
        ) as inspect_mock:
            response = self.client.post(
                "/api/config/1/remote-import-inspect/",
                json.dumps(
                    {
                        "base_url": "https://remote.example.com",
                        "api_token": "secret-token",
                    },
                ),
                content_type="application/json",
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        inspect_mock.assert_called_once()
        self.assertEqual(response.data["remote"]["document_count"], 12)

    def test_api_remote_import_documents(self):
        with mock.patch(
            "paperless.views.RemoteImportService.browse_documents",
            return_value={
                "count": 1,
                "next": None,
                "previous": None,
                "all": [7],
                "results": [
                    {
                        "id": 7,
                        "document_url": "https://remote.example.com/documents/7",
                        "title": "Invoice 7",
                        "created": "2026-03-29",
                        "original_file_name": "invoice-7.pdf",
                        "archive_serial_number": None,
                        "correspondent": None,
                        "document_type": None,
                        "storage_path": None,
                        "tags": [],
                        "custom_fields": [],
                    },
                ],
            },
        ) as browse_mock:
            response = self.client.post(
                "/api/config/1/remote-import-documents/",
                json.dumps(
                    {
                        "base_url": "https://remote.example.com",
                        "api_token": "secret-token",
                        "query": "invoice",
                        "page": 1,
                        "page_size": 25,
                    },
                ),
                content_type="application/json",
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        browse_mock.assert_called_once_with(query="invoice", page=1, page_size=25)
        self.assertEqual(response.data["results"][0]["id"], 7)
        self.assertEqual(
            response.data["results"][0]["document_url"],
            "https://remote.example.com/documents/7",
        )

    def test_api_remote_import_start_creates_task(self):
        async_result = mock.Mock()
        async_result.id = str(uuid.uuid4())

        with mock.patch(
            "paperless.views.import_remote_documents.delay",
            return_value=async_result,
        ) as delay_mock:
            response = self.client.post(
                "/api/config/1/remote-import-start/",
                json.dumps(
                    {
                        "base_url": "https://remote.example.com",
                        "api_token": "secret-token",
                        "selected_document_ids": [4, 5],
                        "create_missing_items": True,
                        "import_notes": True,
                    },
                ),
                content_type="application/json",
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["task_id"], async_result.id)
        delay_mock.assert_called_once()

        created_task = PaperlessTask.objects.get(task_id=async_result.id)
        self.assertEqual(created_task.task_name, PaperlessTask.TaskName.IMPORT_FILE)
        self.assertEqual(created_task.status, states.PENDING)

    def test_api_reset_runtime_data_creates_task(self):
        async_result = mock.Mock()
        async_result.id = str(uuid.uuid4())

        with mock.patch(
            "paperless.views.reset_runtime_data.apply_async",
            return_value=async_result,
        ) as delay_mock:
            response = self.client.post(
                "/api/config/1/reset-runtime-data/",
                content_type="application/json",
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["detail"], "Runtime data reset started.")
        delay_mock.assert_called_once()
        self.assertIsInstance(response.data["task_id"], str)

        created_task = PaperlessTask.objects.get(task_id=response.data["task_id"])
        self.assertEqual(
            created_task.task_name,
            PaperlessTask.TaskName.RESET_RUNTIME_DATA,
        )
        self.assertEqual(created_task.status, states.PENDING)

    def test_api_reset_runtime_data_requires_superuser(self):
        self.client.force_authenticate(user=None)
        user = User.objects.create_user(username="staff-user")
        user.user_permissions.add(
            Permission.objects.get(codename="view_applicationconfiguration"),
            Permission.objects.get(codename="change_applicationconfiguration"),
        )
        self.client.force_authenticate(user=user)

        response = self.client.post(
            "/api/config/1/reset-runtime-data/",
            content_type="application/json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_api_reset_runtime_data_ignores_stale_running_task(self):
        stale_task = PaperlessTask.objects.create(
            type=PaperlessTask.TaskType.MANUAL_TASK,
            task_id=str(uuid.uuid4()),
            task_name=PaperlessTask.TaskName.RESET_RUNTIME_DATA,
            status=states.PENDING,
            date_created=timezone.now() - timedelta(minutes=20),
            task_file_name="Runtime data reset",
        )
        async_result = mock.Mock()
        async_result.id = str(uuid.uuid4())

        with mock.patch(
            "paperless.views.reset_runtime_data.apply_async",
            return_value=async_result,
        ):
            response = self.client.post(
                "/api/config/1/reset-runtime-data/",
                content_type="application/json",
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        stale_task.refresh_from_db()
        self.assertEqual(stale_task.status, states.FAILURE)
        self.assertIsNotNone(stale_task.date_done)

    def test_api_release_runtime_reset_lock(self):
        locked_task = PaperlessTask.objects.create(
            type=PaperlessTask.TaskType.MANUAL_TASK,
            task_id=str(uuid.uuid4()),
            task_name=PaperlessTask.TaskName.RESET_RUNTIME_DATA,
            status=states.STARTED,
            date_created=timezone.now(),
            date_started=timezone.now(),
            task_file_name="Runtime data reset",
        )

        response = self.client.post(
            "/api/config/1/release-runtime-reset-lock/",
            content_type="application/json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["released_tasks"], 1)
        locked_task.refresh_from_db()
        self.assertEqual(locked_task.status, states.FAILURE)
        self.assertEqual(
            locked_task.result,
            (
                "Task lock manually released by a superuser before starting a new "
                "runtime data reset."
            ),
        )

    def test_api_seed_demo_crafts_data_creates_task(self):
        async_result = mock.Mock()
        async_result.id = str(uuid.uuid4())

        with mock.patch(
            "paperless.views.create_demo_crafts_data.apply_async",
            return_value=async_result,
        ) as delay_mock:
            response = self.client.post(
                "/api/config/1/seed-demo-crafts-data/",
                content_type="application/json",
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["detail"], "Demo data generation started.")
        delay_mock.assert_called_once()
        self.assertIsInstance(response.data["task_id"], str)

        created_task = PaperlessTask.objects.get(task_id=response.data["task_id"])
        self.assertEqual(
            created_task.task_name,
            PaperlessTask.TaskName.CREATE_DEMO_CRAFTS_DATA,
        )
        self.assertEqual(created_task.status, states.PENDING)

    def test_seed_handwerksbetrieb_demo_data_creates_demo_content(self):
        owner = User.objects.create_superuser(username="seed-demo-owner")
        result = seed_handwerksbetrieb_demo_data(owner=owner)

        self.assertIn("Created 301 demo document(s)", result)
        self.assertEqual(Document.objects.count(), 301)
        self.assertEqual(Correspondent.objects.count(), 13)
        self.assertEqual(Tag.objects.count(), 17)
        self.assertEqual(DocumentType.objects.count(), 6)
        self.assertEqual(StoragePath.objects.count(), 6)
        self.assertEqual(CustomField.objects.count(), 6)

        invoice = Document.objects.get(title="Rechnung Klein und Sohn")
        self.assertEqual(invoice.correspondent.name, "Klein & Sohn GmbH")
        self.assertEqual(invoice.document_type.name, "Rechnung")
        self.assertTrue(invoice.tags.filter(name="dringend").exists())
        self.assertIn("\n", invoice.content)
        self.assertIn("Positionen", invoice.content)
        self.assertTrue(
            CustomFieldInstance.objects.filter(
                document=invoice,
                field__name="Auftragsnummer",
                value_text="2026-0312",
            ).exists(),
        )
        self.assertTrue(invoice.source_exists())
        self.assertTrue(
            Document.objects.filter(created__year__lte=2011).exists(),
        )

    def test_demo_pdf_generation_uses_gotenberg_html_to_pdf(self):
        response = mock.Mock(content=b"%PDF-1.4 demo")
        route = mock.MagicMock()
        margins_route = route.index.return_value.margins.return_value
        size_route = margins_route.size.return_value
        scale_route = size_route.scale.return_value
        scale_route.run.return_value = response
        route_cm = mock.MagicMock()
        route_cm.__enter__.return_value = route
        client = mock.MagicMock()
        client.chromium.html_to_pdf.return_value = route_cm
        client_cm = mock.MagicMock()
        client_cm.__enter__.return_value = client

        with (
            mock.patch("documents.demo_data.GotenbergClient", return_value=client_cm),
            mock.patch.object(
                settings,
                "TIKA_GOTENBERG_ENDPOINT",
                "http://gotenberg:3000",
            ),
        ):
            with tempfile.TemporaryDirectory() as tmpdir:
                html_path = Path(tmpdir) / "demo.html"
                html_path.write_text(
                    "<html><body><h1>Demo</h1></body></html>",
                    encoding="utf-8",
                )
                pdf_bytes = _render_html_to_pdf_bytes(html_path)

        self.assertEqual(pdf_bytes, b"%PDF-1.4 demo")
        client.chromium.html_to_pdf.assert_called_once()
        route.index.assert_called_once_with(html_path)
        route.index.return_value.margins.assert_called_once()
        margins_route.size.assert_called_once()
        size_route.scale.assert_called_once()
        scale_route.run.assert_called_once()

    def test_reset_runtime_data_deletes_custom_fields_and_workflow_children(self):
        owner = User.objects.create_superuser(username="reset-runtime-owner")
        custom_field = CustomField.objects.create(
            name="Auftragsnummer",
            data_type=CustomField.FieldDataType.STRING,
        )
        trigger = WorkflowTrigger.objects.create(
            schedule_date_custom_field=custom_field,
        )
        action = WorkflowAction.objects.create()
        action.assign_custom_fields.add(custom_field)
        action.remove_custom_fields.add(custom_field)
        workflow = Workflow.objects.create(name="Test Workflow")
        workflow.triggers.add(trigger)
        workflow.actions.add(action)

        result = reset_runtime_data.apply(kwargs={"owner_id": owner.id})

        self.assertEqual(result.state, states.SUCCESS)
        self.assertEqual(CustomField.objects.count(), 0)
        self.assertEqual(Workflow.objects.count(), 0)
        self.assertEqual(WorkflowTrigger.objects.count(), 0)
        self.assertEqual(WorkflowAction.objects.count(), 0)

    def test_api_remote_import_inspect_uses_saved_masked_token(self):
        config = ApplicationConfiguration.objects.first()
        config.remote_import_base_url = "https://remote.example.com"
        config.remote_import_api_token = "saved-secret-token"
        config.save()

        with mock.patch(
            "paperless.views.RemoteImportService.inspect",
            return_value={"remote": {}, "mappings": {}},
        ) as inspect_mock:
            response = self.client.post(
                "/api/config/1/remote-import-inspect/",
                json.dumps(
                    {
                        "base_url": "https://remote.example.com",
                        "api_token": "**********",
                    },
                ),
                content_type="application/json",
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        _, kwargs = inspect_mock.call_args
        self.assertEqual(kwargs["api_token"], "saved-secret-token")

    def test_api_remote_import_documents_accepts_nested_remote_objects(self):
        with mock.patch(
            "paperless.views.RemoteImportService.browse_documents",
            return_value={
                "count": 1,
                "next": None,
                "previous": None,
                "all": [7],
                "results": [
                    {
                        "id": 7,
                        "document_url": "https://remote.example.com/documents/7",
                        "title": "Invoice 7",
                        "created": "2026-03-29",
                        "original_file_name": "invoice-7.pdf",
                        "archive_serial_number": None,
                        "correspondent": {"id": 3, "name": "ACME"},
                        "document_type": {"id": 2, "name": "Invoices"},
                        "storage_path": {"id": 4, "name": "Inbox"},
                        "tags": [{"id": 9, "name": "mail"}],
                        "custom_fields": [],
                    },
                ],
            },
        ):
            response = self.client.post(
                "/api/config/1/remote-import-documents/",
                json.dumps(
                    {
                        "base_url": "https://remote.example.com",
                        "api_token": "secret-token",
                        "query": "",
                        "page": 1,
                        "page_size": 25,
                    },
                ),
                content_type="application/json",
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["results"][0]["correspondent"]["name"], "ACME")
        self.assertEqual(
            response.data["results"][0]["document_url"],
            "https://remote.example.com/documents/7",
        )

    def test_remote_import_service_builds_document_urls_from_api_base_url(self):
        service = RemoteImportService(
            base_url="https://remote.example.com/paperless/api",
            api_token="secret-token",
        )

        self.assertEqual(
            service._build_remote_document_url(7),
            "https://remote.example.com/paperless/documents/7",
        )
