from django.db import migrations
from django.db import models


class Migration(migrations.Migration):
    dependencies = [
        ("paperless", "0008_s3storageconfiguration_prefix"),
    ]

    operations = [
        migrations.AlterField(
            model_name="s3storageconfiguration",
            name="prefix",
            field=models.CharField(
                blank=True,
                max_length=255,
                null=True,
                verbose_name="Sets the S3 storage prefix",
            ),
        ),
    ]
