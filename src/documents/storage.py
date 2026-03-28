from __future__ import annotations

import hashlib
import shutil
import uuid
from contextlib import contextmanager
from datetime import datetime
from datetime import timezone
from functools import lru_cache
from importlib import import_module
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING
from typing import cast

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.files import File
from django.core.files.base import ContentFile
from django.db import OperationalError
from django.db import ProgrammingError

from paperless.config import DocumentBackupConfig
from paperless.config import DocumentStorageConfig
from paperless.models import ApplicationConfiguration
from paperless.models import DocumentStorageTypeChoices
from paperless.models import S3StorageConfiguration

if TYPE_CHECKING:
    from collections.abc import Iterator

    from django.core.files.storage import Storage

DOCUMENT_STORAGE_LOCATIONS = {
    "originals": lambda: settings.ORIGINALS_DIR,
    "archive": lambda: settings.ARCHIVE_DIR,
    "thumbnails": lambda: settings.THUMBNAIL_DIR,
}


def _local_document_path(kind: str, name: str) -> Path:
    return Path(DOCUMENT_STORAGE_LOCATIONS[kind]()) / str(name)


def _local_document_exists(kind: str, name: str | None) -> bool:
    return bool(name) and _local_document_path(kind, str(name)).exists()


def _build_s3_location(prefix: str, kind: str) -> str:
    return "/".join(part for part in (prefix.strip("/"), kind) if part)


def _build_s3_storage(
    *,
    kind: str,
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
    s3_querystring_auth: bool,
    s3_use_ssl: bool,
) -> Storage:
    if not s3_bucket:
        raise ImproperlyConfigured(
            "S3 storage requires a bucket name in configuration.",
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
        return _build_s3_storage(
            kind=kind,
            prefix=prefix,
            s3_bucket=s3_bucket,
            s3_endpoint_url=s3_endpoint_url,
            s3_access_key_id=s3_access_key_id,
            s3_secret_access_key=s3_secret_access_key,
            s3_region_name=s3_region_name,
            s3_default_acl=s3_default_acl,
            s3_custom_domain=s3_custom_domain,
            s3_url_protocol=s3_url_protocol,
            s3_addressing_style=s3_addressing_style,
            s3_querystring_auth=s3_querystring_auth,
            s3_use_ssl=s3_use_ssl,
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


@lru_cache(maxsize=32)
def _build_document_backup_storage(
    kind: str,
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
    return _build_s3_storage(
        kind=kind,
        prefix=prefix,
        s3_bucket=s3_bucket,
        s3_endpoint_url=s3_endpoint_url,
        s3_access_key_id=s3_access_key_id,
        s3_secret_access_key=s3_secret_access_key,
        s3_region_name=s3_region_name,
        s3_default_acl=s3_default_acl,
        s3_custom_domain=s3_custom_domain,
        s3_url_protocol=s3_url_protocol,
        s3_addressing_style=s3_addressing_style,
        s3_querystring_auth=s3_querystring_auth,
        s3_use_ssl=s3_use_ssl,
    )


def get_document_backup_storage(
    kind: str,
    overrides: dict[str, object] | None = None,
) -> Storage | None:
    config = DocumentBackupConfig(overrides=overrides)
    if not config.is_configured:
        return None
    return _build_document_backup_storage(
        kind,
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


def test_s3_connection(
    *,
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
    s3_querystring_auth: bool,
    s3_use_ssl: bool,
) -> None:
    for kind in DOCUMENT_STORAGE_LOCATIONS:
        storage = _build_s3_storage(
            kind=kind,
            prefix=prefix,
            s3_bucket=s3_bucket,
            s3_endpoint_url=s3_endpoint_url,
            s3_access_key_id=s3_access_key_id,
            s3_secret_access_key=s3_secret_access_key,
            s3_region_name=s3_region_name,
            s3_default_acl=s3_default_acl,
            s3_custom_domain=s3_custom_domain,
            s3_url_protocol=s3_url_protocol,
            s3_addressing_style=s3_addressing_style,
            s3_querystring_auth=s3_querystring_auth,
            s3_use_ssl=s3_use_ssl,
        )
        probe_name = f"_paperless_storage_test/{uuid.uuid4().hex}.txt"
        if kind == "originals":
            storage.save(probe_name, ContentFile(b"paperless-s3-test"))
            storage.delete(probe_name)
        else:
            storage.exists(probe_name)


def get_s3_configuration_storage(
    storage_config: S3StorageConfiguration,
    *,
    location: str,
) -> Storage:
    return cast(
        "Storage",
        _build_s3_storage(
            kind=location.strip("/"),
            prefix=storage_config.prefix or "",
            s3_bucket=storage_config.bucket,
            s3_endpoint_url=storage_config.endpoint_url,
            s3_access_key_id=storage_config.access_key_id,
            s3_secret_access_key=storage_config.secret_access_key,
            s3_region_name=storage_config.region_name,
            s3_default_acl=storage_config.default_acl,
            s3_custom_domain=storage_config.custom_domain,
            s3_url_protocol=storage_config.url_protocol or "https:",
            s3_addressing_style=storage_config.addressing_style,
            s3_querystring_auth=storage_config.querystring_auth
            if storage_config.querystring_auth is not None
            else False,
            s3_use_ssl=storage_config.use_ssl
            if storage_config.use_ssl is not None
            else True,
        ),
    )


def test_document_backup_storage_connection(
    overrides: dict[str, object] | None = None,
) -> None:
    config = DocumentBackupConfig(overrides=overrides)
    if not config.is_configured:
        raise ImproperlyConfigured(
            "The document backup storage requires PAPERLESS_DOCUMENTS_BACKUP_S3_BUCKET or an application configuration value.",
        )

    test_s3_connection(
        prefix=config.prefix,
        s3_bucket=config.s3_bucket,
        s3_endpoint_url=config.s3_endpoint_url,
        s3_access_key_id=config.s3_access_key_id,
        s3_secret_access_key=config.s3_secret_access_key,
        s3_region_name=config.s3_region_name,
        s3_default_acl=config.s3_default_acl,
        s3_custom_domain=config.s3_custom_domain,
        s3_url_protocol=config.s3_url_protocol,
        s3_addressing_style=config.s3_addressing_style,
        s3_querystring_auth=config.s3_querystring_auth,
        s3_use_ssl=config.s3_use_ssl,
    )


def _supports_local_path(storage: Storage) -> bool:
    try:
        storage.path("_paperless_probe_")
    except (AttributeError, NotImplementedError):
        return False
    return True


def _build_storage_from_config(kind: str, config: DocumentStorageConfig) -> Storage:
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


def _get_application_config() -> ApplicationConfiguration:
    try:
        app_config = ApplicationConfiguration.objects.all().first()
    except (OperationalError, ProgrammingError):
        return ApplicationConfiguration()
    return app_config or ApplicationConfiguration()


def _iter_s3_read_storages(kind: str) -> Iterator[Storage]:
    app_config = _get_application_config()
    legacy_prefixes = [
        prefix.strip("/")
        for prefix in (
            app_config.documents_storage_prefix,
            settings.DOCUMENTS_STORAGE_PREFIX,
        )
        if prefix
    ]
    base_overrides: list[dict[str, object]] = [
        {"documents_storage_type": DocumentStorageTypeChoices.S3},
    ]

    if app_config.documents_s3_storage_id:
        base_overrides.insert(
            0,
            {
                "documents_storage_type": DocumentStorageTypeChoices.S3,
                "documents_s3_storage": app_config.documents_s3_storage,
            },
        )

    try:
        configured_storages = list(S3StorageConfiguration.objects.order_by("pk"))
    except (OperationalError, ProgrammingError):
        configured_storages = []

    for storage in configured_storages:
        base_overrides.append(
            {
                "documents_storage_type": DocumentStorageTypeChoices.S3,
                "documents_s3_storage": storage,
            },
        )

    seen_signatures: set[tuple[object, ...]] = set()
    for overrides in base_overrides:
        base_config = DocumentStorageConfig(app_config=app_config, overrides=overrides)
        candidate_prefixes = [base_config.prefix, *legacy_prefixes]
        for prefix in candidate_prefixes:
            candidate_config = DocumentStorageConfig(
                app_config=app_config,
                overrides={
                    **overrides,
                    "documents_storage_prefix": prefix,
                },
            )
            if not candidate_config.s3_bucket:
                continue
            signature = (
                candidate_config.prefix,
                candidate_config.s3_bucket,
                candidate_config.s3_endpoint_url,
                candidate_config.s3_access_key_id,
                candidate_config.s3_secret_access_key,
                candidate_config.s3_region_name,
                candidate_config.s3_default_acl,
                candidate_config.s3_custom_domain,
                candidate_config.s3_url_protocol,
                candidate_config.s3_addressing_style,
                candidate_config.s3_querystring_auth,
                candidate_config.s3_use_ssl,
            )
            if signature in seen_signatures:
                continue
            seen_signatures.add(signature)
            yield _build_storage_from_config(kind, candidate_config)


def _find_document_storage(kind: str, name: str | None) -> Storage | None:
    if not name:
        return None

    current_storage = get_document_storage(kind)
    try:
        if current_storage.exists(str(name)):
            return current_storage
    except Exception:
        pass

    for storage in _iter_s3_read_storages(kind):
        try:
            if storage.exists(str(name)):
                return storage
        except Exception:
            continue
    return None


def document_storage_is_local(kind: str) -> bool:
    return _supports_local_path(get_document_storage(kind))


def document_storage_path(kind: str, name: str) -> Path:
    local_path = _local_document_path(kind, name)
    if local_path.exists():
        return local_path.resolve()

    storage = _find_document_storage(kind, name) or get_document_storage(kind)
    try:
        return Path(storage.path(name)).resolve()
    except (AttributeError, NotImplementedError) as exc:
        raise NotImplementedError(
            f'The "{kind}" document storage backend does not expose local paths.',
        ) from exc


def document_exists(kind: str, name: str | None) -> bool:
    return (
        _local_document_exists(kind, name)
        or _find_document_storage(kind, name) is not None
    )


def document_open(kind: str, name: str, mode: str = "rb"):
    local_path = _local_document_path(kind, name)
    if local_path.exists():
        return local_path.open(mode)
    storage = _find_document_storage(kind, name) or get_document_storage(kind)
    return storage.open(name, mode)


def document_delete(kind: str, name: str | None) -> None:
    if _local_document_exists(kind, name):
        _local_document_path(kind, str(name)).unlink(missing_ok=True)
    if name and get_document_storage(kind).exists(str(name)):
        get_document_storage(kind).delete(str(name))
    backup_storage = get_document_backup_storage(kind)
    if backup_storage is not None and name and backup_storage.exists(str(name)):
        backup_storage.delete(str(name))


def document_size(kind: str, name: str | None) -> int | None:
    if not name or not document_exists(kind, name):
        return None
    local_path = _local_document_path(kind, str(name))
    if local_path.exists():
        return local_path.stat().st_size
    storage = _find_document_storage(kind, name) or get_document_storage(kind)
    return storage.size(str(name))


def document_modified_time(kind: str, name: str | None):
    if not name or not document_exists(kind, name):
        return None
    local_path = _local_document_path(kind, str(name))
    if local_path.exists():
        return datetime.fromtimestamp(local_path.stat().st_mtime, tz=timezone.utc)
    storage = _find_document_storage(kind, name) or get_document_storage(kind)
    return storage.get_modified_time(str(name))


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
    else:
        with source_path.open("rb") as handle:
            document_write_file(kind, name, handle)

    backup_document_write_from_path(kind, name, source_path)


def backup_document_write_from_path(kind: str, name: str, source_path: Path) -> None:
    storage = get_document_backup_storage(kind)
    if storage is None:
        return

    if storage.exists(name):
        storage.delete(name)

    with source_path.open("rb") as handle:
        storage.save(name, File(handle, name=Path(name).name))


def document_move(kind: str, old_name: str, new_name: str) -> None:
    storage = get_document_storage(kind)
    if old_name == new_name:
        return

    local_source_path = _local_document_path(kind, old_name)
    local_target_path = _local_document_path(kind, new_name)
    if local_source_path.exists():
        local_target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(local_source_path, local_target_path)
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

    backup_storage = get_document_backup_storage(kind)
    if backup_storage is None:
        return

    with document_open(kind, new_name, "rb") as handle:
        if backup_storage.exists(new_name):
            backup_storage.delete(new_name)
        backup_storage.save(new_name, File(handle, name=Path(new_name).name))
    if backup_storage.exists(old_name):
        backup_storage.delete(old_name)


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
    local_path = _local_document_path(kind, name)
    if local_path.exists():
        if writeback:
            local_path.parent.mkdir(parents=True, exist_ok=True)
        yield local_path
        return

    storage = _find_document_storage(kind, name) or get_document_storage(kind)

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
