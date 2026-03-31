from paperless.remote_import import extract_filename_from_content_disposition


def test_extract_filename_from_content_disposition_prefers_filename_star() -> None:
    disposition = (
        "attachment; "
        'filename="2026-03-30 Abschlagsrechnung.pdf"; '
        "filename*=utf-8''2026-03-30%20Abschlagsrechnung.pdf"
    )

    assert (
        extract_filename_from_content_disposition(
            disposition,
            fallback="document-11",
        )
        == "2026-03-30 Abschlagsrechnung.pdf"
    )


def test_extract_filename_from_content_disposition_uses_filename_fallback() -> None:
    disposition = 'attachment; filename="invoice.pdf"'

    assert (
        extract_filename_from_content_disposition(
            disposition,
            fallback="document-11",
        )
        == "invoice.pdf"
    )


def test_extract_filename_from_content_disposition_uses_default_fallback() -> None:
    assert (
        extract_filename_from_content_disposition(
            "",
            fallback="document-11",
        )
        == "document-11"
    )
