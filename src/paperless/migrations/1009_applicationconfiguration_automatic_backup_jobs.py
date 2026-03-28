from django.db import migrations
from django.db import models


class Migration(migrations.Migration):
    dependencies = [
        ("paperless", "1008_applicationconfiguration_automatic_backups"),
    ]

    operations = [
        migrations.AddField(
            model_name="applicationconfiguration",
            name="documents_backup_schedule_jobs",
            field=models.JSONField(
                blank=True,
                default=list,
                null=True,
                verbose_name="Stores automatic backup job definitions",
            ),
        ),
    ]
