from pathlib import Path

import pytest

from backend.services import whatsapp_bridge
from backend.services.whatsapp import media


def test_media_component_matches_facade_for_requests_and_references() -> None:
    text = (
        "Mande a foto do SKU 001. "
        "![frente](cadastro_fotos/001.jpg) "
        "![duplicada](cadastro_fotos/001.jpg) "
        "/api/cadastro/foto-arquivo/002.png"
    )

    assert media.image_requested(text) == whatsapp_bridge._whatsapp_image_requested(text) is True
    assert media.image_references(text) == whatsapp_bridge._whatsapp_image_references(text) == [
        ("frente", "cadastro_fotos/001.jpg"),
        ("", "/api/cadastro/foto-arquivo/002.png"),
    ]
    assert media.strip_image_references(text) == whatsapp_bridge._whatsapp_strip_image_references(text)


def test_media_references_are_deduplicated_and_limited() -> None:
    text = " ".join(f"![foto {index}](cadastro_fotos/{index}.jpg)" for index in range(8))
    assert len(media.image_references(text)) == media.WHATSAPP_MAX_OUTBOUND_IMAGES == 3


def test_paths_must_remain_inside_an_authorized_root(tmp_path: Path) -> None:
    root = tmp_path / "tenant" / "cadastro_fotos"
    root.mkdir(parents=True)
    inside = root / "001.jpg"
    outside = tmp_path / "outro" / "001.jpg"

    assert media.path_within(inside, [root]) == whatsapp_bridge._whatsapp_path_within(inside, [root]) is True
    assert media.path_within(outside, [root]) == whatsapp_bridge._whatsapp_path_within(outside, [root]) is False


@pytest.mark.parametrize(
    ("signature", "expected"),
    [
        (b"\xff\xd8\xff\xe0imagem", "image/jpeg"),
        (b"\x89PNG\r\n\x1a\nresto", "image/png"),
        (b"GIF89aresto", "image/gif"),
        (b"RIFF1234WEBP", "image/webp"),
        (b"BMarquivo", "image/bmp"),
        (b"conteudo inseguro", ""),
    ],
)
def test_image_mime_uses_magic_bytes_not_extension(tmp_path: Path, signature: bytes, expected: str) -> None:
    path = tmp_path / "arquivo.jpg"
    path.write_bytes(signature)
    assert media.image_mime(path) == whatsapp_bridge._whatsapp_image_mime(path) == expected


def test_sku_matching_preserves_exact_and_numeric_zero_rules() -> None:
    assert media.sku_candidates("SKU 001, SKU: ABC-9 e sku 001") == ["001", "ABC-9"]
    assert media.image_matches_skus(Path("0001.jpg"), ["001"]) is True
    assert media.image_matches_skus(Path("foto-001.jpg"), ["001"]) is False
    assert media.image_matches_skus(Path("ABC-9.jpg"), ["ABC-9"]) is True
    assert media.normalized_sku("ABC-0009", strip_numeric_zeroes=True) == "ABC9"


def test_caption_filename_and_extension_match_facade() -> None:
    response = "*Relatorio de estoque*\nProduto 001 disponivel.\n![foto](cadastro_fotos/001.jpg)"
    assert media.outbound_image_caption(response, "Produto") == whatsapp_bridge._whatsapp_outbound_image_caption(
        response, "Produto"
    )
    assert media.safe_filename("../produto 001?.jpg", "arquivo.bin") == whatsapp_bridge._safe_filename(
        "../produto 001?.jpg", "arquivo.bin"
    )
    assert media.media_extension("audio/ogg") == whatsapp_bridge._media_extension("audio/ogg") == ".ogg"
