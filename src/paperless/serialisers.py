import logging

import magic
from allauth.mfa.adapter import get_adapter as get_mfa_adapter
from allauth.mfa.models import Authenticator
from allauth.mfa.totp.internal.auth import TOTP
from allauth.socialaccount.models import SocialAccount
from allauth.socialaccount.models import SocialApp
from django.conf import settings
from django.contrib.auth.models import Group
from django.contrib.auth.models import Permission
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.files.uploadedfile import UploadedFile
from rest_framework import serializers
from rest_framework.authtoken.serializers import AuthTokenSerializer

from paperless.models import ApplicationConfiguration
from paperless.models import S3StorageConfiguration
from paperless.network import validate_outbound_http_url
from paperless.validators import reject_dangerous_svg
from paperless_mail.serialisers import ObfuscatedPasswordField

logger = logging.getLogger("paperless.settings")


class PasswordValidationMixin:
    def _has_real_password(self, value: str | None) -> bool:
        return bool(value) and value.replace("*", "") != ""

    def validate_password(self, value: str) -> str:
        if not self._has_real_password(value):
            return value

        request = self.context.get("request") if hasattr(self, "context") else None
        user = self.instance or (
            request.user if request and hasattr(request, "user") else None
        )
        validate_password(value, user)  # raise ValidationError if invalid

        return value


class PaperlessAuthTokenSerializer(AuthTokenSerializer):
    code = serializers.CharField(
        label="MFA Code",
        write_only=True,
        required=False,
    )

    def validate(self, attrs):
        attrs = super().validate(attrs)
        user = attrs.get("user")
        code = attrs.get("code")
        mfa_adapter = get_mfa_adapter()
        if mfa_adapter.is_mfa_enabled(user):
            if not code:
                raise serializers.ValidationError(
                    "MFA code is required",
                )
            authenticator = Authenticator.objects.get(
                user=user,
                type=Authenticator.Type.TOTP,
            )
            if not TOTP(instance=authenticator).validate_code(
                code,
            ):
                raise serializers.ValidationError(
                    "Invalid MFA code",
                )
        return attrs


class UserSerializer(PasswordValidationMixin, serializers.ModelSerializer):
    password = ObfuscatedPasswordField(required=False)
    user_permissions = serializers.SlugRelatedField(
        many=True,
        queryset=Permission.objects.exclude(content_type__app_label="admin"),
        slug_field="codename",
        required=False,
    )
    inherited_permissions = serializers.SerializerMethodField()
    is_mfa_enabled = serializers.SerializerMethodField()

    def get_is_mfa_enabled(self, user: User) -> bool:
        mfa_adapter = get_mfa_adapter()
        return mfa_adapter.is_mfa_enabled(user)

    class Meta:
        model = User
        fields = (
            "id",
            "username",
            "email",
            "password",
            "first_name",
            "last_name",
            "date_joined",
            "is_staff",
            "is_active",
            "is_superuser",
            "groups",
            "user_permissions",
            "inherited_permissions",
            "is_mfa_enabled",
        )

    def get_inherited_permissions(self, obj) -> list[str]:
        return obj.get_group_permissions()

    def update(self, instance, validated_data):
        password = validated_data.pop("password", None)
        if self._has_real_password(password):
            instance.set_password(password)
            instance.save()

        super().update(instance, validated_data)
        return instance

    def create(self, validated_data):
        groups = None
        if "groups" in validated_data:
            groups = validated_data.pop("groups")
        user_permissions = None
        if "user_permissions" in validated_data:
            user_permissions = validated_data.pop("user_permissions")
        password = validated_data.pop("password", None)
        user = User.objects.create(**validated_data)
        # set groups
        if groups:
            user.groups.set(groups)
        # set permissions
        if user_permissions:
            user.user_permissions.set(user_permissions)
        # set password
        if self._has_real_password(password):
            user.set_password(password)
        user.save()
        return user


class GroupSerializer(serializers.ModelSerializer):
    permissions = serializers.SlugRelatedField(
        many=True,
        queryset=Permission.objects.exclude(content_type__app_label="admin"),
        slug_field="codename",
    )

    class Meta:
        model = Group
        fields = (
            "id",
            "name",
            "permissions",
        )


class SocialAccountSerializer(serializers.ModelSerializer):
    name = serializers.SerializerMethodField()

    class Meta:
        model = SocialAccount
        fields = (
            "id",
            "provider",
            "name",
        )

    def get_name(self, obj: SocialAccount) -> str:
        try:
            return obj.get_provider_account().to_str()
        except SocialApp.DoesNotExist:
            return "Unknown App"


class ProfileSerializer(PasswordValidationMixin, serializers.ModelSerializer):
    email = serializers.EmailField(allow_blank=True, required=False)
    password = ObfuscatedPasswordField(required=False, allow_null=False)
    auth_token = serializers.SlugRelatedField(read_only=True, slug_field="key")
    social_accounts = SocialAccountSerializer(
        many=True,
        read_only=True,
        source="socialaccount_set",
    )
    is_mfa_enabled = serializers.SerializerMethodField()
    has_usable_password = serializers.SerializerMethodField()

    def get_is_mfa_enabled(self, user: User) -> bool:
        mfa_adapter = get_mfa_adapter()
        return mfa_adapter.is_mfa_enabled(user)

    def get_has_usable_password(self, user: User) -> bool:
        return user.has_usable_password()

    class Meta:
        model = User
        fields = (
            "email",
            "password",
            "first_name",
            "last_name",
            "auth_token",
            "social_accounts",
            "has_usable_password",
            "is_mfa_enabled",
        )


class ApplicationConfigurationSerializer(serializers.ModelSerializer):
    user_args = serializers.JSONField(binary=True, allow_null=True)
    barcode_tag_mapping = serializers.JSONField(binary=True, allow_null=True)
    llm_api_key = ObfuscatedPasswordField(
        required=False,
        allow_null=True,
    )
    documents_backup_schedule_jobs = serializers.JSONField(
        required=False,
        allow_null=True,
    )
    documents_s3_secret_access_key = ObfuscatedPasswordField(
        required=False,
        allow_null=True,
        allow_blank=True,
    )
    documents_backup_s3_secret_access_key = ObfuscatedPasswordField(
        required=False,
        allow_null=True,
        allow_blank=True,
    )

    def run_validation(self, data):
        # Empty strings treated as None to avoid unexpected behavior
        if "user_args" in data and data["user_args"] == "":
            data["user_args"] = None
        if "barcode_tag_mapping" in data and data["barcode_tag_mapping"] == "":
            data["barcode_tag_mapping"] = None
        if "language" in data and data["language"] == "":
            data["language"] = None
        if "llm_api_key" in data and data["llm_api_key"] is not None:
            if data["llm_api_key"] == "":
                data["llm_api_key"] = None
            elif len(data["llm_api_key"].replace("*", "")) == 0:
                del data["llm_api_key"]
        nullable_string_fields = (
            "documents_storage_prefix",
            "documents_s3_bucket",
            "documents_s3_endpoint_url",
            "documents_s3_access_key_id",
            "documents_s3_secret_access_key",
            "documents_s3_region_name",
            "documents_s3_default_acl",
            "documents_s3_custom_domain",
            "documents_s3_url_protocol",
            "documents_s3_addressing_style",
            "documents_backup_prefix",
            "documents_backup_s3_bucket",
            "documents_backup_s3_endpoint_url",
            "documents_backup_s3_access_key_id",
            "documents_backup_s3_secret_access_key",
            "documents_backup_s3_region_name",
            "documents_backup_s3_default_acl",
            "documents_backup_s3_custom_domain",
            "documents_backup_s3_url_protocol",
            "documents_backup_s3_addressing_style",
        )
        for field in nullable_string_fields:
            if field in data and data[field] == "":
                data[field] = None
        return super().run_validation(data)

    def update(self, instance, validated_data):
        if instance.app_logo and "app_logo" in validated_data:
            instance.app_logo.delete()
        if (
            "documents_s3_secret_access_key" in validated_data
            and validated_data["documents_s3_secret_access_key"]
            and validated_data["documents_s3_secret_access_key"].replace("*", "") == ""
        ):
            validated_data.pop("documents_s3_secret_access_key")
        if (
            "documents_backup_s3_secret_access_key" in validated_data
            and validated_data["documents_backup_s3_secret_access_key"]
            and validated_data["documents_backup_s3_secret_access_key"].replace("*", "")
            == ""
        ):
            validated_data.pop("documents_backup_s3_secret_access_key")
        return super().update(instance, validated_data)

    def validate_app_logo(self, file: UploadedFile):
        if file and magic.from_buffer(file.read(2048), mime=True) == "image/svg+xml":
            reject_dangerous_svg(file)
        return file

    def validate_llm_endpoint(self, value: str | None) -> str | None:
        if not value:
            return value

        try:
            validate_outbound_http_url(
                value,
                allow_internal=settings.LLM_ALLOW_INTERNAL_ENDPOINTS,
            )
        except ValueError as e:
            raise serializers.ValidationError(
                f"Invalid LLM endpoint: {e.args[0]}, see logs for details",
            ) from e

        return value

    def validate_documents_backup_schedule_jobs(self, value):
        if value in (None, ""):
            return []
        if not isinstance(value, list):
            raise serializers.ValidationError("Expected a list of backup jobs.")

        validated_jobs = []
        seen_names = set()
        for index, job in enumerate(value):
            if not isinstance(job, dict):
                raise serializers.ValidationError(
                    f"Backup job at index {index} must be an object.",
                )

            name = job.get("name")
            if not isinstance(name, str) or not name.strip():
                raise serializers.ValidationError(
                    f'Backup job at index {index} requires a non-empty "name".',
                )
            name = name.strip()
            if name in seen_names:
                raise serializers.ValidationError(
                    f'Backup job name "{name}" is duplicated.',
                )
            seen_names.add(name)

            enabled = bool(job.get("enabled", True))
            storage = job.get("storage")
            frequency_days = job.get("frequency_days")
            hour = job.get("hour")
            minute = job.get("minute")
            retain_count = job.get("retain_count")
            last_run = job.get("last_run")

            if storage is not None and not isinstance(storage, int):
                raise serializers.ValidationError(
                    f'Backup job "{name}" has an invalid "storage" value.',
                )
            if frequency_days is not None and (
                not isinstance(frequency_days, int) or frequency_days < 1
            ):
                raise serializers.ValidationError(
                    f'Backup job "{name}" requires "frequency_days" >= 1.',
                )
            if hour is not None and (
                not isinstance(hour, int) or hour < 0 or hour > 23
            ):
                raise serializers.ValidationError(
                    f'Backup job "{name}" requires "hour" between 0 and 23.',
                )
            if minute is not None and (
                not isinstance(minute, int) or minute < 0 or minute > 59
            ):
                raise serializers.ValidationError(
                    f'Backup job "{name}" requires "minute" between 0 and 59.',
                )
            if retain_count is not None and (
                not isinstance(retain_count, int) or retain_count < 1
            ):
                raise serializers.ValidationError(
                    f'Backup job "{name}" requires "retain_count" >= 1.',
                )
            if last_run is not None and not isinstance(last_run, str):
                raise serializers.ValidationError(
                    f'Backup job "{name}" has an invalid "last_run" value.',
                )

            validated_jobs.append(
                {
                    "name": name,
                    "enabled": enabled,
                    "storage": storage,
                    "frequency_days": frequency_days,
                    "hour": hour,
                    "minute": minute,
                    "retain_count": retain_count,
                    "last_run": last_run,
                },
            )

        return validated_jobs

    class Meta:
        model = ApplicationConfiguration
        fields = "__all__"


class S3StorageConfigurationSerializer(serializers.ModelSerializer):
    secret_access_key = ObfuscatedPasswordField(
        required=False,
        allow_null=True,
        allow_blank=True,
    )

    def run_validation(self, data):
        nullable_string_fields = (
            "prefix",
            "endpoint_url",
            "access_key_id",
            "secret_access_key",
            "region_name",
            "default_acl",
            "custom_domain",
            "url_protocol",
            "addressing_style",
        )
        for field in nullable_string_fields:
            if field in data and data[field] == "":
                data[field] = None
        return super().run_validation(data)

    def update(self, instance, validated_data):
        if (
            "secret_access_key" in validated_data
            and validated_data["secret_access_key"]
            and validated_data["secret_access_key"].replace("*", "") == ""
        ):
            validated_data.pop("secret_access_key")
        return super().update(instance, validated_data)

    class Meta:
        model = S3StorageConfiguration
        fields = "__all__"
