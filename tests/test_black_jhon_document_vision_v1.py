import base64
from types import SimpleNamespace

import fitz

from backend.modules.perguntas_pos_venda.ai import document_vision
from backend.modules.perguntas_pos_venda.ai.document_vision import (
    DocumentVisionBatchV1,
    MAX_DOCUMENT_VISION_PAGE_POINTS,
    MAX_DOCUMENT_VISION_IMAGES,
    PRODUCT_DOCUMENT_VISION_POLICY,
    collect_document_vision_batch,
    render_relevant_pdf_pages,
    technical_focus_terms,
)
from backend.schemas.ia import IAChatAttachment


def _pdf_with_pages(*texts: str) -> bytes:
    document = fitz.open()
    for text in texts:
        page = document.new_page(width=500, height=700)
        if text:
            page.insert_textbox(fitz.Rect(40, 40, 460, 660), text, fontsize=12)
    payload = document.tobytes()
    document.close()
    return payload


def test_render_selects_page_where_code_component_and_predicate_converge():
    payload = _pdf_with_pages(
        "Generic sales and contact page",
        "Honda Fit 1.4 2003 2004 2005 - sensor 37760-P00-003 installed in thermostat housing diagram",
        "Distractor 37760-PWA-J01 used by another component",
    )

    attachments, refs = render_relevant_pdf_pages(
        payload,
        source_url="https://catalog.example/manual.pdf",
        focus_terms=(
            "Honda Fit 1.4", "2003", "2004", "2005", "37760-P00-003",
            "installed in thermostat housing",
        ),
    )

    assert attachments
    assert refs[0]["page"] == 2
    assert refs[0]["source_url"] == "https://catalog.example/manual.pdf"
    assert refs[0]["attachment_name"] == attachments[0].name
    assert base64.b64decode(attachments[0].data_base64).startswith(b"\x89PNG\r\n\x1a\n")


def test_scanned_or_diagram_pdf_without_text_is_rendered_but_not_called_absent():
    payload = _pdf_with_pages("", "")

    attachments, refs = render_relevant_pdf_pages(
        payload,
        source_url="https://catalog.example/diagram.pdf",
        focus_terms=("37760-P00-003",),
    )

    assert len(attachments) == 2
    assert [row["page"] for row in refs] == [1, 2]
    assert all(row["text_layer"] is False for row in refs)


def test_batch_is_bounded_and_exposes_only_aggregate_diagnostics():
    payload = _pdf_with_pages(*[f"diagram page {index}" for index in range(12)])
    research = {
        "result": {
            "research_sources": ["https://catalog.example/fit.pdf"],
        },
    }

    batch = collect_document_vision_batch(
        research,
        focus_terms=("Honda Fit",),
        fetch_pdf=lambda _url, **_kwargs: payload,
    )

    assert batch.policy == PRODUCT_DOCUMENT_VISION_POLICY
    assert len(batch.attachments) == MAX_DOCUMENT_VISION_IMAGES
    assert batch.diagnostics() == {
        "policy": PRODUCT_DOCUMENT_VISION_POLICY,
        "documents_attempted": 1,
        "documents_processed": 1,
        "documents_unprocessed": 0,
        "images_created": MAX_DOCUMENT_VISION_IMAGES,
        "stop_reason": "image_limit",
    }
    assert "source_url" not in batch.diagnostics()


def test_long_scan_samples_pages_after_eight_and_marks_partial_coverage():
    payload = _pdf_with_pages(*([""] * 24))
    batch = collect_document_vision_batch(
        {"result": {"research_sources": ["https://catalog.example/scan.pdf"]}},
        focus_terms=("37760-P00-003",),
        fetch_pdf=lambda _url, **_kwargs: payload,
    )

    pages = [int(ref["page"]) for ref in batch.page_refs]
    assert len(pages) == MAX_DOCUMENT_VISION_IMAGES
    assert pages[0] == 1
    assert pages[-1] == 24
    assert any(page > 8 for page in pages)
    assert batch.stop_reason == "image_limit"


def test_pdf_discovery_uses_content_type_source_type_and_document_url():
    payload = _pdf_with_pages("Honda technical document")
    fetched = []
    sources = [
        {"url": "https://catalog.example/download?id=42", "content_type": "application/pdf"},
        {"url": "https://oem.example/opaque?id=7", "source_type": "technical_manual"},
        {"url": "https://maker.example/document/fit-sensor", "source_type": "official_oem"},
    ]

    def fetch(url, **_kwargs):
        fetched.append(url)
        return payload

    batch = collect_document_vision_batch(
        {"result": {"document_sources": sources}},
        focus_terms=("Honda",),
        fetch_pdf=fetch,
        maximum_images=3,
    )

    assert fetched == [source["url"] for source in sources]
    assert len(batch.attachments) == 3


def test_malformed_or_encrypted_like_pdf_is_unprocessed_without_factual_claim():
    research = {"result": {"research_sources": ["https://catalog.example/broken.pdf"]}}

    batch = collect_document_vision_batch(
        research,
        focus_terms=("sensor",),
        fetch_pdf=lambda _url, **_kwargs: b"%PDF-not-a-valid-document",
    )

    assert batch.attachments == ()
    assert batch.documents_unprocessed == 1
    assert batch.diagnostics()["images_created"] == 0


def test_focus_terms_remove_vin_but_keep_fit_engine_years_and_code():
    terms = technical_focus_terms(
        "Chassi 8AD2MKFWXCG035615: serve no Fit 1.4 2003 2004?",
        listing_title="Sensor 37760-P00-003",
    )

    joined = " ".join(terms)
    assert "8ad2mkfwxcg035615" not in joined
    assert "fit" in terms
    assert "1.4" in terms
    assert "2003" in terms
    assert "2004" in terms
    assert "37760-p00-003" in terms


def test_pdf_page_scan_stops_inside_document_when_deadline_expires(monkeypatch):
    payload = _pdf_with_pages(*[f"page {index}" for index in range(40)])
    monkeypatch.setattr(document_vision.time, "monotonic", lambda: 2.0)

    attachments, refs = render_relevant_pdf_pages(
        payload,
        source_url="https://catalog.example/long-manual.pdf",
        focus_terms=("sensor",),
        deadline_monotonic=1.0,
    )

    assert attachments == []
    assert refs == []


def test_oversized_pdf_page_is_rejected_before_pixmap_allocation():
    document = fitz.open()
    document.new_page(width=MAX_DOCUMENT_VISION_PAGE_POINTS + 1, height=700)
    payload = document.tobytes()
    document.close()

    attachments, refs = render_relevant_pdf_pages(
        payload,
        source_url="https://catalog.example/oversized.pdf",
        focus_terms=("sensor",),
    )

    assert attachments == []
    assert refs == []


def test_two_vision_rounds_share_one_deduplicated_eight_image_budget(monkeypatch):
    from backend.modules.perguntas_pos_venda.ai import client_workflow_support

    calls = []

    def fake_collect(_research, *, maximum_images, excluded_page_keys, **_kwargs):
        calls.append((maximum_images, set(excluded_page_keys)))
        start = len(excluded_page_keys) + 1
        batch_size = min(maximum_images, 5 if len(calls) == 1 else 3)
        refs = tuple({
            "attachment_name": f"page-{index}.png",
            "source_url": "https://catalog.example/manual.pdf",
            "page": index,
            "text_layer": True,
            "content_hash": "a" * 64,
        } for index in range(start, start + batch_size))
        attachments = tuple(IAChatAttachment(
            name=str(ref["attachment_name"]),
            mime_type="image/png",
            data_base64=base64.b64encode(b"\x89PNG\r\n\x1a\nsynthetic").decode("ascii"),
        ) for ref in refs)
        return DocumentVisionBatchV1(
            attachments=attachments,
            page_refs=refs,
            documents_attempted=1,
            documents_processed=1,
            documents_unprocessed=0,
            stop_reason="completed",
        )

    monkeypatch.setattr(client_workflow_support, "collect_document_vision_batch", fake_collect)
    monkeypatch.setattr(
        client_workflow_support,
        "research_session",
        lambda _value: SimpleNamespace(phase_deadline_monotonic=lambda _phase: 999999999.0),
    )
    client = SimpleNamespace(
        agent_input={
            "question": {"text": "Onde instala?"},
            "item": {"title": "Sensor"},
        },
        context_pipeline=[],
    )

    initial = client_workflow_support.prepare_document_vision(
        client, {}, [{"result": {}}], phase="initial",
    )
    gap = client_workflow_support.prepare_document_vision(
        client, {}, [{"result": {}}], phase="gap",
    )
    exhausted = client_workflow_support.prepare_document_vision(
        client, {}, [{"result": {}}], phase="gap",
    )

    assert len(initial) == 5
    assert len(gap) == 3
    assert exhausted == []
    assert calls[0] == (8, set())
    assert calls[1][0] == 3
    assert len(calls[1][1]) == 5
    assert client._document_vision_images_used == MAX_DOCUMENT_VISION_IMAGES
    assert len(client._document_vision_seen_page_keys) == MAX_DOCUMENT_VISION_IMAGES
    assert client.context_pipeline[-1]["stop_reason"] == "global_image_limit"
