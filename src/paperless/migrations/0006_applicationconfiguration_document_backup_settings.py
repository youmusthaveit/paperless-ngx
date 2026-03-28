from django.db import migrations
from django.db import models


class Migration(migrations.Migration):
    dependencies = [
        ("paperless", "0005_applicationconfiguration_document_storage_settings"),
    ]

    operations = [
        migrations.AddField(
            model_name="applicationconfiguration",
            name="documents_backup_prefix",
            field=models.CharField(
                blank=True,
                max_length=255,
                null=True,
                verbose_name="Sets the S3 backup prefix for documents",
            ),
        ),
        migrations.AddField(
            model_name="applicationconfiguration",
            name="documents_backup_s3_access_key_id",
            field=models.CharField(
                blank=True,
                max_length=255,
                null=True,
                verbose_name="Sets the S3 backup access key ID",
            ),
        ),
        migrations.AddField(
            model_name="applicationconfiguration",
            name="documents_backup_s3_addressing_style",
            field=models.CharField(
                blank=True,
                max_length=32,
                null=True,
                verbose_name="Sets the S3 backup addressing style",
            ),
        ),
        migrations.AddField(
            model_name="applicationconfiguration",
            name="documents_backup_s3_bucket",
            field=models.CharField(
                blank=True,
                max_length=255,
                null=True,
                verbose_name="Sets the S3 backup bucket for documents",
            ),
        ),
        migrations.AddField(
            model_name="applicationconfiguration",
            name="documents_backup_s3_custom_domain",
            field=models.CharField(
                blank=True,
                max_length=255,
                null=True,
                verbose_name="Sets the S3 backup custom domain",
            ),
        ),
        migrations.AddField(
            model_name="applicationconfiguration",
            name="documents_backup_s3_default_acl",
            field=models.CharField(
                blank=True,
                max_length=255,
                null=True,
                verbose_name="Sets the S3 backup default ACL",
            ),
        ),
        migrations.AddField(
            model_name="applicationconfiguration",
            name="documents_backup_s3_endpoint_url",
            field=models.CharField(
                blank=True,
                max_length=512,
                null=True,
                verbose_name="Sets the S3 backup endpoint URL",
            ),
        ),
        migrations.AddField(
            model_name="applicationconfiguration",
            name="documents_backup_s3_querystring_auth",
            field=models.BooleanField(
                null=True,
                verbose_name="Sets whether S3 backup querystring auth is enabled",
            ),
        ),
        migrations.AddField(
            model_name="applicationconfiguration",
            name="documents_backup_s3_region_name",
            field=models.CharField(
                blank=True,
                max_length=255,
                null=True,
                verbose_name="Sets the S3 backup region",
            ),
        ),
        migrations.AddField(
            model_name="applicationconfiguration",
            name="documents_backup_s3_secret_access_key",
            field=models.CharField(
                blank=True,
                max_length=255,
                null=True,
                verbose_name="Sets the S3 backup secret access key",
            ),
        ),
        migrations.AddField(
            model_name="applicationconfiguration",
            name="documents_backup_s3_url_protocol",
            field=models.CharField(
                blank=True,
                max_length=32,
                null=True,
                verbose_name="Sets the S3 backup URL protocol",
            ),
        ),
        migrations.AddField(
            model_name="applicationconfiguration",
            name="documents_backup_s3_use_ssl",
            field=models.BooleanField(
                null=True,
                verbose_name="Sets whether S3 backup uses SSL",
            ),
        ),
    ]
