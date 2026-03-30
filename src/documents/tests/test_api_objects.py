import datetime
import json
from unittest import mock

from django.contrib.auth.models import Permission
from django.contrib.auth.models import User
from django.test import override_settings
from guardian.shortcuts import assign_perm
from rest_framework import status
from rest_framework.test import APITestCase

from documents.models import Correspondent
from documents.models import CustomField
from documents.models import CustomFieldInstance
from documents.models import Document
from documents.models import DocumentType
from documents.models import StoragePath
from documents.models import Tag
from documents.tests.utils import DirectoriesMixin
from paperless.parsers.xrechnung import InvoiceData
from paperless.parsers.xrechnung import InvoiceParty


class TestApiObjects(DirectoriesMixin, APITestCase):
    def setUp(self) -> None:
        super().setUp()

        user = User.objects.create_superuser(username="temp_admin")
        self.client.force_authenticate(user=user)

        self.tag1 = Tag.objects.create(name="t1", is_inbox_tag=True)
        self.tag2 = Tag.objects.create(name="t2")
        self.tag3 = Tag.objects.create(name="t3")
        self.c1 = Correspondent.objects.create(name="c1")
        self.c2 = Correspondent.objects.create(name="c2")
        self.c3 = Correspondent.objects.create(name="c3")
        self.dt1 = DocumentType.objects.create(name="dt1")
        self.dt2 = DocumentType.objects.create(name="dt2")
        self.sp1 = StoragePath.objects.create(name="sp1", path="Something/{title}")
        self.sp2 = StoragePath.objects.create(name="sp2", path="Something2/{title}")

    def test_object_filters(self) -> None:
        response = self.client.get(
            f"/api/tags/?id={self.tag2.id}",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data["results"]
        self.assertEqual(len(results), 1)

        response = self.client.get(
            f"/api/tags/?id__in={self.tag1.id},{self.tag3.id}",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data["results"]
        self.assertEqual(len(results), 2)

        response = self.client.get(
            f"/api/correspondents/?id={self.c2.id}",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data["results"]
        self.assertEqual(len(results), 1)

        response = self.client.get(
            f"/api/correspondents/?id__in={self.c1.id},{self.c3.id}",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data["results"]
        self.assertEqual(len(results), 2)

        response = self.client.get(
            f"/api/document_types/?id={self.dt1.id}",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data["results"]
        self.assertEqual(len(results), 1)

        response = self.client.get(
            f"/api/document_types/?id__in={self.dt1.id},{self.dt2.id}",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data["results"]
        self.assertEqual(len(results), 2)

        response = self.client.get(
            f"/api/storage_paths/?id={self.sp1.id}",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data["results"]
        self.assertEqual(len(results), 1)

        response = self.client.get(
            f"/api/storage_paths/?id__in={self.sp1.id},{self.sp2.id}",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data["results"]
        self.assertEqual(len(results), 2)

    def test_correspondent_last_correspondence(self) -> None:
        """
        GIVEN:
            - Correspondent with documents
        WHEN:
            - API is called
        THEN:
            - Last correspondence date is returned only if requested for list, and for detail
        """

        Document.objects.create(
            mime_type="application/pdf",
            correspondent=self.c1,
            created=datetime.date(2022, 1, 1),
            checksum="123",
        )
        Document.objects.create(
            mime_type="application/pdf",
            correspondent=self.c1,
            created=datetime.date(2022, 1, 2),
            checksum="456",
        )

        # Only if requested for list
        response = self.client.get(
            "/api/correspondents/",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data["results"]
        self.assertNotIn("last_correspondence", results[0])

        response = self.client.get(
            "/api/correspondents/?last_correspondence=true",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data["results"]
        self.assertIn(
            "2022-01-02",
            results[0]["last_correspondence"],
        )

        # Included in detail by default
        response = self.client.get(
            f"/api/correspondents/{self.c1.id}/",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn(
            "2022-01-02",
            response.data["last_correspondence"],
        )


class TestApiStoragePaths(DirectoriesMixin, APITestCase):
    ENDPOINT = "/api/storage_paths/"

    def setUp(self) -> None:
        super().setUp()

        user = User.objects.create_superuser(username="temp_admin")
        self.client.force_authenticate(user=user)

        self.sp1 = StoragePath.objects.create(name="sp1", path="Something/{checksum}")

    def test_api_get_storage_path(self) -> None:
        """
        GIVEN:
            - API request to get all storage paths
        WHEN:
            - API is called
        THEN:
            - Existing storage paths are returned
        """
        response = self.client.get(self.ENDPOINT, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 1)

        resp_storage_path = response.data["results"][0]
        self.assertEqual(resp_storage_path["id"], self.sp1.id)
        self.assertEqual(resp_storage_path["path"], self.sp1.path)

    def test_api_create_storage_path(self) -> None:
        """
        GIVEN:
            - API request to create a storage paths
        WHEN:
            - API is called
        THEN:
            - Correct HTTP response
            - New storage path is created
        """
        response = self.client.post(
            self.ENDPOINT,
            json.dumps(
                {
                    "name": "A storage path",
                    "path": "Somewhere/{asn}",
                },
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(StoragePath.objects.count(), 2)

    def test_api_create_invalid_storage_path(self) -> None:
        """
        GIVEN:
            - API request to create a storage paths
            - Storage path format is incorrect
        WHEN:
            - API is called
        THEN:
            - Correct HTTP 400 response
            - No storage path is created
        """
        response = self.client.post(
            self.ENDPOINT,
            json.dumps(
                {
                    "name": "Another storage path",
                    "path": "Somewhere/{correspdent}",
                },
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(StoragePath.objects.count(), 1)

    def test_api_create_storage_path_rejects_traversal(self) -> None:
        """
        GIVEN:
            - API request to create a storage paths
            - Storage path attempts directory traversal
        WHEN:
            - API is called
        THEN:
            - Correct HTTP 400 response
            - No storage path is created
        """
        response = self.client.post(
            self.ENDPOINT,
            json.dumps(
                {
                    "name": "Traversal path",
                    "path": "../../../../../tmp/proof",
                },
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(StoragePath.objects.count(), 1)

    def test_api_storage_path_placeholders(self) -> None:
        """
        GIVEN:
            - API request to create a storage path with placeholders
            - Storage path is valid
        WHEN:
            - API is called
        THEN:
            - Correct HTTP response
            - New storage path is created
        """
        response = self.client.post(
            self.ENDPOINT,
            json.dumps(
                {
                    "name": "Storage path with placeholders",
                    "path": "{title}/{correspondent}/{document_type}/{created}/{created_year}"
                    "/{created_year_short}/{created_month}/{created_month_name}"
                    "/{created_month_name_short}/{created_day}/{added}/{added_year}"
                    "/{added_year_short}/{added_month}/{added_month_name}"
                    "/{added_month_name_short}/{added_day}/{asn}"
                    "/{tag_list}/{owner_username}/{original_name}/{doc_pk}/",
                },
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(StoragePath.objects.count(), 2)

    @mock.patch("documents.bulk_edit.bulk_update_documents.delay")
    def test_api_update_storage_path(self, bulk_update_mock) -> None:
        """
        GIVEN:
            - API request to get all storage paths
        WHEN:
            - API is called
        THEN:
            - Existing storage paths are returned
        """
        document = Document.objects.create(
            mime_type="application/pdf",
            storage_path=self.sp1,
        )
        response = self.client.patch(
            f"{self.ENDPOINT}{self.sp1.pk}/",
            data={
                "path": "somewhere/{created} - {title}",
            },
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        bulk_update_mock.assert_called_once()

        args, _ = bulk_update_mock.call_args

        self.assertCountEqual([document.pk], args[0])

    @mock.patch("documents.bulk_edit.bulk_update_documents.delay")
    def test_api_delete_storage_path(self, bulk_update_mock) -> None:
        """
        GIVEN:
            - API request to delete a storage
        WHEN:
            - API is called
        THEN:
            - Documents using the storage path are updated
        """
        document = Document.objects.create(
            mime_type="application/pdf",
            storage_path=self.sp1,
        )
        response = self.client.delete(
            f"{self.ENDPOINT}{self.sp1.pk}/",
        )
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

        # sp with no documents
        sp2 = StoragePath.objects.create(name="sp2", path="Something2/{checksum}")
        response = self.client.delete(
            f"{self.ENDPOINT}{sp2.pk}/",
        )
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

        # only called once
        bulk_update_mock.assert_called_once_with([document.pk])

    def test_test_storage_path(self) -> None:
        """
        GIVEN:
            - API request to test a storage path
        WHEN:
            - API is called
        THEN:
            - Correct HTTP response
            - Correct response data
        """
        document = Document.objects.create(
            mime_type="application/pdf",
            storage_path=self.sp1,
            title="Something",
            checksum="123",
        )
        response = self.client.post(
            f"{self.ENDPOINT}test/",
            json.dumps(
                {
                    "document": document.id,
                    "path": "path/{{ title }}",
                },
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, "path/Something")

    def test_test_storage_path_respects_none_placeholder_setting(self) -> None:
        """
        GIVEN:
            - A storage path template referencing an empty field
        WHEN:
            - Testing the template before and after enabling remove-none
        THEN:
            - The preview shows "none" by default and drops the placeholder when configured
        """
        document = Document.objects.create(
            mime_type="application/pdf",
            storage_path=self.sp1,
            title="Something",
            checksum="123",
        )
        payload = json.dumps(
            {
                "document": document.id,
                "path": "folder/{{ correspondent }}/{{ title }}",
            },
        )

        response = self.client.post(
            f"{self.ENDPOINT}test/",
            payload,
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, "folder/none/Something")

        with override_settings(FILENAME_FORMAT_REMOVE_NONE=True):
            response = self.client.post(
                f"{self.ENDPOINT}test/",
                payload,
                content_type="application/json",
            )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, "folder/Something")

    def test_test_storage_path_requires_document_view_permission(self) -> None:
        owner = User.objects.create_user(username="owner")
        unprivileged = User.objects.create_user(username="unprivileged")
        document = Document.objects.create(
            mime_type="application/pdf",
            owner=owner,
            title="Sensitive",
            checksum="123",
        )
        self.client.force_authenticate(user=unprivileged)
        response = self.client.post(
            f"{self.ENDPOINT}test/",
            json.dumps(
                {
                    "document": document.id,
                    "path": "path/{{ title }}",
                },
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("document", response.data)

    def test_test_storage_path_allows_shared_document_view_permission(self) -> None:
        owner = User.objects.create_user(username="owner")
        viewer = User.objects.create_user(username="viewer")
        document = Document.objects.create(
            mime_type="application/pdf",
            owner=owner,
            title="Shared",
            checksum="123",
        )
        assign_perm("view_document", viewer, document)

        self.client.force_authenticate(user=viewer)
        response = self.client.post(
            f"{self.ENDPOINT}test/",
            json.dumps(
                {
                    "document": document.id,
                    "path": "path/{{ title }}",
                },
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, "path/Shared")

    def test_test_storage_path_exposes_basic_document_context_but_not_sensitive_owner_data(
        self,
    ) -> None:
        owner = User.objects.create_user(
            username="owner",
            password="password",
            email="owner@example.com",
        )
        document = Document.objects.create(
            mime_type="application/pdf",
            owner=owner,
            title="Document",
            content="Top secret content",
            page_count=2,
            checksum="123",
        )
        self.client.force_authenticate(user=owner)

        response = self.client.post(
            f"{self.ENDPOINT}test/",
            json.dumps(
                {
                    "document": document.id,
                    "path": "{{ document.owner.username }}",
                },
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, "owner")

        for expression, expected in (
            ("{{ document.content }}", "Top secret content"),
            ("{{ document.id }}", str(document.id)),
            ("{{ document.page_count }}", "2"),
        ):
            response = self.client.post(
                f"{self.ENDPOINT}test/",
                json.dumps(
                    {
                        "document": document.id,
                        "path": expression,
                    },
                ),
                content_type="application/json",
            )
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            self.assertEqual(response.data, expected)

        for expression in (
            "{{ document.owner.password }}",
            "{{ document.owner.email }}",
        ):
            response = self.client.post(
                f"{self.ENDPOINT}test/",
                json.dumps(
                    {
                        "document": document.id,
                        "path": expression,
                    },
                ),
                content_type="application/json",
            )
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            self.assertIsNone(response.data)

    def test_test_storage_path_includes_related_objects_for_visible_document(
        self,
    ) -> None:
        owner = User.objects.create_user(username="owner")
        viewer = User.objects.create_user(username="viewer")
        private_correspondent = Correspondent.objects.create(
            name="Private Correspondent",
            owner=owner,
        )
        document = Document.objects.create(
            mime_type="application/pdf",
            owner=owner,
            correspondent=private_correspondent,
            title="Document",
            checksum="123",
        )
        assign_perm("view_document", viewer, document)

        self.client.force_authenticate(user=viewer)
        response = self.client.post(
            f"{self.ENDPOINT}test/",
            json.dumps(
                {
                    "document": document.id,
                    "path": "{{ correspondent }}",
                },
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, "Private Correspondent")

        response = self.client.post(
            f"{self.ENDPOINT}test/",
            json.dumps(
                {
                    "document": document.id,
                    "path": (
                        "{{ document.correspondent.name if document.correspondent else 'none' }}"
                    ),
                },
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, "Private Correspondent")

    def test_test_storage_path_superuser_can_view_private_related_objects(self) -> None:
        owner = User.objects.create_user(username="owner")
        private_correspondent = Correspondent.objects.create(
            name="Private Correspondent",
            owner=owner,
        )
        document = Document.objects.create(
            mime_type="application/pdf",
            owner=owner,
            correspondent=private_correspondent,
            title="Document",
            checksum="123",
        )

        response = self.client.post(
            f"{self.ENDPOINT}test/",
            json.dumps(
                {
                    "document": document.id,
                    "path": (
                        "{{ document.correspondent.name if document.correspondent else 'none' }}"
                    ),
                },
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, "Private Correspondent")

    def test_test_storage_path_includes_doc_type_storage_path_and_tags(
        self,
    ) -> None:
        owner = User.objects.create_user(username="owner")
        viewer = User.objects.create_user(username="viewer")
        private_document_type = DocumentType.objects.create(
            name="Private Type",
            owner=owner,
        )
        private_storage_path = StoragePath.objects.create(
            name="Private Storage Path",
            path="private/path",
            owner=owner,
        )
        private_tag = Tag.objects.create(
            name="Private Tag",
            owner=owner,
        )
        document = Document.objects.create(
            mime_type="application/pdf",
            owner=owner,
            document_type=private_document_type,
            storage_path=private_storage_path,
            title="Document",
            checksum="123",
        )
        document.tags.add(private_tag)
        assign_perm("view_document", viewer, document)

        self.client.force_authenticate(user=viewer)
        response = self.client.post(
            f"{self.ENDPOINT}test/",
            json.dumps(
                {
                    "document": document.id,
                    "path": (
                        "{{ document.document_type.name if document.document_type else 'none' }}/"
                        "{{ document.storage_path.path if document.storage_path else 'none' }}/"
                        "{{ document.tags[0].name if document.tags else 'none' }}"
                    ),
                },
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, "Private Type/private/path/Private Tag")

        response = self.client.post(
            f"{self.ENDPOINT}test/",
            json.dumps(
                {
                    "document": document.id,
                    "path": "{{ document_type }}/{{ tag_list if tag_list else 'none' }}",
                },
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, "Private Type/Private Tag")

    def test_test_storage_path_includes_custom_fields_for_visible_document(
        self,
    ) -> None:
        owner = User.objects.create_user(username="owner")
        viewer = User.objects.create_user(username="viewer")
        document = Document.objects.create(
            mime_type="application/pdf",
            owner=owner,
            title="Document",
            checksum="123",
        )
        custom_field = CustomField.objects.create(
            name="Secret Number",
            data_type=CustomField.FieldDataType.INT,
        )
        CustomFieldInstance.objects.create(
            document=document,
            field=custom_field,
            value_int=42,
        )
        assign_perm("view_document", viewer, document)

        self.client.force_authenticate(user=viewer)
        response = self.client.post(
            f"{self.ENDPOINT}test/",
            json.dumps(
                {
                    "document": document.id,
                    "path": "{{ custom_fields | get_cf_value('Secret Number', 'none') }}",
                },
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, "42")


class TestApiDocumentTypes(DirectoriesMixin, APITestCase):
    ENDPOINT = "/api/document_types/"

    def setUp(self) -> None:
        super().setUp()

        user = User.objects.create_superuser(username="temp_admin")
        self.client.force_authenticate(user=user)

    def test_api_create_document_type_with_custom_fields(self) -> None:
        custom_field_1 = CustomField.objects.create(
            name="Invoice Number",
            data_type=CustomField.FieldDataType.STRING,
        )
        custom_field_2 = CustomField.objects.create(
            name="Due Date",
            data_type=CustomField.FieldDataType.DATE,
        )

        response = self.client.post(
            self.ENDPOINT,
            json.dumps(
                {
                    "name": "Invoice",
                    "custom_fields": [custom_field_1.id, custom_field_2.id],
                    "retention_period_years": 10,
                },
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertCountEqual(
            response.data["custom_fields"],
            [custom_field_1.id, custom_field_2.id],
        )
        self.assertEqual(response.data["retention_period_years"], 10)

    def test_api_retrieve_document_type_includes_custom_fields(self) -> None:
        custom_field = CustomField.objects.create(
            name="Contract Number",
            data_type=CustomField.FieldDataType.STRING,
        )
        document_type = DocumentType.objects.create(
            name="Contract",
            retention_period_years=6,
        )
        document_type.custom_fields.add(custom_field)

        response = self.client.get(f"{self.ENDPOINT}{document_type.id}/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["custom_fields"], [custom_field.id])
        self.assertEqual(response.data["retention_period_years"], 6)

    def test_api_create_document_type_with_xrechnung_settings(self) -> None:
        custom_field = CustomField.objects.create(
            name="Invoice Number",
            data_type=CustomField.FieldDataType.STRING,
        )

        response = self.client.post(
            self.ENDPOINT,
            json.dumps(
                {
                    "name": "XRechnung",
                    "enable_xrechnung_import": True,
                    "xrechnung_correspondent_field": "seller_name",
                    "xrechnung_custom_field_mappings": [
                        {
                            "custom_field": custom_field.id,
                            "source": "invoice_number",
                        },
                    ],
                },
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(response.data["enable_xrechnung_import"])
        self.assertEqual(response.data["xrechnung_correspondent_field"], "seller_name")
        self.assertEqual(
            response.data["xrechnung_custom_field_mappings"],
            [
                {
                    "custom_field": custom_field.id,
                    "source": "invoice_number",
                },
            ],
        )

    def test_api_rejects_multiple_xrechnung_document_types(self) -> None:
        DocumentType.objects.create(
            name="Existing XRechnung",
            enable_xrechnung_import=True,
        )

        response = self.client.post(
            self.ENDPOINT,
            json.dumps(
                {
                    "name": "Second XRechnung",
                    "enable_xrechnung_import": True,
                },
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("enable_xrechnung_import", response.data)

    def test_api_updating_xrechnung_mappings_does_not_modify_existing_documents(
        self,
    ) -> None:
        custom_field = CustomField.objects.create(
            name="Invoice Number",
            data_type=CustomField.FieldDataType.STRING,
        )
        document_type = DocumentType.objects.create(
            name="XRechnung",
            enable_xrechnung_import=True,
        )
        document = Document.objects.create(
            title="existing metadata",
            content="content",
            checksum="doc-type-update-1",
            mime_type="application/xml",
            document_type=document_type,
        )
        CustomFieldInstance.objects.create(
            document=document,
            field=custom_field,
            value_text="keep-me",
        )

        response = self.client.patch(
            f"{self.ENDPOINT}{document_type.pk}/",
            data={
                "xrechnung_custom_field_mappings": [
                    {
                        "custom_field": custom_field.pk,
                        "source": "invoice_number",
                    },
                ],
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        document.refresh_from_db()

        self.assertEqual(
            document.custom_fields.get(field=custom_field).value,
            "keep-me",
        )

    @mock.patch("documents.serialisers.parse_xrechnung_invoice")
    def test_api_apply_xrechnung_mappings_action_updates_existing_documents(
        self,
        mock_parse_xrechnung_invoice,
    ) -> None:
        custom_field = CustomField.objects.create(
            name="Invoice Number",
            data_type=CustomField.FieldDataType.STRING,
        )
        document_type = DocumentType.objects.create(
            name="XRechnung",
            enable_xrechnung_import=True,
            xrechnung_correspondent_field="seller_name",
            xrechnung_custom_field_mappings=[
                {
                    "custom_field": custom_field.pk,
                    "source": "invoice_number",
                },
            ],
        )
        document = Document.objects.create(
            title="xrechnung",
            content="content",
            checksum="doc-type-update-2",
            mime_type="application/xml",
            document_type=document_type,
        )
        document.source_write_bytes(b"<invoice />")
        CustomFieldInstance.objects.create(
            document=document,
            field=custom_field,
            value_text="old-value",
        )
        mock_parse_xrechnung_invoice.return_value = InvoiceData(
            profile="xrechnung",
            invoice_number="1122334455",
            invoice_type_code=None,
            issue_date=None,
            due_amount=None,
            grand_total=None,
            tax_total=None,
            currency="EUR",
            buyer_reference=None,
            payment_reference=None,
            payment_terms=None,
            seller=InvoiceParty(name="TÜV Rheinland GmbH"),
            buyer=InvoiceParty(),
            notes=[],
            lines=[],
            raw_text="",
        )

        response = self.client.post(
            f"{self.ENDPOINT}{document_type.pk}/apply_xrechnung_mappings/",
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["updated_documents"], 1)
        document.refresh_from_db()

        self.assertEqual(document.correspondent.name, "TÜV Rheinland GmbH")
        self.assertEqual(
            document.custom_fields.get(field=custom_field).value,
            "1122334455",
        )


class TestBulkEditObjects(APITestCase):
    # See test_api_permissions.py for bulk tests on permissions
    def setUp(self) -> None:
        super().setUp()

        self.temp_admin = User.objects.create_superuser(username="temp_admin")
        self.client.force_authenticate(user=self.temp_admin)

        self.t1 = Tag.objects.create(name="t1")
        self.t2 = Tag.objects.create(name="t2")
        self.c1 = Correspondent.objects.create(name="c1")
        self.dt1 = DocumentType.objects.create(name="dt1")
        self.sp1 = StoragePath.objects.create(name="sp1")
        self.user1 = User.objects.create(username="user1")
        self.user2 = User.objects.create(username="user2")
        self.user3 = User.objects.create(username="user3")

    def test_bulk_objects_delete(self) -> None:
        """
        GIVEN:
            - Existing objects
        WHEN:
            - bulk_edit_objects API endpoint is called with delete operation
        THEN:
            - Objects are deleted
        """
        response = self.client.post(
            "/api/bulk_edit_objects/",
            json.dumps(
                {
                    "objects": [self.t1.id, self.t2.id],
                    "object_type": "tags",
                    "operation": "delete",
                },
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(Tag.objects.count(), 0)

        response = self.client.post(
            "/api/bulk_edit_objects/",
            json.dumps(
                {
                    "objects": [self.c1.id],
                    "object_type": "correspondents",
                    "operation": "delete",
                },
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(Correspondent.objects.count(), 0)

        response = self.client.post(
            "/api/bulk_edit_objects/",
            json.dumps(
                {
                    "objects": [self.dt1.id],
                    "object_type": "document_types",
                    "operation": "delete",
                },
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(DocumentType.objects.count(), 0)

        response = self.client.post(
            "/api/bulk_edit_objects/",
            json.dumps(
                {
                    "objects": [self.sp1.id],
                    "object_type": "storage_paths",
                    "operation": "delete",
                },
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(StoragePath.objects.count(), 0)

    def test_bulk_edit_object_permissions_insufficient_global_perms(self) -> None:
        """
        GIVEN:
            - Existing objects, user does not have global delete permissions
        WHEN:
            - bulk_edit_objects API endpoint is called with delete operation
        THEN:
            - User is not able to delete objects
        """
        self.client.force_authenticate(user=self.user1)

        response = self.client.post(
            "/api/bulk_edit_objects/",
            json.dumps(
                {
                    "objects": [self.t1.id, self.t2.id],
                    "object_type": "tags",
                    "operation": "delete",
                },
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.content, b"Insufficient permissions")

    def test_bulk_edit_object_permissions_sufficient_global_perms(self) -> None:
        """
        GIVEN:
            - Existing objects, user does have global delete permissions
        WHEN:
            - bulk_edit_objects API endpoint is called with delete operation
        THEN:
            - User is able to delete objects
        """
        self.user1.user_permissions.add(
            *Permission.objects.filter(codename="delete_tag"),
        )
        self.user1.save()
        self.client.force_authenticate(user=self.user1)

        response = self.client.post(
            "/api/bulk_edit_objects/",
            json.dumps(
                {
                    "objects": [self.t1.id, self.t2.id],
                    "object_type": "tags",
                    "operation": "delete",
                },
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_bulk_edit_object_permissions_insufficient_object_perms(self) -> None:
        """
        GIVEN:
            - Objects owned by user other than logged in user
        WHEN:
            - bulk_edit_objects API endpoint is called with delete operation
        THEN:
            - User is not able to delete objects
        """
        self.t2.owner = User.objects.get(username="temp_admin")
        self.t2.save()

        self.user1.user_permissions.add(
            *Permission.objects.filter(codename="delete_tag"),
        )
        self.user1.save()
        self.client.force_authenticate(user=self.user1)

        response = self.client.post(
            "/api/bulk_edit_objects/",
            json.dumps(
                {
                    "objects": [self.t1.id, self.t2.id],
                    "object_type": "tags",
                    "operation": "delete",
                },
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.content, b"Insufficient permissions")
