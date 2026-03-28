from django.db import migrations
from django.db import models


class Migration(migrations.Migration):
    dependencies = [
        ("documents", "1079_documenttype_custom_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="documenttype",
            name="enable_xrechnung_import",
            field=models.BooleanField(
                default=False,
                help_text="Automatically assign this document type to detected XRechnung XML imports.",
                verbose_name="enable XRechnung import",
            ),
        ),
        migrations.AddField(
            model_name="documenttype",
            name="xrechnung_correspondent_field",
            field=models.CharField(
                blank=True,
                help_text="Optional XRechnung field used to populate the correspondent.",
                max_length=64,
                null=True,
                verbose_name="XRechnung correspondent field",
            ),
        ),
        migrations.AddField(
            model_name="documenttype",
            name="xrechnung_custom_field_mappings",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="Maps XRechnung fields to custom fields during import.",
                verbose_name="XRechnung custom field mappings",
            ),
        ),
    ]
