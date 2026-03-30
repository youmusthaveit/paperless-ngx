from __future__ import annotations

import datetime
from typing import TYPE_CHECKING

import pytest
from django.utils import timezone

from documents.parsers import ParseError
from paperless.parsers import ParserContext
from paperless.parsers import ParserProtocol
from paperless.parsers.registry import get_parser_registry
from paperless.parsers.xrechnung import XRechnungDocumentParser

if TYPE_CHECKING:
    from pathlib import Path

    from pytest_httpx import HTTPXMock


class TestXRechnungParserRegistryInterface:
    def test_satisfies_parser_protocol(self) -> None:
        assert isinstance(XRechnungDocumentParser(), ParserProtocol)

    def test_supported_mime_types(self) -> None:
        mime_types = XRechnungDocumentParser.supported_mime_types()
        assert mime_types == {
            "application/xml": ".xml",
            "text/xml": ".xml",
            "application/pdf": ".pdf",
        }

    def test_score_returns_high_score_for_xrechnung(
        self,
        sample_xrechnung_cii_file: Path,
    ) -> None:
        score = XRechnungDocumentParser.score(
            "application/xml",
            sample_xrechnung_cii_file.name,
            sample_xrechnung_cii_file,
        )
        assert score == 30

    def test_score_returns_none_for_other_xml(self, tmp_path: Path) -> None:
        xml_file = tmp_path / "other.xml"
        xml_file.write_text("<root><value>test</value></root>", encoding="utf-8")

        score = XRechnungDocumentParser.score(
            "application/xml",
            xml_file.name,
            xml_file,
        )
        assert score is None

    def test_parser_registry_selects_xrechnung_parser(
        self,
        sample_xrechnung_cii_file: Path,
    ) -> None:
        parser_cls = get_parser_registry().get_parser_for_file(
            "application/xml",
            sample_xrechnung_cii_file.name,
            sample_xrechnung_cii_file,
        )
        assert parser_cls is XRechnungDocumentParser

    def test_score_returns_high_score_for_zugferd_pdf(
        self,
        sample_zugferd_pdf_file: Path,
    ) -> None:
        score = XRechnungDocumentParser.score(
            "application/pdf",
            sample_zugferd_pdf_file.name,
            sample_zugferd_pdf_file,
        )
        assert score == 30

    def test_parser_registry_selects_xrechnung_parser_for_zugferd_pdf(
        self,
        sample_zugferd_pdf_file: Path,
    ) -> None:
        parser_cls = get_parser_registry().get_parser_for_file(
            "application/pdf",
            sample_zugferd_pdf_file.name,
            sample_zugferd_pdf_file,
        )
        assert parser_cls is XRechnungDocumentParser

    def test_requires_pdf_rendition_is_true(self) -> None:
        assert XRechnungDocumentParser().requires_pdf_rendition is True

    def test_can_produce_archive_is_false(self) -> None:
        assert XRechnungDocumentParser().can_produce_archive is False


@pytest.mark.django_db()
class TestXRechnungParser:
    def test_parse(
        self,
        httpx_mock: HTTPXMock,
        xrechnung_parser: XRechnungDocumentParser,
        sample_xrechnung_cii_file: Path,
        simple_digital_pdf_file: Path,
    ) -> None:
        httpx_mock.add_response(
            url="http://localhost:3000/forms/chromium/convert/html",
            method="POST",
            content=simple_digital_pdf_file.read_bytes(),
        )

        xrechnung_parser.configure(ParserContext())
        xrechnung_parser.parse(sample_xrechnung_cii_file, "application/xml")

        text = xrechnung_parser.get_text()
        assert text is not None
        assert "XRechnung" in text
        assert "1122334455" in text
        assert "TÜV Rheinland GmbH" in text
        assert "Kunde GmbH & Co.KG" in text

        archive_path = xrechnung_parser.get_archive_path()
        assert archive_path is not None
        assert archive_path.read_bytes() == simple_digital_pdf_file.read_bytes()

        assert xrechnung_parser.get_date() == datetime.datetime(
            2024,
            12,
            6,
            tzinfo=timezone.get_current_timezone(),
        )

    def test_parse_invalid_xml_raises_parse_error(
        self,
        xrechnung_parser: XRechnungDocumentParser,
        tmp_path: Path,
    ) -> None:
        broken_xml = tmp_path / "broken.xml"
        broken_xml.write_text("<root>", encoding="utf-8")

        with pytest.raises(ParseError):
            xrechnung_parser.parse(broken_xml, "application/xml")

    def test_extract_metadata(
        self,
        xrechnung_parser: XRechnungDocumentParser,
        sample_xrechnung_cii_file: Path,
    ) -> None:
        metadata = xrechnung_parser.extract_metadata(
            sample_xrechnung_cii_file,
            "application/xml",
        )

        assert any(entry["key"] == "invoice_number" for entry in metadata)
        assert any(entry["key"] == "seller_name" for entry in metadata)

    def test_parse_zugferd_pdf(
        self,
        xrechnung_parser: XRechnungDocumentParser,
        sample_zugferd_pdf_file: Path,
    ) -> None:
        xrechnung_parser.configure(ParserContext())
        xrechnung_parser.parse(sample_zugferd_pdf_file, "application/pdf")

        text = xrechnung_parser.get_text()
        assert text is not None
        assert "XRechnung" in text
        assert "000073" in text
        assert "achtmacher KOMMUNIKATION" in text
        assert "Klavierklang GmbH" in text

        assert xrechnung_parser.get_archive_path() == sample_zugferd_pdf_file
        assert (
            xrechnung_parser.get_page_count(
                sample_zugferd_pdf_file,
                "application/pdf",
            )
            == 1
        )

    def test_extract_metadata_from_zugferd_pdf(
        self,
        xrechnung_parser: XRechnungDocumentParser,
        sample_zugferd_pdf_file: Path,
    ) -> None:
        metadata = xrechnung_parser.extract_metadata(
            sample_zugferd_pdf_file,
            "application/pdf",
        )

        assert any(
            entry["key"] == "invoice_number" and entry["value"] == "000073"
            for entry in metadata
        )
        assert any(
            entry["key"] == "seller_name"
            and entry["value"] == "achtmacher KOMMUNIKATION"
            for entry in metadata
        )

    def test_get_page_count_uses_generated_pdf(
        self,
        httpx_mock: HTTPXMock,
        xrechnung_parser: XRechnungDocumentParser,
        sample_xrechnung_cii_file: Path,
        simple_digital_pdf_file: Path,
    ) -> None:
        httpx_mock.add_response(content=simple_digital_pdf_file.read_bytes())

        xrechnung_parser.parse(sample_xrechnung_cii_file, "application/xml")

        assert (
            xrechnung_parser.get_page_count(
                sample_xrechnung_cii_file,
                "application/xml",
            )
            == 1
        )

    @pytest.mark.httpx_mock(can_send_already_matched_responses=True)
    def test_parse_retries_after_initial_gotenberg_failure(
        self,
        httpx_mock: HTTPXMock,
        xrechnung_parser: XRechnungDocumentParser,
        sample_xrechnung_cii_file: Path,
        simple_digital_pdf_file: Path,
    ) -> None:
        httpx_mock.add_response(
            url="http://localhost:3000/forms/chromium/convert/html",
            method="POST",
            status_code=500,
        )
        httpx_mock.add_response(
            url="http://localhost:3000/forms/chromium/convert/html",
            method="POST",
            content=simple_digital_pdf_file.read_bytes(),
        )

        xrechnung_parser.parse(sample_xrechnung_cii_file, "application/xml")

        assert xrechnung_parser.get_archive_path() is not None
