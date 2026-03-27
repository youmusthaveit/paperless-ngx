from django.db import migrations
from django.db import models


class Migration(migrations.Migration):
    dependencies = [
        ("documents", "1075_workflowaction_order"),
    ]

    operations = [
        migrations.AlterField(
            model_name="paperlesstask",
            name="task_name",
            field=models.CharField(
                choices=[
                    ("consume_file", "Consume File"),
                    ("import_file", "Import File"),
                    ("train_classifier", "Train Classifier"),
                    ("check_sanity", "Check Sanity"),
                    ("index_optimize", "Index Optimize"),
                    ("export_s3_storage", "Export S3 Storage"),
                    ("import_s3_storage", "Import S3 Storage"),
                ],
                help_text="Name of the task that was run",
                max_length=255,
                null=True,
                verbose_name="Task Name",
            ),
        ),
    ]
