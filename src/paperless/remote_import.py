import logging
import re
import shutil
from collections.abc import Callable
from pathlib import Path
from tempfile import mkdtemp
from typing import Any
from urllib.parse import unquote
from urllib.parse import urlsplit

import httpx
import pathvalidate
from celery import states
from django.conf import settings
from django.contrib.auth.models import User
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.dateparse import parse_datetime

from documents import index
from documents.data_models import ConsumableDocument
from documents.data_models import DocumentMetadataOverrides
from documents.data_models import DocumentSource
from documents.models import Correspondent
from documents.models import CustomField
from documents.models import Document
from documents.models import DocumentType
from documents.models import Note
from documents.models import PaperlessTask
from documents.models import StoragePath
from documents.models import Tag
from documents.tasks import consume_file
from paperless.network import validate_outbound_http_url

logger = logging.getLogger("paperless.remote_import")
CREATED_DOCUMENT_RE = re.compile(r"New document id (\d+) created")


class RemoteImportError(Exception):
    pass


def extract_filename_from_content_disposition(
    disposition: str,
    *,
    fallback: str,
) -> str:
    if not disposition:
        return fallback

    filename: str | None = None
    filename_star: str | None = None

    for part in disposition.split(";")[1:]:
        key, separator, value = part.strip().partition("=")
        if not separator:
            continue

        normalized_key = key.strip().lower()
        normalized_value = value.strip().strip('"')

        if normalized_key == "filename*" and normalized_value:
            try:
                charset, _, encoded_filename = normalized_value.split("'", 2)
            except ValueError:
                filename_star = unquote(normalized_value)
            else:
                filename_star = unquote(
                    encoded_filename,
                    encoding=charset or "utf-8",
                    errors="replace",
                )
        elif normalized_key == "filename" and normalized_value:
            filename = normalized_value

    return filename_star or filename or fallback


def normalize_remote_api_url(base_url: str) -> str:
    parsed = validate_outbound_http_url(base_url, allow_internal=True)
    normalized = parsed.geturl().rstrip("/")
    if normalized.endswith("/api"):
        normalized = f"{normalized}/"
    elif not normalized.endswith("/api/"):
        normalized = f"{normalized}/api/"
    else:
        normalized = f"{normalized}/"
    return normalized


def parse_remote_created_date(value: Any):
    if not isinstance(value, str) or not value:
        return None

    parsed_datetime = parse_datetime(value)
    if parsed_datetime is not None:
        return parsed_datetime.date()

    return parse_date(value)


def coerce_remote_id(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    if isinstance(value, dict):
        nested_id = value.get("id")
        if isinstance(nested_id, int):
            return nested_id
        if isinstance(nested_id, str):
            try:
                return int(nested_id)
            except ValueError:
                return None
    return None


def get_remote_name(value: Any) -> str | None:
    if isinstance(value, dict):
        name = value.get("name")
        if isinstance(name, str):
            return name
    return None


def extract_related_document_id_from_result(result: Any) -> int | None:
    if not result or not isinstance(result, str):
        return None
    match = CREATED_DOCUMENT_RE.search(result)
    if not match:
        return None
    try:
        return int(match.group(1))
    except (TypeError, ValueError):
        return None


class RemotePaperlessClient:
    def __init__(self, *, base_url: str, api_token: str) -> None:
        self.base_url = normalize_remote_api_url(base_url)
        self.client = httpx.Client(
            base_url=self.base_url,
            headers={
                "Authorization": f"Token {api_token}",
                "Accept": "application/json",
            },
            follow_redirects=True,
            timeout=httpx.Timeout(30.0, connect=10.0),
        )

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> "RemotePaperlessClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        try:
            response = self.client.request(method, path, **kwargs)
            response.raise_for_status()
            return response
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text or str(exc)
            raise RemoteImportError(
                f"Remote API request failed with status {exc.response.status_code}: {detail}",
            ) from exc
        except httpx.HTTPError as exc:
            raise RemoteImportError(
                f"Could not connect to remote instance: {exc}",
            ) from exc

    def get_json(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any] | list[Any]:
        return self._request("GET", path, params=params).json()

    def get_all_results(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        merged_params = {"page": 1, "page_size": 1000}
        if params:
            merged_params.update(params)

        results: list[dict[str, Any]] = []
        while True:
            payload = self.get_json(path, params=merged_params)
            if isinstance(payload, list):
                return payload
            if not isinstance(payload, dict):
                raise RemoteImportError("Remote API returned an unexpected response.")

            page_results = payload.get("results")
            if not isinstance(page_results, list):
                raise RemoteImportError("Remote API pagination response is invalid.")
            results.extend(page_results)

            if not payload.get("next"):
                break
            merged_params["page"] += 1

        return results

    def get_remote_title(self) -> str:
        payload = self.get_json("ui_settings/")
        if isinstance(payload, dict):
            settings_payload = payload.get("settings")
            if isinstance(settings_payload, dict):
                app_title = settings_payload.get("app_title")
                if isinstance(app_title, str) and app_title.strip():
                    return app_title.strip()

        hostname = urlsplit(self.base_url).hostname
        return hostname or self.base_url

    def get_documents_page(
        self,
        *,
        query: str,
        page: int,
        page_size: int,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "page": page,
            "page_size": page_size,
        }
        if query:
            params["query"] = query

        payload = self.get_json(
            "documents/",
            params=params,
        )
        if not isinstance(payload, dict):
            raise RemoteImportError("Remote documents response is invalid.")
        return payload

    def iter_matching_document_ids(self, *, query: str) -> tuple[int, list[int]]:
        payload = self.get_documents_page(query=query, page=1, page_size=1000)
        all_ids = payload.get("all")
        if isinstance(all_ids, list):
            ids = [int(doc_id) for doc_id in all_ids]
            return int(payload.get("count", len(ids))), ids

        count = int(payload.get("count", 0))
        ids: list[int] = []
        page = 1
        while True:
            page_payload = (
                payload
                if page == 1
                else self.get_documents_page(
                    query=query,
                    page=page,
                    page_size=1000,
                )
            )
            results = page_payload.get("results", [])
            ids.extend(
                int(result["id"])
                for result in results
                if isinstance(result, dict) and result.get("id") is not None
            )
            if not page_payload.get("next"):
                break
            page += 1

        return count, ids

    def get_document_detail(self, document_id: int) -> dict[str, Any]:
        payload = self.get_json(f"documents/{document_id}/")
        if not isinstance(payload, dict):
            raise RemoteImportError("Remote document detail is invalid.")
        return payload

    def get_filtered_results(
        self,
        path: str,
        *,
        ids: set[int],
    ) -> list[dict[str, Any]]:
        if not ids:
            return []

        payload = self.get_json(
            f"{path}/",
            params={
                "id__in": ",".join(str(object_id) for object_id in sorted(ids)),
                "page_size": len(ids),
            },
        )
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if not isinstance(payload, dict):
            raise RemoteImportError(f"Remote {path} response is invalid.")

        results = payload.get("results")
        if not isinstance(results, list):
            raise RemoteImportError(f"Remote {path} pagination response is invalid.")

        return [item for item in results if isinstance(item, dict)]

    def download_document_original(self, document_id: int) -> tuple[str, bytes]:
        response = self._request(
            "GET",
            f"documents/{document_id}/download/",
            params={"original": "true"},
        )

        filename = extract_filename_from_content_disposition(
            response.headers.get("content-disposition", ""),
            fallback=f"document-{document_id}",
        )
        return filename, response.content

    def get_notes(self, document_id: int) -> list[dict[str, Any]]:
        payload = self.get_json(f"documents/{document_id}/notes/")
        if not isinstance(payload, list):
            raise RemoteImportError("Remote notes response is invalid.")
        return payload


class RemoteImportService:
    def __init__(
        self,
        *,
        base_url: str,
        api_token: str,
        create_missing_items: bool = True,
        import_notes: bool = True,
        owner_id: int | None = None,
    ) -> None:
        self.base_url = normalize_remote_api_url(base_url)
        self.api_token = api_token
        self.create_missing_items = create_missing_items
        self.import_notes = import_notes
        self.owner = User.objects.filter(pk=owner_id).first() if owner_id else None

    def _client(self) -> RemotePaperlessClient:
        return RemotePaperlessClient(base_url=self.base_url, api_token=self.api_token)

    def _build_remote_document_url(self, document_id: int) -> str:
        remote_ui_base_url = self.base_url.removesuffix("/api/")
        return f"{remote_ui_base_url}/documents/{document_id}"

    def _collect_reference_ids(
        self,
        payload: dict[str, Any],
        *,
        field_name: str,
        nested_field_name: str | None = None,
    ) -> set[int]:
        ids: set[int] = set()
        results = payload.get("results", [])
        if not isinstance(results, list):
            return ids

        for result in results:
            if not isinstance(result, dict):
                continue

            if nested_field_name is None:
                raw_values = (
                    result.get(field_name)
                    if isinstance(result.get(field_name), list)
                    else []
                )
                for raw_value in raw_values:
                    resolved_id = coerce_remote_id(raw_value)
                    if resolved_id is not None:
                        ids.add(resolved_id)
                continue

            raw_value = result.get(field_name)
            if (
                isinstance(raw_value, dict)
                and nested_field_name is not None
                and isinstance(raw_value.get(nested_field_name), str)
            ):
                continue

            resolved_id = coerce_remote_id(raw_value)
            if resolved_id is not None:
                ids.add(resolved_id)

        return ids

    def _fetch_entity_map(
        self,
        *,
        client: RemotePaperlessClient,
        path: str,
        ids: set[int],
    ) -> dict[int, dict[str, Any]]:
        if not ids:
            return {}

        try:
            payload = client.get_filtered_results(path, ids=ids)
        except RemoteImportError as exc:
            logger.warning(
                "Remote import could not filter %s by ids, falling back to full list: %s",
                path,
                exc,
            )
            try:
                payload = client.get_all_results(f"{path}/")
            except RemoteImportError as fallback_exc:
                logger.warning(
                    "Remote import could not load %s list: %s",
                    path,
                    fallback_exc,
                )
                return {}

        entities: dict[int, dict[str, Any]] = {}
        for entity in payload:
            entity_id = coerce_remote_id(entity.get("id"))
            if entity_id is None or entity_id not in ids:
                continue
            entities[entity_id] = entity

        return entities

    def _extract_named_reference(
        self,
        raw_value: Any,
        entities: dict[int, dict[str, Any]],
    ) -> dict[str, Any] | None:
        resolved_id = coerce_remote_id(raw_value)
        entity = entities.get(resolved_id) if resolved_id is not None else None

        if isinstance(entity, dict):
            return {
                "id": entity.get("id"),
                "name": entity.get("name"),
            }

        if isinstance(raw_value, dict):
            name = raw_value.get("name")
            if isinstance(name, str):
                return {
                    "id": raw_value.get("id"),
                    "name": name,
                }

        return None

    def _extract_named_tags(
        self,
        raw_tags: Any,
        entities: dict[int, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if not isinstance(raw_tags, list):
            return []

        tags = []
        for raw_tag in raw_tags:
            extracted = self._extract_named_reference(raw_tag, entities)
            if extracted is not None:
                tags.append(extracted)

        return tags

    def _build_local_index(self, model) -> tuple[dict[str, Any], dict[str, Any]]:
        objects = list(model.objects.all())
        exact = {obj.name: obj for obj in objects}
        folded = {obj.name.casefold(): obj for obj in objects}
        return exact, folded

    def _find_by_name(self, exact: dict[str, Any], folded: dict[str, Any], name: str):
        return exact.get(name) or folded.get(name.casefold())

    def _resolve_named_object(
        self,
        *,
        model,
        name: str | None,
        exact: dict[str, Any],
        folded: dict[str, Any],
    ):
        if not name:
            return None

        existing = self._find_by_name(exact, folded, name)
        if existing is not None or not self.create_missing_items:
            return existing

        created = model.objects.create(name=name)
        exact[created.name] = created
        folded[created.name.casefold()] = created
        return created

    def _resolve_custom_field(
        self,
        *,
        remote_field: dict[str, Any],
        exact: dict[str, CustomField],
        folded: dict[str, CustomField],
    ) -> CustomField | None:
        name = remote_field.get("name")
        data_type = remote_field.get("data_type")
        if not isinstance(name, str) or not isinstance(data_type, str):
            return None

        existing = self._find_by_name(exact, folded, name)
        if existing is not None:
            if existing.data_type != data_type:
                return None
            return existing

        if not self.create_missing_items:
            return None

        created = CustomField.objects.create(
            name=name,
            data_type=data_type,
            extra_data=remote_field.get("extra_data"),
        )
        exact[created.name] = created
        folded[created.name.casefold()] = created
        return created

    def _map_select_value(
        self,
        *,
        remote_field: dict[str, Any],
        remote_value: Any,
        local_field: CustomField,
    ) -> Any:
        remote_options = (
            remote_field.get("extra_data", {}).get("select_options", [])
            if isinstance(remote_field.get("extra_data"), dict)
            else []
        )
        local_options = (
            local_field.extra_data.get("select_options", [])
            if isinstance(local_field.extra_data, dict)
            else []
        )

        remote_label = next(
            (
                option.get("label")
                for option in remote_options
                if option.get("id") == remote_value
            ),
            None,
        )
        if remote_label is None:
            return None

        local_match = next(
            (
                option.get("id")
                for option in local_options
                if option.get("label") == remote_label
            ),
            None,
        )
        return local_match

    def _map_custom_fields(
        self,
        *,
        remote_doc: dict[str, Any],
        remote_custom_fields: dict[int, dict[str, Any]],
        custom_field_exact: dict[str, CustomField],
        custom_field_folded: dict[str, CustomField],
    ) -> tuple[dict[int, Any], list[str]]:
        mapped: dict[int, Any] = {}
        warnings: list[str] = []

        for field_instance in remote_doc.get("custom_fields", []):
            if not isinstance(field_instance, dict):
                continue

            remote_field_id = field_instance.get("field")
            remote_field = (
                remote_custom_fields.get(int(remote_field_id))
                if remote_field_id is not None
                else None
            )
            if remote_field is None:
                continue

            local_field = self._resolve_custom_field(
                remote_field=remote_field,
                exact=custom_field_exact,
                folded=custom_field_folded,
            )
            if local_field is None:
                warnings.append(
                    f'Custom field "{remote_field.get("name", remote_field_id)}" could not be mapped.',
                )
                continue

            value = field_instance.get("value")
            if local_field.data_type == CustomField.FieldDataType.DOCUMENTLINK:
                warnings.append(
                    f'Custom field "{local_field.name}" was skipped because document links cannot be remapped safely.',
                )
                continue

            if local_field.data_type == CustomField.FieldDataType.SELECT:
                value = self._map_select_value(
                    remote_field=remote_field,
                    remote_value=value,
                    local_field=local_field,
                )
                if value is None:
                    warnings.append(
                        f'Select value for custom field "{local_field.name}" could not be mapped.',
                    )
                    continue

            mapped[local_field.id] = value

        return mapped, warnings

    def _format_note(self, note_data: dict[str, Any]) -> str:
        note_text = str(note_data.get("note", "")).strip()
        if not note_text:
            return ""

        user_data = note_data.get("user")
        username = (
            user_data.get("username")
            if isinstance(user_data, dict)
            and isinstance(user_data.get("username"), str)
            else None
        )
        created = note_data.get("created")
        meta_parts = [part for part in [username, created] if part]
        if not meta_parts:
            return note_text

        return f"Imported note ({', '.join(meta_parts)}):\n{note_text}"

    def inspect(self) -> dict[str, Any]:
        with self._client() as client:
            remote_title = client.get_remote_title()
            correspondents = client.get_all_results("correspondents/")
            tags = client.get_all_results("tags/")
            document_types = client.get_all_results("document_types/")
            storage_paths = client.get_all_results("storage_paths/")
            custom_fields = client.get_all_results("custom_fields/")
            documents_page = client.get_documents_page(query="", page=1, page_size=1)

        def summarize(items: list[dict[str, Any]], local_model) -> dict[str, Any]:
            exact, folded = self._build_local_index(local_model)
            missing = []
            matched = 0

            for item in items:
                name = item.get("name")
                if not isinstance(name, str):
                    continue
                if self._find_by_name(exact, folded, name) is not None:
                    matched += 1
                else:
                    missing.append({"id": item.get("id"), "name": name})

            return {
                "total": len(items),
                "matched": matched,
                "missing": missing,
            }

        custom_field_exact, custom_field_folded = self._build_local_index(CustomField)
        matched_custom_fields = 0
        missing_custom_fields = []
        for custom_field in custom_fields:
            name = custom_field.get("name")
            data_type = custom_field.get("data_type")
            if not isinstance(name, str) or not isinstance(data_type, str):
                continue

            local_field = self._find_by_name(
                custom_field_exact,
                custom_field_folded,
                name,
            )
            if local_field is not None and local_field.data_type == data_type:
                matched_custom_fields += 1
            else:
                missing_custom_fields.append(
                    {
                        "id": custom_field.get("id"),
                        "name": name,
                        "data_type": data_type,
                    },
                )

        return {
            "remote": {
                "base_url": self.base_url,
                "app_title": remote_title,
                "document_count": int(documents_page.get("count", 0)),
                "correspondent_count": len(correspondents),
                "tag_count": len(tags),
                "document_type_count": len(document_types),
                "storage_path_count": len(storage_paths),
                "custom_field_count": len(custom_fields),
            },
            "mappings": {
                "correspondents": summarize(correspondents, Correspondent),
                "tags": summarize(tags, Tag),
                "document_types": summarize(document_types, DocumentType),
                "storage_paths": summarize(storage_paths, StoragePath),
                "custom_fields": {
                    "total": len(custom_fields),
                    "matched": matched_custom_fields,
                    "missing": missing_custom_fields,
                },
            },
        }

    def browse_documents(
        self,
        *,
        query: str,
        page: int,
        page_size: int,
    ) -> dict[str, Any]:
        with self._client() as client:
            payload = client.get_documents_page(
                query=query,
                page=page,
                page_size=page_size,
            )
            correspondents = self._fetch_entity_map(
                client=client,
                path="correspondents",
                ids=self._collect_reference_ids(
                    payload,
                    field_name="correspondent",
                    nested_field_name="name",
                ),
            )
            tags = self._fetch_entity_map(
                client=client,
                path="tags",
                ids=self._collect_reference_ids(payload, field_name="tags"),
            )
            document_types = self._fetch_entity_map(
                client=client,
                path="document_types",
                ids=self._collect_reference_ids(
                    payload,
                    field_name="document_type",
                    nested_field_name="name",
                ),
            )
            storage_paths = self._fetch_entity_map(
                client=client,
                path="storage_paths",
                ids=self._collect_reference_ids(
                    payload,
                    field_name="storage_path",
                    nested_field_name="name",
                ),
            )
            custom_fields = self._fetch_entity_map(
                client=client,
                path="custom_fields",
                ids={
                    field_id
                    for result in payload.get("results", [])
                    if isinstance(result, dict)
                    for field_instance in (
                        result.get("custom_fields", [])
                        if isinstance(result.get("custom_fields"), list)
                        else []
                    )
                    if isinstance(field_instance, dict)
                    for field_id in [coerce_remote_id(field_instance.get("field"))]
                    if field_id is not None
                },
            )

        results = []
        for result in payload.get("results", []):
            if not isinstance(result, dict):
                continue

            correspondent = self._extract_named_reference(
                result.get("correspondent"),
                correspondents,
            )
            document_type = self._extract_named_reference(
                result.get("document_type"),
                document_types,
            )
            storage_path = self._extract_named_reference(
                result.get("storage_path"),
                storage_paths,
            )

            mapped_custom_fields = []
            custom_field_instances = result.get("custom_fields", [])
            if not isinstance(custom_field_instances, list):
                custom_field_instances = []

            for field_instance in custom_field_instances:
                if not isinstance(field_instance, dict):
                    continue
                field_id = coerce_remote_id(field_instance.get("field"))
                field = custom_fields.get(field_id) if field_id is not None else None
                if field is None:
                    continue
                mapped_custom_fields.append(
                    {
                        "field_id": field.get("id"),
                        "field_name": field.get("name"),
                        "data_type": field.get("data_type"),
                        "value": field_instance.get("value"),
                    },
                )

            results.append(
                {
                    "id": result.get("id"),
                    "document_url": (
                        self._build_remote_document_url(int(result["id"]))
                        if result.get("id") is not None
                        else None
                    ),
                    "title": result.get("title"),
                    "created": result.get("created"),
                    "original_file_name": result.get("original_file_name"),
                    "archive_serial_number": result.get("archive_serial_number"),
                    "correspondent": correspondent,
                    "document_type": document_type,
                    "storage_path": storage_path,
                    "tags": self._extract_named_tags(result.get("tags"), tags),
                    "custom_fields": mapped_custom_fields,
                },
            )

        return {
            "count": payload.get("count", 0),
            "next": payload.get("next"),
            "previous": payload.get("previous"),
            "all": payload.get("all", []),
            "results": results,
        }

    def _import_notes(
        self,
        *,
        client: RemotePaperlessClient,
        remote_document_id: int,
        local_document_id: int,
    ) -> int:
        remote_notes = client.get_notes(remote_document_id)
        if not remote_notes:
            return 0

        document = Document.objects.get(pk=local_document_id)
        imported_count = 0
        for remote_note in reversed(remote_notes):
            formatted_note = self._format_note(remote_note)
            if not formatted_note:
                continue
            Note.objects.create(
                document=document,
                note=formatted_note,
                user=self.owner,
            )
            imported_count += 1

        if imported_count:
            document.modified = timezone.now()
            document.save(update_fields=["modified"])
            index.add_or_update_document(document)

        return imported_count

    def _import_single_document(
        self,
        *,
        client: RemotePaperlessClient,
        remote_document_id: int,
        remote_reference_data: dict[str, dict[int, dict[str, Any]]],
        local_reference_data: dict[str, tuple[dict[str, Any], dict[str, Any]]],
    ) -> tuple[list[str], int]:
        remote_doc = client.get_document_detail(remote_document_id)
        remote_filename, remote_content = client.download_document_original(
            remote_document_id,
        )

        correspondent = None
        remote_correspondent_id = coerce_remote_id(remote_doc.get("correspondent"))
        if remote_correspondent_id is not None:
            remote_correspondent = remote_reference_data["correspondents"].get(
                remote_correspondent_id,
            )
            correspondent = self._resolve_named_object(
                model=Correspondent,
                name=(
                    remote_correspondent.get("name")
                    if remote_correspondent
                    else get_remote_name(remote_doc.get("correspondent"))
                ),
                exact=local_reference_data["correspondents"][0],
                folded=local_reference_data["correspondents"][1],
            )

        document_type = None
        remote_document_type_id = coerce_remote_id(remote_doc.get("document_type"))
        if remote_document_type_id is not None:
            remote_document_type = remote_reference_data["document_types"].get(
                remote_document_type_id,
            )
            document_type = self._resolve_named_object(
                model=DocumentType,
                name=(
                    remote_document_type.get("name")
                    if remote_document_type
                    else get_remote_name(remote_doc.get("document_type"))
                ),
                exact=local_reference_data["document_types"][0],
                folded=local_reference_data["document_types"][1],
            )

        storage_path = None
        remote_storage_path_id = coerce_remote_id(remote_doc.get("storage_path"))
        if remote_storage_path_id is not None:
            remote_storage_path = remote_reference_data["storage_paths"].get(
                remote_storage_path_id,
            )
            storage_path = self._resolve_named_object(
                model=StoragePath,
                name=(
                    remote_storage_path.get("name")
                    if remote_storage_path
                    else get_remote_name(remote_doc.get("storage_path"))
                ),
                exact=local_reference_data["storage_paths"][0],
                folded=local_reference_data["storage_paths"][1],
            )

        tag_ids: list[int] = []
        warnings: list[str] = []
        remote_tags = remote_doc.get("tags", [])
        if not isinstance(remote_tags, list):
            remote_tags = []

        for remote_tag_value in remote_tags:
            remote_tag_id = coerce_remote_id(remote_tag_value)
            remote_tag = (
                remote_reference_data["tags"].get(remote_tag_id)
                if remote_tag_id is not None
                else None
            )
            local_tag = self._resolve_named_object(
                model=Tag,
                name=(
                    remote_tag.get("name")
                    if remote_tag
                    else get_remote_name(remote_tag_value)
                ),
                exact=local_reference_data["tags"][0],
                folded=local_reference_data["tags"][1],
            )
            if local_tag is None and remote_tag:
                warnings.append(f'Tag "{remote_tag.get("name")}" could not be mapped.')
                continue
            if local_tag is not None:
                tag_ids.append(local_tag.id)

        custom_fields, custom_field_warnings = self._map_custom_fields(
            remote_doc=remote_doc,
            remote_custom_fields=remote_reference_data["custom_fields"],
            custom_field_exact=local_reference_data["custom_fields"][0],
            custom_field_folded=local_reference_data["custom_fields"][1],
        )
        warnings.extend(custom_field_warnings)

        sanitized_filename = pathvalidate.sanitize_filename(
            remote_filename
            or remote_doc.get("original_file_name")
            or f"document-{remote_document_id}",
        )
        settings.SCRATCH_DIR.mkdir(parents=True, exist_ok=True)

        temp_dir = Path(
            mkdtemp(
                dir=settings.SCRATCH_DIR,
                prefix="paperless-remote-import-",
            ),
        )
        try:
            file_path = temp_dir / sanitized_filename
            file_path.write_bytes(remote_content)

            input_doc = ConsumableDocument(
                source=DocumentSource.ApiUpload,
                original_file=file_path,
            )
            overrides = DocumentMetadataOverrides(
                filename=sanitized_filename,
                title=remote_doc.get("title"),
                correspondent_id=correspondent.id if correspondent else None,
                document_type_id=document_type.id if document_type else None,
                storage_path_id=storage_path.id if storage_path else None,
                tag_ids=tag_ids or None,
                created=parse_remote_created_date(remote_doc.get("created")),
                asn=remote_doc.get("archive_serial_number"),
                owner_id=self.owner.id if self.owner else None,
                custom_fields=custom_fields or None,
            )
            consume_result = consume_file.apply(args=(input_doc, overrides))
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

        if consume_result.failed():
            raise RemoteImportError(
                str(consume_result.result)
                or f"Local import failed for remote document {remote_document_id}.",
            )

        local_document_id = extract_related_document_id_from_result(
            consume_result.result,
        )
        imported_note_count = 0
        if self.import_notes and local_document_id is not None:
            imported_note_count = self._import_notes(
                client=client,
                remote_document_id=remote_document_id,
                local_document_id=local_document_id,
            )

        return warnings, imported_note_count

    def import_documents(
        self,
        *,
        selected_document_ids: list[int] | None,
        query: str,
        import_all: bool,
        progress_callback: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        with self._client() as client:
            if import_all:
                total, document_ids = client.iter_matching_document_ids(query=query)
            else:
                document_ids = [
                    int(document_id) for document_id in selected_document_ids or []
                ]
                total = len(document_ids)

            if not document_ids:
                raise RemoteImportError("No remote documents selected for import.")

            remote_reference_data = {
                "correspondents": {
                    int(item["id"]): item
                    for item in client.get_all_results("correspondents/")
                    if isinstance(item, dict) and item.get("id") is not None
                },
                "tags": {
                    int(item["id"]): item
                    for item in client.get_all_results("tags/")
                    if isinstance(item, dict) and item.get("id") is not None
                },
                "document_types": {
                    int(item["id"]): item
                    for item in client.get_all_results("document_types/")
                    if isinstance(item, dict) and item.get("id") is not None
                },
                "storage_paths": {
                    int(item["id"]): item
                    for item in client.get_all_results("storage_paths/")
                    if isinstance(item, dict) and item.get("id") is not None
                },
                "custom_fields": {
                    int(item["id"]): item
                    for item in client.get_all_results("custom_fields/")
                    if isinstance(item, dict) and item.get("id") is not None
                },
            }

            local_reference_data = {
                "correspondents": self._build_local_index(Correspondent),
                "tags": self._build_local_index(Tag),
                "document_types": self._build_local_index(DocumentType),
                "storage_paths": self._build_local_index(StoragePath),
                "custom_fields": self._build_local_index(CustomField),
            }

            success_count = 0
            failure_count = 0
            imported_notes = 0
            warnings: list[str] = []
            failures: list[str] = []

            for index_number, remote_document_id in enumerate(document_ids, start=1):
                if progress_callback is not None:
                    progress_callback(
                        f"Importing document {index_number}/{total} (remote id {remote_document_id})...",
                    )

                try:
                    doc_warnings, doc_notes = self._import_single_document(
                        client=client,
                        remote_document_id=remote_document_id,
                        remote_reference_data=remote_reference_data,
                        local_reference_data=local_reference_data,
                    )
                    imported_notes += doc_notes
                    warnings.extend(doc_warnings)
                    success_count += 1
                except Exception as exc:
                    logger.warning(
                        "Remote import failed for document %s: %s",
                        remote_document_id,
                        exc,
                    )
                    failure_count += 1
                    failures.append(f"Document {remote_document_id}: {exc}")

            return {
                "total": total,
                "success_count": success_count,
                "failure_count": failure_count,
                "imported_notes": imported_notes,
                "warnings": warnings,
                "failures": failures,
            }

    @staticmethod
    def format_result(summary: dict[str, Any]) -> str:
        lines = [
            f"Imported {summary['success_count']} of {summary['total']} remote document(s).",
        ]
        if summary["failure_count"]:
            lines.append(f"Failures: {summary['failure_count']}.")
        if summary["imported_notes"]:
            lines.append(f"Imported notes: {summary['imported_notes']}.")
        if summary["warnings"]:
            lines.append("Warnings:")
            lines.extend(f"- {warning}" for warning in summary["warnings"][:10])
            if len(summary["warnings"]) > 10:
                lines.append(f"- {len(summary['warnings']) - 10} more warning(s).")
        if summary["failures"]:
            lines.append("Failed items:")
            lines.extend(f"- {failure}" for failure in summary["failures"][:10])
            if len(summary["failures"]) > 10:
                lines.append(f"- {len(summary['failures']) - 10} more failure(s).")
        return "\n".join(lines)


def run_remote_import_task(
    *,
    task_id: str,
    base_url: str,
    api_token: str,
    selected_document_ids: list[int] | None,
    query: str,
    import_all: bool,
    create_missing_items: bool,
    import_notes: bool,
    owner_id: int | None,
) -> str:
    task = PaperlessTask.objects.filter(task_id=task_id).first()
    if task is not None:
        task.status = states.STARTED
        task.date_started = timezone.now()
        task.result = "Preparing remote import..."
        task.save(update_fields=["status", "date_started", "result"])

    service = RemoteImportService(
        base_url=base_url,
        api_token=api_token,
        create_missing_items=create_missing_items,
        import_notes=import_notes,
        owner_id=owner_id,
    )

    def progress_callback(message: str) -> None:
        if task is None:
            return
        task.result = message
        task.save(update_fields=["result"])

    try:
        summary = service.import_documents(
            selected_document_ids=selected_document_ids,
            query=query,
            import_all=import_all,
            progress_callback=progress_callback,
        )
        result = service.format_result(summary)
        status = states.SUCCESS if summary["failure_count"] == 0 else states.FAILURE
    except Exception as exc:
        logger.exception("Remote import task failed")
        result = str(exc)
        status = states.FAILURE

    if task is not None:
        task.status = status
        task.result = result
        task.date_done = timezone.now()
        task.save(update_fields=["status", "result", "date_done"])

    return result
