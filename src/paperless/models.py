from django.core.validators import FileExtensionValidator
from django.core.validators import MaxValueValidator
from django.core.validators import MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

DEFAULT_SINGLETON_INSTANCE_ID = 1


class AbstractSingletonModel(models.Model):
    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        """
        Always save as the first and only model
        """
        self.pk = DEFAULT_SINGLETON_INSTANCE_ID
        super().save(*args, **kwargs)


class OutputTypeChoices(models.TextChoices):
    """
    Matches to --output-type
    """

    PDF = ("pdf", _("pdf"))
    PDF_A = ("pdfa", _("pdfa"))
    PDF_A1 = ("pdfa-1", _("pdfa-1"))
    PDF_A2 = ("pdfa-2", _("pdfa-2"))
    PDF_A3 = ("pdfa-3", _("pdfa-3"))


class ModeChoices(models.TextChoices):
    """
    Matches to --skip-text, --redo-ocr, --force-ocr
    and our own custom setting
    """

    SKIP = ("skip", _("skip"))
    REDO = ("redo", _("redo"))
    FORCE = ("force", _("force"))
    SKIP_NO_ARCHIVE = ("skip_noarchive", _("skip_noarchive"))


class ArchiveFileChoices(models.TextChoices):
    """
    Settings to control creation of an archive PDF file
    """

    NEVER = ("never", _("never"))
    WITH_TEXT = ("with_text", _("with_text"))
    ALWAYS = ("always", _("always"))


class CleanChoices(models.TextChoices):
    """
    Matches to --clean, --clean-final
    """

    CLEAN = ("clean", _("clean"))
    FINAL = ("clean-final", _("clean-final"))
    NONE = ("none", _("none"))


class ColorConvertChoices(models.TextChoices):
    """
    Refer to the Ghostscript documentation for valid options
    """

    UNCHANGED = ("LeaveColorUnchanged", _("LeaveColorUnchanged"))
    RGB = ("RGB", _("RGB"))
    INDEPENDENT = ("UseDeviceIndependentColor", _("UseDeviceIndependentColor"))
    GRAY = ("Gray", _("Gray"))
    CMYK = ("CMYK", _("CMYK"))


class LLMEmbeddingBackend(models.TextChoices):
    OPENAI = ("openai", _("OpenAI"))
    HUGGINGFACE = ("huggingface", _("Huggingface"))


class LLMBackend(models.TextChoices):
    """
    Matches to --llm-backend
    """

    OPENAI = ("openai", _("OpenAI"))
    OLLAMA = ("ollama", _("Ollama"))


class DocumentStorageTypeChoices(models.TextChoices):
    LOCAL = ("local", _("local"))
    S3 = ("s3", _("s3"))


class S3StorageConfiguration(models.Model):
    name = models.CharField(
        verbose_name=_("Storage name"),
        max_length=255,
        unique=True,
    )

    prefix = models.CharField(
        verbose_name=_("Sets the S3 storage prefix"),
        null=True,
        blank=True,
        max_length=255,
    )

    bucket = models.CharField(
        verbose_name=_("Sets the S3 bucket"),
        max_length=255,
    )

    endpoint_url = models.CharField(
        verbose_name=_("Sets the S3 endpoint URL"),
        null=True,
        blank=True,
        max_length=512,
    )

    access_key_id = models.CharField(
        verbose_name=_("Sets the S3 access key ID"),
        null=True,
        blank=True,
        max_length=255,
    )

    secret_access_key = models.CharField(
        verbose_name=_("Sets the S3 secret access key"),
        null=True,
        blank=True,
        max_length=255,
    )

    region_name = models.CharField(
        verbose_name=_("Sets the S3 region"),
        null=True,
        blank=True,
        max_length=255,
    )

    default_acl = models.CharField(
        verbose_name=_("Sets the S3 default ACL"),
        null=True,
        blank=True,
        max_length=255,
    )

    custom_domain = models.CharField(
        verbose_name=_("Sets the S3 custom domain"),
        null=True,
        blank=True,
        max_length=255,
    )

    url_protocol = models.CharField(
        verbose_name=_("Sets the S3 URL protocol"),
        null=True,
        blank=True,
        max_length=32,
    )

    addressing_style = models.CharField(
        verbose_name=_("Sets the S3 addressing style"),
        null=True,
        blank=True,
        max_length=32,
    )

    querystring_auth = models.BooleanField(
        verbose_name=_("Sets whether S3 querystring auth is enabled"),
        null=True,
    )

    use_ssl = models.BooleanField(
        verbose_name=_("Sets whether S3 uses SSL"),
        null=True,
    )

    class Meta:
        ordering = ("name",)
        verbose_name = _("S3 storage configuration")
        verbose_name_plural = _("S3 storage configurations")

    def __str__(self) -> str:
        return self.name


class ApplicationConfiguration(AbstractSingletonModel):
    """
    Settings which are common across more than 1 parser
    """

    output_type = models.CharField(
        verbose_name=_("Sets the output PDF type"),
        null=True,
        blank=True,
        max_length=8,
        choices=OutputTypeChoices.choices,
    )

    """
    Settings for the Tesseract based OCR parser
    """

    pages = models.PositiveSmallIntegerField(
        verbose_name=_("Do OCR from page 1 to this value"),
        null=True,
        validators=[MinValueValidator(1)],
    )

    language = models.CharField(
        verbose_name=_("Do OCR using these languages"),
        null=True,
        blank=True,
        max_length=32,
    )

    mode = models.CharField(
        verbose_name=_("Sets the OCR mode"),
        null=True,
        blank=True,
        max_length=16,
        choices=ModeChoices.choices,
    )

    skip_archive_file = models.CharField(
        verbose_name=_("Controls the generation of an archive file"),
        null=True,
        blank=True,
        max_length=16,
        choices=ArchiveFileChoices.choices,
    )

    image_dpi = models.PositiveSmallIntegerField(
        verbose_name=_("Sets image DPI fallback value"),
        null=True,
        validators=[MinValueValidator(1)],
    )

    # Can't call it clean, that's a model method
    unpaper_clean = models.CharField(
        verbose_name=_("Controls the unpaper cleaning"),
        null=True,
        blank=True,
        max_length=16,
        choices=CleanChoices.choices,
    )

    deskew = models.BooleanField(verbose_name=_("Enables deskew"), null=True)

    rotate_pages = models.BooleanField(
        verbose_name=_("Enables page rotation"),
        null=True,
    )

    rotate_pages_threshold = models.FloatField(
        verbose_name=_("Sets the threshold for rotation of pages"),
        null=True,
        validators=[MinValueValidator(0.0)],
    )

    max_image_pixels = models.FloatField(
        verbose_name=_("Sets the maximum image size for decompression"),
        null=True,
        validators=[MinValueValidator(0.0)],
    )

    color_conversion_strategy = models.CharField(
        verbose_name=_("Sets the Ghostscript color conversion strategy"),
        blank=True,
        null=True,
        max_length=32,
        choices=ColorConvertChoices.choices,
    )

    user_args = models.JSONField(
        verbose_name=_("Adds additional user arguments for OCRMyPDF"),
        null=True,
    )

    """
    Settings for the Paperless application
    """

    app_title = models.CharField(
        verbose_name=_("Application title"),
        null=True,
        blank=True,
        max_length=48,
    )

    remote_import_base_url = models.CharField(
        verbose_name=_("Remote import base URL"),
        null=True,
        blank=True,
        max_length=512,
    )

    remote_import_api_token = models.CharField(
        verbose_name=_("Remote import API token"),
        null=True,
        blank=True,
        max_length=255,
    )

    app_logo = models.FileField(
        verbose_name=_("Application logo"),
        null=True,
        blank=True,
        validators=[
            FileExtensionValidator(allowed_extensions=["jpg", "png", "gif", "svg"]),
        ],
        upload_to="logo/",
    )

    documents_storage_type = models.CharField(
        verbose_name=_("Sets the document storage backend"),
        null=True,
        blank=True,
        max_length=16,
        choices=DocumentStorageTypeChoices.choices,
    )

    documents_storage_prefix = models.CharField(
        verbose_name=_("Sets the document storage prefix"),
        null=True,
        blank=True,
        max_length=255,
    )

    documents_s3_storage = models.ForeignKey(
        S3StorageConfiguration,
        verbose_name=_("Selects the S3 storage for documents"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="documents_primary_configs",
    )

    documents_s3_bucket = models.CharField(
        verbose_name=_("Sets the S3 bucket for documents"),
        null=True,
        blank=True,
        max_length=255,
    )

    documents_s3_endpoint_url = models.CharField(
        verbose_name=_("Sets the S3 endpoint URL"),
        null=True,
        blank=True,
        max_length=512,
    )

    documents_s3_access_key_id = models.CharField(
        verbose_name=_("Sets the S3 access key ID"),
        null=True,
        blank=True,
        max_length=255,
    )

    documents_s3_secret_access_key = models.CharField(
        verbose_name=_("Sets the S3 secret access key"),
        null=True,
        blank=True,
        max_length=255,
    )

    documents_s3_region_name = models.CharField(
        verbose_name=_("Sets the S3 region"),
        null=True,
        blank=True,
        max_length=255,
    )

    documents_s3_default_acl = models.CharField(
        verbose_name=_("Sets the S3 default ACL"),
        null=True,
        blank=True,
        max_length=255,
    )

    documents_s3_custom_domain = models.CharField(
        verbose_name=_("Sets the S3 custom domain"),
        null=True,
        blank=True,
        max_length=255,
    )

    documents_s3_url_protocol = models.CharField(
        verbose_name=_("Sets the S3 URL protocol"),
        null=True,
        blank=True,
        max_length=32,
    )

    documents_s3_addressing_style = models.CharField(
        verbose_name=_("Sets the S3 addressing style"),
        null=True,
        blank=True,
        max_length=32,
    )

    documents_s3_querystring_auth = models.BooleanField(
        verbose_name=_("Sets whether S3 querystring auth is enabled"),
        null=True,
    )

    documents_s3_use_ssl = models.BooleanField(
        verbose_name=_("Sets whether S3 uses SSL"),
        null=True,
    )

    documents_backup_prefix = models.CharField(
        verbose_name=_("Sets the S3 backup prefix for documents"),
        null=True,
        blank=True,
        max_length=255,
    )

    documents_backup_s3_storage = models.ForeignKey(
        S3StorageConfiguration,
        verbose_name=_("Selects the S3 storage for document backups"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="documents_backup_configs",
    )

    documents_backup_s3_bucket = models.CharField(
        verbose_name=_("Sets the S3 backup bucket for documents"),
        null=True,
        blank=True,
        max_length=255,
    )

    documents_backup_s3_endpoint_url = models.CharField(
        verbose_name=_("Sets the S3 backup endpoint URL"),
        null=True,
        blank=True,
        max_length=512,
    )

    documents_backup_s3_access_key_id = models.CharField(
        verbose_name=_("Sets the S3 backup access key ID"),
        null=True,
        blank=True,
        max_length=255,
    )

    documents_backup_s3_secret_access_key = models.CharField(
        verbose_name=_("Sets the S3 backup secret access key"),
        null=True,
        blank=True,
        max_length=255,
    )

    documents_backup_s3_region_name = models.CharField(
        verbose_name=_("Sets the S3 backup region"),
        null=True,
        blank=True,
        max_length=255,
    )

    documents_backup_s3_default_acl = models.CharField(
        verbose_name=_("Sets the S3 backup default ACL"),
        null=True,
        blank=True,
        max_length=255,
    )

    documents_backup_s3_custom_domain = models.CharField(
        verbose_name=_("Sets the S3 backup custom domain"),
        null=True,
        blank=True,
        max_length=255,
    )

    documents_backup_s3_url_protocol = models.CharField(
        verbose_name=_("Sets the S3 backup URL protocol"),
        null=True,
        blank=True,
        max_length=32,
    )

    documents_backup_s3_addressing_style = models.CharField(
        verbose_name=_("Sets the S3 backup addressing style"),
        null=True,
        blank=True,
        max_length=32,
    )

    documents_backup_s3_querystring_auth = models.BooleanField(
        verbose_name=_("Sets whether S3 backup querystring auth is enabled"),
        null=True,
    )

    documents_backup_s3_use_ssl = models.BooleanField(
        verbose_name=_("Sets whether S3 backup uses SSL"),
        null=True,
    )

    documents_backup_schedule_enabled = models.BooleanField(
        verbose_name=_("Enables automatic full backups"),
        null=True,
    )

    documents_backup_schedule_storage = models.ForeignKey(
        S3StorageConfiguration,
        verbose_name=_("Selects the S3 storage for automatic full backups"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="automatic_backup_configs",
    )

    documents_backup_schedule_frequency_days = models.PositiveIntegerField(
        verbose_name=_("Sets the automatic backup interval in days"),
        null=True,
        blank=True,
        validators=[MinValueValidator(1)],
    )

    documents_backup_schedule_hour = models.PositiveIntegerField(
        verbose_name=_("Sets the automatic backup hour"),
        null=True,
        blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(23)],
    )

    documents_backup_schedule_minute = models.PositiveIntegerField(
        verbose_name=_("Sets the automatic backup minute"),
        null=True,
        blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(59)],
    )

    documents_backup_schedule_retain_count = models.PositiveIntegerField(
        verbose_name=_("Sets how many automatic backups are retained"),
        null=True,
        blank=True,
        validators=[MinValueValidator(1)],
    )

    documents_backup_schedule_last_run = models.DateTimeField(
        verbose_name=_("Tracks the last automatic backup run"),
        null=True,
        blank=True,
    )

    documents_backup_schedule_jobs = models.JSONField(
        verbose_name=_("Stores automatic backup job definitions"),
        null=True,
        blank=True,
        default=list,
    )

    """
    Settings for the barcode scanner
    """

    # PAPERLESS_CONSUMER_ENABLE_BARCODES
    barcodes_enabled = models.BooleanField(
        verbose_name=_("Enables barcode scanning"),
        null=True,
    )

    # PAPERLESS_CONSUMER_BARCODE_TIFF_SUPPORT
    barcode_enable_tiff_support = models.BooleanField(
        verbose_name=_("Enables barcode TIFF support"),
        null=True,
    )

    # PAPERLESS_CONSUMER_BARCODE_STRING
    barcode_string = models.CharField(
        verbose_name=_("Sets the barcode string"),
        null=True,
        blank=True,
        max_length=32,
    )

    # PAPERLESS_CONSUMER_BARCODE_RETAIN_SPLIT_PAGES
    barcode_retain_split_pages = models.BooleanField(
        verbose_name=_("Retains split pages"),
        null=True,
    )

    # PAPERLESS_CONSUMER_ENABLE_ASN_BARCODE
    barcode_enable_asn = models.BooleanField(
        verbose_name=_("Enables ASN barcode"),
        null=True,
    )

    # PAPERLESS_CONSUMER_ASN_BARCODE_PREFIX
    barcode_asn_prefix = models.CharField(
        verbose_name=_("Sets the ASN barcode prefix"),
        null=True,
        blank=True,
        max_length=32,
    )

    # PAPERLESS_CONSUMER_BARCODE_UPSCALE
    barcode_upscale = models.FloatField(
        verbose_name=_("Sets the barcode upscale factor"),
        null=True,
        validators=[MinValueValidator(1.0)],
    )

    # PAPERLESS_CONSUMER_BARCODE_DPI
    barcode_dpi = models.PositiveSmallIntegerField(
        verbose_name=_("Sets the barcode DPI"),
        null=True,
        validators=[MinValueValidator(1)],
    )

    # PAPERLESS_CONSUMER_BARCODE_MAX_PAGES
    barcode_max_pages = models.PositiveSmallIntegerField(
        verbose_name=_("Sets the maximum pages for barcode"),
        null=True,
        validators=[MinValueValidator(1)],
    )

    # PAPERLESS_CONSUMER_ENABLE_TAG_BARCODE
    barcode_enable_tag = models.BooleanField(
        verbose_name=_("Enables tag barcode"),
        null=True,
    )

    # PAPERLESS_CONSUMER_TAG_BARCODE_MAPPING
    barcode_tag_mapping = models.JSONField(
        verbose_name=_("Sets the tag barcode mapping"),
        null=True,
    )

    # PAPERLESS_CONSUMER_TAG_BARCODE_SPLIT
    barcode_tag_split = models.BooleanField(
        verbose_name=_("Enables splitting on tag barcodes"),
        null=True,
    )

    """
    AI related settings
    """

    ai_enabled = models.BooleanField(
        verbose_name=_("Enables AI features"),
        null=True,
        default=False,
    )

    llm_embedding_backend = models.CharField(
        verbose_name=_("Sets the LLM embedding backend"),
        blank=True,
        null=True,
        max_length=128,
        choices=LLMEmbeddingBackend.choices,
    )

    llm_embedding_model = models.CharField(
        verbose_name=_("Sets the LLM embedding model"),
        blank=True,
        null=True,
        max_length=128,
    )

    llm_backend = models.CharField(
        verbose_name=_("Sets the LLM backend"),
        blank=True,
        null=True,
        max_length=128,
        choices=LLMBackend.choices,
    )

    llm_model = models.CharField(
        verbose_name=_("Sets the LLM model"),
        blank=True,
        null=True,
        max_length=128,
    )

    llm_api_key = models.CharField(
        verbose_name=_("Sets the LLM API key"),
        blank=True,
        null=True,
        max_length=1024,
    )

    llm_endpoint = models.CharField(
        verbose_name=_("Sets the LLM endpoint, optional"),
        blank=True,
        null=True,
        max_length=256,
    )

    class Meta:
        verbose_name = _("paperless application settings")

    def __str__(self) -> str:  # pragma: no cover
        return "ApplicationConfiguration"
