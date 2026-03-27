from __future__ import annotations

import hashlib
import shutil
import uuid
from contextlib import contextmanager
from functools import lru_cache
from importlib import import_module
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.files import File
from django.core.files.base import ContentFile

from paperless.config import DocumentStorageConfig
from paperless.models import DocumentStorageTypeChoices

if TYPE_CHECKING:
    from collections.abc import Iterator

    from django.core.files.storage import Storage

DOCUMENT_STORAGE_LOCATIONS = {
    "originals": lambda: settings.ORIGINALS_DIR,
    "archive": lambda: settings.ARCHIVE_DIR,
    "thumbnails": lambda: settings.THUMBNAIL_DIR,
}


def _build_s3_location(prefix: str, kind: str) -> str:
    return "/".join(part for part in (prefix.strip("/"), kind) if part)


@lru_cache(maxsize=32)
def _build_document_storage(
    kind: str,
    storage_type: str,
    prefix: str,
    s3_bucket: str | None,
    s3_endpoint_url: str | None,
    s3_access_key_id: str | None,
    s3_secret_access_key: str | None,
    s3_region_name: str | None,
    s3_default_acl: str | None,
    s3_custom_domain: str | None,
    s3_url_protocol: str,
    s3_addressing_style: str | None,
    *,
    s3_querystring_auth: bool,
    s3_use_ssl: bool,
) -> Storage:
    if storage_type == DocumentStorageTypeChoices.S3:
        if not s3_bucket:
            raise ImproperlyConfigured(
                "S3 document storage requires PAPERLESS_DOCUMENTS_S3_BUCKET or an application configuration value.",
            )

        backend = getattr(
            import_module("storages.backends.s3"),
            "S3Storage",
        )
        options = {
            "bucket_name": s3_bucket,
            "location": _build_s3_location(prefix, kind),
            "default_acl": s3_default_acl,
            "endpoint_url": s3_endpoint_url,
            "access_key": s3_access_key_id,
            "secret_key": s3_secret_access_key,
            "region_name": s3_region_name,
            "querystring_auth": s3_querystring_auth,
            "use_ssl": s3_use_ssl,
            "file_overwrite": False,
            "custom_domain": s3_custom_domain,
            "url_protocol": s3_url_protocol,
        }
        if s3_addressing_style:
            options["addressing_style"] = s3_addressing_style

        return backend(
            **{key: value for key, value in options.items() if value is not None},
        )

    from django.core.files.storage import FileSystemStorage

    return FileSystemStorage(location=str(DOCUMENT_STORAGE_LOCATIONS[kind]()))


def get_document_storage(
    kind: str,
    overrides: dict[str, object] | None = None,
) -> Storage:
    config = DocumentStorageConfig(overrides=overrides)
    return _build_document_storage(
        kind,
        config.storage_type,
        config.prefix,
        config.s3_bucket,
        config.s3_endpoint_url,
        config.s3_access_key_id,
        config.s3_secret_access_key,
        config.s3_region_name,
        config.s3_default_acl,
        config.s3_custom_domain,
        config.s3_url_protocol,
        config.s3_addressing_style,
        s3_querystring_auth=config.s3_querystring_auth,
        s3_use_ssl=config.s3_use_ssl,
    )


def test_document_storage_connection(
    overrides: dict[str, object] | None = None,
) -> None:
    config = DocumentStorageConfig(overrides=overrides)
    if config.storage_type != DocumentStorageTypeChoices.S3:
        raise ImproperlyConfigured(
            "The document storage backend must be set to s3 to run this test.",
        )

    for kind in DOCUMENT_STORAGE_LOCATIONS:
        storage = get_document_storage(kind, overrides=overrides)
        probe_name = f"_paperless_storage_test/{uuid.uuid4().hex}.txt"
        if kind == "originals":
            storage.save(probe_name, ContentFile(b"paperless-s3-test"))
            storage.delete(probe_name)
        else:
            storage.exists(probe_name)


def _supports_local_path(storage: Storage) -> bool:
    try:
        storage.path("_paperless_probe_")
    except (AttributeError, NotImplementedError):
        return False
    return True


def document_storage_is_local(kind: str) -> bool:
    return _supports_local_path(get_document_storage(kind))


def document_storage_path(kind: str, name: str) -> Path:
    storage = get_document_storage(kind)
    try:
        return Path(storage.path(name)).resolve()
    except (AttributeError, NotImplementedError) as exc:
        raise NotImplementedError(
            f'The "{kind}" document storage backend does not expose local paths.',
        ) from exc


def document_exists(kind: str, name: str | None) -> bool:
    return bool(name) and get_document_storage(kind).exists(str(name))


def document_open(kind: str, name: str, mode: str = "rb"):
    return get_document_storage(kind).open(name, mode)


def document_delete(kind: str, name: str | None) -> None:
    if name and document_exists(kind, name):
        get_document_storage(kind).delete(str(name))


def document_size(kind: str, name: str | None) -> int | None:
    if not name or not document_exists(kind, name):
        return None
    return get_document_storage(kind).size(str(name))


def document_modified_time(kind: str, name: str | None):
    if not name or not document_exists(kind, name):
        return None
    return get_document_storage(kind).get_modified_time(str(name))


def document_read_bytes(kind: str, name: str) -> bytes:
    with document_open(kind, name, "rb") as handle:
        return handle.read()


def document_write_file(kind: str, name: str, fileobj) -> None:
    storage = get_document_storage(kind)
    if storage.exists(name):
        storage.delete(name)
    storage.save(name, File(fileobj, name=Path(name).name))


def document_write_bytes(kind: str, name: str, content: bytes) -> None:
    with TemporaryDirectory(prefix="paperless-storage-write-") as temp_dir:
        temp_path = Path(temp_dir) / Path(name).name
        temp_path.write_bytes(content)
        document_write_from_path(kind, name, temp_path)


def document_write_from_path(kind: str, name: str, source_path: Path) -> None:
    storage = get_document_storage(kind)
    if _supports_local_path(storage):
        target_path = document_storage_path(kind, name)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, target_path)
        return

    with source_path.open("rb") as handle:
        document_write_file(kind, name, handle)


def document_move(kind: str, old_name: str, new_name: str) -> None:
    storage = get_document_storage(kind)
    if old_name == new_name:
        return

    if _supports_local_path(storage):
        source_path = document_storage_path(kind, old_name)
        target_path = document_storage_path(kind, new_name)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(source_path, target_path)
        return

    with storage.open(old_name, "rb") as handle:
        if storage.exists(new_name):
            storage.delete(new_name)
        storage.save(new_name, File(handle, name=Path(new_name).name))
    storage.delete(old_name)


def document_checksum_matches(kind: str, name: str, checksum: str | None) -> bool:
    if checksum is None or not document_exists(kind, name):
        return False

    digest = hashlib.md5(usedforsecurity=False)
    with document_open(kind, name, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest() == checksum


def delete_empty_document_directories(kind: str, name: str | None) -> None:
    if not name or not document_storage_is_local(kind):
        return

    root = document_storage_path(kind, ".")
    directory = document_storage_path(kind, name).parent
    while directory != root:
        try:
            directory.rmdir()
        except OSError:
            return
        directory = directory.parent


@contextmanager
def local_document_path(
    kind: str,
    name: str,
    *,
    writeback: bool = False,
) -> Iterator[Path]:
    storage = get_document_storage(kind)
    if _supports_local_path(storage):
        path = document_storage_path(kind, name)
        if writeback:
            path.parent.mkdir(parents=True, exist_ok=True)
        yield path
        return

    with TemporaryDirectory(prefix=f"paperless-{kind}-") as temp_dir:
        local_path = Path(temp_dir) / Path(name).name
        if storage.exists(name):
            with (
                storage.open(name, "rb") as source_handle,
                local_path.open("wb") as target,
            ):
                shutil.copyfileobj(source_handle, target)

        yield local_path

        if writeback:
            document_write_from_path(kind, name, local_path)
