from unittest import mock

from paperless.remote_import import RemoteImportError
from paperless.remote_import import RemoteImportService
from paperless.remote_import import extract_filename_from_content_disposition


def test_extract_filename_from_content_disposition_prefers_filename_star() -> None:
    disposition = (
        "attachment; "
        'filename="2026-03-30 Abschlagsrechnung.pdf"; '
        "filename*=utf-8''2026-03-30%20Abschlagsrechnung.pdf"
    )

    assert (
        extract_filename_from_content_disposition(
            disposition,
            fallback="document-11",
        )
        == "2026-03-30 Abschlagsrechnung.pdf"
    )


def test_extract_filename_from_content_disposition_uses_filename_fallback() -> None:
    disposition = 'attachment; filename="invoice.pdf"'

    assert (
        extract_filename_from_content_disposition(
            disposition,
            fallback="document-11",
        )
        == "invoice.pdf"
    )


def test_extract_filename_from_content_disposition_uses_default_fallback() -> None:
    assert (
        extract_filename_from_content_disposition(
            "",
            fallback="document-11",
        )
        == "document-11"
    )


class DummyRemoteClient:
    def __init__(self, payload, entities=None, failing_paths=None) -> None:
        self.payload = payload
        self.entities = entities or {}
        self.failing_paths = set(failing_paths or [])

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def get_documents_page(self, *, query: str, page: int, page_size: int):
        return self.payload

    def get_entity_detail(self, path: str, object_id: int):
        if path in self.failing_paths:
            raise RemoteImportError(f"{path} endpoint failed")
        return self.entities[(path, object_id)]


def test_browse_documents_fetches_only_referenced_entities() -> None:
    payload = {
        "count": 1,
        "next": None,
        "previous": None,
        "all": [7],
        "results": [
            {
                "id": 7,
                "title": "Invoice 7",
                "created": "2026-03-29",
                "original_file_name": "invoice-7.pdf",
                "archive_serial_number": None,
                "correspondent": 3,
                "document_type": {"id": 2, "name": "Invoices"},
                "storage_path": None,
                "tags": [9],
                "custom_fields": [{"field": 11, "value": "ABC"}],
            },
        ],
    }
    entities = {
        ("correspondents", 3): {"id": 3, "name": "ACME"},
        ("tags", 9): {"id": 9, "name": "mail"},
        ("document_types", 2): {"id": 2, "name": "Invoices"},
        ("custom_fields", 11): {
            "id": 11,
            "name": "Order ID",
            "data_type": "string",
        },
    }
    client = DummyRemoteClient(payload, entities)
    service = RemoteImportService(
        base_url="https://remote.example.com",
        api_token="secret-token",
    )

    with mock.patch.object(service, "_client", return_value=client):
        result = service.browse_documents(query="", page=1, page_size=25)

    assert result["results"][0]["correspondent"] == {"id": 3, "name": "ACME"}
    assert result["results"][0]["document_type"] == {"id": 2, "name": "Invoices"}
    assert result["results"][0]["tags"] == [{"id": 9, "name": "mail"}]
    assert result["results"][0]["custom_fields"] == [
        {
            "field_id": 11,
            "field_name": "Order ID",
            "data_type": "string",
            "value": "ABC",
        },
    ]


def test_browse_documents_tolerates_reference_endpoint_failures() -> None:
    payload = {
        "count": 1,
        "next": None,
        "previous": None,
        "all": [7],
        "results": [
            {
                "id": 7,
                "title": "Invoice 7",
                "created": "2026-03-29",
                "original_file_name": "invoice-7.pdf",
                "archive_serial_number": None,
                "correspondent": 3,
                "document_type": None,
                "storage_path": None,
                "tags": [9],
                "custom_fields": [],
            },
        ],
    }
    client = DummyRemoteClient(payload, failing_paths={"correspondents", "tags"})
    service = RemoteImportService(
        base_url="https://remote.example.com",
        api_token="secret-token",
    )

    with mock.patch.object(service, "_client", return_value=client):
        result = service.browse_documents(query="", page=1, page_size=25)

    assert result["results"][0]["id"] == 7
    assert result["results"][0]["correspondent"] is None
    assert result["results"][0]["tags"] == []
