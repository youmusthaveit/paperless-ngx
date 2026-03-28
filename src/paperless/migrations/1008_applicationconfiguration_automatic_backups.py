import django.core.validators
import django.db.models.deletion
from django.db import migrations
from django.db import models


class Migration(migrations.Migration):
    dependencies = [
        ("paperless", "0009_s3storageconfiguration_prefix_optional"),
    ]

    operations = [
        migrations.AddField(
            model_name="applicationconfiguration",
            name="documents_backup_schedule_enabled",
            field=models.BooleanField(
                null=True,
                verbose_name="Enables automatic full backups",
            ),
        ),
        migrations.AddField(
            model_name="applicationconfiguration",
            name="documents_backup_schedule_frequency_days",
            field=models.PositiveIntegerField(
                blank=True,
                null=True,
                validators=[django.core.validators.MinValueValidator(1)],
                verbose_name="Sets the automatic backup interval in days",
            ),
        ),
        migrations.AddField(
            model_name="applicationconfiguration",
            name="documents_backup_schedule_hour",
            field=models.PositiveIntegerField(
                blank=True,
                null=True,
                validators=[
                    django.core.validators.MinValueValidator(0),
                    django.core.validators.MaxValueValidator(23),
                ],
                verbose_name="Sets the automatic backup hour",
            ),
        ),
        migrations.AddField(
            model_name="applicationconfiguration",
            name="documents_backup_schedule_last_run",
            field=models.DateTimeField(
                blank=True,
                null=True,
                verbose_name="Tracks the last automatic backup run",
            ),
        ),
        migrations.AddField(
            model_name="applicationconfiguration",
            name="documents_backup_schedule_minute",
            field=models.PositiveIntegerField(
                blank=True,
                null=True,
                validators=[
                    django.core.validators.MinValueValidator(0),
                    django.core.validators.MaxValueValidator(59),
                ],
                verbose_name="Sets the automatic backup minute",
            ),
        ),
        migrations.AddField(
            model_name="applicationconfiguration",
            name="documents_backup_schedule_retain_count",
            field=models.PositiveIntegerField(
                blank=True,
                null=True,
                validators=[django.core.validators.MinValueValidator(1)],
                verbose_name="Sets how many automatic backups are retained",
            ),
        ),
        migrations.AddField(
            model_name="applicationconfiguration",
            name="documents_backup_schedule_storage",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="automatic_backup_configs",
                to="paperless.s3storageconfiguration",
                verbose_name="Selects the S3 storage for automatic full backups",
            ),
        ),
    ]
