from django.db import migrations
from django.db import models


class Migration(migrations.Migration):
    dependencies = [
        ("documents", "1082_alter_documenttype_retention_period_years_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="workflowtrigger",
            name="type",
            field=models.PositiveSmallIntegerField(
                choices=[
                    (1, "Consumption Started"),
                    (2, "Document Added"),
                    (3, "Document Updated"),
                    (4, "Scheduled"),
                    (5, "Manual"),
                ],
                default=1,
                verbose_name="Workflow Trigger Type",
            ),
        ),
        migrations.AlterField(
            model_name="workflowrun",
            name="type",
            field=models.PositiveSmallIntegerField(
                choices=[
                    (1, "Consumption Started"),
                    (2, "Document Added"),
                    (3, "Document Updated"),
                    (4, "Scheduled"),
                    (5, "Manual"),
                ],
                null=True,
                verbose_name="workflow trigger type",
            ),
        ),
    ]
