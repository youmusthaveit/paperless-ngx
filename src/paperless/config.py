import dataclasses
import json

from django.conf import settings
from django.db import OperationalError
from django.db import ProgrammingError

from paperless.models import ApplicationConfiguration
from paperless.models import DocumentStorageTypeChoices


@dataclasses.dataclass
class BaseConfig:
    """
    Almost all parsers care about the chosen PDF output format
    """

    @staticmethod
    def _get_config_instance() -> ApplicationConfiguration:
        try:
            app_config = ApplicationConfiguration.objects.all().first()
            # Workaround for a test where the migration hasn't run to create the single model
            if app_config is None:
                ApplicationConfiguration.objects.create()
                app_config = ApplicationConfiguration.objects.all().first()
            return app_config
        except (OperationalError, ProgrammingError):
            return ApplicationConfiguration()


@dataclasses.dataclass
class OutputTypeConfig(BaseConfig):
    """
    Almost all parsers care about the chosen PDF output format
    """

    output_type: str = dataclasses.field(init=False)

    def __post_init__(self) -> None:
        app_config = self._get_config_instance()

        self.output_type = app_config.output_type or settings.OCR_OUTPUT_TYPE


@dataclasses.dataclass
class OcrConfig(OutputTypeConfig):
    """
    Specific settings for the Tesseract based parser.  Options generally
    correspond almost directly to the OCRMyPDF options
    """

    pages: int | None = dataclasses.field(init=False)
    language: str = dataclasses.field(init=False)
    mode: str = dataclasses.field(init=False)
    skip_archive_file: str = dataclasses.field(init=False)
    image_dpi: int | None = dataclasses.field(init=False)
    clean: str = dataclasses.field(init=False)
    deskew: bool = dataclasses.field(init=False)
    rotate: bool = dataclasses.field(init=False)
    rotate_threshold: float = dataclasses.field(init=False)
    max_image_pixel: float | None = dataclasses.field(init=False)
    color_conversion_strategy: str = dataclasses.field(init=False)
    user_args: dict[str, str] | None = dataclasses.field(init=False)

    def __post_init__(self) -> None:
        super().__post_init__()

        app_config = self._get_config_instance()

        self.pages = app_config.pages or settings.OCR_PAGES
        self.language = app_config.language or settings.OCR_LANGUAGE
        self.mode = app_config.mode or settings.OCR_MODE
        self.skip_archive_file = (
            app_config.skip_archive_file or settings.OCR_SKIP_ARCHIVE_FILE
        )
        self.image_dpi = app_config.image_dpi or settings.OCR_IMAGE_DPI
        self.clean = app_config.unpaper_clean or settings.OCR_CLEAN
        self.deskew = (
            app_config.deskew if app_config.deskew is not None else settings.OCR_DESKEW
        )
        self.rotate = (
            app_config.rotate_pages
            if app_config.rotate_pages is not None
            else settings.OCR_ROTATE_PAGES
        )
        self.rotate_threshold = (
            app_config.rotate_pages_threshold or settings.OCR_ROTATE_PAGES_THRESHOLD
        )
        self.max_image_pixel = (
            app_config.max_image_pixels or settings.OCR_MAX_IMAGE_PIXELS
        )
        self.color_conversion_strategy = (
            app_config.color_conversion_strategy
            or settings.OCR_COLOR_CONVERSION_STRATEGY
        )

        user_args = None
        if app_config.user_args:
            user_args = app_config.user_args
        elif settings.OCR_USER_ARGS is not None:  # pragma: no cover
            try:
                user_args = json.loads(settings.OCR_USER_ARGS)
            except json.JSONDecodeError:
                user_args = {}
        self.user_args = user_args


@dataclasses.dataclass
class BarcodeConfig(BaseConfig):
    """
    Barcodes settings
    """

    barcodes_enabled: bool = dataclasses.field(init=False)
    barcode_enable_tiff_support: bool = dataclasses.field(init=False)
    barcode_string: str = dataclasses.field(init=False)
    barcode_retain_split_pages: bool = dataclasses.field(init=False)
    barcode_enable_asn: bool = dataclasses.field(init=False)
    barcode_asn_prefix: str = dataclasses.field(init=False)
    barcode_upscale: float = dataclasses.field(init=False)
    barcode_dpi: int = dataclasses.field(init=False)
    barcode_max_pages: int = dataclasses.field(init=False)
    barcode_enable_tag: bool = dataclasses.field(init=False)
    barcode_tag_mapping: dict[str, str] = dataclasses.field(init=False)

    def __post_init__(self) -> None:
        app_config = self._get_config_instance()

        self.barcodes_enabled = (
            app_config.barcodes_enabled or settings.CONSUMER_ENABLE_BARCODES
        )
        self.barcode_enable_tiff_support = (
            app_config.barcode_enable_tiff_support
            or settings.CONSUMER_BARCODE_TIFF_SUPPORT
        )
        self.barcode_string = (
            app_config.barcode_string or settings.CONSUMER_BARCODE_STRING
        )
        self.barcode_retain_split_pages = (
            app_config.barcode_retain_split_pages
            or settings.CONSUMER_BARCODE_RETAIN_SPLIT_PAGES
        )
        self.barcode_enable_asn = (
            app_config.barcode_enable_asn or settings.CONSUMER_ENABLE_ASN_BARCODE
        )
        self.barcode_asn_prefix = (
            app_config.barcode_asn_prefix or settings.CONSUMER_ASN_BARCODE_PREFIX
        )
        self.barcode_upscale = (
            app_config.barcode_upscale or settings.CONSUMER_BARCODE_UPSCALE
        )
        self.barcode_dpi = app_config.barcode_dpi or settings.CONSUMER_BARCODE_DPI
        self.barcode_max_pages = (
            app_config.barcode_max_pages or settings.CONSUMER_BARCODE_MAX_PAGES
        )
        self.barcode_enable_tag = (
            app_config.barcode_enable_tag or settings.CONSUMER_ENABLE_TAG_BARCODE
        )
        self.barcode_tag_mapping = (
            app_config.barcode_tag_mapping or settings.CONSUMER_TAG_BARCODE_MAPPING
        )


@dataclasses.dataclass
class GeneralConfig(BaseConfig):
    """
    General application settings that require global scope
    """

    app_title: str = dataclasses.field(init=False)
    app_logo: str = dataclasses.field(init=False)

    def __post_init__(self) -> None:
        app_config = self._get_config_instance()

        self.app_title = app_config.app_title or None
        self.app_logo = app_config.app_logo.url if app_config.app_logo else None


@dataclasses.dataclass
class DocumentStorageConfig(BaseConfig):
    overrides: dict[str, object] | None = None
    app_config: ApplicationConfiguration | None = None
    storage_type: str = dataclasses.field(init=False)
    prefix: str = dataclasses.field(init=False)
    s3_bucket: str | None = dataclasses.field(init=False)
    s3_endpoint_url: str | None = dataclasses.field(init=False)
    s3_access_key_id: str | None = dataclasses.field(init=False)
    s3_secret_access_key: str | None = dataclasses.field(init=False)
    s3_region_name: str | None = dataclasses.field(init=False)
    s3_default_acl: str | None = dataclasses.field(init=False)
    s3_custom_domain: str | None = dataclasses.field(init=False)
    s3_url_protocol: str = dataclasses.field(init=False)
    s3_addressing_style: str | None = dataclasses.field(init=False)
    s3_querystring_auth: bool = dataclasses.field(init=False)
    s3_use_ssl: bool = dataclasses.field(init=False)

    def __post_init__(self) -> None:
        app_config = self.app_config or self._get_config_instance()
        overrides = self.overrides or {}

        def get_value(key: str, current_value):
            return overrides.get(key, current_value)

        self.storage_type = (
            get_value("documents_storage_type", app_config.documents_storage_type)
            or settings.DOCUMENTS_STORAGE_TYPE
            or DocumentStorageTypeChoices.LOCAL
        )
        self.prefix = (
            get_value(
                "documents_storage_prefix",
                app_config.documents_storage_prefix,
            )
            or settings.DOCUMENTS_STORAGE_PREFIX
            or "documents"
        ).strip("/")
        self.s3_bucket = (
            get_value("documents_s3_bucket", app_config.documents_s3_bucket)
            or settings.DOCUMENTS_S3_BUCKET
        )
        self.s3_endpoint_url = (
            get_value(
                "documents_s3_endpoint_url",
                app_config.documents_s3_endpoint_url,
            )
            or settings.DOCUMENTS_S3_ENDPOINT_URL
        )
        self.s3_access_key_id = (
            get_value(
                "documents_s3_access_key_id",
                app_config.documents_s3_access_key_id,
            )
            or settings.DOCUMENTS_S3_ACCESS_KEY_ID
        )
        self.s3_secret_access_key = (
            get_value(
                "documents_s3_secret_access_key",
                app_config.documents_s3_secret_access_key,
            )
            or settings.DOCUMENTS_S3_SECRET_ACCESS_KEY
        )
        self.s3_region_name = (
            get_value(
                "documents_s3_region_name",
                app_config.documents_s3_region_name,
            )
            or settings.DOCUMENTS_S3_REGION_NAME
        )
        self.s3_default_acl = (
            get_value(
                "documents_s3_default_acl",
                app_config.documents_s3_default_acl,
            )
            or settings.DOCUMENTS_S3_DEFAULT_ACL
        )
        self.s3_custom_domain = (
            get_value(
                "documents_s3_custom_domain",
                app_config.documents_s3_custom_domain,
            )
            or settings.DOCUMENTS_S3_CUSTOM_DOMAIN
        )
        self.s3_url_protocol = (
            get_value(
                "documents_s3_url_protocol",
                app_config.documents_s3_url_protocol,
            )
            or settings.DOCUMENTS_S3_URL_PROTOCOL
            or "https:"
        )
        self.s3_addressing_style = (
            get_value(
                "documents_s3_addressing_style",
                app_config.documents_s3_addressing_style,
            )
            or settings.DOCUMENTS_S3_ADDRESSING_STYLE
        )
        self.s3_querystring_auth = (
            get_value(
                "documents_s3_querystring_auth",
                app_config.documents_s3_querystring_auth,
            )
            if get_value(
                "documents_s3_querystring_auth",
                app_config.documents_s3_querystring_auth,
            )
            is not None
            else settings.DOCUMENTS_S3_QUERYSTRING_AUTH
        )
        self.s3_use_ssl = (
            get_value(
                "documents_s3_use_ssl",
                app_config.documents_s3_use_ssl,
            )
            if get_value(
                "documents_s3_use_ssl",
                app_config.documents_s3_use_ssl,
            )
            is not None
            else settings.DOCUMENTS_S3_USE_SSL
        )
