from collections import OrderedDict
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from allauth.mfa import signals
from allauth.mfa.adapter import get_adapter as get_mfa_adapter
from allauth.mfa.base.internal.flows import delete_and_cleanup
from allauth.mfa.models import Authenticator
from allauth.mfa.recovery_codes.internal.flows import auto_generate_recovery_codes
from allauth.mfa.totp.internal import auth as totp_auth
from allauth.socialaccount.adapter import get_adapter
from allauth.socialaccount.models import SocialAccount
from celery import states
from django.conf import settings
from django.contrib.auth.models import Group
from django.contrib.auth.models import User
from django.contrib.staticfiles.storage import staticfiles_storage
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.db.models import Q
from django.db.models.functions import Lower
from django.http import FileResponse
from django.http import HttpResponseBadRequest
from django.http import HttpResponseForbidden
from django.http import HttpResponseNotFound
from django.utils import timezone
from django.views.generic import View
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from drf_spectacular.utils import extend_schema_view
from rest_framework.authtoken.models import Token
from rest_framework.authtoken.views import ObtainAuthToken
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.fields import BooleanField
from rest_framework.filters import OrderingFilter
from rest_framework.generics import GenericAPIView
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import DjangoModelPermissions
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from documents.index import DelayedQuery
from documents.models import PaperlessTask
from documents.permissions import PaperlessObjectPermissions
from documents.storage import get_s3_configuration_storage
from documents.storage import test_document_backup_storage_connection
from documents.storage import test_document_storage_connection
from documents.storage import test_s3_connection
from documents.tasks import AUTOMATIC_S3_TRANSFER_LOCATION
from documents.tasks import MANUAL_S3_TRANSFER_LOCATION
from documents.tasks import build_manual_s3_export_filename
from documents.tasks import create_demo_crafts_data
from documents.tasks import export_documents_to_s3_storage
from documents.tasks import import_documents_from_s3_storage
from documents.tasks import llmindex_index
from documents.tasks import reset_runtime_data
from paperless.filters import GroupFilterSet
from paperless.filters import UserFilterSet
from paperless.models import ApplicationConfiguration
from paperless.models import S3StorageConfiguration
from paperless.remote_import import RemoteImportService
from paperless.serialisers import ApplicationConfigurationSerializer
from paperless.serialisers import GroupSerializer
from paperless.serialisers import PaperlessAuthTokenSerializer
from paperless.serialisers import ProfileSerializer
from paperless.serialisers import RemoteImportBrowseSerializer
from paperless.serialisers import RemoteImportConnectionSerializer
from paperless.serialisers import RemoteImportStartSerializer
from paperless.serialisers import S3StorageConfigurationSerializer
from paperless.serialisers import UserSerializer
from paperless.tasks import import_remote_documents
from paperless_ai.indexing import vector_store_file_exists


class PaperlessObtainAuthTokenView(ObtainAuthToken):
    serializer_class = PaperlessAuthTokenSerializer


class StandardPagination(PageNumberPagination):
    page_size = 25
    page_size_query_param = "page_size"
    max_page_size = 100000

    def get_paginated_response(self, data):
        return Response(
            OrderedDict(
                [
                    ("count", self.page.paginator.count),
                    ("next", self.get_next_link()),
                    ("previous", self.get_previous_link()),
                    ("all", self.get_all_result_ids()),
                    ("results", data),
                ],
            ),
        )

    def get_all_result_ids(self):
        query = self.page.paginator.object_list
        if isinstance(query, DelayedQuery):
            try:
                ids = [
                    query.searcher.ixreader.stored_fields(
                        doc_num,
                    )["id"]
                    for doc_num in query.saved_results.get(0).results.docs()
                ]
            except Exception:
                pass
        else:
            ids = self.page.paginator.object_list.values_list("pk", flat=True)
        return ids

    def get_paginated_response_schema(self, schema):
        response_schema = super().get_paginated_response_schema(schema)
        response_schema["properties"]["all"] = {
            "type": "array",
            "example": "[1, 2, 3]",
            "items": {"type": "integer"},
        }
        return response_schema


class FaviconView(View):
    def get(self, request, *args, **kwargs):
        try:
            path = Path(staticfiles_storage.path("paperless/img/favicon.ico"))
            return FileResponse(path.open("rb"), content_type="image/x-icon")
        except FileNotFoundError:
            return HttpResponseNotFound("favicon.ico not found")


class UserViewSet(ModelViewSet):
    _BOOL_NOT_PROVIDED = object()
    model = User

    queryset = User.objects.exclude(
        username__in=["consumer", "AnonymousUser"],
    ).order_by(Lower("username"))

    serializer_class = UserSerializer
    pagination_class = StandardPagination
    permission_classes = (IsAuthenticated, PaperlessObjectPermissions)
    filter_backends = (DjangoFilterBackend, OrderingFilter)
    filterset_class = UserFilterSet
    ordering_fields = ("username",)

    @staticmethod
    def _parse_requested_bool(data, key: str):
        if key not in data:
            return UserViewSet._BOOL_NOT_PROVIDED
        try:
            return BooleanField().to_internal_value(data.get(key))
        except ValidationError:
            # Let serializer validation report invalid values as 400 responses
            return UserViewSet._BOOL_NOT_PROVIDED

    def create(self, request, *args, **kwargs):
        requested_is_superuser = self._parse_requested_bool(
            request.data,
            "is_superuser",
        )
        requested_is_staff = self._parse_requested_bool(request.data, "is_staff")

        if not request.user.is_superuser:
            if requested_is_superuser is True:
                return HttpResponseForbidden(
                    "Superuser status can only be granted by a superuser",
                )
            if requested_is_staff is True:
                return HttpResponseForbidden(
                    "Staff status can only be granted by a superuser",
                )

        return super().create(request, *args, **kwargs)

    def update(self, request, *args, **kwargs):
        user_to_update: User = self.get_object()

        if not request.user.is_superuser and user_to_update.is_superuser:
            return HttpResponseForbidden(
                "Superusers can only be modified by other superusers",
            )

        requested_is_superuser = self._parse_requested_bool(
            request.data,
            "is_superuser",
        )
        requested_is_staff = self._parse_requested_bool(request.data, "is_staff")

        if (
            not request.user.is_superuser
            and requested_is_superuser is not self._BOOL_NOT_PROVIDED
            and requested_is_superuser != user_to_update.is_superuser
        ):
            return HttpResponseForbidden(
                "Superuser status can only be changed by a superuser",
            )
        if (
            not request.user.is_superuser
            and requested_is_staff is not self._BOOL_NOT_PROVIDED
            and requested_is_staff != user_to_update.is_staff
        ):
            return HttpResponseForbidden(
                "Staff status can only be changed by a superuser",
            )
        return super().update(request, *args, **kwargs)

    @extend_schema(
        request=None,
        responses={
            200: OpenApiTypes.BOOL,
            404: OpenApiTypes.STR,
        },
    )
    @action(detail=True, methods=["post"])
    def deactivate_totp(self, request, pk=None):
        request_user = request.user
        user = User.objects.get(pk=pk)
        if not request_user.is_superuser and request_user != user:
            return HttpResponseForbidden(
                "You do not have permission to deactivate TOTP for this user",
            )
        authenticator = Authenticator.objects.filter(
            user=user,
            type=Authenticator.Type.TOTP,
        ).first()
        if authenticator is not None:
            delete_and_cleanup(request, authenticator)
            return Response(data=True)
        else:
            return HttpResponseNotFound("TOTP not found")


class GroupViewSet(ModelViewSet):
    model = Group

    queryset = Group.objects.order_by(Lower("name"))

    serializer_class = GroupSerializer
    pagination_class = StandardPagination
    permission_classes = (IsAuthenticated, PaperlessObjectPermissions)
    filter_backends = (DjangoFilterBackend, OrderingFilter)
    filterset_class = GroupFilterSet
    ordering_fields = ("name",)


class S3StorageConfigurationViewSet(ModelViewSet):
    model = S3StorageConfiguration

    queryset = S3StorageConfiguration.objects.order_by(Lower("name"))

    serializer_class = S3StorageConfigurationSerializer
    permission_classes = (IsAuthenticated, DjangoModelPermissions)
    pagination_class = None
    ordering_fields = ("name",)

    @action(detail=True, methods=["post"], url_path="test-connection")
    def test_connection(self, request, pk=None):
        storage = self.get_object()
        serializer = self.get_serializer(storage, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)

        payload = dict(serializer.validated_data)
        secret = payload.get("secret_access_key")
        if isinstance(secret, str) and secret and secret.replace("*", "") == "":
            payload["secret_access_key"] = storage.secret_access_key

        try:
            test_s3_connection(
                prefix=payload.get("prefix", storage.prefix),
                s3_bucket=payload.get("bucket", storage.bucket),
                s3_endpoint_url=payload.get("endpoint_url", storage.endpoint_url),
                s3_access_key_id=payload.get("access_key_id", storage.access_key_id),
                s3_secret_access_key=payload.get(
                    "secret_access_key",
                    storage.secret_access_key,
                ),
                s3_region_name=payload.get("region_name", storage.region_name),
                s3_default_acl=payload.get("default_acl", storage.default_acl),
                s3_custom_domain=payload.get("custom_domain", storage.custom_domain),
                s3_url_protocol=payload.get("url_protocol", storage.url_protocol)
                or "https:",
                s3_addressing_style=payload.get(
                    "addressing_style",
                    storage.addressing_style,
                ),
                s3_querystring_auth=payload.get(
                    "querystring_auth",
                    storage.querystring_auth,
                )
                if payload.get("querystring_auth", storage.querystring_auth) is not None
                else False,
                s3_use_ssl=payload.get("use_ssl", storage.use_ssl)
                if payload.get("use_ssl", storage.use_ssl) is not None
                else True,
            )
        except (ImproperlyConfigured, OSError, ValueError) as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        except Exception as exc:
            raise ValidationError(
                {"detail": f"S3 storage test failed: {exc}"},
            ) from exc

        return Response({"detail": "S3 storage test succeeded."})

    @action(detail=True, methods=["post"], url_path="export")
    def export_to_storage(self, request, pk=None):
        if not request.user.is_superuser:
            return HttpResponseForbidden("Insufficient permissions")

        storage = self.get_object()
        async_result = export_documents_to_s3_storage.delay(storage.pk)
        PaperlessTask.objects.create(
            type=PaperlessTask.TaskType.MANUAL_TASK,
            task_id=async_result.id or str(uuid4()),
            status=states.PENDING,
            task_file_name=storage.name,
            task_name=PaperlessTask.TaskName.EXPORT_S3_STORAGE,
            result=None,
            owner=request.user,
        )
        return Response(
            {
                "detail": (
                    f'Started export to S3 storage "{storage.name}" in the background.'
                ),
                "task_id": async_result.id,
            },
            status=202,
        )

    @staticmethod
    def _validate_export_name(export_name: object) -> str:
        if not isinstance(export_name, str) or not export_name:
            raise ValidationError({"export_name": "This field is required."})
        export_path = Path(export_name)
        if (
            export_path.is_absolute()
            or ".." in export_path.parts
            or export_path.name != export_path.parts[-1]
            or not export_name.endswith(".zip")
        ):
            raise ValidationError({"export_name": "Invalid export name."})
        return export_name

    @staticmethod
    def _list_export_names(storage_backend, path: str = ""):
        directories, filenames = storage_backend.listdir(path)
        exports = [f"{path}/{filename}" if path else filename for filename in filenames]
        for directory in directories:
            nested_path = f"{path}/{directory}" if path else directory
            exports.extend(
                S3StorageConfigurationViewSet._list_export_names(
                    storage_backend,
                    nested_path,
                ),
            )
        return exports

    @staticmethod
    def _get_export_storage_candidates(storage):
        return [
            (
                MANUAL_S3_TRANSFER_LOCATION,
                get_s3_configuration_storage(
                    storage,
                    location=MANUAL_S3_TRANSFER_LOCATION,
                ),
            ),
            (
                AUTOMATIC_S3_TRANSFER_LOCATION,
                get_s3_configuration_storage(
                    storage,
                    location=AUTOMATIC_S3_TRANSFER_LOCATION,
                ),
            ),
        ]

    @classmethod
    def _get_storage_backend_for_export(cls, storage, export_name: str):
        for _, storage_backend in cls._get_export_storage_candidates(storage):
            if storage_backend.exists(export_name):
                return storage_backend
        raise ValidationError({"export_name": "Export not found."})

    @action(detail=True, methods=["get"], url_path="exports")
    def list_exports(self, request, pk=None):
        storage = self.get_object()
        try:
            exports = []
            for _, storage_backend in self._get_export_storage_candidates(storage):
                for filename in self._list_export_names(storage_backend):
                    if not filename.endswith(".zip"):
                        continue
                    exports.append(
                        {
                            "name": filename,
                            "size": storage_backend.size(filename),
                            "modified": storage_backend.get_modified_time(filename),
                        },
                    )
            exports.sort(key=lambda export: export["name"], reverse=True)
        except Exception as exc:
            raise ValidationError(
                {"detail": f'Unable to list exports for "{storage.name}": {exc}'},
            ) from exc

        return Response(exports)

    @action(detail=True, methods=["post"], url_path="download-export")
    def download_export_from_storage(self, request, pk=None):
        if not request.user.is_superuser:
            return HttpResponseForbidden("Insufficient permissions")

        storage = self.get_object()
        export_name = self._validate_export_name(request.data.get("export_name"))
        try:
            storage_backend = self._get_storage_backend_for_export(storage, export_name)
            return FileResponse(
                storage_backend.open(export_name, "rb"),
                as_attachment=True,
                filename=Path(export_name).name,
                content_type="application/zip",
            )
        except ValidationError:
            raise
        except Exception as exc:
            raise ValidationError(
                {"detail": f'Unable to download export "{export_name}": {exc}'},
            ) from exc

    @action(detail=True, methods=["post"], url_path="delete-export")
    def delete_export_from_storage(self, request, pk=None):
        if not request.user.is_superuser:
            return HttpResponseForbidden("Insufficient permissions")

        storage = self.get_object()
        export_name = self._validate_export_name(request.data.get("export_name"))
        try:
            storage_backend = self._get_storage_backend_for_export(storage, export_name)
            storage_backend.delete(export_name)
        except ValidationError:
            raise
        except Exception as exc:
            raise ValidationError(
                {"detail": f'Unable to delete export "{export_name}": {exc}'},
            ) from exc

        return Response(
            {"detail": f'Successfully deleted export "{export_name}".'},
        )

    @action(detail=True, methods=["post"], url_path="import")
    def import_from_storage(self, request, pk=None):
        if not request.user.is_superuser:
            return HttpResponseForbidden("Insufficient permissions")

        storage = self.get_object()
        export_name = self._validate_export_name(request.data.get("export_name"))

        async_result = import_documents_from_s3_storage.delay(
            storage.pk,
            export_name,
            request.user.pk,
        )
        PaperlessTask.objects.create(
            type=PaperlessTask.TaskType.MANUAL_TASK,
            task_id=async_result.id or str(uuid4()),
            status=states.PENDING,
            task_file_name=storage.name,
            task_name=PaperlessTask.TaskName.IMPORT_S3_STORAGE,
            result=None,
            owner=request.user,
        )
        return Response(
            {
                "detail": (
                    f'Started import from S3 storage "{storage.name}" '
                    f'using "{export_name}" in the background.'
                ),
                "task_id": async_result.id,
            },
            status=202,
        )


class ProfileView(GenericAPIView):
    """
    User profile view, only available when logged in
    """

    permission_classes = [IsAuthenticated]
    serializer_class = ProfileSerializer

    def get(self, request, *args, **kwargs):
        user = self.request.user

        serializer = self.get_serializer(data=request.data)
        return Response(serializer.to_representation(user))

    def patch(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = self.request.user if hasattr(self.request, "user") else None

        password = serializer.validated_data.pop("password", None)
        if password and password.replace("*", ""):
            user.set_password(password)
            user.save()

        for key, value in serializer.validated_data.items():
            setattr(user, key, value)
        user.save()

        return Response(serializer.to_representation(user))


@extend_schema_view(
    get=extend_schema(
        responses={
            (200, "application/json"): OpenApiTypes.OBJECT,
        },
    ),
    post=extend_schema(
        request={
            "application/json": {
                "type": "object",
                "properties": {
                    "secret": {"type": "string"},
                    "code": {"type": "string"},
                },
                "required": ["secret", "code"],
            },
        },
        responses={
            (200, "application/json"): OpenApiTypes.OBJECT,
        },
    ),
    delete=extend_schema(
        responses={
            (200, "application/json"): OpenApiTypes.BOOL,
            404: OpenApiTypes.STR,
        },
    ),
)
class TOTPView(GenericAPIView):
    """
    TOTP views
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        """
        Generates a new TOTP secret and returns the URL and SVG
        """
        user = self.request.user
        mfa_adapter = get_mfa_adapter()
        secret = totp_auth.get_totp_secret(regenerate=True)
        url = mfa_adapter.build_totp_url(user, secret)
        svg = mfa_adapter.build_totp_svg(url)
        return Response(
            {
                "url": url,
                "qr_svg": svg,
                "secret": secret,
            },
        )

    def post(self, request, *args, **kwargs):
        """
        Validates a TOTP code and activates the TOTP authenticator
        """
        valid = totp_auth.validate_totp_code(
            request.data["secret"],
            request.data["code"],
        )
        recovery_codes = None
        if valid:
            auth = totp_auth.TOTP.activate(
                request.user,
                request.data["secret"],
            ).instance
            signals.authenticator_added.send(
                sender=Authenticator,
                request=request,
                user=request.user,
                authenticator=auth,
            )
            rc_auth: Authenticator = auto_generate_recovery_codes(request)
            if rc_auth:
                recovery_codes = rc_auth.wrap().get_unused_codes()
        return Response(
            {
                "success": valid,
                "recovery_codes": recovery_codes,
            },
        )

    def delete(self, request, *args, **kwargs):
        """
        Deactivates the TOTP authenticator
        """
        user = self.request.user
        authenticator = Authenticator.objects.filter(
            user=user,
            type=Authenticator.Type.TOTP,
        ).first()
        if authenticator is not None:
            delete_and_cleanup(request, authenticator)
            return Response(data=True)
        else:
            return HttpResponseNotFound("TOTP not found")


@extend_schema_view(
    post=extend_schema(
        request={
            "application/json": None,
        },
        responses={
            (200, "application/json"): OpenApiTypes.STR,
        },
    ),
)
class GenerateAuthTokenView(GenericAPIView):
    """
    Generates (or re-generates) an auth token, requires a logged in user
    unlike the default DRF endpoint
    """

    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        user = self.request.user

        existing_token = Token.objects.filter(user=user).first()
        if existing_token is not None:
            existing_token.delete()
        token = Token.objects.create(user=user)
        return Response(
            token.key,
        )


@extend_schema_view(
    list=extend_schema(
        description="Get the application configuration",
        external_docs={
            "description": "Application Configuration",
            "url": "https://docs.paperless-ngx.com/configuration/",
        },
    ),
)
class ApplicationConfigurationViewSet(ModelViewSet):
    model = ApplicationConfiguration

    queryset = ApplicationConfiguration.objects

    serializer_class = ApplicationConfigurationSerializer
    permission_classes = (IsAuthenticated, DjangoModelPermissions)

    @extend_schema(exclude=True)
    def create(self, request, *args, **kwargs):
        return Response(status=405)  # Not Allowed

    def perform_update(self, serializer):
        old_instance = ApplicationConfiguration.objects.all().first()
        old_ai_index_enabled = (
            old_instance.ai_enabled and old_instance.llm_embedding_backend
        )

        new_instance: ApplicationConfiguration = serializer.save()
        new_ai_index_enabled = (
            new_instance.ai_enabled and new_instance.llm_embedding_backend
        )

        if (
            not old_ai_index_enabled
            and new_ai_index_enabled
            and not vector_store_file_exists()
        ):
            # AI index was just enabled and vector store file does not exist
            llmindex_index.delay(
                rebuild=True,
                scheduled=False,
                auto=True,
            )

    @staticmethod
    def _restore_masked_secret(
        *,
        overrides: dict[str, object],
        config: ApplicationConfiguration,
        key: str,
    ) -> None:
        secret = overrides.get(key)
        if isinstance(secret, str) and secret and secret.replace("*", "") == "":
            overrides[key] = getattr(config, key)

    def _mark_stale_runtime_reset_tasks(self) -> None:
        now = timezone.now()
        stale_before = now - timedelta(minutes=15)
        stale_tasks = PaperlessTask.objects.filter(
            task_name=PaperlessTask.TaskName.RESET_RUNTIME_DATA,
            status__in={states.PENDING, states.STARTED},
        ).filter(
            Q(date_started__lt=stale_before)
            | Q(date_started__isnull=True, date_created__lt=stale_before),
        )
        stale_tasks.update(
            status=states.FAILURE,
            result=(
                "Task marked as failed because it stopped updating before a new "
                "runtime data reset was requested."
            ),
            date_done=now,
        )

    def _release_runtime_reset_tasks(self, *, reason: str) -> int:
        now = timezone.now()
        released = PaperlessTask.objects.filter(
            task_name=PaperlessTask.TaskName.RESET_RUNTIME_DATA,
            status__in={states.PENDING, states.STARTED},
        ).update(
            status=states.FAILURE,
            result=reason,
            date_done=now,
        )
        return released

    @action(detail=True, methods=["post"], url_path="test-s3-storage")
    def test_s3_storage(self, request, *args, **kwargs):
        config = self.get_object()
        serializer = self.get_serializer(config, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)

        overrides = {
            key: value
            for key, value in serializer.validated_data.items()
            if key.startswith("documents_") and not key.startswith("documents_backup_")
        }

        self._restore_masked_secret(
            overrides=overrides,
            config=config,
            key="documents_s3_secret_access_key",
        )

        try:
            test_document_storage_connection(overrides)
        except (ImproperlyConfigured, OSError, ValueError) as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        except Exception as exc:
            raise ValidationError(
                {"detail": f"S3 storage test failed: {exc}"},
            ) from exc

        return Response({"detail": "S3 storage test succeeded."})

    @action(detail=True, methods=["post"], url_path="test-s3-backup-storage")
    def test_s3_backup_storage(self, request, *args, **kwargs):
        config = self.get_object()
        serializer = self.get_serializer(config, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)

        overrides = {
            key: value
            for key, value in serializer.validated_data.items()
            if key.startswith("documents_backup_")
        }

        self._restore_masked_secret(
            overrides=overrides,
            config=config,
            key="documents_backup_s3_secret_access_key",
        )

        try:
            test_document_backup_storage_connection(overrides)
        except (ImproperlyConfigured, OSError, ValueError) as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        except Exception as exc:
            raise ValidationError(
                {"detail": f"S3 backup storage test failed: {exc}"},
            ) from exc

        return Response({"detail": "S3 backup storage test succeeded."})

    @action(detail=True, methods=["post"], url_path="download-export")
    def download_export(self, request, *args, **kwargs):
        if not request.user.is_superuser:
            return HttpResponseForbidden("Insufficient permissions")

        export_filename = build_manual_s3_export_filename()
        settings.SCRATCH_DIR.mkdir(parents=True, exist_ok=True)
        temp_dir = TemporaryDirectory(
            dir=settings.SCRATCH_DIR,
            prefix="paperless-download-export-",
        )
        export_dir = Path(temp_dir.name)

        call_command(
            "document_exporter",
            export_dir,
            zip=True,
            zip_name=export_filename.removesuffix(".zip"),
            use_filename_format=True,
            use_folder_prefix=True,
            no_progress_bar=True,
        )

        export_path = export_dir / export_filename
        if not export_path.exists():
            temp_dir.cleanup()
            raise ValidationError({"detail": "Export file was not created."})

        response = FileResponse(
            export_path.open("rb"),
            as_attachment=True,
            filename=export_filename,
            content_type="application/zip",
        )
        response._resource_closers.append(temp_dir.cleanup)
        return response

    @action(detail=True, methods=["post"], url_path="reset-runtime-data")
    def reset_runtime_data_action(self, request, *args, **kwargs):
        if not request.user.is_superuser:
            return HttpResponseForbidden("Insufficient permissions")

        self._mark_stale_runtime_reset_tasks()

        if PaperlessTask.objects.filter(
            task_name=PaperlessTask.TaskName.RESET_RUNTIME_DATA,
            status__in={states.PENDING, states.STARTED},
        ).exists():
            raise ValidationError(
                {"detail": "A runtime data reset is already running."},
            )

        task_id = str(uuid4())

        PaperlessTask.objects.create(
            type=PaperlessTask.TaskType.MANUAL_TASK,
            task_id=task_id,
            task_name=PaperlessTask.TaskName.RESET_RUNTIME_DATA,
            status=states.PENDING,
            date_created=timezone.now(),
            task_file_name="Runtime data reset",
            owner=request.user
            if request.user and request.user.is_authenticated
            else None,
        )

        reset_runtime_data.apply_async(
            kwargs={
                "owner_id": request.user.id if request.user.is_authenticated else None,
            },
            task_id=task_id,
        )

        return Response(
            {
                "detail": "Runtime data reset started.",
                "task_id": task_id,
            },
        )

    @action(detail=True, methods=["post"], url_path="release-runtime-reset-lock")
    def release_runtime_reset_lock(self, request, *args, **kwargs):
        if not request.user.is_superuser:
            return HttpResponseForbidden("Insufficient permissions")

        released = self._release_runtime_reset_tasks(
            reason=(
                "Task lock manually released by a superuser before starting a new "
                "runtime data reset."
            ),
        )
        return Response(
            {
                "detail": (
                    "Runtime data reset lock released."
                    if released
                    else "No runtime data reset lock was active."
                ),
                "released_tasks": released,
            },
        )

    @action(detail=True, methods=["post"], url_path="seed-demo-crafts-data")
    def seed_demo_crafts_data(self, request, *args, **kwargs):
        if not request.user.is_superuser:
            return HttpResponseForbidden("Insufficient permissions")

        if PaperlessTask.objects.filter(
            task_name=PaperlessTask.TaskName.CREATE_DEMO_CRAFTS_DATA,
            status__in={states.PENDING, states.STARTED},
        ).exists():
            raise ValidationError(
                {"detail": "Demo data generation is already running."},
            )

        task_id = str(uuid4())

        PaperlessTask.objects.create(
            type=PaperlessTask.TaskType.MANUAL_TASK,
            task_id=task_id,
            task_name=PaperlessTask.TaskName.CREATE_DEMO_CRAFTS_DATA,
            status=states.PENDING,
            date_created=timezone.now(),
            task_file_name="Handwerksbetrieb Demo",
            owner=request.user
            if request.user and request.user.is_authenticated
            else None,
        )

        create_demo_crafts_data.apply_async(
            kwargs={
                "owner_id": request.user.id if request.user.is_authenticated else None,
            },
            task_id=task_id,
        )

        return Response(
            {
                "detail": "Demo data generation started.",
                "task_id": task_id,
            },
        )

    @action(detail=True, methods=["post"], url_path="remote-import-inspect")
    def remote_import_inspect(self, request, *args, **kwargs):
        config = self.get_object()
        serializer = RemoteImportConnectionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        service = RemoteImportService(
            base_url=self._resolve_remote_import_base_url(
                serializer.validated_data.get("base_url"),
                config,
            ),
            api_token=self._resolve_remote_import_api_token(
                serializer.validated_data.get("api_token"),
                config,
            ),
            owner_id=request.user.id if request.user else None,
        )
        return Response(service.inspect())

    @action(detail=True, methods=["post"], url_path="remote-import-documents")
    def remote_import_documents(self, request, *args, **kwargs):
        config = self.get_object()
        serializer = RemoteImportBrowseSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        service = RemoteImportService(
            base_url=self._resolve_remote_import_base_url(
                serializer.validated_data.get("base_url"),
                config,
            ),
            api_token=self._resolve_remote_import_api_token(
                serializer.validated_data.get("api_token"),
                config,
            ),
            owner_id=request.user.id if request.user else None,
        )
        return Response(
            service.browse_documents(
                query=serializer.validated_data["query"],
                page=serializer.validated_data["page"],
                page_size=serializer.validated_data["page_size"],
            ),
        )

    @action(detail=True, methods=["post"], url_path="remote-import-start")
    def remote_import_start(self, request, *args, **kwargs):
        config = self.get_object()
        serializer = RemoteImportStartSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        resolved_base_url = self._resolve_remote_import_base_url(
            serializer.validated_data.get("base_url"),
            config,
        )
        resolved_api_token = self._resolve_remote_import_api_token(
            serializer.validated_data.get("api_token"),
            config,
        )

        async_task = import_remote_documents.delay(
            base_url=resolved_base_url,
            api_token=resolved_api_token,
            selected_document_ids=serializer.validated_data.get(
                "selected_document_ids",
            ),
            query=serializer.validated_data.get("query", ""),
            import_all=serializer.validated_data.get("import_all", False),
            create_missing_items=serializer.validated_data.get(
                "create_missing_items",
                True,
            ),
            import_notes=serializer.validated_data.get("import_notes", True),
            owner_id=request.user.id if request.user else None,
        )

        parsed_url = resolved_base_url.rstrip("/")
        PaperlessTask.objects.create(
            type=PaperlessTask.TaskType.MANUAL_TASK,
            task_id=async_task.id,
            task_name=PaperlessTask.TaskName.IMPORT_FILE,
            status=states.PENDING,
            date_created=timezone.now(),
            task_file_name=f"Remote import from {parsed_url}",
            owner=request.user
            if request.user and request.user.is_authenticated
            else None,
        )

        return Response({"task_id": async_task.id})

    @staticmethod
    def _looks_masked_secret(value: str | None) -> bool:
        return bool(value) and value.replace("*", "") == ""

    def _resolve_remote_import_base_url(
        self,
        value: str | None,
        config: ApplicationConfiguration,
    ) -> str:
        resolved = value or config.remote_import_base_url
        if not resolved:
            raise ValidationError(
                {"base_url": "A remote base URL is required."},
            )
        return resolved

    def _resolve_remote_import_api_token(
        self,
        value: str | None,
        config: ApplicationConfiguration,
    ) -> str:
        resolved = (
            config.remote_import_api_token
            if self._looks_masked_secret(value) or not value
            else value
        )
        if not resolved:
            raise ValidationError(
                {"api_token": "A remote API token is required."},
            )
        return resolved


@extend_schema_view(
    post=extend_schema(
        request={
            "application/json": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                },
                "required": ["id"],
            },
        },
        responses={
            (200, "application/json"): OpenApiTypes.INT,
            400: OpenApiTypes.STR,
        },
    ),
)
class DisconnectSocialAccountView(GenericAPIView):
    """
    Disconnects a social account provider from the user account
    """

    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        user = self.request.user

        try:
            account = user.socialaccount_set.get(pk=request.data["id"])
            account_id = account.id
            account.delete()
            return Response(account_id)
        except SocialAccount.DoesNotExist:
            return HttpResponseBadRequest("Social account not found")


@extend_schema_view(
    get=extend_schema(
        responses={
            (200, "application/json"): OpenApiTypes.OBJECT,
        },
    ),
)
class SocialAccountProvidersView(GenericAPIView):
    """
    List of social account providers
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        adapter = get_adapter()
        providers = adapter.list_providers(request)
        resp = [
            {"name": p.name, "login_url": p.get_login_url(request, process="connect")}
            for p in providers
            if p.id != "openid"
        ]

        for openid_provider in filter(lambda p: p.id == "openid", providers):
            resp += [
                {
                    "name": b["name"],
                    "login_url": openid_provider.get_login_url(
                        request,
                        process="connect",
                        openid=b["openid_url"],
                    ),
                }
                for b in openid_provider.get_brands()
            ]

        return Response(sorted(resp, key=lambda p: p["name"]))
