"""
Built-in XRechnung parser.

Handles XRechnung XML invoices by extracting searchable text locally and
rendering a human-readable PDF via Gotenberg for display in the frontend.
The original XML document remains untouched and is stored as the source file.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
import time as time_module
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date
from datetime import datetime
from datetime import time
from html import escape
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Self

from django.conf import settings
from django.utils import timezone
from gotenberg_client import GotenbergClient
from gotenberg_client.constants import A4
from gotenberg_client.options import Measurement
from gotenberg_client.options import MeasurementUnitType
from gotenberg_client.options import PageMarginsType
from gotenberg_client.options import PdfAFormat

from documents.parsers import ParseError
from documents.parsers import make_thumbnail_from_pdf
from paperless.models import OutputTypeChoices
from paperless.version import __full_version_str__

if TYPE_CHECKING:
    from types import TracebackType

    from paperless.parsers import MetadataEntry
    from paperless.parsers import ParserContext

logger = logging.getLogger("paperless.parsing.xrechnung")

_SUPPORTED_MIME_TYPES: dict[str, str] = {
    "application/xml": ".xml",
    "text/xml": ".xml",
    "application/pdf": ".pdf",
}

XRECHNUNG_SOURCE_FIELDS: tuple[str, ...] = (
    "profile",
    "invoice_number",
    "invoice_type_code",
    "issue_date",
    "due_amount",
    "grand_total",
    "tax_total",
    "currency",
    "buyer_reference",
    "payment_reference",
    "payment_terms",
    "seller_name",
    "seller_identifier",
    "seller_tax_identifier",
    "seller_email",
    "buyer_name",
    "buyer_identifier",
    "buyer_tax_identifier",
    "buyer_email",
)


@dataclass(slots=True)
class InvoiceParty:
    name: str | None = None
    identifier: str | None = None
    tax_identifier: str | None = None
    email: str | None = None
    street: str | None = None
    postcode: str | None = None
    city: str | None = None
    country: str | None = None


@dataclass(slots=True)
class InvoiceLine:
    line_id: str | None
    name: str | None
    description: str | None
    quantity: str | None
    unit_code: str | None
    unit_price: str | None
    total_amount: str | None
    tax_rate: str | None


@dataclass(slots=True)
class InvoiceData:
    profile: str | None
    invoice_number: str | None
    invoice_type_code: str | None
    issue_date: date | None
    due_amount: str | None
    grand_total: str | None
    tax_total: str | None
    currency: str | None
    buyer_reference: str | None
    payment_reference: str | None
    payment_terms: str | None
    seller: InvoiceParty
    buyer: InvoiceParty
    notes: list[str]
    lines: list[InvoiceLine]
    raw_text: str


def _local_name(tag: str) -> str:
    return tag.split("}", 1)[1] if "}" in tag else tag


def _normalize_space(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = " ".join(value.split())
    return normalized or None


def _text(element: ET.Element | None) -> str | None:
    if element is None:
        return None
    return _normalize_space("".join(element.itertext()))


def _iter_elements(root: ET.Element, local_name: str) -> list[ET.Element]:
    return [
        element for element in root.iter() if _local_name(element.tag) == local_name
    ]


def _first_element(root: ET.Element, local_name: str) -> ET.Element | None:
    return next(iter(_iter_elements(root, local_name)), None)


def _first_text(root: ET.Element, local_name: str) -> str | None:
    return _text(_first_element(root, local_name))


def _all_texts(root: ET.Element, local_name: str) -> list[str]:
    return [
        _text(element) for element in _iter_elements(root, local_name) if _text(element)
    ]


def _descendant_text(element: ET.Element | None, local_name: str) -> str | None:
    if element is None:
        return None
    return _first_text(element, local_name)


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    normalized = value.strip()
    for fmt in ("%Y%m%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(normalized, fmt).date()
        except ValueError:
            continue
    return None


def _looks_like_xrechnung(root: ET.Element) -> bool:
    root_name = _local_name(root.tag)
    if root_name != "CrossIndustryInvoice":
        return False

    indicators = [
        *_all_texts(root, "GuidelineSpecifiedDocumentContextParameter"),
        *_all_texts(root, "CustomizationID"),
        *_all_texts(root, "CustomisationID"),
        *_all_texts(root, "ProfileID"),
    ]
    indicator_blob = " ".join(indicators).lower()
    if "xrechnung" in indicator_blob:
        return True

    return "urn:cen.eu:en16931:2017" in indicator_blob


def _parse_party(element: ET.Element | None) -> InvoiceParty:
    tax_ids = _all_texts(element, "ID") if element is not None else []
    return InvoiceParty(
        name=_descendant_text(element, "Name"),
        identifier=_descendant_text(element, "ID"),
        tax_identifier=tax_ids[1]
        if len(tax_ids) > 1
        else (tax_ids[0] if tax_ids else None),
        email=_descendant_text(element, "URIID"),
        street=_descendant_text(element, "LineOne"),
        postcode=_descendant_text(element, "PostcodeCode"),
        city=_descendant_text(element, "CityName"),
        country=_descendant_text(element, "CountryID"),
    )


def _parse_cii_invoice(root: ET.Element) -> InvoiceData:
    seller = _parse_party(_first_element(root, "SellerTradeParty"))
    buyer = _parse_party(_first_element(root, "BuyerTradeParty"))

    lines: list[InvoiceLine] = []
    for line in _iter_elements(root, "IncludedSupplyChainTradeLineItem"):
        quantity_element = _first_element(line, "BilledQuantity")
        lines.append(
            InvoiceLine(
                line_id=_descendant_text(line, "LineID"),
                name=_descendant_text(line, "Name"),
                description=_descendant_text(line, "Description"),
                quantity=_text(quantity_element),
                unit_code=quantity_element.get("unitCode")
                if quantity_element is not None
                else None,
                unit_price=_descendant_text(line, "ChargeAmount"),
                total_amount=_descendant_text(line, "LineTotalAmount"),
                tax_rate=_descendant_text(line, "RateApplicablePercent"),
            ),
        )

    issue_date = None
    for element in _iter_elements(root, "IssueDateTime"):
        issue_date = _parse_date(_descendant_text(element, "DateTimeString"))
        if issue_date is not None:
            break

    raw_text = "\n".join(
        text for text in (_normalize_space(text) for text in root.itertext()) if text
    )

    profile = None
    guideline = _first_element(root, "GuidelineSpecifiedDocumentContextParameter")
    if guideline is not None:
        profile = _descendant_text(guideline, "ID")

    return InvoiceData(
        profile=profile,
        invoice_number=_descendant_text(
            _first_element(root, "ExchangedDocument"),
            "ID",
        ),
        invoice_type_code=_descendant_text(
            _first_element(root, "ExchangedDocument"),
            "TypeCode",
        ),
        issue_date=issue_date,
        due_amount=_first_text(root, "DuePayableAmount"),
        grand_total=_first_text(root, "GrandTotalAmount"),
        tax_total=_first_text(root, "TaxTotalAmount"),
        currency=_first_text(root, "InvoiceCurrencyCode"),
        buyer_reference=_first_text(root, "BuyerReference"),
        payment_reference=_first_text(root, "PaymentReference"),
        payment_terms=_descendant_text(
            _first_element(root, "SpecifiedTradePaymentTerms"),
            "Description",
        ),
        seller=seller,
        buyer=buyer,
        notes=_all_texts(root, "Content"),
        lines=lines,
        raw_text=raw_text,
    )


def _parse_invoice_root(document_path: Path, mime_type: str) -> ET.Element:
    if mime_type == "application/pdf":
        _, xml_bytes = extract_embedded_einvoice_xml(document_path)
        try:
            return ET.fromstring(xml_bytes)
        except ET.ParseError as err:
            raise ParseError(f"Invalid XML document: {err}") from err

    try:
        root = ET.parse(document_path).getroot()
    except ET.ParseError as err:
        raise ParseError(f"Invalid XML document: {err}") from err

    return root


def extract_embedded_einvoice_xml(document_path: Path) -> tuple[str, bytes]:
    try:
        import pikepdf
    except ImportError as err:
        raise ParseError(
            "PDF E-Rechnung support requires pikepdf to be installed.",
        ) from err

    try:
        with pikepdf.open(document_path) as pdf:
            for attachment in pdf.attachments.values():
                filename = attachment.filename or "invoice.xml"
                if not filename.lower().endswith(".xml"):
                    continue
                return filename, attachment.get_file().read_bytes()
    except pikepdf.PdfError as err:
        raise ParseError(f"Invalid PDF document: {err}") from err

    raise ParseError("PDF document does not contain a supported embedded XML invoice.")


def _parse_invoice(document_path: Path, mime_type: str) -> InvoiceData:
    root = _parse_invoice_root(document_path, mime_type)

    if not _looks_like_xrechnung(root):
        raise ParseError("Document is not a supported XRechnung or E-Rechnung invoice.")

    return _parse_cii_invoice(root)


def parse_xrechnung_invoice(document_path: Path) -> InvoiceData:
    return _parse_invoice(document_path, "application/xml")


def get_xrechnung_source_value(
    invoice: InvoiceData,
    source_field: str,
) -> str | date | None:
    values: dict[str, str | date | None] = {
        "profile": invoice.profile,
        "invoice_number": invoice.invoice_number,
        "invoice_type_code": invoice.invoice_type_code,
        "issue_date": invoice.issue_date,
        "due_amount": invoice.due_amount,
        "grand_total": invoice.grand_total,
        "tax_total": invoice.tax_total,
        "currency": invoice.currency,
        "buyer_reference": invoice.buyer_reference,
        "payment_reference": invoice.payment_reference,
        "payment_terms": invoice.payment_terms,
        "seller_name": invoice.seller.name,
        "seller_identifier": invoice.seller.identifier,
        "seller_tax_identifier": invoice.seller.tax_identifier,
        "seller_email": invoice.seller.email,
        "buyer_name": invoice.buyer.name,
        "buyer_identifier": invoice.buyer.identifier,
        "buyer_tax_identifier": invoice.buyer.tax_identifier,
        "buyer_email": invoice.buyer.email,
    }
    return values.get(source_field)


class XRechnungDocumentParser:
    name: str = "Paperless-ngx XRechnung Parser"
    version: str = __full_version_str__
    author: str = "Paperless-ngx Contributors"
    url: str = "https://github.com/paperless-ngx/paperless-ngx"

    @classmethod
    def supported_mime_types(cls) -> dict[str, str]:
        return _SUPPORTED_MIME_TYPES

    @classmethod
    def score(
        cls,
        mime_type: str,
        filename: str,
        path: Path | None = None,
    ) -> int | None:
        if mime_type not in _SUPPORTED_MIME_TYPES:
            return None

        if path is None:
            return None

        try:
            root = _parse_invoice_root(path, mime_type)
        except ParseError:
            return None

        return 30 if _looks_like_xrechnung(root) else None

    @property
    def can_produce_archive(self) -> bool:
        return False

    @property
    def requires_pdf_rendition(self) -> bool:
        return True

    def __init__(self, logging_group: object = None) -> None:
        settings.SCRATCH_DIR.mkdir(parents=True, exist_ok=True)
        self._tempdir = Path(
            tempfile.mkdtemp(prefix="paperless-", dir=settings.SCRATCH_DIR),
        )
        self._archive_path: Path | None = None
        self._date: datetime | None = None
        self._invoice: InvoiceData | None = None
        self._text: str | None = None

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        logger.debug("Cleaning up temporary directory %s", self._tempdir)
        shutil.rmtree(self._tempdir, ignore_errors=True)

    def configure(self, context: ParserContext) -> None:
        pass

    def parse(
        self,
        document_path: Path,
        mime_type: str,
        *,
        produce_archive: bool = True,
    ) -> None:
        self._invoice = _parse_invoice(document_path, mime_type)
        self._text = self._render_search_text(self._invoice)

        if self._invoice.issue_date is not None:
            self._date = timezone.make_aware(
                datetime.combine(self._invoice.issue_date, time.min),
            )

        if mime_type == "application/pdf":
            self._archive_path = document_path
        else:
            self._archive_path = self._generate_pdf(self._invoice)

    def get_text(self) -> str | None:
        return self._text

    def get_date(self) -> datetime | None:
        return self._date

    def get_archive_path(self) -> Path | None:
        return self._archive_path

    def get_invoice(self) -> InvoiceData | None:
        return self._invoice

    def get_thumbnail(self, document_path: Path, mime_type: str) -> Path:
        if self._archive_path is None:
            raise ParseError("XRechnung PDF rendition has not been generated yet.")
        return make_thumbnail_from_pdf(self._archive_path, self._tempdir)

    def get_page_count(
        self,
        document_path: Path,
        mime_type: str,
    ) -> int | None:
        if self._archive_path is not None:
            from paperless.parsers.utils import get_page_count_for_pdf

            return get_page_count_for_pdf(self._archive_path, log=logger)
        return None

    def extract_metadata(
        self,
        document_path: Path,
        mime_type: str,
    ) -> list[MetadataEntry]:
        try:
            invoice = _parse_invoice(document_path, mime_type)
        except ParseError as err:
            logger.warning(
                "Error while extracting XRechnung metadata for %s: %s",
                document_path,
                err,
            )
            return []

        values = {
            field_name: (value.isoformat() if isinstance(value, date) else value)
            for field_name in XRECHNUNG_SOURCE_FIELDS
            if (value := get_xrechnung_source_value(invoice, field_name)) is not None
        }

        return [
            {
                "namespace": "urn:paperless:xrechnung",
                "prefix": "xrechnung",
                "key": key,
                "value": value,
            }
            for key, value in values.items()
            if value
        ]

    def _render_search_text(self, invoice: InvoiceData) -> str:
        parts = [
            "XRechnung",
            invoice.invoice_number,
            invoice.invoice_type_code,
            invoice.issue_date.isoformat() if invoice.issue_date else None,
            invoice.currency,
            invoice.grand_total,
            invoice.due_amount,
            invoice.buyer_reference,
            invoice.payment_reference,
            invoice.seller.name,
            invoice.buyer.name,
            invoice.payment_terms,
            *invoice.notes,
            *(line.name for line in invoice.lines if line.name),
            *(line.description for line in invoice.lines if line.description),
            invoice.raw_text,
        ]
        return "\n".join(part for part in parts if part)

    def _generate_pdf(self, invoice: InvoiceData) -> Path:
        html_file = self._tempdir / "index.html"
        html_file.write_text(self._render_html(invoice), encoding="utf-8")

        response = None
        last_error: Exception | None = None
        for attempt in range(2):
            with (
                GotenbergClient(
                    host=settings.TIKA_GOTENBERG_ENDPOINT,
                    timeout=settings.CELERY_TASK_TIME_LIMIT,
                ) as client,
                client.chromium.html_to_pdf() as route,
            ):
                pdf_a_format = self._settings_to_gotenberg_pdfa()
                if pdf_a_format is not None:
                    route.pdf_format(pdf_a_format)

                try:
                    response = (
                        route.index(html_file)
                        .margins(
                            PageMarginsType(
                                top=Measurement(0.2, MeasurementUnitType.Inches),
                                bottom=Measurement(0.2, MeasurementUnitType.Inches),
                                left=Measurement(0.2, MeasurementUnitType.Inches),
                                right=Measurement(0.2, MeasurementUnitType.Inches),
                            ),
                        )
                        .size(A4)
                        .scale(1.0)
                        .run()
                    )
                    break
                except Exception as err:
                    last_error = err
                    if attempt == 0:
                        logger.warning(
                            "XRechnung PDF generation failed on first attempt, retrying once: %s",
                            err,
                        )
                        time_module.sleep(2)
                        continue
                    raise ParseError(
                        f"Error while converting XRechnung to PDF: {err}",
                    ) from err

        if response is None:
            raise ParseError(
                f"Error while converting XRechnung to PDF: {last_error}",
            )

        archive_path = self._tempdir / "xrechnung.pdf"
        archive_path.write_bytes(response.content)
        return archive_path

    def _render_html(self, invoice: InvoiceData) -> str:
        def render_party(title: str, party: InvoiceParty) -> str:
            rows = [
                ("Name", party.name),
                ("ID", party.identifier),
                ("Steuer-ID", party.tax_identifier),
                ("E-Mail", party.email),
                ("Strasse", party.street),
                ("PLZ", party.postcode),
                ("Ort", party.city),
                ("Land", party.country),
            ]
            content = "".join(
                f"<tr><th>{escape(label)}</th><td>{escape(value)}</td></tr>"
                for label, value in rows
                if value
            )
            return f"""
            <section class="card">
              <h2>{escape(title)}</h2>
              <table>{content}</table>
            </section>
            """

        line_rows = "".join(
            f"""
            <tr>
              <td>{escape(line.line_id or "")}</td>
              <td>{escape(line.name or "")}</td>
              <td>{escape(line.description or "")}</td>
              <td>{escape(line.quantity or "")}</td>
              <td>{escape(line.unit_code or "")}</td>
              <td>{escape(line.unit_price or "")}</td>
              <td>{escape(line.total_amount or "")}</td>
              <td>{escape(line.tax_rate or "")}</td>
            </tr>
            """
            for line in invoice.lines
        )

        notes = "".join(f"<li>{escape(note)}</li>" for note in invoice.notes)

        issue_date = invoice.issue_date.isoformat() if invoice.issue_date else ""
        return f"""<!doctype html>
<html lang="de">
  <head>
    <meta charset="utf-8">
    <title>XRechnung {escape(invoice.invoice_number or "")}</title>
    <style>
      :root {{
        color-scheme: light;
        font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
        color: #1c2431;
      }}
      body {{
        margin: 0;
        background: #f3f6fa;
      }}
      main {{
        padding: 28px;
      }}
      .hero {{
        background: linear-gradient(135deg, #113355, #2b6a88);
        color: white;
        padding: 24px 28px;
        border-radius: 16px;
        margin-bottom: 20px;
      }}
      .hero h1 {{
        margin: 0 0 12px;
        font-size: 28px;
      }}
      .grid {{
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 16px;
      }}
      .card {{
        background: white;
        border-radius: 14px;
        padding: 18px;
        box-shadow: 0 6px 20px rgba(17, 51, 85, 0.08);
        margin-bottom: 16px;
      }}
      h2 {{
        margin: 0 0 12px;
        font-size: 18px;
      }}
      table {{
        width: 100%;
        border-collapse: collapse;
      }}
      th, td {{
        text-align: left;
        padding: 8px 10px;
        border-bottom: 1px solid #dfe7ef;
        vertical-align: top;
      }}
      th {{
        width: 32%;
        color: #486072;
        font-weight: 600;
      }}
      .line-table th {{
        width: auto;
        background: #eef4f8;
      }}
      .line-table td, .line-table th {{
        font-size: 12px;
      }}
      ul {{
        margin: 0;
        padding-left: 18px;
      }}
      .meta {{
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: 12px;
      }}
      .pill {{
        background: rgba(255,255,255,0.14);
        border: 1px solid rgba(255,255,255,0.18);
        border-radius: 12px;
        padding: 10px 12px;
      }}
      .pill small {{
        display: block;
        opacity: 0.8;
        margin-bottom: 4px;
      }}
    </style>
  </head>
  <body>
    <main>
      <section class="hero">
        <h1>XRechnung</h1>
        <div class="meta">
          <div class="pill"><small>Rechnungsnummer</small>{escape(invoice.invoice_number or "")}</div>
          <div class="pill"><small>Ausstellungsdatum</small>{escape(issue_date)}</div>
          <div class="pill"><small>Gesamtbetrag</small>{escape(invoice.grand_total or invoice.due_amount or "")} {escape(invoice.currency or "")}</div>
        </div>
      </section>

      <section class="card">
        <h2>Rechnungsdaten</h2>
        <table>
          <tr><th>Profil</th><td>{escape(invoice.profile or "")}</td></tr>
          <tr><th>Typcode</th><td>{escape(invoice.invoice_type_code or "")}</td></tr>
          <tr><th>Kaeuferreferenz</th><td>{escape(invoice.buyer_reference or "")}</td></tr>
          <tr><th>Zahlungsreferenz</th><td>{escape(invoice.payment_reference or "")}</td></tr>
          <tr><th>Steuerbetrag</th><td>{escape(invoice.tax_total or "")} {escape(invoice.currency or "")}</td></tr>
          <tr><th>Faelliger Betrag</th><td>{escape(invoice.due_amount or "")} {escape(invoice.currency or "")}</td></tr>
          <tr><th>Zahlungsbedingungen</th><td>{escape(invoice.payment_terms or "")}</td></tr>
        </table>
      </section>

      <div class="grid">
        {render_party("Verkaeufer", invoice.seller)}
        {render_party("Kaeufer", invoice.buyer)}
      </div>

      <section class="card">
        <h2>Positionen</h2>
        <table class="line-table">
          <thead>
            <tr>
              <th>ID</th>
              <th>Bezeichnung</th>
              <th>Beschreibung</th>
              <th>Menge</th>
              <th>Einheit</th>
              <th>Einzelpreis</th>
              <th>Gesamt</th>
              <th>Steuer %</th>
            </tr>
          </thead>
          <tbody>{line_rows}</tbody>
        </table>
      </section>

      <section class="card">
        <h2>Hinweise</h2>
        <ul>{notes}</ul>
      </section>
    </main>
  </body>
</html>
"""

    def _settings_to_gotenberg_pdfa(self) -> PdfAFormat | None:
        output_type = settings.OCR_OUTPUT_TYPE
        if output_type in {
            OutputTypeChoices.PDF_A,
            OutputTypeChoices.PDF_A2,
        }:
            return PdfAFormat.A2b
        if output_type == OutputTypeChoices.PDF_A1:  # pragma: no cover
            logger.warning(
                "Gotenberg does not support PDF/A-1a, choosing PDF/A-2b instead",
            )
            return PdfAFormat.A2b
        if output_type == OutputTypeChoices.PDF_A3:  # pragma: no cover
            return PdfAFormat.A3b
        return None
