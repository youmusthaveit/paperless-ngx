from __future__ import annotations

import datetime
import hashlib
import tempfile
import textwrap
from dataclasses import dataclass
from datetime import date
from html import escape
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any

from django.utils import timezone
from gotenberg_client import GotenbergClient
from gotenberg_client.constants import A4
from gotenberg_client.options import Measurement
from gotenberg_client.options import MeasurementUnitType
from gotenberg_client.options import PageMarginsType
from lxml import html as lxml_html

from documents import index
from documents.models import Correspondent
from documents.models import CustomField
from documents.models import CustomFieldInstance
from documents.models import Document
from documents.models import DocumentType
from documents.models import StoragePath
from documents.models import Tag
from documents.parsers import ParseError
from paperless import settings

if TYPE_CHECKING:
    from django.contrib.auth.models import User


_demo_pdf_rendering_available: bool | None = None


def _escape_pdf_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def build_simple_pdf(lines: list[str]) -> bytes:
    wrapped_lines: list[str] = []
    for line in lines[:250]:
        if not line:
            wrapped_lines.append("")
            continue
        wrapped = textwrap.wrap(
            line,
            width=78,
            break_long_words=False,
            break_on_hyphens=False,
        )
        wrapped_lines.extend(wrapped or [""])

    pages: list[list[str]] = []
    current_page: list[str] = []
    for line in wrapped_lines:
        current_page.append(line)
        if len(current_page) >= 42:
            pages.append(current_page)
            current_page = []
    if current_page or not pages:
        pages.append(current_page)

    page_streams: list[bytes] = []
    for page_lines in pages:
        content_lines = ["BT", "/F1 11 Tf", "50 790 Td"]
        for index_, line in enumerate(page_lines):
            if index_:
                content_lines.append("T*")
            content_lines.append(f"({_escape_pdf_text(line[:120])}) Tj")
        content_lines.append("ET")
        page_streams.append(
            "\n".join(content_lines).encode("ascii", errors="ignore"),
        )

    page_count = len(page_streams)
    pages_id = 2
    page_ids = [3 + index for index in range(page_count)]
    font_id = 3 + page_count
    content_ids = [4 + page_count + index for index in range(page_count)]

    objects: list[bytes] = [
        f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode("ascii"),
        (
            "<< /Type /Pages /Kids ["
            + " ".join(f"{page_id} 0 R" for page_id in page_ids)
            + f"] /Count {page_count} >>"
        ).encode("ascii"),
    ]
    for page_id, content_id in zip(page_ids, content_ids, strict=True):
        objects.append(
            (
                f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 595 842] "
                f"/Resources << /Font << /F1 {font_id} 0 R >> >> /Contents {content_id} 0 R >>"
            ).encode("ascii"),
        )
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    for stream in page_streams:
        objects.append(
            b"<< /Length "
            + str(len(stream)).encode("ascii")
            + b" >>\nstream\n"
            + stream
            + b"\nendstream",
        )

    buffer = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for object_id, body in enumerate(objects, start=1):
        offsets.append(len(buffer))
        buffer.extend(f"{object_id} 0 obj\n".encode("ascii"))
        buffer.extend(body)
        buffer.extend(b"\nendobj\n")

    xref_offset = len(buffer)
    buffer.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    buffer.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        buffer.extend(f"{offset:010} 00000 n \n".encode("ascii"))
    buffer.extend(
        (
            "trailer\n"
            f"<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii"),
    )
    return bytes(buffer)


@dataclass(frozen=True, slots=True)
class DemoCustomFieldValue:
    field_name: str
    value: Any


@dataclass(frozen=True, slots=True)
class DemoDocumentSpec:
    filename: str
    title: str
    correspondent: str
    document_type: str
    storage_path: str
    tags: tuple[str, ...]
    created: date
    content_html: str
    content_lines: tuple[str, ...]
    custom_fields: tuple[DemoCustomFieldValue, ...] = ()


def _ensure_choice_fields(field: CustomField, options: list[tuple[str, str]]) -> None:
    field.extra_data = {
        "select_options": [
            {"id": option_id, "label": label} for option_id, label in options
        ],
    }
    field.save(update_fields=["extra_data"])


def _ensure_document_bytes(document: Document, pdf_bytes: bytes) -> None:
    if not document.source_exists():
        document.source_write_bytes(pdf_bytes)


def _ensure_document_index(document: Document) -> None:
    index.add_or_update_document(document)


def _render_html_to_pdf_bytes(html_path: Path) -> bytes:
    if not settings.TIKA_GOTENBERG_ENDPOINT:
        raise ParseError("Gotenberg endpoint is not configured.")

    with (
        GotenbergClient(
            host=settings.TIKA_GOTENBERG_ENDPOINT,
            timeout=settings.CELERY_TASK_TIME_LIMIT,
        ) as client,
        client.chromium.html_to_pdf() as route,
    ):
        response = (
            route.index(html_path)
            .margins(
                PageMarginsType(
                    top=Measurement(0.1, MeasurementUnitType.Inches),
                    bottom=Measurement(0.1, MeasurementUnitType.Inches),
                    left=Measurement(0.1, MeasurementUnitType.Inches),
                    right=Measurement(0.1, MeasurementUnitType.Inches),
                ),
            )
            .size(A4)
            .scale(1.0)
            .run()
        )
    return response.content


def _render_demo_pdf_bytes(
    html_path: Path,
    display_lines: tuple[str, ...],
) -> bytes:
    global _demo_pdf_rendering_available

    if _demo_pdf_rendering_available is False:
        return build_simple_pdf(list(display_lines))

    try:
        pdf_bytes = _render_html_to_pdf_bytes(html_path)
    except Exception:
        _demo_pdf_rendering_available = False
        return build_simple_pdf(list(display_lines))
    else:
        _demo_pdf_rendering_available = True
        return pdf_bytes


def _html_to_display_lines(html_text: str) -> tuple[str, ...]:
    document = lxml_html.fromstring(html_text)
    sheet = document.xpath(
        "//div[contains(concat(' ', normalize-space(@class), ' '), ' sheet ')]",
    )
    root = sheet[0] if sheet else document

    lines: list[str] = []

    def add_text(value: str) -> None:
        text = " ".join(value.split())
        if text:
            lines.append(text)

    def add_blank_line() -> None:
        if lines and lines[-1] != "":
            lines.append("")

    def walk(node) -> None:
        tag = node.tag.lower() if isinstance(node.tag, str) else ""
        classes = f" {node.get('class', '').strip()} "

        if tag == "div" and " brand " in classes:
            add_text(node.text_content())
            add_blank_line()
            return
        if tag == "div" and " headline " in classes:
            add_text(node.text_content())
            add_blank_line()
            return
        if tag == "table" and " meta " in classes:
            for row in node.xpath("./tbody/tr"):
                cells = [
                    " ".join(cell.text_content().split())
                    for cell in row.xpath("./th|./td")
                ]
                add_text(" - ".join(cells))
            add_blank_line()
            return
        if tag == "table" and " positions " in classes:
            for row in node.xpath("./thead/tr"):
                cells = [
                    " ".join(cell.text_content().split())
                    for cell in row.xpath("./th|./td")
                ]
                add_text(" | ".join(cells))
            for row in node.xpath("./tbody/tr"):
                cells = [
                    " ".join(cell.text_content().split())
                    for cell in row.xpath("./th|./td")
                ]
                add_text(" | ".join(cells))
            add_blank_line()
            return
        if tag == "table" and " summary " in classes:
            for row in node.xpath("./tbody/tr"):
                cells = [
                    " ".join(cell.text_content().split())
                    for cell in row.xpath("./th|./td")
                ]
                add_text(" - ".join(cells))
            add_blank_line()
            return
        if tag == "ul":
            for item in node.xpath("./li"):
                add_text(f"- {item.text_content().strip()}")
            add_blank_line()
            return
        if tag in {"h1", "h2", "h3"}:
            add_text(node.text_content())
            add_blank_line()
            return
        if tag == "p":
            add_text(node.text_content())
            return
        if tag in {"div", "section", "article", "footer"}:
            for child in node:
                walk(child)
            add_blank_line()
            return
        for child in node:
            walk(child)

    walk(root)
    while lines and lines[0] == "":
        lines.pop(0)
    while lines and lines[-1] == "":
        lines.pop()
    return tuple(lines)


def _build_business_document_html(
    *,
    sender: str,
    headline: str,
    document_type: str,
    doc_no: str,
    issue_date: date,
    correspondent: str,
    project: str,
    subject: str,
    rows: tuple[str, ...],
    totals: tuple[str, ...] = (),
    notes: tuple[str, ...] = (),
    footer: tuple[str, ...] = (),
) -> str:
    document_type_key = document_type.lower()
    type_settings = {
        "angebot": {
            "accent": "#1d4ed8",
            "kicker": "ANGEBOT",
            "meta_label": "Angebotsdaten",
            "rows_label": "Positionen",
            "totals_label": "Kalkulation",
            "notes_label": "Hinweise",
            "closing_label": "Angebotsabschluss",
            "docno_label": "Angebotsnummer",
            "total_intro": "Leistungsumfang",
        },
        "auftragsbestaetigung": {
            "accent": "#0f766e",
            "kicker": "AUFTRAGSBESTÄTIGUNG",
            "meta_label": "Auftragsdaten",
            "rows_label": "Leistungsumfang",
            "totals_label": "Termin und Konditionen",
            "notes_label": "Hinweise",
            "closing_label": "Auftragsabschluss",
            "docno_label": "Auftragsnummer",
            "total_intro": "Ausführung",
        },
        "rechnung": {
            "accent": "#166534",
            "kicker": "RECHNUNG",
            "meta_label": "Rechnungsdaten",
            "rows_label": "Positionen",
            "totals_label": "Zahlungsinformationen",
            "notes_label": "Hinweise",
            "closing_label": "Rechnungsabschluss",
            "docno_label": "Rechnungsnummer",
            "total_intro": "Betrag",
        },
        "lieferschein": {
            "accent": "#b45309",
            "kicker": "LIEFERSCHEIN",
            "meta_label": "Lieferdaten",
            "rows_label": "Lieferpositionen",
            "totals_label": "Übergabe",
            "notes_label": "Hinweise",
            "closing_label": "Empfang",
            "docno_label": "Lieferscheinnummer",
            "total_intro": "Lieferumfang",
        },
        "stundennachweis": {
            "accent": "#4c1d95",
            "kicker": "STUNDENNACHWEIS",
            "meta_label": "Einsatzdaten",
            "rows_label": "Arbeitszeiten",
            "totals_label": "Nachweis",
            "notes_label": "Unterschrift / Freigabe",
            "closing_label": "Tagesabschluss",
            "docno_label": "Nachweisnummer",
            "total_intro": "Arbeitszeit",
        },
        "wartungsbericht": {
            "accent": "#7c2d12",
            "kicker": "WARTUNGSBERICHT",
            "meta_label": "Servicedaten",
            "rows_label": "Prüfpunkte",
            "totals_label": "Ergebnis",
            "notes_label": "Empfehlungen",
            "closing_label": "Serviceabschluss",
            "docno_label": "Berichtsnummer",
            "total_intro": "Kontrolle",
        },
    }.get(
        document_type_key,
        {
            "accent": "#374151",
            "kicker": document_type.upper(),
            "meta_label": "Dokumentdaten",
            "rows_label": "Positionen",
            "totals_label": "Zusammenfassung",
            "notes_label": "Hinweise",
            "closing_label": "Abschluss",
            "docno_label": "Dokumentnummer",
            "total_intro": "Inhalt",
        },
    )
    meta_rows = [
        (type_settings["docno_label"], doc_no),
        ("Datum", f"{issue_date:%d.%m.%Y}"),
        ("Kunde", correspondent),
        ("Object", project),
    ]
    row_items = "".join(
        f"<tr><td>{escape(f'{index}.')}</td><td>{escape(row)}</td></tr>"
        for index, row in enumerate(rows, start=1)
    )
    total_items = "".join(f"<tr><td>{escape(entry)}</td></tr>" for entry in totals)
    note_items = "".join(f"<li>{escape(entry)}</li>" for entry in notes)
    footer_items = "".join(f"<p>{escape(entry)}</p>" for entry in footer)
    return f"""
<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <title>{escape(headline)}</title>
  <style>
    body {{
      font-family: Arial, Helvetica, sans-serif;
      font-size: 12pt;
      line-height: 1.45;
      color: #1b1b1b;
      margin: 36px;
    }}
    .sheet {{
      max-width: 760px;
      margin: 0 auto;
      border: 1px solid #d9d9d9;
      padding: 28px 30px 32px;
      box-shadow: 0 2px 10px rgba(0, 0, 0, 0.04);
      border-top: 8px solid {type_settings["accent"]};
    }}
    .brand {{
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      gap: 16px;
      font-size: 18pt;
      font-weight: 700;
      margin-bottom: 14px;
    }}
    .headline {{
      font-size: 16pt;
      font-weight: 700;
      margin: 0 0 18px 0;
    }}
    .kicker {{
      display: inline-block;
      color: {type_settings["accent"]};
      font-size: 10pt;
      font-weight: 700;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      margin-bottom: 4px;
    }}
    .meta {{
      width: 100%;
      border-collapse: collapse;
      margin: 0 0 18px 0;
    }}
    .meta th {{
      text-align: left;
      width: 30%;
      padding: 5px 8px 5px 0;
      vertical-align: top;
      font-weight: 700;
    }}
    .meta td {{
      padding: 5px 0;
      vertical-align: top;
    }}
    .block {{
      margin-top: 16px;
    }}
    .intro {{
      margin: 0 0 16px 0;
      padding: 12px 14px;
      background: #f8fafc;
      border-left: 4px solid {type_settings["accent"]};
    }}
    h2 {{
      font-size: 13pt;
      margin: 18px 0 8px;
      border-bottom: 1px solid #d0d0d0;
      padding-bottom: 3px;
    }}
    p {{
      margin: 0 0 10px 0;
    }}
    table.positions,
    table.summary {{
      width: 100%;
      border-collapse: collapse;
      margin: 0 0 12px 0;
    }}
    table.positions th,
    table.positions td,
    table.summary td {{
      text-align: left;
      padding: 7px 8px;
      border-bottom: 1px solid #e4e4e4;
      vertical-align: top;
    }}
    table.positions thead th {{
      background: #f6f6f6;
      border-bottom: 2px solid #bcbcbc;
    }}
    ul {{
      margin: 0 0 10px 22px;
      padding: 0;
    }}
    li {{
      margin: 0 0 4px 0;
    }}
    .signature {{
      margin-top: 28px;
    }}
  </style>
</head>
<body>
  <div class="sheet">
    <div class="brand">
      <div>{escape(sender)}</div>
      <div>{escape(issue_date.strftime("%d.%m.%Y"))}</div>
    </div>
    <div class="kicker">{escape(type_settings["kicker"])}</div>
    <div class="headline">{escape(headline)}</div>
    <table class="meta">
      <tbody>
        {
        "".join(
            f"<tr><th>{escape(label)}</th><td>{escape(value)}</td></tr>"
            for label, value in meta_rows
        )
    }
      </tbody>
    </table>
    <div class="block">
      <h2>{escape(type_settings["meta_label"])}</h2>
      <p>{escape(subject)}</p>
    </div>
    {
        f'<div class="intro">{escape(type_settings["total_intro"])}: {escape(subject)}</div>'
        if document_type_key in {"angebot", "auftragsbestaetigung", "rechnung"}
        else ""
    }
    {
        f'<div class="block"><h2>{escape(type_settings["rows_label"])}</h2><table class="positions"><thead><tr><th>Pos.</th><th>Beschreibung</th></tr></thead><tbody>{row_items}</tbody></table></div>'
        if rows
        else ""
    }
    {
        f'<div class="block"><h2>{escape(type_settings["totals_label"])}</h2><table class="summary"><tbody>{total_items}</tbody></table></div>'
        if totals
        else ""
    }
    {
        f'<div class="block"><h2>{escape(type_settings["notes_label"])}</h2><ul>{note_items}</ul></div>'
        if notes
        else ""
    }
    {
        f'<div class="signature"><h2>{escape(type_settings["closing_label"])}</h2>{footer_items}</div>'
        if footer
        else ""
    }
  </div>
</body>
</html>
""".strip()


def _build_demo_document_spec(
    *,
    date_: date,
    slug: str,
    title: str,
    correspondent: str,
    document_type: str,
    storage_path: str,
    tags: tuple[str, ...],
    doc_no: str,
    subject: str,
    project: str,
    rows: tuple[str, ...],
    totals: tuple[str, ...] = (),
    notes: tuple[str, ...] = (),
    custom_fields: tuple[DemoCustomFieldValue, ...] = (),
    sender: str = "Muster Bau GmbH",
    headline: str | None = None,
    footer: tuple[str, ...] = (),
) -> DemoDocumentSpec:
    html_text = _build_business_document_html(
        sender=sender,
        headline=headline or document_type,
        document_type=document_type,
        doc_no=doc_no,
        issue_date=date_,
        correspondent=correspondent,
        project=project,
        subject=subject,
        rows=rows,
        totals=totals,
        notes=notes,
        footer=footer or ("Mit freundlichen Gruessen", "Muster Bau GmbH"),
    )
    return DemoDocumentSpec(
        filename=f"handwerk-demo-{date_.isoformat()}-{slug}.pdf",
        title=title,
        correspondent=correspondent,
        document_type=document_type,
        storage_path=storage_path,
        tags=tags,
        created=date_,
        content_html=html_text,
        content_lines=_html_to_display_lines(html_text),
        custom_fields=custom_fields,
    )


def _build_additional_demo_document_specs() -> list[DemoDocumentSpec]:
    project_cases = [
        (
            "familienbad-birkenweg",
            "Modernisierung Familienbad",
            "Familie Becker",
            "privat",
            "Kunden/Privat",
            ("bad", "privatkunde"),
            "Birkenweg 14, 60311 Frankfurt",
        ),
        (
            "werkhalle-west",
            "Heizungsmodernisierung Werkhalle",
            "Wagner Holzbau",
            "gewerbe",
            "Kunden/Gewerbe",
            ("heizung", "gewerbe"),
            "Werkhalle West, Tor 3",
        ),
        (
            "kita-sonnenweg",
            "Sicherheitsbeleuchtung Kita",
            "Kindertagesstaette Sonnenweg",
            "oeffentlich",
            "Kunden/Gewerbe",
            ("elektrik", "oeffentlich"),
            "Sonnenweg 5, 60439 Frankfurt",
        ),
        (
            "hotel-lindenhof",
            "Sanitaerwartung Hotel",
            "Stadthalle West",
            "gewerbe",
            "Kunden/Gewerbe",
            ("wartung", "gewerbe"),
            "Hotel Lindenhof, Empfang Nord",
        ),
        (
            "hauptstrasse-18",
            "Dachsanierung Verwaltungsobjekt",
            "Lindner Immobilienverwaltung",
            "gewerbe",
            "Kunden/Gewerbe",
            ("sanierung", "gewerbe"),
            "Hauptstrasse 18, Gebaeude A",
        ),
        (
            "cafe-am-markt",
            "Heizungsservice Cafe",
            "Cafe am Markt",
            "gewerbe",
            "Service/Notdienst",
            ("wartung", "heizung"),
            "Rathausplatz 8, 60313 Frankfurt",
        ),
        (
            "industriestrasse-7",
            "Warmwasseranlage Produktionshalle",
            "Klein & Sohn GmbH",
            "gewerbe",
            "Kunden/Gewerbe",
            ("heizung", "dringend"),
            "Industriestrasse 7, Halle 2",
        ),
        (
            "stadtwerke-nord",
            "Rohrnetz Wartung Nord",
            "Stadtwerke Nord",
            "oeffentlich",
            "Service/Notdienst",
            ("heizung", "oeffentlich"),
            "Versorgungsring Nord 12",
        ),
        (
            "neubau-wiesengrund",
            "Elektroinstallation Neubau",
            "Schulz Elektrotechnik",
            "gewerbe",
            "Baustellen",
            ("elektrik", "material"),
            "Im Wiesengrund 22, Haus B",
        ),
        (
            "architektur-musterwohnung",
            "Ausbau Musterwohnung Ost",
            "Meyer & Partner Architektur",
            "gewerbe",
            "Baustellen",
            ("sanierung", "object"),
            "Musterwohnung Ost, 3. OG",
        ),
        (
            "heiztechnik-rathaus",
            "Notdienst Kesselraum Rathaus",
            "Heiztechnik Weber",
            "oeffentlich",
            "Service/Notdienst",
            ("notdienst", "heizung"),
            "Rathausplatz 1, Kesselraum",
        ),
        (
            "bauzentrum-lager-nord",
            "Materialbereitstellung Lager Nord",
            "Bauzentrum Nord GmbH",
            "gewerbe",
            "Lieferanten",
            ("material", "gewerbe"),
            "Lager Nord, Tor 6",
        ),
        (
            "verwaltung-rosenweg",
            "Badsanierung Rosenweg",
            "Familie Becker",
            "privat",
            "Kunden/Privat",
            ("bad", "sanierung"),
            "Rosenweg 5, 60385 Frankfurt",
        ),
        (
            "stadthalle-technik",
            "Technikraum Stadthalle",
            "Stadthalle West",
            "oeffentlich",
            "Verwaltung",
            ("elektrik", "wartung"),
            "Stadthalle West, Technikraum",
        ),
        (
            "wohnanlage-west",
            "Instandsetzung Wohnanlage",
            "Lindner Immobilienverwaltung",
            "gewerbe",
            "Baustellen",
            ("sanierung", "material"),
            "Wohnanlage West, Haus 4",
        ),
        (
            "servicehof-cafe",
            "Warmwasser Servicehof Cafe",
            "Cafe am Markt",
            "gewerbe",
            "Service/Notdienst",
            ("heizung", "service"),
            "Servicehof, Rathausplatz 8",
        ),
        (
            "holzbau-lager",
            "Werkstattumbau Holzbau",
            "Wagner Holzbau",
            "gewerbe",
            "Baustellen",
            ("montage", "material"),
            "Werkstattlager, Abschnitt C",
        ),
        (
            "kita-verwaltung",
            "Pruefprotokolle Kita",
            "Kindertagesstaette Sonnenweg",
            "oeffentlich",
            "Verwaltung",
            ("wartung", "oeffentlich"),
            "Sonnenweg 5, Bueroleitung",
        ),
        (
            "lieferung-ventile",
            "Armaturenlieferung Nord",
            "Stadtwerke Nord",
            "oeffentlich",
            "Lieferanten",
            ("material", "heizung"),
            "Versorgungsring Nord 12, Lager",
        ),
        (
            "buero-klein-sohn",
            "Buchhaltung Klein und Sohn",
            "Klein & Sohn GmbH",
            "gewerbe",
            "Verwaltung",
            ("rechnung", "dringend"),
            "Industriestrasse 7, Verwaltung",
        ),
    ]

    variant_templates = [
        {
            "doc_type": "Angebot",
            "title_prefix": "Angebot",
            "slug_prefix": "angebot",
            "tags": ("angebot",),
            "amount": 1480,
            "rows": (
                "Baustelleneinrichtung und Absicherung",
                "Montage und technische Ausfuehrung",
                "Dokumentation und Abschlusskontrolle",
            ),
            "totals": (
                "Gueltig fuer 21 Tg. ab Dokumentdatum",
                "Ausfuehrung nach Terminfreigabe",
            ),
            "notes": ("Bitte Freigabe per E-Mail, telefonisch bestaetigen.",),
        },
        {
            "doc_type": "Auftragsbestaetigung",
            "title_prefix": "Auftragsbestaetigung",
            "slug_prefix": "auftrag",
            "tags": ("auftrag",),
            "amount": 1560,
            "rows": (
                "Leistungsumfang und Materialeinsatz freigegeben",
                "Ausfuehrung in abgestimmtem Zeitfenster",
                "Abschluss mit Funktionspruefung",
            ),
            "totals": ("Terminfenster abgestimmt und reserviert",),
            "notes": ("Zugang bitte am Einsatztag ab 07:30 Uhr sicherstellen.",),
        },
        {
            "doc_type": "Rechnung",
            "title_prefix": "Rechnung",
            "slug_prefix": "rechnung",
            "tags": ("rechnung",),
            "amount": 1720,
            "rows": (
                "Arbeitsleistung laut Freigabe",
                "Material und Kleinverbrauch",
                "Abnahme und Dokumentationspaket",
            ),
            "totals": (
                "Zahlbar innerhalb von 14 Tagen",
                "Bitte Rechnungsnummer bei Ueberweisung angeben",
            ),
            "notes": ("Vielen Dank fuer den Auftrag.",),
        },
        {
            "doc_type": "Lieferschein",
            "title_prefix": "Lieferschein",
            "slug_prefix": "lieferschein",
            "tags": ("material",),
            "amount": 0,
            "rows": (
                "Material fuer Einsatz vorbereitet",
                "Ausgabe an Montageteam dokumentiert",
                "Restmengen nach Einsatz rueckmelden",
            ),
            "totals": (),
            "notes": ("Keine Berechnung, dient nur dem Materialnachweis.",),
        },
        {
            "doc_type": "Stundennachweis",
            "title_prefix": "Stundennachweis",
            "slug_prefix": "stunden",
            "tags": ("montage",),
            "amount": 0,
            "rows": (
                "07:30-10:15 Vorbereitung und Aufbau",
                "10:30-13:00 Hauptleistung ausgefuehrt",
                "13:45-16:00 Kontrolle und Uebergabe",
            ),
            "totals": (),
            "notes": ("Zeiten wurden durch den Vorarbeiter bestaetigt.",),
        },
        {
            "doc_type": "Wartungsbericht",
            "title_prefix": "Wartungsbericht",
            "slug_prefix": "wartung",
            "tags": ("wartung",),
            "amount": 0,
            "rows": (
                "Sichtpruefung und Funktionskontrolle",
                "Verschleissteile geprueft",
                "Empfehlungen dokumentiert",
            ),
            "totals": (),
            "notes": ("Naechster Turnus nach Betriebsbuch einplanen.",),
        },
        {
            "doc_type": "Rechnung",
            "title_prefix": "Abschlagsrechnung",
            "slug_prefix": "abschlag",
            "tags": ("rechnung", "object"),
            "amount": 1940,
            "rows": (
                "Teilabschnitt 1 abgeschlossen",
                "Materialnachweis und Zwischenabnahme",
                "Abschlag gemaess Baufortschritt",
            ),
            "totals": (
                "Zahlbar innerhalb von 10 Tagen",
                "Teilrechnung gemaess Bauzeitenplan",
            ),
            "notes": ("Weitere Teilleistung folgt nach Abschnittsfreigabe.",),
        },
        {
            "doc_type": "Rechnung",
            "title_prefix": "Zahlungserinnerung",
            "slug_prefix": "erinnerung",
            "tags": ("rechnung", "dringend"),
            "amount": 2130,
            "rows": (
                "Oftener Rechnungsbetrag laut Buchhaltung",
                "Leistung bereits vollstaendig erbracht",
                "Bitte Zahlungsziel beachten",
            ),
            "totals": (
                "Mahnstufe 1 ohne Zusatzkosten",
                "Rueckfragen bitte an die Verwaltung",
            ),
            "notes": ("Dieses Schreiben wurde automatisch erstellt.",),
        },
    ]

    additional_documents: list[DemoDocumentSpec] = []
    start_date = date(2026, 4, 14)

    for project_index, case in enumerate(project_cases, start=1):
        (
            case_slug,
            case_title,
            correspondent,
            customer_type,
            default_storage_path,
            case_tags,
            project_address,
        ) = case

        for variant_index, template in enumerate(variant_templates, start=1):
            current_date = start_date + datetime.timedelta(
                days=((project_index - 1) * len(variant_templates))
                + (variant_index - 1),
            )
            amount = (
                f"EUR {template['amount'] + (project_index * 37) + (variant_index * 11):.2f}"
                if template["amount"]
                else None
            )
            doc_no = (
                f"{template['slug_prefix'][:3].upper()}-2026-"
                f"{project_index:02d}{variant_index:02d}"
            )
            title = f"{template['title_prefix']} {case_title} {project_index:02d}-{variant_index}"
            storage_path = (
                "Service/Notdienst"
                if "notdienst" in case_tags
                and template["doc_type"] in {"Rechnung", "Wartungsbericht"}
                else default_storage_path
            )
            tags = template["tags"] + case_tags
            subject = f"{case_title} fuer {project_address}"
            rows = tuple(
                f"{row} fuer Abschnitt {project_index:02d}-{variant_index}"
                for row in template["rows"]
            )
            totals = tuple(template["totals"])
            if amount is not None and template["doc_type"] == "Angebot":
                totals += (f"Netto gesamt {amount.replace('EUR ', '')}",)
            elif amount is not None and template["doc_type"] == "Auftragsbestaetigung":
                totals += (f"Freigegebener Netto-Betrag {amount.replace('EUR ', '')}",)
            elif amount is not None and template["doc_type"] == "Rechnung":
                totals += (f"Oftener Netto-Betrag {amount.replace('EUR ', '')}",)

            custom_fields = [
                DemoCustomFieldValue("Auftragsnummer", doc_no),
                DemoCustomFieldValue("Bauort", project_address),
                DemoCustomFieldValue("Kundentyp", customer_type),
            ]
            if amount is not None:
                custom_fields.append(
                    DemoCustomFieldValue("Netto-Betrag", amount),
                )
                custom_fields.append(
                    DemoCustomFieldValue(
                        "Faelligkeit",
                        current_date + datetime.timedelta(days=14),
                    ),
                )
            if "dringend" in tags or "notdienst" in tags:
                custom_fields.append(
                    DemoCustomFieldValue(field_name="Eilauftrag", value=True),
                )

            additional_documents.append(
                _build_demo_document_spec(
                    date_=current_date,
                    slug=f"{template['slug_prefix']}-{case_slug}-{project_index:02d}-{variant_index}",
                    title=title,
                    correspondent=correspondent,
                    document_type=template["doc_type"],
                    storage_path=storage_path,
                    tags=tags,
                    doc_no=doc_no,
                    subject=subject,
                    project=project_address,
                    rows=rows,
                    totals=totals,
                    notes=template["notes"],
                    custom_fields=tuple(custom_fields),
                ),
            )

    return additional_documents


def _build_historical_demo_document_specs() -> list[DemoDocumentSpec]:
    historical_cases = [
        (
            "altbau-bad",
            "Altbau Badsanierung",
            "Familie Becker",
            "privat",
            "Kunden/Privat",
            ("bad", "privatkunde", "sanierung"),
            "Altbau Birkenweg 14",
        ),
        (
            "heizzentrale-rathaus",
            "Heizzentrale Rathaus",
            "Stadtwerke Nord",
            "oeffentlich",
            "Service/Notdienst",
            ("heizung", "oeffentlich", "wartung"),
            "Rathausplatz 1, Technikzentrale",
        ),
        (
            "werkhalle-brennwert",
            "Werkhalle Brennwertanlage",
            "Wagner Holzbau",
            "gewerbe",
            "Kunden/Gewerbe",
            ("heizung", "gewerbe", "montage"),
            "Werkhalle West",
        ),
        (
            "hotel-sanitaer",
            "Hotel Sanitaertrakt",
            "Stadthalle West",
            "gewerbe",
            "Kunden/Gewerbe",
            ("wartung", "gewerbe", "bad"),
            "Hotel Lindenhof",
        ),
        (
            "kita-elektro",
            "Kita Elektrobereich",
            "Kindertagesstaette Sonnenweg",
            "oeffentlich",
            "Verwaltung",
            ("elektrik", "oeffentlich", "wartung"),
            "Sonnenweg 5",
        ),
        (
            "immobilien-dach",
            "Wohnanlage Dachinstandsetzung",
            "Lindner Immobilienverwaltung",
            "gewerbe",
            "Baustellen",
            ("sanierung", "gewerbe", "object"),
            "Hauptstrasse 18",
        ),
        (
            "cafe-heizung",
            "Cafe Heizungsservice",
            "Cafe am Markt",
            "gewerbe",
            "Service/Notdienst",
            ("service", "heizung", "gewerbe"),
            "Rathausplatz 8",
        ),
        (
            "industrie-warmwasser",
            "Produktionshalle Warmwasser",
            "Klein & Sohn GmbH",
            "gewerbe",
            "Kunden/Gewerbe",
            ("heizung", "dringend", "gewerbe"),
            "Industriestrasse 7",
        ),
        (
            "neubau-elektro",
            "Neubau Elektroinstallation",
            "Schulz Elektrotechnik",
            "gewerbe",
            "Baustellen",
            ("elektrik", "material", "gewerbe"),
            "Im Wiesengrund 22",
        ),
        (
            "lager-material",
            "Lager Materialbereitstellung",
            "Bauzentrum Nord GmbH",
            "gewerbe",
            "Lieferanten",
            ("material", "gewerbe"),
            "Lager Nord",
        ),
    ]

    historical_templates = [
        {
            "doc_type": "Rechnung",
            "title_prefix": "Archivrechnung",
            "slug_prefix": "archivrechnung",
            "tags": ("rechnung",),
            "amount": 1280,
            "rows": (
                "Leistung gemaess abgeschlossenem Einsatz",
                "Materialeinsatz und Verbrauch dokumentiert",
                "Abnahmeprotokoll archiviert",
            ),
            "notes": ("Archivbeleg aus dem Jahresabschluss.",),
        },
        {
            "doc_type": "Lieferschein",
            "title_prefix": "Archivlieferschein",
            "slug_prefix": "archivlieferschein",
            "tags": ("material",),
            "amount": 0,
            "rows": (
                "Materialausgabe dokumentiert",
                "Warenannahme durch Monteur bestaetigt",
                "Restmengen im Lager verbucht",
            ),
            "notes": ("Historischer Materialnachweis.",),
        },
        {
            "doc_type": "Stundennachweis",
            "title_prefix": "Archivstundennachweis",
            "slug_prefix": "archivstunden",
            "tags": ("montage",),
            "amount": 0,
            "rows": (
                "07:00-09:45 Vorbereitung und Einrichtung",
                "10:00-13:15 Montage und Anpassungen",
                "14:00-16:00 Abschluss und Reinigung",
            ),
            "notes": ("Zeitnachweis aus dem Altarchiv.",),
        },
        {
            "doc_type": "Wartungsbericht",
            "title_prefix": "Archivwartungsbericht",
            "slug_prefix": "archivwartung",
            "tags": ("wartung",),
            "amount": 0,
            "rows": (
                "Funktionspruefung durchgefuehrt",
                "Verschleiss kontrolliert",
                "Empfehlungen fuer Folgetermin notiert",
            ),
            "notes": ("Bericht aus dem Servicearchiv.",),
        },
        {
            "doc_type": "Angebot",
            "title_prefix": "Archivangebot",
            "slug_prefix": "archivangebot",
            "tags": ("angebot",),
            "amount": 1440,
            "rows": (
                "Leistungsbeschreibung archiviert",
                "Kalkulation fuer Altprojekt dokumentiert",
                "Ausfuehrung damals optional angeboten",
            ),
            "notes": ("Angebot aus einem historischen Projektstand.",),
        },
        {
            "doc_type": "Auftragsbestaetigung",
            "title_prefix": "Archivauftrag",
            "slug_prefix": "archivauftrag",
            "tags": ("auftrag",),
            "amount": 1510,
            "rows": (
                "Beauftragter Leistungsumfang",
                "Zeitfenster und Zugang abgestimmt",
                "Abschlussdokumentation vorbereitet",
            ),
            "notes": ("Auftragsbestaetigung fuer Altprojekt.",),
        },
        {
            "doc_type": "Rechnung",
            "title_prefix": "Altfall Zahlungserinnerung",
            "slug_prefix": "altfall-erinnerung",
            "tags": ("rechnung", "dringend"),
            "amount": 1670,
            "rows": (
                "Oftener Betrag laut damaliger Buchhaltung",
                "Leistung bereits abgeschlossen",
                "Bitte Archivvorgang pruefen",
            ),
            "notes": ("Historische Zahlungserinnerung aus dem Archive.",),
        },
        {
            "doc_type": "Rechnung",
            "title_prefix": "Abschlagsrechnung Altprojekt",
            "slug_prefix": "altprojekt-abschlag",
            "tags": ("rechnung", "object"),
            "amount": 1735,
            "rows": (
                "Teilabschnitt im Altprojekt abgeschlossen",
                "Zwischenstand mit Materialnachweis",
                "Abrechnung nach Baufortschritt",
            ),
            "notes": ("Teilrechnung aus einem archivierten Projekt.",),
        },
        {
            "doc_type": "Rechnung",
            "title_prefix": "Serviceabrechnung Archive",
            "slug_prefix": "serviceabrechnung",
            "tags": ("rechnung", "service"),
            "amount": 1395,
            "rows": (
                "Serviceeinsatz dokumentiert",
                "Arbeitszeit und Ersatzteile erfasst",
                "Uebergabe an Kunden erfolgt",
            ),
            "notes": ("Archivierte Serviceabrechnung.",),
        },
        {
            "doc_type": "Lieferschein",
            "title_prefix": "Notdienstmaterial Archive",
            "slug_prefix": "archiv-notdienst",
            "tags": ("material", "notdienst"),
            "amount": 0,
            "rows": (
                "Material fuer Notfalleinsatz gepackt",
                "Ausgabe ausserhalb der Regelzeit erfolgt",
                "Rueckmeldung an Werkstatt erfasst",
            ),
            "notes": ("Historischer Notdiensteinsatz.",),
        },
    ]

    historical_documents: list[DemoDocumentSpec] = []
    start_year = 2011

    for case_index, case in enumerate(historical_cases, start=1):
        (
            case_slug,
            case_title,
            correspondent,
            customer_type,
            storage_path,
            case_tags,
            project_address,
        ) = case

        for template_index, template in enumerate(historical_templates, start=1):
            year = start_year + ((case_index + template_index - 2) % 15)
            month = ((case_index * 3 + template_index) % 12) + 1
            day = ((case_index * 2 + template_index * 3) % 28) + 1
            created = date(year, month, day)
            doc_no = f"HIS-{year}-{case_index:02d}{template_index:02d}"
            amount = (
                f"EUR {template['amount'] + (case_index * 24) + (template_index * 9):.2f}"
                if template["amount"]
                else None
            )
            totals: tuple[str, ...] = ()
            if amount is not None and template["doc_type"] == "Angebot":
                totals = (f"Netto gesamt {amount.replace('EUR ', '')}",)
            elif amount is not None and template["doc_type"] == "Auftragsbestaetigung":
                totals = (f"Freigegebener Netto-Betrag {amount.replace('EUR ', '')}",)
            elif amount is not None and template["doc_type"] == "Rechnung":
                totals = (
                    f"Oftener Netto-Betrag {amount.replace('EUR ', '')}",
                    "Archiviert fuer steuerliche Aufbewahrung",
                )

            custom_fields = [
                DemoCustomFieldValue("Auftragsnummer", doc_no),
                DemoCustomFieldValue("Bauort", project_address),
                DemoCustomFieldValue("Kundentyp", customer_type),
            ]
            if amount is not None:
                custom_fields.append(DemoCustomFieldValue("Netto-Betrag", amount))
                custom_fields.append(
                    DemoCustomFieldValue(
                        "Faelligkeit",
                        created + datetime.timedelta(days=14),
                    ),
                )
            if "dringend" in template["tags"] or "notdienst" in template["tags"]:
                custom_fields.append(
                    DemoCustomFieldValue(field_name="Eilauftrag", value=True),
                )

            historical_documents.append(
                _build_demo_document_spec(
                    date_=created,
                    slug=f"{template['slug_prefix']}-{case_slug}-{year}-{case_index:02d}-{template_index}",
                    title=f"{template['title_prefix']} {case_title} {year}",
                    correspondent=correspondent,
                    document_type=template["doc_type"],
                    storage_path=storage_path,
                    tags=template["tags"] + case_tags,
                    doc_no=doc_no,
                    subject=f"{case_title} aus dem Archivjahr {year}",
                    project=project_address,
                    rows=tuple(f"{row} ({year})" for row in template["rows"]),
                    totals=totals,
                    notes=template["notes"],
                    custom_fields=tuple(custom_fields),
                ),
            )

    return historical_documents


def _upsert_document(
    spec: DemoDocumentSpec,
    *,
    correspondent: Correspondent,
    document_type: DocumentType,
    storage_path: StoragePath,
    tags: dict[str, Tag],
    fields: dict[str, CustomField],
    owner: User | None,
) -> tuple[Document, bool]:
    with tempfile.TemporaryDirectory(prefix="paperless-demo-md-") as tmpdir:
        html_path = Path(tmpdir) / Path(spec.filename).with_suffix(".html").name
        html_path.write_text(spec.content_html, encoding="utf-8")
        display_lines = _html_to_display_lines(html_path.read_text(encoding="utf-8"))
        pdf_bytes = _render_demo_pdf_bytes(html_path, display_lines)
        checksum = hashlib.sha256(pdf_bytes).hexdigest()
    defaults = {
        "title": spec.title,
        "correspondent": correspondent,
        "document_type": document_type,
        "storage_path": storage_path,
        "content": "\n".join(display_lines),
        "mime_type": "application/pdf",
        "created": spec.created,
        "added": timezone.make_aware(
            datetime.datetime.combine(spec.created, datetime.time.min),
        ),
        "page_count": 1,
        "original_filename": spec.filename,
        "owner": owner,
    }
    document, created = Document.objects.update_or_create(
        filename=spec.filename,
        defaults=defaults | {"checksum": checksum},
    )

    _ensure_document_bytes(document, pdf_bytes)
    document.tags.set([tags[tag_name] for tag_name in spec.tags])

    for custom_field in spec.custom_fields:
        field = fields[custom_field.field_name]
        value_kwargs: dict[str, Any] = {}
        if field.data_type == CustomField.FieldDataType.STRING:
            value_kwargs["value_text"] = custom_field.value
        elif field.data_type == CustomField.FieldDataType.URL:
            value_kwargs["value_url"] = custom_field.value
        elif field.data_type == CustomField.FieldDataType.DATE:
            value_kwargs["value_date"] = custom_field.value
        elif field.data_type == CustomField.FieldDataType.BOOL:
            value_kwargs["value_bool"] = custom_field.value
        elif field.data_type == CustomField.FieldDataType.INT:
            value_kwargs["value_int"] = custom_field.value
        elif field.data_type == CustomField.FieldDataType.FLOAT:
            value_kwargs["value_float"] = custom_field.value
        elif field.data_type == CustomField.FieldDataType.MONETARY:
            value_kwargs["value_monetary"] = custom_field.value
        elif field.data_type == CustomField.FieldDataType.SELECT:
            value_kwargs["value_select"] = custom_field.value
        elif field.data_type == CustomField.FieldDataType.LONG_TEXT:
            value_kwargs["value_long_text"] = custom_field.value
        CustomFieldInstance.objects.update_or_create(
            document=document,
            field=field,
            defaults=value_kwargs,
        )

    _ensure_document_index(document)
    return document, created


def seed_handwerksbetrieb_demo_data(*, owner: User | None = None) -> str:
    global _demo_pdf_rendering_available
    _demo_pdf_rendering_available = None

    correspondent_names = [
        "Bauzentrum Nord GmbH",
        "Cafe am Markt",
        "Familie Becker",
        "Heiztechnik Weber",
        "Klein & Sohn GmbH",
        "Kindertagesstaette Sonnenweg",
        "Lindner Immobilienverwaltung",
        "Muster Bau GmbH",
        "Meyer & Partner Architektur",
        "Schulz Elektrotechnik",
        "Stadthalle West",
        "Stadtwerke Nord",
        "Wagner Holzbau",
    ]
    correspondents = {
        name: Correspondent.objects.update_or_create(
            name=name,
            defaults={"match": "", "matching_algorithm": Correspondent.MATCH_NONE},
        )[0]
        for name in correspondent_names
    }

    document_type_names = [
        "Angebot",
        "Auftragsbestaetigung",
        "Rechnung",
        "Lieferschein",
        "Stundennachweis",
        "Wartungsbericht",
    ]
    document_types = {
        name: DocumentType.objects.update_or_create(
            name=name,
            defaults={"match": "", "matching_algorithm": DocumentType.MATCH_NONE},
        )[0]
        for name in document_type_names
    }

    storage_paths = {
        name: StoragePath.objects.update_or_create(
            name=name,
            defaults={
                "path": path,
                "match": "",
                "matching_algorithm": StoragePath.MATCH_NONE,
            },
        )[0]
        for name, path in [
            ("Baustellen", "Baustellen/{created_year}/{title}"),
            ("Kunden/Gewerbe", "Kunden/Gewerbe/{title}"),
            ("Kunden/Privat", "Kunden/Privat/{title}"),
            ("Service/Notdienst", "Service/Notdienst/{created_year}/{title}"),
            ("Lieferanten", "Lieferanten/{created_year}/{title}"),
            ("Verwaltung", "Verwaltung/{created_year}/{title}"),
        ]
    }

    tags = {
        name: Tag.objects.update_or_create(
            name=name,
            defaults={
                "match": "",
                "matching_algorithm": Tag.MATCH_NONE,
                "is_inbox_tag": False,
            },
        )[0]
        for name in [
            "angebot",
            "auftrag",
            "rechnung",
            "material",
            "montage",
            "bad",
            "heizung",
            "elektrik",
            "service",
            "notdienst",
            "wartung",
            "privatkunde",
            "gewerbe",
            "oeffentlich",
            "object",
            "sanierung",
            "dringend",
        ]
    }

    field_defs = [
        ("Auftragsnummer", CustomField.FieldDataType.STRING),
        ("Bauort", CustomField.FieldDataType.STRING),
        ("Kundentyp", CustomField.FieldDataType.SELECT),
        ("Netto-Betrag", CustomField.FieldDataType.MONETARY),
        ("Faelligkeit", CustomField.FieldDataType.DATE),
        ("Eilauftrag", CustomField.FieldDataType.BOOL),
    ]
    fields = {
        name: CustomField.objects.update_or_create(
            name=name,
            defaults={"data_type": data_type},
        )[0]
        for name, data_type in field_defs
    }
    _ensure_choice_fields(
        fields["Kundentyp"],
        [
            ("privat", "Privatkunde"),
            ("gewerbe", "Gewerbekunde"),
            ("oeffentlich", "Oeffentliche Hand"),
        ],
    )

    demo_documents = [
        _build_demo_document_spec(
            date_=date(2026, 3, 3),
            slug="angebot-badmodernisierung-becker",
            title="Angebot Badmodernisierung Becker",
            correspondent="Familie Becker",
            document_type="Angebot",
            storage_path="Kunden/Privat",
            tags=("angebot", "privatkunde", "bad"),
            doc_no="A-2026-0031",
            subject="Sanierung eines Familienbads mit bodengleicher Dusche",
            project="Birkenweg 14, 60311 Frankfurt",
            rows=(
                "Demontage Alteinrichtung 680 EUR",
                "Rohrarbeiten und Montage 1.240 EUR",
                "Sanitaerkeramik und Armaturen 1.420 EUR",
            ),
            totals=("Netto gesamt 3.340 EUR", "Gueltig bis 2026-04-03"),
            custom_fields=(
                DemoCustomFieldValue("Auftragsnummer", "A-2026-0031"),
                DemoCustomFieldValue("Bauort", "Birkenweg 14, 60311 Frankfurt"),
                DemoCustomFieldValue("Kundentyp", "privat"),
                DemoCustomFieldValue("Netto-Betrag", "EUR 3340.00"),
                DemoCustomFieldValue("Faelligkeit", date(2026, 4, 3)),
            ),
        ),
        _build_demo_document_spec(
            date_=date(2026, 3, 5),
            slug="auftragsbestaetigung-cafe-am-markt",
            title="Auftragsbestaetigung Cafe am Markt",
            correspondent="Cafe am Markt",
            document_type="Auftragsbestaetigung",
            storage_path="Kunden/Gewerbe",
            tags=("auftrag", "wartung", "gewerbe", "heizung"),
            doc_no="O-2026-0042",
            subject="Wartung der Heizungsanlage und Dichtheitspruefung",
            project="Cafe am Markt, Rathausplatz 8",
            rows=(
                "Wartung Heizungsanlage",
                "Dichtheitspruefung",
                "Kleinteile und Anfahrt",
            ),
            totals=("Netto gesamt 890 EUR", "Termin: 2026-03-12 07:30 Uhr"),
            notes=("Zugang ueber Hofeinfahrt.", "Rueckruf bitte an Frau Sommer."),
            custom_fields=(
                DemoCustomFieldValue("Auftragsnummer", "O-2026-0042"),
                DemoCustomFieldValue("Bauort", "Cafe am Markt, Rathausplatz 8"),
                DemoCustomFieldValue("Kundentyp", "gewerbe"),
                DemoCustomFieldValue("Netto-Betrag", "EUR 890.00"),
                DemoCustomFieldValue(field_name="Eilauftrag", value=True),
            ),
        ),
        _build_demo_document_spec(
            date_=date(2026, 3, 6),
            slug="materialrechnung-bauzentrum-nord",
            title="Materialrechnung Bauzentrum Nord",
            correspondent="Bauzentrum Nord GmbH",
            document_type="Rechnung",
            storage_path="Lieferanten",
            tags=("rechnung", "material", "gewerbe"),
            doc_no="R-77821",
            subject="Materiallieferung fuer die Baustelle Schule West",
            project="Lager Nord / Baustelle Schule West",
            rows=(
                "Montagebohrer 12 mm",
                "Dichtband und Kupferrohr",
                "Akkuschrauber-Zubehoer",
            ),
            totals=("Netto gesamt 842.50 EUR", "Faellig bis 2026-03-20"),
            custom_fields=(
                DemoCustomFieldValue("Auftragsnummer", "R-77821"),
                DemoCustomFieldValue("Bauort", "Lager Nord / Baustelle Schule West"),
                DemoCustomFieldValue("Kundentyp", "gewerbe"),
                DemoCustomFieldValue("Netto-Betrag", "EUR 842.50"),
                DemoCustomFieldValue("Faelligkeit", date(2026, 3, 20)),
            ),
        ),
        _build_demo_document_spec(
            date_=date(2026, 3, 8),
            slug="lieferschein-schulz-elektrotechnik",
            title="Lieferschein Elektromaterial Schulz",
            correspondent="Schulz Elektrotechnik",
            document_type="Lieferschein",
            storage_path="Baustellen",
            tags=("material", "gewerbe", "elektrik"),
            doc_no="LS-2026-110",
            subject="Lieferung fuer den Neubau Im Wiesengrund",
            project="Neubau Im Wiesengrund 22",
            rows=(
                "Schalterprogramm",
                "Kabelkanal und Sicherungen",
                "Installationsmaterial",
            ),
            notes=("Keine Berechnung, reine Warenuebergabe.",),
            custom_fields=(
                DemoCustomFieldValue("Auftragsnummer", "LS-2026-110"),
                DemoCustomFieldValue("Bauort", "Neubau Im Wiesengrund 22"),
                DemoCustomFieldValue("Kundentyp", "gewerbe"),
            ),
        ),
        _build_demo_document_spec(
            date_=date(2026, 3, 10),
            slug="stundennachweis-montage-team",
            title="Stundennachweis Montageteam",
            correspondent="Muster Bau GmbH",
            document_type="Stundennachweis",
            storage_path="Baustellen",
            tags=("montage", "sanierung", "object"),
            doc_no="SN-2026-011",
            subject="Montagearbeiten in der energetischen Sanierung",
            project="Am Park 3",
            rows=(
                "08:00-10:00 Leitungen gelegt",
                "10:15-12:00 Verteiler gesetzt",
                "13:00-16:30 Dichtheitspruefung",
            ),
            notes=("Unterschrift Vorarbeiter: M. Kraft",),
            custom_fields=(
                DemoCustomFieldValue("Auftragsnummer", "SN-2026-011"),
                DemoCustomFieldValue("Bauort", "Am Park 3"),
                DemoCustomFieldValue("Kundentyp", "oeffentlich"),
            ),
        ),
        _build_demo_document_spec(
            date_=date(2026, 3, 12),
            slug="rechnung-klein-und-sohn",
            title="Rechnung Klein und Sohn",
            correspondent="Klein & Sohn GmbH",
            document_type="Rechnung",
            storage_path="Kunden/Gewerbe",
            tags=("rechnung", "dringend", "gewerbe", "heizung"),
            doc_no="2026-0312",
            subject="Austausch Warmwasserboiler und Druckpruefung",
            project="Industriestrasse 7",
            rows=(
                "Austausch Warmwasserboiler",
                "Druckpruefung und Dichtigkeitskontrolle",
                "Entsorgung Altgeraet",
            ),
            totals=(
                "Netto 1.980 EUR",
                "Umsatzsteuer 19 Prozent",
                "Gesamtbetrag 2.356,20 EUR",
                "Zahlbar innerhalb von 14 Tagen",
            ),
            custom_fields=(
                DemoCustomFieldValue("Auftragsnummer", "2026-0312"),
                DemoCustomFieldValue("Bauort", "Industriestrasse 7"),
                DemoCustomFieldValue("Kundentyp", "gewerbe"),
                DemoCustomFieldValue("Netto-Betrag", "EUR 1980.00"),
                DemoCustomFieldValue("Faelligkeit", date(2026, 3, 26)),
                DemoCustomFieldValue(field_name="Eilauftrag", value=True),
            ),
        ),
    ]

    offer_specs = [
        (
            date(2026, 3, 14),
            "angebot-dachsanierung-lindner",
            "Angebot Dachsanierung Lindner",
            "Lindner Immobilienverwaltung",
            "Angebot",
            "Kunden/Gewerbe",
            ("angebot", "gewerbe", "sanierung"),
            "A-2026-0044",
            "Sanierung eines Flachdachs inklusive Abdichtung",
            "Mietobjekt Hauptstrasse 18",
            (
                "Vorbereitung und Absicherung der Baustelle 520 EUR",
                "Abdichtung und Schichtaufbau 2.280 EUR",
                "Geruest- und Entsorgungspauschale 940 EUR",
            ),
            ("Netto gesamt 3.740 EUR", "Gueltig bis 2026-04-14"),
            ("Projekt ruecklaeufig durch Eigentuemerverwaltung freigegeben.",),
            (
                DemoCustomFieldValue("Auftragsnummer", "A-2026-0044"),
                DemoCustomFieldValue("Bauort", "Hauptstrasse 18"),
                DemoCustomFieldValue("Kundentyp", "gewerbe"),
                DemoCustomFieldValue("Netto-Betrag", "EUR 3740.00"),
                DemoCustomFieldValue("Faelligkeit", date(2026, 4, 14)),
            ),
        ),
        (
            date(2026, 3, 16),
            "angebot-heizungsmodernisierung-werner",
            "Angebot Heizungsmodernisierung Werner",
            "Wagner Holzbau",
            "Angebot",
            "Kunden/Gewerbe",
            ("angebot", "heizung", "gewerbe"),
            "A-2026-0045",
            "Umstellung auf Brennwerttechnik im Betriebsgebaeude",
            "Werkhalle West",
            (
                "Demontage Altanlage 640 EUR",
                "Montage Brennwertkessel 2.950 EUR",
                "Einregulierung und Protokoll 430 EUR",
            ),
            ("Netto gesamt 4.020 EUR", "Gueltig bis 2026-04-16"),
            ("Einbau moeglich in Kalenderwoche 13.",),
            (
                DemoCustomFieldValue("Auftragsnummer", "A-2026-0045"),
                DemoCustomFieldValue("Bauort", "Werkhalle West"),
                DemoCustomFieldValue("Kundentyp", "gewerbe"),
                DemoCustomFieldValue("Netto-Betrag", "EUR 4020.00"),
            ),
        ),
        (
            date(2026, 3, 18),
            "angebot-elektro-ki-ta-sonnenweg",
            "Angebot Elektroinstallation Kita Sonnenweg",
            "Kindertagesstaette Sonnenweg",
            "Angebot",
            "Kunden/Privat",
            ("angebot", "elektrik", "privatkunde"),
            "A-2026-0046",
            "Erneuerung Beleuchtung und Sicherheitsstromkreis",
            "Kindertagesstaette Sonnenweg",
            (
                "Austausch LED-Leuchten 1.120 EUR",
                "Sicherheitsstromkreis 780 EUR",
                "Pruefung und Dokumentation 260 EUR",
            ),
            ("Netto gesamt 2.160 EUR", "Gueltig bis 2026-04-18"),
            ("Ausfuehrung ausserhalb der Oeffnungszeiten.",),
            (
                DemoCustomFieldValue("Auftragsnummer", "A-2026-0046"),
                DemoCustomFieldValue("Bauort", "Kindertagesstaette Sonnenweg"),
                DemoCustomFieldValue("Kundentyp", "oeffentlich"),
                DemoCustomFieldValue("Netto-Betrag", "EUR 2160.00"),
            ),
        ),
        (
            date(2026, 3, 20),
            "angebot-badrenovierung-meier",
            "Angebot Badrenovierung Meier",
            "Familie Becker",
            "Angebot",
            "Kunden/Privat",
            ("angebot", "bad", "privatkunde"),
            "A-2026-0047",
            "Komplette Badrenovierung im Einfamilienhaus",
            "Rosenweg 5",
            (
                "Abbruch und Entsorgung 860 EUR",
                "Sanitaerinstallation 1.540 EUR",
                "Fliesen- und Silikonarbeiten 1.280 EUR",
            ),
            ("Netto gesamt 3.680 EUR", "Gueltig bis 2026-04-20"),
            ("Kunde wuenscht Terminbesprechung am Abend.",),
            (
                DemoCustomFieldValue("Auftragsnummer", "A-2026-0047"),
                DemoCustomFieldValue("Bauort", "Rosenweg 5"),
                DemoCustomFieldValue("Kundentyp", "privat"),
                DemoCustomFieldValue("Netto-Betrag", "EUR 3680.00"),
            ),
        ),
        (
            date(2026, 3, 22),
            "angebot-sanitaer-hotel-lindenhof",
            "Angebot Sanitaerarbeiten Hotel Lindenhof",
            "Stadthalle West",
            "Angebot",
            "Kunden/Gewerbe",
            ("angebot", "wartung", "gewerbe"),
            "A-2026-0048",
            "Sanitaerwartung und Erneuerung Armaturen",
            "Hotel Lindenhof",
            (
                "Austausch Armaturen 1.020 EUR",
                "Wartung Warmwasserstrang 540 EUR",
                "Kleinteile und Dichtungen 180 EUR",
            ),
            ("Netto gesamt 1.740 EUR", "Gueltig bis 2026-04-22"),
            ("Bitte for Anreise telefonisch anmelden.",),
            (
                DemoCustomFieldValue("Auftragsnummer", "A-2026-0048"),
                DemoCustomFieldValue("Bauort", "Hotel Lindenhof"),
                DemoCustomFieldValue("Kundentyp", "gewerbe"),
                DemoCustomFieldValue("Netto-Betrag", "EUR 1740.00"),
            ),
        ),
    ]

    for item in offer_specs:
        demo_documents.append(
            _build_demo_document_spec(
                date_=item[0],
                slug=item[1],
                title=item[2],
                correspondent=item[3],
                document_type=item[4],
                storage_path=item[5],
                tags=item[6],
                doc_no=item[7],
                subject=item[8],
                project=item[9],
                rows=item[10],
                totals=item[11],
                notes=item[12],
                custom_fields=item[13],
            ),
        )

    order_specs = [
        (
            date(2026, 3, 24),
            "auftragsbestaetigung-immobilienverwaltung-lindner",
            "Auftragsbestaetigung Dachsanierung Lindner",
            "Lindner Immobilienverwaltung",
            "Auftragsbestaetigung",
            "Kunden/Gewerbe",
            ("auftrag", "gewerbe", "sanierung"),
            "O-2026-0050",
            "Dachsanierung im Mietobjekt",
            "Hauptstrasse 18",
            (
                "Baustelleneinrichtung 420 EUR",
                "Abdichtung und Entwaesserung 2.120 EUR",
                "Abschlussarbeiten 380 EUR",
            ),
            ("Terminbestaetigung fuer 2026-03-31.",),
            (
                DemoCustomFieldValue("Auftragsnummer", "O-2026-0050"),
                DemoCustomFieldValue("Bauort", "Hauptstrasse 18"),
                DemoCustomFieldValue("Kundentyp", "gewerbe"),
                DemoCustomFieldValue("Netto-Betrag", "EUR 2920.00"),
            ),
        ),
        (
            date(2026, 3, 25),
            "auftragsbestaetigung-hotel-lindenhof",
            "Auftragsbestaetigung Hotel Lindenhof",
            "Stadthalle West",
            "Auftragsbestaetigung",
            "Kunden/Gewerbe",
            ("auftrag", "wartung", "gewerbe"),
            "O-2026-0051",
            "Sanitaerwartung und Armaturenwechsel",
            "Hotel Lindenhof",
            (
                "Wartung Warmwasserstrang 540 EUR",
                "Armaturenwechsel 1.020 EUR",
                "Kleinteile 180 EUR",
            ),
            ("Bitte Schluessel ueber Empfang abholen.",),
            (
                DemoCustomFieldValue("Auftragsnummer", "O-2026-0051"),
                DemoCustomFieldValue("Bauort", "Hotel Lindenhof"),
                DemoCustomFieldValue("Kundentyp", "gewerbe"),
                DemoCustomFieldValue("Netto-Betrag", "EUR 1740.00"),
            ),
        ),
        (
            date(2026, 3, 26),
            "auftragsbestaetigung-elektro-kita-sonnenweg",
            "Auftragsbestaetigung Kita Sonnenweg",
            "Kindertagesstaette Sonnenweg",
            "Auftragsbestaetigung",
            "Kunden/Privat",
            ("auftrag", "elektrik", "privatkunde"),
            "O-2026-0052",
            "Elektroarbeiten und Sicherheitsstromkreis",
            "Kindertagesstaette Sonnenweg",
            (
                "Beleuchtung 1.120 EUR",
                "Sicherheitsstromkreis 780 EUR",
                "Pruefung 260 EUR",
            ),
            ("Arbeiten nachmittags nach Kinderbetrieb.",),
            (
                DemoCustomFieldValue("Auftragsnummer", "O-2026-0052"),
                DemoCustomFieldValue("Bauort", "Kindertagesstaette Sonnenweg"),
                DemoCustomFieldValue("Kundentyp", "oeffentlich"),
                DemoCustomFieldValue("Netto-Betrag", "EUR 2160.00"),
            ),
        ),
        (
            date(2026, 3, 27),
            "auftragsbestaetigung-becker-bad",
            "Auftragsbestaetigung Becker Badrenovierung",
            "Familie Becker",
            "Auftragsbestaetigung",
            "Kunden/Privat",
            ("auftrag", "bad", "privatkunde"),
            "O-2026-0053",
            "Badrenovierung im Einfamilienhaus",
            "Rosenweg 5",
            (
                "Abbruch 860 EUR",
                "Sanitaerinstallation 1.540 EUR",
                "Fliesenarbeiten 1.280 EUR",
            ),
            ("Bitte Musterfliesen for Ort abstimmen.",),
            (
                DemoCustomFieldValue("Auftragsnummer", "O-2026-0053"),
                DemoCustomFieldValue("Bauort", "Rosenweg 5"),
                DemoCustomFieldValue("Kundentyp", "privat"),
                DemoCustomFieldValue("Netto-Betrag", "EUR 3680.00"),
            ),
        ),
        (
            date(2026, 3, 28),
            "auftragsbestaetigung-werner-heizung",
            "Auftragsbestaetigung Heizungsmodernisierung Werner",
            "Wagner Holzbau",
            "Auftragsbestaetigung",
            "Kunden/Gewerbe",
            ("auftrag", "heizung", "gewerbe"),
            "O-2026-0054",
            "Umstellung auf Brennwerttechnik",
            "Werkhalle West",
            (
                "Demontage 640 EUR",
                "Montage Brennwertkessel 2.950 EUR",
                "Einregulierung 430 EUR",
            ),
            ("Material kann aus Lager Nord kommen.",),
            (
                DemoCustomFieldValue("Auftragsnummer", "O-2026-0054"),
                DemoCustomFieldValue("Bauort", "Werkhalle West"),
                DemoCustomFieldValue("Kundentyp", "gewerbe"),
                DemoCustomFieldValue("Netto-Betrag", "EUR 4020.00"),
            ),
        ),
    ]

    for item in order_specs:
        demo_documents.append(
            _build_demo_document_spec(
                date_=item[0],
                slug=item[1],
                title=item[2],
                correspondent=item[3],
                document_type=item[4],
                storage_path=item[5],
                tags=item[6],
                doc_no=item[7],
                subject=item[8],
                project=item[9],
                rows=item[10],
                totals=(),
                notes=item[11],
                custom_fields=item[12],
            ),
        )

    invoice_specs = [
        (
            date(2026, 3, 4),
            "rechnung-immobilienverwaltung-lindner",
            "Rechnung Lindner Immobilienverwaltung",
            "Lindner Immobilienverwaltung",
            "Rechnung",
            "Kunden/Gewerbe",
            ("rechnung", "gewerbe", "sanierung"),
            "R-2026-2001",
            "Schlussrechnung Dachsanierung",
            "Hauptstrasse 18",
            (
                "Abschlagsrechnung 1 1.250 EUR",
                "Abschlagsrechnung 2 1.600 EUR",
                "Schlussleistung 890 EUR",
            ),
            ("Zahlbar innerhalb von 10 Tagen.",),
            (
                "Netto gesamt 3.740 EUR",
                "Faellig bis 2026-03-14",
                "Projekt bereits abgenommen.",
            ),
            (
                DemoCustomFieldValue("Auftragsnummer", "R-2026-2001"),
                DemoCustomFieldValue("Bauort", "Hauptstrasse 18"),
                DemoCustomFieldValue("Kundentyp", "gewerbe"),
                DemoCustomFieldValue("Netto-Betrag", "EUR 3740.00"),
                DemoCustomFieldValue("Faelligkeit", date(2026, 3, 14)),
            ),
        ),
        (
            date(2026, 3, 9),
            "rechnung-hotel-lindenhof",
            "Rechnung Hotel Lindenhof",
            "Stadthalle West",
            "Rechnung",
            "Kunden/Gewerbe",
            ("rechnung", "wartung", "gewerbe"),
            "R-2026-2002",
            "Rechnung fuer Wartungsarbeiten",
            "Hotel Lindenhof",
            (
                "Wartung Warmwasserstrang 540 EUR",
                "Armaturenwechsel 1.020 EUR",
                "Kleinteile 180 EUR",
            ),
            ("Zahlbar innerhalb von 14 Tagen.",),
            (
                "Netto gesamt 1.740 EUR",
                "Faellig bis 2026-03-23",
            ),
            (
                DemoCustomFieldValue("Auftragsnummer", "R-2026-2002"),
                DemoCustomFieldValue("Bauort", "Hotel Lindenhof"),
                DemoCustomFieldValue("Kundentyp", "gewerbe"),
                DemoCustomFieldValue("Netto-Betrag", "EUR 1740.00"),
                DemoCustomFieldValue("Faelligkeit", date(2026, 3, 23)),
            ),
        ),
        (
            date(2026, 3, 11),
            "rechnung-kita-sonnenweg",
            "Rechnung Kita Sonnenweg",
            "Kindertagesstaette Sonnenweg",
            "Rechnung",
            "Kunden/Privat",
            ("rechnung", "elektrik", "gewerbe"),
            "R-2026-2003",
            "Rechnung fuer Elektroarbeiten",
            "Kindertagesstaette Sonnenweg",
            (
                "Beleuchtung 1.120 EUR",
                "Sicherheitsstromkreis 780 EUR",
                "Pruefung 260 EUR",
            ),
            ("Zahlbar innerhalb von 10 Tagen.",),
            ("Netto gesamt 2.160 EUR",),
            (
                DemoCustomFieldValue("Auftragsnummer", "R-2026-2003"),
                DemoCustomFieldValue("Bauort", "Kindertagesstaette Sonnenweg"),
                DemoCustomFieldValue("Kundentyp", "oeffentlich"),
                DemoCustomFieldValue("Netto-Betrag", "EUR 2160.00"),
                DemoCustomFieldValue("Faelligkeit", date(2026, 3, 21)),
            ),
        ),
        (
            date(2026, 3, 13),
            "rechnung-becker-bad",
            "Rechnung Becker Badrenovierung",
            "Familie Becker",
            "Rechnung",
            "Kunden/Privat",
            ("rechnung", "bad", "privatkunde", "dringend"),
            "R-2026-2004",
            "Rechnung fuer Badrenovierung",
            "Rosenweg 5",
            (
                "Demontage 860 EUR",
                "Sanitaerinstallation 1.540 EUR",
                "Fliesenarbeiten 1.280 EUR",
            ),
            ("Zahlbar innerhalb von 14 Tagen.",),
            ("Netto gesamt 3.680 EUR", "Terminabstimmung abgeschlossen."),
            (
                DemoCustomFieldValue("Auftragsnummer", "R-2026-2004"),
                DemoCustomFieldValue("Bauort", "Rosenweg 5"),
                DemoCustomFieldValue("Kundentyp", "privat"),
                DemoCustomFieldValue("Netto-Betrag", "EUR 3680.00"),
                DemoCustomFieldValue("Faelligkeit", date(2026, 3, 27)),
                DemoCustomFieldValue(field_name="Eilauftrag", value=True),
            ),
        ),
        (
            date(2026, 3, 15),
            "rechnung-werner-heizung",
            "Rechnung Werner Heizungsmodernisierung",
            "Wagner Holzbau",
            "Rechnung",
            "Kunden/Gewerbe",
            ("rechnung", "heizung", "gewerbe"),
            "R-2026-2005",
            "Rechnung fuer Brennwerttechnik",
            "Werkhalle West",
            (
                "Demontage 640 EUR",
                "Montage Brennwertkessel 2.950 EUR",
                "Einregulierung 430 EUR",
            ),
            ("Zahlbar innerhalb von 10 Tagen.",),
            ("Netto gesamt 4.020 EUR",),
            (
                DemoCustomFieldValue("Auftragsnummer", "R-2026-2005"),
                DemoCustomFieldValue("Bauort", "Werkhalle West"),
                DemoCustomFieldValue("Kundentyp", "gewerbe"),
                DemoCustomFieldValue("Netto-Betrag", "EUR 4020.00"),
                DemoCustomFieldValue("Faelligkeit", date(2026, 3, 25)),
            ),
        ),
    ]

    for item in invoice_specs:
        demo_documents.append(
            _build_demo_document_spec(
                date_=item[0],
                slug=item[1],
                title=item[2],
                correspondent=item[3],
                document_type=item[4],
                storage_path=item[5],
                tags=item[6],
                doc_no=item[7],
                subject=item[8],
                project=item[9],
                rows=item[10],
                totals=(),
                notes=item[11] + item[12],
                custom_fields=item[13],
            ),
        )

    delivery_specs = [
        (
            date(2026, 3, 17),
            "lieferschein-schulz-elektrotechnik",
            "Lieferschein Elektromaterial Schulz",
            "Schulz Elektrotechnik",
            "Lieferschein",
            "Baustellen",
            ("material", "gewerbe", "elektrik"),
            "LS-2026-110",
            "Lieferung fuer den Neubau",
            "Neubau Im Wiesengrund 22",
            (
                "Schalterprogramm",
                "Kabelkanal und Sicherungen",
                "Installationsmaterial",
            ),
            ("Keine Berechnung, reine Warenuebergabe.",),
            (),
            (
                DemoCustomFieldValue("Auftragsnummer", "LS-2026-110"),
                DemoCustomFieldValue("Bauort", "Neubau Im Wiesengrund 22"),
                DemoCustomFieldValue("Kundentyp", "gewerbe"),
            ),
        ),
        (
            date(2026, 3, 19),
            "lieferschein-stadtwerke-nord",
            "Lieferschein Stadtwerke Nord",
            "Stadtwerke Nord",
            "Lieferschein",
            "Baustellen",
            ("material", "gewerbe", "heizung"),
            "LS-2026-111",
            "Lieferung von Armaturen und Rohrmaterial",
            "Sanierungsobjekt Nord",
            (
                "Kupferrohr",
                "Absperrventile",
                "Isolierung und Fittings",
            ),
            ("Bitte Eingang Nordtor verwenden.",),
            (),
            (
                DemoCustomFieldValue("Auftragsnummer", "LS-2026-111"),
                DemoCustomFieldValue("Bauort", "Sanierungsobjekt Nord"),
                DemoCustomFieldValue("Kundentyp", "gewerbe"),
            ),
        ),
        (
            date(2026, 3, 21),
            "lieferschein-meyer-architektur",
            "Lieferschein Meyer Architektur",
            "Meyer & Partner Architektur",
            "Lieferschein",
            "Baustellen",
            ("material", "gewerbe", "sanierung"),
            "LS-2026-112",
            "Lieferung fuer Musterwohnung",
            "Musterwohnung Ost",
            (
                "Trockenbauprofile",
                "Montageschienen",
                "Schaltermaterial",
            ),
            ("Bitte mit dem Bauleiter abstimmen.",),
            (),
            (
                DemoCustomFieldValue("Auftragsnummer", "LS-2026-112"),
                DemoCustomFieldValue("Bauort", "Musterwohnung Ost"),
                DemoCustomFieldValue("Kundentyp", "gewerbe"),
            ),
        ),
        (
            date(2026, 3, 23),
            "lieferschein-hausverwaltung-kern",
            "Lieferschein Hausverwaltung Kern",
            "Lindner Immobilienverwaltung",
            "Lieferschein",
            "Baustellen",
            ("material", "gewerbe"),
            "LS-2026-113",
            "Lieferung fuer Instandsetzung",
            "Wohnanlage West",
            (
                "Ersatzteilset",
                "Dichtungen",
                "Montagezubehoer",
            ),
            ("Ohne berechnete Leistung.",),
            (),
            (
                DemoCustomFieldValue("Auftragsnummer", "LS-2026-113"),
                DemoCustomFieldValue("Bauort", "Wohnanlage West"),
                DemoCustomFieldValue("Kundentyp", "gewerbe"),
            ),
        ),
        (
            date(2026, 3, 29),
            "lieferschein-notdienst-weber",
            "Lieferschein Heiztechnik Weber",
            "Heiztechnik Weber",
            "Lieferschein",
            "Service/Notdienst",
            ("material", "notdienst", "heizung"),
            "LS-2026-114",
            "Sofortlieferung fuer den Notdienst",
            "Kesselraum Rathaus",
            (
                "Notfallpumpe",
                "Dichtmaterial",
                "Kleinmaterial fuer Reparatur",
            ),
            ("Auslieferung per Werkstattfahrer 21:15 Uhr.",),
            (),
            (
                DemoCustomFieldValue("Auftragsnummer", "LS-2026-114"),
                DemoCustomFieldValue("Bauort", "Kesselraum Rathaus"),
                DemoCustomFieldValue("Kundentyp", "oeffentlich"),
            ),
        ),
    ]

    for item in delivery_specs:
        demo_documents.append(
            _build_demo_document_spec(
                date_=item[0],
                slug=item[1],
                title=item[2],
                correspondent=item[3],
                document_type=item[4],
                storage_path=item[5],
                tags=item[6],
                doc_no=item[7],
                subject=item[8],
                project=item[9],
                rows=item[10],
                notes=item[11],
                custom_fields=item[13],
            ),
        )

    timesheet_specs = [
        (
            date(2026, 3, 30),
            "stundennachweis-montage-team",
            "Stundennachweis Montageteam",
            "Muster Bau GmbH",
            "Stundennachweis",
            "Baustellen",
            ("montage", "sanierung", "object"),
            "SN-2026-011",
            "Montagearbeiten in der energetischen Sanierung",
            "Am Park 3",
            (
                "08:00-10:00 Leitungen gelegt",
                "10:15-12:00 Verteiler gesetzt",
                "13:00-16:30 Dichtheitspruefung",
            ),
            ("Unterschrift Vorarbeiter: M. Kraft",),
            (),
            (
                DemoCustomFieldValue("Auftragsnummer", "SN-2026-011"),
                DemoCustomFieldValue("Bauort", "Am Park 3"),
                DemoCustomFieldValue("Kundentyp", "oeffentlich"),
            ),
        ),
        (
            date(2026, 3, 31),
            "stundennachweis-bad-team",
            "Stundennachweis Badteam",
            "Muster Bau GmbH",
            "Stundennachweis",
            "Baustellen",
            ("montage", "bad", "privatkunde"),
            "SN-2026-012",
            "Montagearbeiten an der Badrenovierung",
            "Rosenweg 5",
            (
                "07:30-11:00 Demontage Alteinrichtung",
                "11:30-15:45 Rohinstallation",
                "16:00-17:00 Baustellenaufraeumung",
            ),
            ("Kunde stellte Kaffee und Wasser bereit.",),
            (),
            (
                DemoCustomFieldValue("Auftragsnummer", "SN-2026-012"),
                DemoCustomFieldValue("Bauort", "Rosenweg 5"),
                DemoCustomFieldValue("Kundentyp", "privat"),
            ),
        ),
        (
            date(2026, 4, 1),
            "stundennachweis-heizungs-team",
            "Stundennachweis Heizungsmodernisierung",
            "Muster Bau GmbH",
            "Stundennachweis",
            "Baustellen",
            ("montage", "heizung", "gewerbe"),
            "SN-2026-013",
            "Montagearbeiten an der Heizungsanlage",
            "Werkhalle West",
            (
                "08:00-09:30 Demontage Altgeraet",
                "09:45-13:00 Montage Brennwertkessel",
                "13:45-16:15 Einregulierung",
            ),
            ("Abnahme durch Herrn Werner geplant.",),
            (),
            (
                DemoCustomFieldValue("Auftragsnummer", "SN-2026-013"),
                DemoCustomFieldValue("Bauort", "Werkhalle West"),
                DemoCustomFieldValue("Kundentyp", "gewerbe"),
            ),
        ),
        (
            date(2026, 4, 2),
            "stundennachweis-elektro-team",
            "Stundennachweis Elektroteam",
            "Muster Bau GmbH",
            "Stundennachweis",
            "Baustellen",
            ("montage", "elektrik", "gewerbe"),
            "SN-2026-014",
            "Montagearbeiten an der Elektroinstallation",
            "Kindertagesstaette Sonnenweg",
            (
                "07:45-10:00 Kabelwege vorbereitet",
                "10:15-14:00 Leitungen gezogen",
                "14:15-16:30 Pruefung und Messprotokoll",
            ),
            ("Arbeiten nur in Nebenraeumen.",),
            (),
            (
                DemoCustomFieldValue("Auftragsnummer", "SN-2026-014"),
                DemoCustomFieldValue("Bauort", "Kindertagesstaette Sonnenweg"),
                DemoCustomFieldValue("Kundentyp", "oeffentlich"),
            ),
        ),
        (
            date(2026, 4, 3),
            "stundennachweis-notdienst",
            "Stundennachweis Notdienst",
            "Muster Bau GmbH",
            "Stundennachweis",
            "Service/Notdienst",
            ("montage", "notdienst", "heizung"),
            "SN-2026-015",
            "Notdiensteinsatz mit Sofortreparatur",
            "Kesselraum Rathaus",
            (
                "21:00-21:30 Anfahrt",
                "21:30-23:10 Leckage lokalisiert",
                "23:10-23:45 Provisorische Reparatur",
            ),
            ("Einsatz wurde mit Bereitschaftspauschale abgerechnet.",),
            (),
            (
                DemoCustomFieldValue("Auftragsnummer", "SN-2026-015"),
                DemoCustomFieldValue("Bauort", "Kesselraum Rathaus"),
                DemoCustomFieldValue("Kundentyp", "oeffentlich"),
                DemoCustomFieldValue(field_name="Eilauftrag", value=True),
            ),
        ),
    ]

    for item in timesheet_specs:
        demo_documents.append(
            _build_demo_document_spec(
                date_=item[0],
                slug=item[1],
                title=item[2],
                correspondent=item[3],
                document_type=item[4],
                storage_path=item[5],
                tags=item[6],
                doc_no=item[7],
                subject=item[8],
                project=item[9],
                rows=item[10],
                notes=item[11],
                custom_fields=item[13],
            ),
        )

    maintenance_specs = [
        (
            date(2026, 4, 4),
            "wartungsbericht-heizungsanlage-cafe",
            "Wartungsbericht Heizungsanlage Cafe",
            "Cafe am Markt",
            "Wartungsbericht",
            "Service/Notdienst",
            ("wartung", "heizung", "gewerbe"),
            "WB-2026-021",
            "Jaehrliche Wartung der Heizungsanlage",
            "Cafe am Markt",
            (
                "Brenner gereinigt",
                "Druckspeicher geprueft",
                "Abgaswerte innerhalb der Tolerance",
            ),
            ("Naechster Termin in 12 Monaten.",),
            (),
            (
                DemoCustomFieldValue("Auftragsnummer", "WB-2026-021"),
                DemoCustomFieldValue("Bauort", "Cafe am Markt, Rathausplatz 8"),
                DemoCustomFieldValue("Kundentyp", "gewerbe"),
            ),
        ),
        (
            date(2026, 4, 5),
            "wartungsbericht-heizungsanlage-hotel",
            "Wartungsbericht Heizungsanlage Hotel",
            "Stadthalle West",
            "Wartungsbericht",
            "Service/Notdienst",
            ("wartung", "heizung", "gewerbe"),
            "WB-2026-022",
            "Wartung der Kesselanlage",
            "Hotel Lindenhof",
            (
                "Kessel gereinigt",
                "Pumpenlauf geprueft",
                "Sicherheitsventil getestet",
            ),
            ("Betrieb wieder freigegeben.",),
            (),
            (
                DemoCustomFieldValue("Auftragsnummer", "WB-2026-022"),
                DemoCustomFieldValue("Bauort", "Hotel Lindenhof"),
                DemoCustomFieldValue("Kundentyp", "gewerbe"),
            ),
        ),
        (
            date(2026, 4, 6),
            "wartungsbericht-elektro-kita",
            "Wartungsbericht Elektroanlage Kita",
            "Kindertagesstaette Sonnenweg",
            "Wartungsbericht",
            "Verwaltung",
            ("wartung", "elektrik", "oeffentlich"),
            "WB-2026-023",
            "Pruefung der Sicherheitsbeleuchtung",
            "Kindertagesstaette Sonnenweg",
            (
                "Sicherheitsbeleuchtung getestet",
                "Notstromfunktion prueft",
                "Pruefprotokoll ausgestellt",
            ),
            ("Keine Auffaelligkeiten.",),
            (),
            (
                DemoCustomFieldValue("Auftragsnummer", "WB-2026-023"),
                DemoCustomFieldValue("Bauort", "Kindertagesstaette Sonnenweg"),
                DemoCustomFieldValue("Kundentyp", "oeffentlich"),
            ),
        ),
        (
            date(2026, 4, 7),
            "wartungsbericht-lueftung-immobilien",
            "Wartungsbericht Lueftungsanlage",
            "Lindner Immobilienverwaltung",
            "Wartungsbericht",
            "Verwaltung",
            ("wartung", "gewerbe", "sanierung"),
            "WB-2026-024",
            "Wartung der Lueftungsanlage im Verwaltungsbau",
            "Hauptstrasse 18",
            (
                "Filter getauscht",
                "Lueftermotor gereinigt",
                "Luftdurchsatz gemessen",
            ),
            ("Protokoll an Hausverwaltung uebermittelt.",),
            (),
            (
                DemoCustomFieldValue("Auftragsnummer", "WB-2026-024"),
                DemoCustomFieldValue("Bauort", "Hauptstrasse 18"),
                DemoCustomFieldValue("Kundentyp", "gewerbe"),
            ),
        ),
        (
            date(2026, 4, 8),
            "wartungsbericht-notdienst-kessel",
            "Wartungsbericht Notdienst Kessel",
            "Heiztechnik Weber",
            "Wartungsbericht",
            "Service/Notdienst",
            ("wartung", "notdienst", "heizung"),
            "WB-2026-025",
            "Notdienstwartung am Kesselraum",
            "Kesselraum Rathaus",
            (
                "Leckage abgedichtet",
                "Druck wiederhergestellt",
                "Provisorium dokumentiert",
            ),
            ("Folgetermin mit Stadtwerke Nord vereinbart.",),
            (),
            (
                DemoCustomFieldValue("Auftragsnummer", "WB-2026-025"),
                DemoCustomFieldValue("Bauort", "Kesselraum Rathaus"),
                DemoCustomFieldValue("Kundentyp", "oeffentlich"),
                DemoCustomFieldValue(field_name="Eilauftrag", value=True),
            ),
        ),
    ]

    for item in maintenance_specs:
        demo_documents.append(
            _build_demo_document_spec(
                date_=item[0],
                slug=item[1],
                title=item[2],
                correspondent=item[3],
                document_type=item[4],
                storage_path=item[5],
                tags=item[6],
                doc_no=item[7],
                subject=item[8],
                project=item[9],
                rows=item[10],
                notes=item[11],
                custom_fields=item[13],
            ),
        )

    reminder_specs = [
        (
            date(2026, 4, 9),
            "zahlungserinnerung-immobilienverwaltung",
            "Zahlungserinnerung Lindner Immobilienverwaltung",
            "Lindner Immobilienverwaltung",
            "Rechnung",
            "Verwaltung",
            ("rechnung", "gewerbe", "dringend"),
            "ZR-2026-0301",
            "Zahlungserinnerung fuer die Dachsanierung",
            "Hauptstrasse 18",
            (
                "Oftener Rechnungsbetrag 3.740 EUR",
                "Forderung aus Leistungsabnahme",
            ),
            ("Bitte kurzfristig begleichen.",),
            (),
            (
                DemoCustomFieldValue("Auftragsnummer", "ZR-2026-0301"),
                DemoCustomFieldValue("Bauort", "Hauptstrasse 18"),
                DemoCustomFieldValue("Kundentyp", "gewerbe"),
                DemoCustomFieldValue("Netto-Betrag", "EUR 3740.00"),
                DemoCustomFieldValue("Faelligkeit", date(2026, 4, 19)),
            ),
        ),
        (
            date(2026, 4, 10),
            "zahlungserinnerung-hotel-lindenhof",
            "Zahlungserinnerung Hotel Lindenhof",
            "Stadthalle West",
            "Rechnung",
            "Verwaltung",
            ("rechnung", "gewerbe", "dringend"),
            "ZR-2026-0302",
            "Zahlungserinnerung fuer Wartungsrechnung",
            "Hotel Lindenhof",
            (
                "Oftener Rechnungsbetrag 1.740 EUR",
                "Wartung bereits abgeschlossen",
            ),
            ("Danke fuer die zuegige Bearbeitung.",),
            (),
            (
                DemoCustomFieldValue("Auftragsnummer", "ZR-2026-0302"),
                DemoCustomFieldValue("Bauort", "Hotel Lindenhof"),
                DemoCustomFieldValue("Kundentyp", "gewerbe"),
                DemoCustomFieldValue("Netto-Betrag", "EUR 1740.00"),
                DemoCustomFieldValue("Faelligkeit", date(2026, 4, 24)),
            ),
        ),
        (
            date(2026, 4, 11),
            "zahlungserinnerung-kita-sonnenweg",
            "Zahlungserinnerung Kita Sonnenweg",
            "Kindertagesstaette Sonnenweg",
            "Rechnung",
            "Verwaltung",
            ("rechnung", "elektrik", "dringend"),
            "ZR-2026-0303",
            "Zahlungserinnerung fuer Elektroarbeiten",
            "Kindertagesstaette Sonnenweg",
            (
                "Oftener Rechnungsbetrag 2.160 EUR",
                "Projekt der Sicherheitsbeleuchtung",
            ),
            ("Zahlung nach Rechnungsstellung vereinbart.",),
            (),
            (
                DemoCustomFieldValue("Auftragsnummer", "ZR-2026-0303"),
                DemoCustomFieldValue("Bauort", "Kindertagesstaette Sonnenweg"),
                DemoCustomFieldValue("Kundentyp", "oeffentlich"),
                DemoCustomFieldValue("Netto-Betrag", "EUR 2160.00"),
                DemoCustomFieldValue("Faelligkeit", date(2026, 4, 21)),
            ),
        ),
        (
            date(2026, 4, 12),
            "zahlungserinnerung-becker-bad",
            "Zahlungserinnerung Becker Badrenovierung",
            "Familie Becker",
            "Rechnung",
            "Verwaltung",
            ("rechnung", "bad", "dringend", "privatkunde"),
            "ZR-2026-0304",
            "Zahlungserinnerung fuer Badrenovierung",
            "Rosenweg 5",
            (
                "Oftener Rechnungsbetrag 3.680 EUR",
                "Badrenovierung bereits uebergeben",
            ),
            ("Mahnstufe 1, freundlich.",),
            (),
            (
                DemoCustomFieldValue("Auftragsnummer", "ZR-2026-0304"),
                DemoCustomFieldValue("Bauort", "Rosenweg 5"),
                DemoCustomFieldValue("Kundentyp", "privat"),
                DemoCustomFieldValue("Netto-Betrag", "EUR 3680.00"),
                DemoCustomFieldValue("Faelligkeit", date(2026, 4, 26)),
            ),
        ),
        (
            date(2026, 4, 13),
            "zahlungserinnerung-werner-heizung",
            "Zahlungserinnerung Werner Heizungsmodernisierung",
            "Wagner Holzbau",
            "Rechnung",
            "Verwaltung",
            ("rechnung", "heizung", "dringend"),
            "ZR-2026-0305",
            "Zahlungserinnerung fuer Brennwerttechnik",
            "Werkhalle West",
            (
                "Oftener Rechnungsbetrag 4.020 EUR",
                "Zahlungsziel bereits ueberschritten",
            ),
            ("Mahnung an die Buchhaltung versendet.",),
            (),
            (
                DemoCustomFieldValue("Auftragsnummer", "ZR-2026-0305"),
                DemoCustomFieldValue("Bauort", "Werkhalle West"),
                DemoCustomFieldValue("Kundentyp", "gewerbe"),
                DemoCustomFieldValue("Netto-Betrag", "EUR 4020.00"),
                DemoCustomFieldValue("Faelligkeit", date(2026, 4, 25)),
            ),
        ),
    ]

    for item in reminder_specs:
        demo_documents.append(
            _build_demo_document_spec(
                date_=item[0],
                slug=item[1],
                title=item[2],
                correspondent=item[3],
                document_type=item[4],
                storage_path=item[5],
                tags=item[6],
                doc_no=item[7],
                subject=item[8],
                project=item[9],
                rows=item[10],
                notes=item[11],
                custom_fields=item[13],
            ),
        )

    demo_documents.extend(_build_additional_demo_document_specs())
    demo_documents.extend(_build_historical_demo_document_specs())

    created_count = 0
    for spec in demo_documents:
        _, created = _upsert_document(
            spec,
            correspondent=correspondents[spec.correspondent],
            document_type=document_types[spec.document_type],
            storage_path=storage_paths[spec.storage_path],
            tags=tags,
            fields=fields,
            owner=owner,
        )
        if created:
            created_count += 1

    return (
        f"Created {created_count} demo document(s) for a classic crafts business "
        f"with {len(correspondents)} correspondents, {len(tags)} tags, "
        f"{len(document_types)} document types, {len(storage_paths)} storage paths "
        f"and {len(fields)} custom fields."
    )
