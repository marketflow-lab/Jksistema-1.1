from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.modules.context_hub.store_sku_compiler import load_legacy_public_guidance


STORE = "b1e5a6efb16c0db69bba1836"
OTHER_STORE = "666955b171470f4fb6f6a4a8"


def _write(info: Path, payload: dict, *, tenant: str = "tenant-a") -> Path:
    root = info / tenant
    root.mkdir(parents=True, exist_ok=True)
    path = root / "ia_treinamento_perguntas_pos_venda.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.mark.parametrize("store_layers", [{}, {"JK Pecas": {}}, {f"store_id:{OTHER_STORE}": {}}])
def test_legacy_global_guidance_cannot_establish_store_ownership(tmp_path: Path, store_layers: dict):
    source = _write(tmp_path, {
        "orientacoes_perguntas": "Regra global",
        "notas_sku": {"001": {"notas": "Nota global"}},
        "exemplos": {"perguntas_anuncio": [{
            "sku": "001", "pergunta": "Pergunta", "resposta": "Equipe JK Pecas",
        }]},
        "por_loja": store_layers,
    })
    before = source.read_bytes()

    assert load_legacy_public_guidance("tenant-a", store_ref=STORE, info_root=tmp_path) == ({}, {}, {})
    assert source.read_bytes() == before


def test_exact_store_layer_does_not_fill_missing_fields_from_global_profile(tmp_path: Path):
    _write(tmp_path, {
        "orientacoes": "Global antiga",
        "orientacoes_perguntas": "Global",
        "contexto_loja": "Contexto global",
        "compatibilidade_autopecas": "Compatibilidade global",
        "proibicoes": "Proibicao global",
        "notas_sku": {"001": {"notas": "Global"}},
        "exemplos": {"perguntas_anuncio": [{"resposta": "Global"}]},
        "por_loja": {f"store_id:{STORE}": {"contexto_loja": "Contexto exato"}},
    })

    assert load_legacy_public_guidance("tenant-a", store_ref=STORE, info_root=tmp_path) == (
        {"contexto_loja": "Contexto exato"}, {}, {},
    )


def test_store_examples_follow_exact_sku_preserving_zeroes_content_and_unknown_fields(tmp_path: Path):
    general = {"pergunta": "Entrega?", "resposta": "Consulte o anuncio.", "campo_futuro": ["a"]}
    product = {
        "sku": "001", "pergunta": "  Pergunta\ncom quebra  ", "resposta": "Resposta  ",
        "observacao": "Modelo especifico", "updated_at": "2026-09-08",
        "campo_futuro": {"valor": 7},
    }
    other_product = {"sku": "1", "pergunta": "Outro produto", "resposta": "Outro"}
    _write(tmp_path, {"por_loja": {
        f"store_id:{STORE}": {
            "orientacoes": "Regra da loja",
            "notas_sku": {"001": {"notas": "Nota existente", "exemplos_perguntas": [product]}},
            "exemplos": {"perguntas_anuncio": [general, product, other_product]},
        },
        f"store_id:{OTHER_STORE}": {
            "exemplos": {"perguntas_anuncio": [{"sku": "001", "resposta": "Outra loja"}]},
        },
    }})
    _write(tmp_path, {"por_loja": {f"store_id:{STORE}": {
        "exemplos": {"perguntas_anuncio": [{"sku": "001", "resposta": "Outro tenant"}]},
    }}}, tenant="tenant-b")

    general_guidance, notes, quarantine = load_legacy_public_guidance(
        "tenant-a", store_ref=STORE, info_root=tmp_path,
    )

    assert general_guidance == {"orientacoes_perguntas": "Regra da loja", "exemplos_perguntas": [general]}
    assert notes == {
        "001": {"notas": "Nota existente", "exemplos_perguntas": [product]},
        "1": {"exemplos_perguntas": [other_product]},
    }
    assert quarantine == {}


@pytest.mark.parametrize("invalid_sku", [True, {}, [], "x" * 101, "bad\x00sku"])
def test_malformed_sku_example_cannot_become_general_guidance(tmp_path: Path, invalid_sku):
    _write(tmp_path, {"por_loja": {f"store_id:{STORE}": {
        "exemplos": {"perguntas_anuncio": [{"sku": invalid_sku, "resposta": "Nao generalizar"}]},
    }}})

    assert load_legacy_public_guidance("tenant-a", store_ref=STORE, info_root=tmp_path) == ({}, {}, {})


def test_unresolved_legacy_sku_keeps_example_out_of_general_guidance(tmp_path: Path):
    example = {"sku": "1599", "pergunta": "Pergunta", "resposta": "Resposta"}
    _write(tmp_path, {"por_loja": {f"store_id:{STORE}": {
        "exemplos": {"perguntas_anuncio": [example]},
    }}})

    assert load_legacy_public_guidance("tenant-a", store_ref=STORE, info_root=tmp_path) == (
        {}, {}, {"1599": {"exemplos_perguntas": [example]}},
    )
