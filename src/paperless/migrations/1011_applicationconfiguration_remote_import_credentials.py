from django.db import migrations
from django.db import models


class Migration(migrations.Migration):
    dependencies = [
        ("paperless", "1010_merge_20260328_0832"),
    ]

    operations = [
        migrations.AddField(
            model_name="applicationconfiguration",
            name="remote_import_api_token",
            field=models.CharField(
                blank=True,
                max_length=255,
                null=True,
                verbose_name="Remote import API token",
            ),
        ),
        migrations.AddField(
            model_name="applicationconfiguration",
            name="remote_import_base_url",
            field=models.CharField(
                blank=True,
                max_length=512,
                null=True,
                verbose_name="Remote import base URL",
            ),
        ),
    ]
