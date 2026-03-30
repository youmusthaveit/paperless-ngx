from django.db import migrations
from django.db import models


class Migration(migrations.Migration):
    dependencies = [
        ("documents", "1080_documenttype_xrechnung_settings"),
    ]

    operations = [
        migrations.AddField(
            model_name="document",
            name="delete_allowed_at",
            field=models.DateField(
                blank=True,
                db_index=True,
                help_text="Date from which this document may be moved to the trash.",
                null=True,
                verbose_name="delete allowed at",
            ),
        ),
        migrations.AddField(
            model_name="documenttype",
            name="retention_period_years",
            field=models.PositiveIntegerField(
                blank=True,
                help_text="Optional retention period in years. Documents of this type cannot be deleted before the calculated date.",
                null=True,
                verbose_name="retention period in years",
            ),
        ),
    ]
