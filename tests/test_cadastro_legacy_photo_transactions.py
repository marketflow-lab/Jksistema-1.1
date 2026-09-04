from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from backend.services import (
    cadastro_fotos,
    cadastro_lojas_produtos,
    cadastro_produtos,
)


CLIENT_ID = "cliente-a"


def _configurar(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    info_root = tmp_path / "info"

    def tenant_path(client_id: str) -> str:
        tenant = info_root / str(client_id)
        tenant.mkdir(parents=True, exist_ok=True)
        return str(tenant)

    for modulo in (cadastro_fotos, cadastro_lojas_produtos, cadastro_produtos):
        monkeypatch.setattr(modulo, "get_tenant_path", tenant_path)
    monkeypatch.setattr(cadastro_fotos, "PASTA_INFO", str(info_root), raising=False)
    monkeypatch.setattr(
        cadastro_fotos,
        "_cadastro_carregar_lojas_foto",
        lambda _client_id: [],
    )
    return Path(tenant_path(CLIENT_ID))


def _data_url(conteudo: bytes, tipo: str = "jpeg") -> str:
    return f"data:image/{tipo};base64,{base64.b64encode(conteudo).decode('ascii')}"


def _escrever_catalogo(tenant: Path, conteudo: str) -> Path:
    caminho = tenant / "cadastro_produtos.csv"
    caminho.write_text(conteudo, encoding="utf-8")
    return caminho


def _ativar_escopo_estrito(tenant: Path) -> None:
    (tenant / cadastro_fotos.CADASTRO_FOTOS_CONFIG_ARQUIVO).write_text(
        json.dumps(
            {
                "schema": cadastro_fotos.CADASTRO_FOTOS_CONFIG_SCHEMA,
                "strict_store_scope": True,
                "shared_groups": [],
            }
        ),
        encoding="utf-8",
    )


def test_atualizacao_reverte_csv_e_variantes_se_commit_falha_apos_foto(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    tenant = _configurar(monkeypatch, tmp_path)
    catalogo = _escrever_catalogo(
        tenant,
        "sku,nome,foto\n009,Produto,cadastro_fotos/009.png\n",
    )
    catalogo_antes = catalogo.read_bytes()
    antiga = tenant / "cadastro_fotos" / "009.png"
    antiga.parent.mkdir(parents=True)
    antiga.write_bytes(b"foto-antiga")
    commit_chamado = False

    def falhar_commit(*_args, **_kwargs) -> None:
        nonlocal commit_chamado
        commit_chamado = True
        assert (tenant / "cadastro_fotos" / "009.jpg").read_bytes() == b"foto-nova"
        assert not antiga.exists()
        raise OSError("falha de CSV injetada")

    monkeypatch.setattr(cadastro_produtos, "_cadastro_salvar_dataframe_atomico", falhar_commit)

    with pytest.raises(HTTPException) as erro:
        asyncio.run(
            cadastro_produtos.atualizar_produto_cadastro_completo(
                "009",
                {
                    "__foto_data_url": _data_url(b"foto-nova"),
                    "__foto_filename": "produto.jpg",
                },
                CLIENT_ID,
            )
        )

    assert erro.value.status_code == 500
    assert commit_chamado is True
    assert catalogo.read_bytes() == catalogo_antes
    assert antiga.read_bytes() == b"foto-antiga"
    assert not (tenant / "cadastro_fotos" / "009.jpg").exists()


def test_inclusao_remove_foto_orfa_se_commit_do_csv_falha(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    tenant = _configurar(monkeypatch, tmp_path)
    catalogo = _escrever_catalogo(tenant, "sku,nome\n001,Existente\n")
    catalogo_antes = catalogo.read_bytes()
    commit_chamado = False

    def falhar_commit(*_args, **_kwargs) -> None:
        nonlocal commit_chamado
        commit_chamado = True
        assert (tenant / "cadastro_fotos" / "009.jpg").read_bytes() == b"foto-nova"
        raise OSError("falha de CSV injetada")

    monkeypatch.setattr(cadastro_produtos, "_cadastro_salvar_dataframe_atomico", falhar_commit)

    with pytest.raises(HTTPException) as erro:
        asyncio.run(
            cadastro_produtos.incluir_produto_cadastro_completo(
                {
                    "sku": "009",
                    "nome": "Novo",
                    "__foto_data_url": _data_url(b"foto-nova"),
                    "__foto_filename": "produto.jpg",
                },
                CLIENT_ID,
            )
        )

    assert erro.value.status_code == 500
    assert commit_chamado is True
    assert catalogo.read_bytes() == catalogo_antes
    assert not (tenant / "cadastro_fotos" / "009.jpg").exists()


def test_nome_original_nao_substitui_identidade_sku_da_foto_legada(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    tenant = _configurar(monkeypatch, tmp_path)
    catalogo = _escrever_catalogo(tenant, "sku,nome\n009,Produto\n")

    resultado = asyncio.run(
        cadastro_produtos.atualizar_produto_cadastro_completo(
            "009",
            {
                "__foto_data_url": _data_url(b"foto-nova"),
                "__foto_filename": "produto.jpg",
            },
            CLIENT_ID,
        )
    )

    assert resultado["foto"] == "cadastro_fotos/009.jpg"
    assert (tenant / "cadastro_fotos" / "009.jpg").read_bytes() == b"foto-nova"
    assert "cadastro_fotos/009.jpg" in catalogo.read_text(encoding="utf-8")
    assert not (tenant / "cadastro_fotos" / "produto.jpg").exists()


@pytest.mark.parametrize("operacao", ["atualizar", "atualizar_cg", "incluir"])
def test_escopo_estrito_rejeita_referencia_local_em_rota_sem_store_id(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    operacao: str,
) -> None:
    tenant = _configurar(monkeypatch, tmp_path)
    catalogo = _escrever_catalogo(tenant, "sku,nome\n009,Produto\n")
    _ativar_escopo_estrito(tenant)
    antes = catalogo.read_bytes()

    with pytest.raises(HTTPException) as erro:
        if operacao in {"atualizar", "atualizar_cg"}:
            asyncio.run(
                cadastro_produtos.atualizar_produto_cadastro_completo(
                    "009",
                    {
                        "cg_foto" if operacao == "atualizar_cg" else "foto": (
                            "cadastro_fotos/009.jpg"
                        )
                    },
                    CLIENT_ID,
                )
            )
        else:
            asyncio.run(
                cadastro_produtos.incluir_produto_cadastro_completo(
                    {
                        "sku": "010",
                        "nome": "Novo",
                        "foto": "cadastro_fotos/lojas/sid-invalido/010.jpg",
                    },
                    CLIENT_ID,
                )
            )

    assert erro.value.status_code == 409
    assert erro.value.detail["code"] == "store_id_required"
    assert catalogo.read_bytes() == antes


def test_escopo_estrito_preserva_url_externa_sem_trata_la_como_foto_local(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    tenant = _configurar(monkeypatch, tmp_path)
    catalogo = _escrever_catalogo(tenant, "sku,nome\n009,Produto\n")
    _ativar_escopo_estrito(tenant)
    externa = "https://http2.mlstatic.com/produto-009.jpg"

    resultado = asyncio.run(
        cadastro_produtos.atualizar_produto_cadastro_completo(
            "009",
            {"foto": externa},
            CLIENT_ID,
        )
    )

    assert resultado["foto"] == externa
    assert externa in catalogo.read_text(encoding="utf-8")
