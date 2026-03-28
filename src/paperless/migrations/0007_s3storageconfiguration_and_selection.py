from django.db import migrations
from django.db import models


class Migration(migrations.Migration):
    dependencies = [
        ("paperless", "0006_applicationconfiguration_document_backup_settings"),
    ]

    operations = [
        migrations.CreateModel(
            name="S3StorageConfiguration",
            fields=[
                (
                    "id",
                    models.AutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "name",
                    models.CharField(
                        max_length=255,
                        unique=True,
                        verbose_name="Storage name",
                    ),
                ),
                (
                    "prefix",
                    models.CharField(
                        blank=True,
                        max_length=255,
                        null=True,
                        verbose_name="Sets the S3 storage prefix",
                    ),
                ),
                (
                    "bucket",
                    models.CharField(max_length=255, verbose_name="Sets the S3 bucket"),
                ),
                (
                    "endpoint_url",
                    models.CharField(
                        blank=True,
                        max_length=512,
                        null=True,
                        verbose_name="Sets the S3 endpoint URL",
                    ),
                ),
                (
                    "access_key_id",
                    models.CharField(
                        blank=True,
                        max_length=255,
                        null=True,
                        verbose_name="Sets the S3 access key ID",
                    ),
                ),
                (
                    "secret_access_key",
                    models.CharField(
                        blank=True,
                        max_length=255,
                        null=True,
                        verbose_name="Sets the S3 secret access key",
                    ),
                ),
                (
                    "region_name",
                    models.CharField(
                        blank=True,
                        max_length=255,
                        null=True,
                        verbose_name="Sets the S3 region",
                    ),
                ),
                (
                    "default_acl",
                    models.CharField(
                        blank=True,
                        max_length=255,
                        null=True,
                        verbose_name="Sets the S3 default ACL",
                    ),
                ),
                (
                    "custom_domain",
                    models.CharField(
                        blank=True,
                        max_length=255,
                        null=True,
                        verbose_name="Sets the S3 custom domain",
                    ),
                ),
                (
                    "url_protocol",
                    models.CharField(
                        blank=True,
                        max_length=32,
                        null=True,
                        verbose_name="Sets the S3 URL protocol",
                    ),
                ),
                (
                    "addressing_style",
                    models.CharField(
                        blank=True,
                        max_length=32,
                        null=True,
                        verbose_name="Sets the S3 addressing style",
                    ),
                ),
                (
                    "querystring_auth",
                    models.BooleanField(
                        null=True,
                        verbose_name="Sets whether S3 querystring auth is enabled",
                    ),
                ),
                (
                    "use_ssl",
                    models.BooleanField(
                        null=True,
                        verbose_name="Sets whether S3 uses SSL",
                    ),
                ),
            ],
            options={
                "verbose_name": "S3 storage configuration",
                "verbose_name_plural": "S3 storage configurations",
                "ordering": ("name",),
            },
        ),
        migrations.AddField(
            model_name="applicationconfiguration",
            name="documents_backup_s3_storage",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.SET_NULL,
                related_name="documents_backup_configs",
                to="paperless.s3storageconfiguration",
                verbose_name="Selects the S3 storage for document backups",
            ),
        ),
        migrations.AddField(
            model_name="applicationconfiguration",
            name="documents_s3_storage",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.SET_NULL,
                related_name="documents_primary_configs",
                to="paperless.s3storageconfiguration",
                verbose_name="Selects the S3 storage for documents",
            ),
        ),
    ]
