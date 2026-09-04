import asyncio
import base64
import csv
import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from backend.schemas import CadastroProdutoLojaAtualizacaoRequest
from backend.services import cadastro_custos, cadastro_fotos, cadastro_lojas_produtos, integracoes


def _configure(monkeypatch, tmp_path, stores_by_client=None):
    info_root = tmp_path / "info"
    stores_by_client = stores_by_client or {
        "cliente-a": [
            {"store_id": "store-a", "nome": "Loja A"},
            {"store_id": "store-b", "nome": "Loja B"},
        ],
        "cliente-b": [{"store_id": "store-a", "nome": "Loja A"}],
    }

    def tenant_path(client_id):
        path = info_root / str(client_id)
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    monkeypatch.setattr(cadastro_lojas_produtos, "get_tenant_path", tenant_path)
    monkeypatch.setattr(cadastro_custos, "get_tenant_path", tenant_path)
    monkeypatch.setattr(cadastro_fotos, "get_tenant_path", tenant_path)
    monkeypatch.setattr(cadastro_fotos, "PASTA_INFO", str(info_root), raising=False)
    monkeypatch.setattr(
        integracoes,
        "carregar_lojas",
        lambda client_id: stores_by_client.get(str(client_id), []),
    )
    return info_root, stores_by_client


def _write_csv(path: Path, rows):
    rows = list(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _foto_data_url(conteudo: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(conteudo).decode("ascii")


_PHOTO_ALIAS_FIELDS = (
    "imagem",
    "image_url",
    "link_imagem",
    "picture",
    "thumbnail_url",
    "link_foto",
)


def _ativar_escopo_fotos_estrito(tenant: Path) -> None:
    tenant.mkdir(parents=True, exist_ok=True)
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


def test_tenant_isolation_and_same_sku_in_two_stores(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)

    produto_a = cadastro_lojas_produtos.salvar_produto_loja(
        "cliente-a", "store-a", {"sku": "1", "nome": "Produto A"}
    )
    produto_b = cadastro_lojas_produtos.salvar_produto_loja(
        "cliente-a", "store-b", {"sku": "001", "nome": "Produto B"}
    )
    cadastro_lojas_produtos.salvar_produto_loja(
        "cliente-b", "store-a", {"sku": "001", "nome": "Outro tenant"}
    )

    assert produto_a["sku_normalizado"] == "001"
    assert produto_a["nome"] == "Produto A"
    assert produto_b["nome"] == "Produto B"
    lista_a = cadastro_lojas_produtos._listar_produtos_loja_sync("cliente-a", "store-a")
    lista_b = cadastro_lojas_produtos._listar_produtos_loja_sync("cliente-a", "store-b")
    outro_tenant = cadastro_lojas_produtos._listar_produtos_loja_sync("cliente-b", "store-a")
    assert [item["nome"] for item in lista_a] == ["Produto A"]
    assert [item["nome"] for item in lista_b] == ["Produto B"]
    assert [item["nome"] for item in outro_tenant] == ["Outro tenant"]


def test_store_ids_diferindo_so_por_caixa_mantem_fotos_da_mesma_sku_isoladas(monkeypatch, tmp_path):
    stores = {
        "cliente-a": [
            {"store_id": "StoreA", "nome": "Loja maiuscula"},
            {"store_id": "storea", "nome": "Loja minuscula"},
        ],
    }
    info_root, _ = _configure(monkeypatch, tmp_path, stores)

    produto_upper = cadastro_lojas_produtos.salvar_produto_loja(
        "cliente-a",
        "StoreA",
        {"sku": "001", "nome": "Upper", "__foto_data_url": _foto_data_url(b"upper")},
    )
    produto_lower = cadastro_lojas_produtos.salvar_produto_loja(
        "cliente-a",
        "storea",
        {"sku": "001", "nome": "Lower", "__foto_data_url": _foto_data_url(b"lower")},
    )

    assert produto_upper["foto"] != produto_lower["foto"]
    assert produto_upper["foto"].split("/")[2] == cadastro_fotos._cadastro_store_id_foto_segmento("StoreA")
    assert produto_lower["foto"].split("/")[2] == cadastro_fotos._cadastro_store_id_foto_segmento("storea")
    assert (info_root / "cliente-a" / produto_upper["foto"]).read_bytes() == b"upper"
    assert (info_root / "cliente-a" / produto_lower["foto"]).read_bytes() == b"lower"


def test_produto_de_uma_store_nao_aceita_referencia_hash_de_outra(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    segmento_b = cadastro_fotos._cadastro_store_id_foto_segmento("store-b")

    with pytest.raises(HTTPException) as exc_info:
        cadastro_lojas_produtos.salvar_produto_loja(
            "cliente-a",
            "store-a",
            {
                "sku": "001",
                "nome": "Invalido",
                "foto": f"cadastro_fotos/lojas/{segmento_b}/001.png",
            },
        )

    assert exc_info.value.status_code == 400


def test_produto_de_uma_store_nao_aceita_url_local_de_foto_de_outra(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    segmento_b = cadastro_fotos._cadastro_store_id_foto_segmento("store-b")

    with pytest.raises(HTTPException) as exc_info:
        cadastro_lojas_produtos.salvar_produto_loja(
            "cliente-a",
            "store-a",
            {
                "sku": "001",
                "nome": "Invalido",
                "foto": (
                    "/api/cadastro/foto/cliente-a/lojas/"
                    f"{segmento_b}/001.png?store_id=store-b"
                ),
            },
        )

    assert exc_info.value.status_code == 400


@pytest.mark.parametrize("photo_column", _PHOTO_ALIAS_FIELDS)
@pytest.mark.parametrize("operacao", ["single", "batch"])
def test_crud_loja_rejeita_alias_de_foto_de_outra_loja_sem_escrever(
    monkeypatch,
    tmp_path,
    photo_column,
    operacao,
):
    info_root, _ = _configure(monkeypatch, tmp_path)
    segmento_b = cadastro_fotos._cadastro_store_id_foto_segmento("store-b")
    invalido = {
        "sku": "001",
        "nome": "Invalido",
        photo_column: f"cadastro_fotos/lojas/{segmento_b}/001.png",
    }

    with pytest.raises(HTTPException) as exc_info:
        if operacao == "single":
            cadastro_lojas_produtos.salvar_produto_loja(
                "cliente-a",
                "store-a",
                invalido,
            )
        else:
            cadastro_lojas_produtos.salvar_produtos_loja_em_lote(
                "cliente-a",
                "store-a",
                [
                    {"sku": "000", "nome": "Valido"},
                    invalido,
                ],
            )

    assert exc_info.value.status_code == 400
    assert not (info_root / "cliente-a" / "cadastro_produtos_lojas.csv").exists()


@pytest.mark.parametrize("photo_column", _PHOTO_ALIAS_FIELDS)
@pytest.mark.parametrize("operacao", ["single", "batch"])
def test_crud_loja_strict_rejeita_alias_de_foto_global_sem_escrever(
    monkeypatch,
    tmp_path,
    photo_column,
    operacao,
):
    info_root, _ = _configure(monkeypatch, tmp_path)
    tenant = info_root / "cliente-a"
    tenant.mkdir(parents=True, exist_ok=True)
    _ativar_escopo_fotos_estrito(tenant)
    invalido = {
        "sku": "001",
        "nome": "Invalido",
        photo_column: "cadastro_fotos/001.png",
    }

    with pytest.raises(HTTPException) as exc_info:
        if operacao == "single":
            cadastro_lojas_produtos.salvar_produto_loja(
                "cliente-a",
                "store-a",
                invalido,
            )
        else:
            cadastro_lojas_produtos.salvar_produtos_loja_em_lote(
                "cliente-a",
                "store-a",
                [
                    {"sku": "000", "nome": "Valido"},
                    invalido,
                ],
            )

    assert exc_info.value.status_code == 400
    assert not (tenant / "cadastro_produtos_lojas.csv").exists()


@pytest.mark.parametrize("photo_column", _PHOTO_ALIAS_FIELDS)
@pytest.mark.parametrize(
    "photo_ref",
    [
        "https://cdn.example/produtos/001.png",
        "//cdn.example/produtos/001.png",
        "blob:https://cdn.example/produtos/001.png",
        "data:image/png;base64,Zm90bw==",
    ],
)
@pytest.mark.parametrize("operacao", ["single", "batch"])
def test_crud_loja_preserva_alias_de_url_externa_ou_data_url(
    monkeypatch,
    tmp_path,
    photo_column,
    photo_ref,
    operacao,
):
    _configure(monkeypatch, tmp_path)
    payload = {
        "sku": "001",
        "nome": "Valido",
        photo_column: photo_ref,
    }

    if operacao == "single":
        cadastro_lojas_produtos.salvar_produto_loja(
            "cliente-a",
            "store-a",
            payload,
        )
    else:
        cadastro_lojas_produtos.salvar_produtos_loja_em_lote(
            "cliente-a",
            "store-a",
            [payload],
        )

    produto = cadastro_lojas_produtos._obter_produto_loja_sync(
        "cliente-a",
        "store-a",
        "001",
    )
    assert produto[photo_column] == photo_ref


def test_store_display_rename_does_not_change_identity(monkeypatch, tmp_path):
    info_root, stores = _configure(monkeypatch, tmp_path)
    cadastro_lojas_produtos.salvar_produto_loja(
        "cliente-a", "store-a", {"sku": "ABC-1", "nome": "Produto"}
    )
    arquivo = info_root / "cliente-a" / "cadastro_produtos_lojas.csv"
    antes = arquivo.read_text(encoding="utf-8-sig")
    stores["cliente-a"][0]["nome"] = "Loja A Renomeada"

    produto = cadastro_lojas_produtos._obter_produto_loja_sync(
        "cliente-a", "store-a", "ABC-1"
    )

    assert produto["store_id"] == "store-a"
    assert produto["loja_sync"] == "Loja A Renomeada"
    assert arquivo.read_text(encoding="utf-8-sig") == antes


def test_historical_cadastro_store_id_wins_over_conflicting_display_name(
    monkeypatch,
    tmp_path,
):
    info_root, _stores = _configure(monkeypatch, tmp_path)
    _write_csv(
        info_root / "cliente-a" / "cadastro_produtos.csv",
        [
            {
                "store_id": "store-a",
                "loja_sync": "Loja B",
                "sku": "001",
                "nome": "Somente identidade A",
            }
        ],
    )

    produtos_a = cadastro_lojas_produtos._listar_produtos_loja_sync(
        "cliente-a", "store-a"
    )
    produtos_b = cadastro_lojas_produtos._listar_produtos_loja_sync(
        "cliente-a", "store-b"
    )

    assert [item["nome"] for item in produtos_a] == ["Somente identidade A"]
    assert produtos_b == []


def test_historical_cadastro_store_id_removido_nao_cai_em_nome_ou_global(
    monkeypatch,
    tmp_path,
):
    stores = {
        "cliente-a": [
            {"store_id": "store-nova", "nome": "Loja Reutilizada"},
        ]
    }
    info_root, _stores = _configure(monkeypatch, tmp_path, stores)
    _write_csv(
        info_root / "cliente-a" / "cadastro_produtos.csv",
        [
            {
                "store_id": "store-removida",
                "loja_sync": "Loja Reutilizada",
                "sku": "001",
                "nome": "Sombra da identidade removida",
            }
        ],
    )

    assert cadastro_lojas_produtos._listar_produtos_loja_sync(
        "cliente-a", "store-nova"
    ) == []


@pytest.mark.parametrize("operacao", ["salvar", "lote", "excluir"])
def test_store_mutation_revalidates_rename_before_commit(
    monkeypatch,
    tmp_path,
    operacao,
):
    _configure(monkeypatch, tmp_path)
    row_version = 0
    if operacao == "excluir":
        existente = cadastro_lojas_produtos.salvar_produto_loja(
            "cliente-a",
            "store-a",
            {"sku": "TOCTOU-1", "nome": "Antes"},
        )
        row_version = existente["row_version"]

    chamadas = 0

    def carregar_com_rename(_client_id):
        nonlocal chamadas
        chamadas += 1
        nome = "Loja A" if chamadas == 1 else "Loja A Renomeada"
        return [
            {"store_id": "store-a", "nome": nome},
            {"store_id": "store-b", "nome": "Loja B"},
        ]

    commits = []
    monkeypatch.setattr(integracoes, "carregar_lojas", carregar_com_rename)
    monkeypatch.setattr(
        cadastro_lojas_produtos,
        "_salvar_registros_atomico",
        lambda *_args, **_kwargs: commits.append(True),
    )

    with pytest.raises(HTTPException) as exc_info:
        if operacao == "salvar":
            cadastro_lojas_produtos.salvar_produto_loja(
                "cliente-a",
                "store-a",
                {"sku": "TOCTOU-1", "nome": "Depois"},
            )
        elif operacao == "lote":
            cadastro_lojas_produtos.salvar_produtos_loja_em_lote(
                "cliente-a",
                "store-a",
                [{"sku": "TOCTOU-1", "nome": "Depois"}],
            )
        else:
            asyncio.run(
                cadastro_lojas_produtos.excluir_produto_loja(
                    "store-a",
                    "TOCTOU-1",
                    row_version,
                    "cliente-a",
                )
            )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "store_config_changed"
    assert commits == []


def test_delete_creates_tombstone_and_suppresses_shadow(monkeypatch, tmp_path):
    info_root, _ = _configure(monkeypatch, tmp_path)
    tenant = info_root / "cliente-a"
    _write_csv(
        tenant / "cadastro_produtos.csv",
        [{"sku": "X-1", "nome": "Legado", "loja_sync": "Loja A"}],
    )
    assert cadastro_lojas_produtos._listar_produtos_loja_sync("cliente-a", "store-a")[0][
        "scope_source"
    ] == "legacy_shadow"

    excluido = asyncio.run(
        cadastro_lojas_produtos.excluir_produto_loja("store-a", "X-1", 0, "cliente-a")
    )

    assert excluido["row_version"] == 1
    assert excluido["deleted_at_utc"]
    assert cadastro_lojas_produtos._listar_produtos_loja_sync("cliente-a", "store-a") == []
    com_tombstone = cadastro_lojas_produtos._listar_produtos_loja_sync(
        "cliente-a", "store-a", include_deleted=True
    )
    assert len(com_tombstone) == 1
    assert com_tombstone[0]["deleted_at_utc"]
    with pytest.raises(HTTPException) as exc:
        cadastro_lojas_produtos._obter_produto_loja_sync("cliente-a", "store-a", "X-1")
    assert exc.value.status_code == 404


def test_delete_rejects_stale_row_version_without_lost_update(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    criado = cadastro_lojas_produtos.salvar_produto_loja(
        "cliente-a", "store-a", {"sku": "DEL-1", "nome": "Versao 1"}
    )
    atualizado = cadastro_lojas_produtos.salvar_produto_loja(
        "cliente-a", "store-a", {"sku": "DEL-1", "nome": "Versao 2"}
    )

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            cadastro_lojas_produtos.excluir_produto_loja(
                "store-a", "DEL-1", criado["row_version"], "cliente-a"
            )
        )

    assert exc.value.status_code == 409
    produto = cadastro_lojas_produtos._obter_produto_loja_sync(
        "cliente-a", "store-a", "DEL-1"
    )
    assert produto["nome"] == "Versao 2"
    assert produto["row_version"] == atualizado["row_version"]


def test_atomic_write_uses_same_directory_replace_and_readback(monkeypatch, tmp_path):
    info_root, _ = _configure(monkeypatch, tmp_path)
    real_replace = cadastro_lojas_produtos.os.replace
    calls = []

    def recording_replace(source, target):
        calls.append((Path(source), Path(target)))
        return real_replace(source, target)

    monkeypatch.setattr(cadastro_lojas_produtos.os, "replace", recording_replace)
    produto = cadastro_lojas_produtos.salvar_produto_loja(
        "cliente-a", "store-a", {"sku": "002", "nome": "Atomico"}
    )

    target = info_root / "cliente-a" / "cadastro_produtos_lojas.csv"
    assert produto["row_version"] == 1
    assert calls and calls[-1][1] == target
    assert calls[-1][0].parent == target.parent
    assert not list(target.parent.glob("*.tmp"))
    persisted, _ = cadastro_lojas_produtos._ler_registros_persistidos("cliente-a")
    assert persisted[0]["nome"] == "Atomico"


def test_csv_roundtrip_preserves_multiline_fields(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    descricao = "Primeira linha\nSegunda linha\r\nTerceira linha"

    salvo = cadastro_lojas_produtos.salvar_produto_loja(
        "cliente-a",
        "store-a",
        {"sku": "MULTI-1", "nome": "Multilinha", "descricao": descricao},
    )
    relido = cadastro_lojas_produtos._obter_produto_loja_sync(
        "cliente-a", "store-a", "MULTI-1"
    )

    assert salvo["descricao"] == descricao
    assert relido["descricao"] == descricao


@pytest.mark.parametrize(
    "conteudo",
    [
        "store_id,sku,row_version,updated_at_utc,deleted_at_utc\nstore-a,001,1,,,EXTRA\n",
        'store_id,sku,row_version,updated_at_utc,deleted_at_utc\nstore-a,001,1,"sem fechamento,\n',
    ],
)
def test_canonical_product_csv_malformed_fails_closed(monkeypatch, tmp_path, conteudo):
    info_root, _ = _configure(monkeypatch, tmp_path)
    arquivo = info_root / "cliente-a" / "cadastro_produtos_lojas.csv"
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    arquivo.write_text(conteudo, encoding="utf-8")
    antes = arquivo.read_bytes()

    with pytest.raises(HTTPException) as exc:
        cadastro_lojas_produtos.salvar_produto_loja(
            "cliente-a", "store-a", {"sku": "002", "nome": "Nao gravar"}
        )

    assert exc.value.status_code == 500
    assert arquivo.read_bytes() == antes


def test_single_rolls_back_product_cost_and_photo_on_cost_failure(monkeypatch, tmp_path):
    info_root, _ = _configure(monkeypatch, tmp_path)
    cadastro_lojas_produtos.salvar_produto_loja(
        "cliente-a",
        "store-a",
        {
            "sku": "TX-1",
            "nome": "Antes",
            "custo": "10",
            "__foto_data_url": _foto_data_url(b"foto-antiga"),
        },
    )
    tenant = info_root / "cliente-a"
    produto_path = tenant / "cadastro_produtos_lojas.csv"
    custo_path = tenant / "cadastro_custos_lojas.csv"
    segmento = cadastro_fotos._cadastro_store_id_foto_segmento("store-a")
    foto_path = tenant / "cadastro_fotos" / "lojas" / segmento / "TX-1.png"
    antes = {
        produto_path: produto_path.read_bytes(),
        custo_path: custo_path.read_bytes(),
        foto_path: foto_path.read_bytes(),
    }

    def falhar_custo(*_args, **_kwargs):
        raise HTTPException(status_code=500, detail="falha injetada")

    monkeypatch.setattr(
        cadastro_lojas_produtos,
        "_cadastro_salvar_custos_item_loja",
        falhar_custo,
    )
    with pytest.raises(HTTPException):
        cadastro_lojas_produtos.salvar_produto_loja(
            "cliente-a",
            "store-a",
            {
                "sku": "TX-1",
                "nome": "Depois",
                "custo": "99",
                "__foto_data_url": _foto_data_url(b"foto-nova"),
            },
        )

    assert {path: path.read_bytes() for path in antes} == antes


def test_single_rolls_back_cost_and_photo_on_product_failure(monkeypatch, tmp_path):
    info_root, _ = _configure(monkeypatch, tmp_path)

    def falhar_produto(*_args, **_kwargs):
        raise HTTPException(status_code=500, detail="falha injetada")

    monkeypatch.setattr(
        cadastro_lojas_produtos,
        "_salvar_registros_atomico",
        falhar_produto,
    )
    with pytest.raises(HTTPException):
        cadastro_lojas_produtos.salvar_produto_loja(
            "cliente-a",
            "store-a",
            {
                "sku": "TX-2",
                "nome": "Novo",
                "custo": "50",
                "__foto_data_url": _foto_data_url(b"foto"),
            },
        )

    tenant = info_root / "cliente-a"
    assert not (tenant / "cadastro_produtos_lojas.csv").exists()
    assert not (tenant / "cadastro_custos_lojas.csv").exists()
    segmento = cadastro_fotos._cadastro_store_id_foto_segmento("store-a")
    assert not (tenant / "cadastro_fotos" / "lojas" / segmento / "TX-2.png").exists()


def test_exact_store_enrichment_never_first_wins_from_other_store(monkeypatch, tmp_path):
    info_root, _ = _configure(monkeypatch, tmp_path)
    tenant = info_root / "cliente-a"
    _write_csv(
        tenant / "produtos_compilado.csv",
        [
            {
                "sku": "010",
                "loja_sync": "Loja B",
                "nome_bling": "Bling B",
                "id_bling": "id-b",
                "ncm_bling": "2222",
            },
            {
                "sku": "010",
                "loja_sync": "Loja A",
                "nome_bling": "Bling A",
                "id_bling": "id-a",
                "ncm_bling": "1111",
            },
        ],
    )
    _write_csv(
        tenant / "cadastro_custos_lojas.csv",
        [
            {"sku": "010", "loja_sync": "Loja B", "custo": "90", "preco": "190", "imposto": "9"},
            {"sku": "010", "loja_sync": "Loja A", "custo": "10", "preco": "110", "imposto": "1"},
        ],
    )

    cadastro_lojas_produtos.salvar_produto_loja(
        "cliente-a",
        "store-a",
        {"sku": "010", "nome": "Ficha explicita", "ncm": "9999"},
    )
    produto = cadastro_lojas_produtos._obter_produto_loja_sync("cliente-a", "store-a", "010")

    assert produto["produto_bling"] == "Bling A"
    assert produto["id_bling"] == "id-a"
    assert produto["ncm"] == "9999"
    assert produto["custo"] == "10"
    assert produto["preco"] == "110"
    assert produto["imposto"] == "1"
    assert "id-b" not in produto.values()


def test_manual_cost_edit_updates_canonical_store_cost(monkeypatch, tmp_path):
    info_root, _ = _configure(monkeypatch, tmp_path)
    tenant = info_root / "cliente-a"
    _write_csv(
        tenant / "cadastro_custos_lojas.csv",
        [{
            "store_id": "store-a",
            "sku": "011",
            "loja_sync": "Loja A",
            "custo": "10",
            "preco": "20",
            "imposto": "1",
        }],
    )

    produto = cadastro_lojas_produtos.salvar_produto_loja(
        "cliente-a",
        "store-a",
        {"sku": "011", "nome": "Produto", "custo": "99", "preco": "199"},
    )
    persistidos, _ = cadastro_lojas_produtos._ler_registros_persistidos("cliente-a")
    custos = cadastro_custos._cadastro_ler_custos_lojas("cliente-a")

    assert produto["custo"] == "99"
    assert produto["preco"] == "199"
    assert "custo" not in persistidos[0]
    assert "preco" not in persistidos[0]
    assert custos.iloc[0]["custo"] == "99"
    assert custos.iloc[0]["preco"] == "199"


def test_exact_store_cost_wins_over_later_legacy_name_row(monkeypatch, tmp_path):
    info_root, _ = _configure(monkeypatch, tmp_path)
    tenant = info_root / "cliente-a"
    _write_csv(
        tenant / "cadastro_custos_lojas.csv",
        [
            {
                "store_id": "store-a",
                "sku": "012",
                "loja_sync": "Nome antigo permitido",
                "custo": "10",
                "preco": "20",
            },
            {
                "store_id": "",
                "sku": "012",
                "loja_sync": "Loja A",
                "custo": "999",
                "preco": "1999",
            },
        ],
    )

    produto = cadastro_lojas_produtos.salvar_produto_loja(
        "cliente-a", "store-a", {"sku": "012", "nome": "Produto"}
    )

    assert produto["custo"] == "10"
    assert produto["preco"] == "20"


def test_preview_is_read_only_and_does_not_guess_old_store_labels(monkeypatch, tmp_path):
    info_root, stores = _configure(monkeypatch, tmp_path)
    stores["cliente-a"] = [{"store_id": "store-a", "nome": "Loja Nova"}]
    tenant = info_root / "cliente-a"
    _write_csv(
        tenant / "cadastro_produtos.csv",
        [
            {"sku": "100", "nome": "Antigo", "loja_sync": "Loja Antiga"},
            {"sku": "200", "nome": "Atual", "loja_sync": "Loja Nova"},
            {"sku": "300", "nome": "Sem prova", "loja_sync": ""},
        ],
    )
    destino = tenant / "cadastro_produtos_lojas.csv"

    preview = cadastro_lojas_produtos.preview_migracao_produtos_loja(
        "cliente-a", "store-a"
    )

    assert preview["dry_run"] is True
    assert preview["would_write"] is False
    assert [item["sku_normalizado"] for item in preview["produtos"]] == ["200"]
    assert any(
        item["loja_sync"] == "Loja Antiga"
        and item["motivo"] == "label_nao_corresponde_a_loja_atual"
        for item in preview["nao_mapeados"]
    )
    assert any(
        item["sku"] == "300" and item["motivo"] == "sem_loja_comprovada"
        for item in preview["nao_mapeados"]
    )
    assert not destino.exists()


def test_legacy_name_fallback_rejects_casefold_homonyms(monkeypatch, tmp_path):
    stores = {
        "cliente-a": [
            {"store_id": "store-a", "nome": "Loja X"},
            {"store_id": "store-b", "nome": "  loja x  "},
        ],
    }
    info_root, _ = _configure(monkeypatch, tmp_path, stores)
    _write_csv(
        info_root / "cliente-a" / "cadastro_produtos.csv",
        [{"sku": "CASE-1", "nome": "Legado", "loja_sync": "LOJA X"}],
    )

    assert cadastro_lojas_produtos._listar_produtos_loja_sync(
        "cliente-a", "store-a"
    ) == []
    assert cadastro_lojas_produtos._listar_produtos_loja_sync(
        "cliente-a", "store-b"
    ) == []
    preview = cadastro_lojas_produtos.preview_migracao_produtos_loja(
        "cliente-a", "store-a"
    )
    assert any(
        item["sku"] == "CASE-1"
        and item["motivo"] == "nome_loja_atual_ambiguo"
        for item in preview["nao_mapeados"]
    )


def test_legacy_shadow_follows_persisted_previous_name_alias(monkeypatch, tmp_path):
    stores = {
        "cliente-a": [
            {
                "store_id": "store-a",
                "nome": "Loja Nova",
                "nomes_anteriores": ["Loja Antiga"],
            },
            {"store_id": "store-b", "nome": "Loja B"},
        ],
    }
    info_root, _ = _configure(monkeypatch, tmp_path, stores)
    _write_csv(
        info_root / "cliente-a" / "cadastro_produtos.csv",
        [{"sku": "REN-1", "nome": "Legado", "loja_sync": " loja antiga "}],
    )

    produtos = cadastro_lojas_produtos._listar_produtos_loja_sync(
        "cliente-a", "store-a"
    )

    assert len(produtos) == 1
    assert produtos[0]["sku_normalizado"] == "REN-1"
    assert produtos[0]["loja_sync"] == "Loja Nova"
    assert produtos[0]["scope_source"] == "legacy_shadow"


def test_previous_name_alias_collision_is_ambiguous_by_casefold(monkeypatch, tmp_path):
    stores = {
        "cliente-a": [
            {
                "store_id": "store-a",
                "nome": "Loja Nova",
                "nomes_anteriores": ["Nome Antigo"],
            },
            {"store_id": "store-b", "nome": " nome antigo "},
        ],
    }
    info_root, _ = _configure(monkeypatch, tmp_path, stores)
    _write_csv(
        info_root / "cliente-a" / "cadastro_produtos.csv",
        [{"sku": "COL-1", "nome": "Legado", "loja_sync": "NOME ANTIGO"}],
    )

    assert cadastro_lojas_produtos._listar_produtos_loja_sync(
        "cliente-a", "store-a"
    ) == []
    assert cadastro_lojas_produtos._listar_produtos_loja_sync(
        "cliente-a", "store-b"
    ) == []


def test_update_materializes_shadow_and_increments_version(monkeypatch, tmp_path):
    info_root, _ = _configure(monkeypatch, tmp_path)
    _write_csv(
        info_root / "cliente-a" / "cadastro_produtos.csv",
        [{"sku": "400", "nome": "Legado", "loja_sync": "Loja A"}],
    )

    resposta = asyncio.run(
        cadastro_lojas_produtos.atualizar_produto_loja(
            "store-a",
            "400",
            CadastroProdutoLojaAtualizacaoRequest(nome="Materializado", row_version=0),
            "cliente-a",
        )
    )
    atualizado = cadastro_lojas_produtos.salvar_produto_loja(
        "cliente-a", "store-a", {"sku": "400", "marca": "Marca"}
    )

    assert resposta["produto"]["scope_source"] == "store_file"
    assert resposta["produto"]["row_version"] == 1
    assert atualizado["row_version"] == 2
    assert atualizado["nome"] == "Materializado"


def test_update_sem_upload_materializa_bytes_scoped_na_linha_versionada(
    monkeypatch,
    tmp_path,
):
    info_root, _ = _configure(monkeypatch, tmp_path)
    tenant = info_root / "cliente-a"
    tenant.mkdir(parents=True, exist_ok=True)
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
    segmento = cadastro_fotos._cadastro_store_id_foto_segmento("store-a")
    foto_rel = f"cadastro_fotos/lojas/{segmento}/400.jpg"
    foto_path = tenant / foto_rel
    foto_path.parent.mkdir(parents=True)
    foto_path.write_bytes(b"foto-ja-migrada")
    _write_csv(
        tenant / "cadastro_produtos.csv",
        [{"sku": "400", "nome": "Legado", "loja_sync": "Loja A"}],
    )

    resposta = asyncio.run(
        cadastro_lojas_produtos.atualizar_produto_loja(
            "store-a",
            "400",
            CadastroProdutoLojaAtualizacaoRequest(nome="Materializado", row_version=0),
            "cliente-a",
        )
    )

    assert resposta["produto"]["foto"] == foto_rel
    with (tenant / "cadastro_produtos_lojas.csv").open(
        "r", encoding="utf-8-sig", newline=""
    ) as arquivo:
        persistido = next(csv.DictReader(arquivo))
    assert persistido["foto"] == foto_rel
    assert persistido["row_version"] == "1"


def test_crud_sem_upload_ressuscita_tombstone_com_foto_local_em_nova_versao(
    monkeypatch,
    tmp_path,
):
    info_root, _ = _configure(monkeypatch, tmp_path)
    tenant = info_root / "cliente-a"
    tenant.mkdir(parents=True, exist_ok=True)
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
    segmento = cadastro_fotos._cadastro_store_id_foto_segmento("store-a")
    foto_rel = f"cadastro_fotos/lojas/{segmento}/401.png"
    foto_path = tenant / foto_rel
    foto_path.parent.mkdir(parents=True)
    foto_path.write_bytes(b"foto-ressurreicao")
    _write_csv(
        tenant / "cadastro_produtos_lojas.csv",
        [
            {
                "store_id": "store-a",
                "sku": "401",
                "sku_normalizado": "401",
                "loja_sync": "Loja A",
                "foto": "",
                "row_version": "3",
                "updated_at_utc": "2026-08-01T00:00:00Z",
                "deleted_at_utc": "2026-08-01T00:00:00Z",
            }
        ],
    )

    produto = cadastro_lojas_produtos.salvar_produto_loja(
        "cliente-a",
        "store-a",
        {"sku": "401", "nome": "Ressuscitado", "row_version": 3},
        somente_criar=True,
    )

    assert produto["foto"] == foto_rel
    assert produto["row_version"] == 4
    with (tenant / "cadastro_produtos_lojas.csv").open(
        "r", encoding="utf-8-sig", newline=""
    ) as arquivo:
        persistido = next(csv.DictReader(arquivo))
    assert persistido["deleted_at_utc"] == ""
    assert persistido["foto"] == foto_rel
    assert persistido["row_version"] == "4"


def test_shadow_uses_version_zero_and_rejects_second_stale_editor(monkeypatch, tmp_path):
    info_root, _ = _configure(monkeypatch, tmp_path)
    _write_csv(
        info_root / "cliente-a" / "cadastro_produtos.csv",
        [{"sku": "401", "nome": "Legado", "loja_sync": "Loja A"}],
    )
    sombra = cadastro_lojas_produtos._obter_produto_loja_sync(
        "cliente-a", "store-a", "401"
    )
    assert sombra["row_version"] == 0

    asyncio.run(
        cadastro_lojas_produtos.atualizar_produto_loja(
            "store-a",
            "401",
            CadastroProdutoLojaAtualizacaoRequest(nome="Editor A", row_version=0),
            "cliente-a",
        )
    )
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            cadastro_lojas_produtos.atualizar_produto_loja(
                "store-a",
                "401",
                CadastroProdutoLojaAtualizacaoRequest(nome="Editor B", row_version=0),
                "cliente-a",
            )
        )

    assert exc.value.status_code == 409
    assert cadastro_lojas_produtos._obter_produto_loja_sync(
        "cliente-a", "store-a", "401"
    )["nome"] == "Editor A"


def test_batch_upsert_uses_one_replace_and_has_no_partial_duplicate_write(monkeypatch, tmp_path):
    info_root, _ = _configure(monkeypatch, tmp_path)
    real_replace = cadastro_lojas_produtos.os.replace
    calls = []

    def recording_replace(source, target):
        calls.append((source, target))
        return real_replace(source, target)

    monkeypatch.setattr(cadastro_lojas_produtos.os, "replace", recording_replace)
    resultado = cadastro_lojas_produtos.salvar_produtos_loja_em_lote(
        "cliente-a",
        "store-a",
        [
            {"sku": "501", "nome": "Um"},
            {"sku": "502", "nome": "Dois"},
        ],
    )

    assert resultado["incluidos"] == 2
    assert resultado["atualizados"] == 0
    assert len(calls) == 1
    arquivo = info_root / "cliente-a" / "cadastro_produtos_lojas.csv"
    antes = arquivo.read_bytes()

    with pytest.raises(HTTPException) as exc:
        cadastro_lojas_produtos.salvar_produtos_loja_em_lote(
            "cliente-a",
            "store-a",
            [
                {"sku": "503", "nome": "Tres"},
                {"sku": "503", "nome": "Duplicado"},
            ],
        )
    assert exc.value.status_code == 400
    assert arquivo.read_bytes() == antes
    assert len(calls) == 1


def test_batch_invalid_second_photo_writes_nothing(monkeypatch, tmp_path):
    info_root, _ = _configure(monkeypatch, tmp_path)

    with pytest.raises(HTTPException):
        cadastro_lojas_produtos.salvar_produtos_loja_em_lote(
            "cliente-a",
            "store-a",
            [
                {
                    "sku": "B-1",
                    "nome": "Um",
                    "custo": "10",
                    "__foto_data_url": _foto_data_url(b"valida"),
                },
                {
                    "sku": "B-2",
                    "nome": "Dois",
                    "custo": "20",
                    "__foto_data_url": "data:image/png;base64,@@invalido@@",
                },
            ],
        )

    tenant = info_root / "cliente-a"
    assert not (tenant / "cadastro_produtos_lojas.csv").exists()
    assert not (tenant / "cadastro_custos_lojas.csv").exists()
    segmento = cadastro_fotos._cadastro_store_id_foto_segmento("store-a")
    assert not (tenant / "cadastro_fotos" / "lojas" / segmento / "B-1.png").exists()


def test_cost_store_id_survives_display_rename_without_name_fallback(monkeypatch, tmp_path):
    info_root, stores = _configure(monkeypatch, tmp_path)
    tenant = info_root / "cliente-a"
    _write_csv(
        tenant / "cadastro_custos_lojas.csv",
        [
            {
                "store_id": "store-a",
                "sku": "600",
                "loja_sync": "Nome Antigo",
                "custo": "12.34",
                "preco": "45.67",
                "imposto": "8",
            },
            {
                "store_id": "store-desconhecida",
                "sku": "601",
                "loja_sync": "Loja A",
                "custo": "999",
            },
        ],
    )
    stores["cliente-a"][0]["nome"] = "Nome Novo"

    lista = cadastro_lojas_produtos._listar_produtos_loja_sync("cliente-a", "store-a")
    preview = cadastro_lojas_produtos.preview_migracao_produtos_loja(
        "cliente-a", "store-a"
    )

    assert len(lista) == 1
    assert lista[0]["sku_normalizado"] == "600"
    assert lista[0]["loja_sync"] == "Nome Novo"
    assert lista[0]["custo"] == "12.34"
    assert all(item["sku_normalizado"] != "601" for item in preview["produtos"])
    assert any(
        item["sku"] == "601"
        and item["motivo"] == "store_id_nao_corresponde_a_loja_atual"
        for item in preview["nao_mapeados"]
    )


def test_router_exposes_store_scoped_crud_and_preview():
    from backend.routers.cadastro import create_cadastro_router

    routes = {
        (route.path, method)
        for route in create_cadastro_router().routes
        for method in (route.methods or set())
    }
    assert ("/api/cadastro/lojas/{store_id}/produtos", "GET") in routes
    assert ("/api/cadastro/lojas/{store_id}/produtos", "POST") in routes
    assert ("/api/cadastro/lojas/{store_id}/produtos/{sku:path}", "GET") in routes
    assert ("/api/cadastro/lojas/{store_id}/produtos/{sku:path}", "PUT") in routes
    assert ("/api/cadastro/lojas/{store_id}/produtos/{sku:path}", "DELETE") in routes
    assert ("/api/cadastro/lojas/{store_id}/migracao/preview", "GET") in routes


def test_empty_store_columns_include_cadastro_base(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)

    resposta = asyncio.run(
        cadastro_lojas_produtos.listar_colunas_produtos_loja(
            "store-a", "cliente-a"
        )
    )

    assert {"sku", "nome", "categoria", "marca", "descricao"}.issubset(
        resposta["colunas"]
    )


def test_store_photo_fallback_prefers_store_then_legacy(monkeypatch, tmp_path):
    info_root, _ = _configure(monkeypatch, tmp_path)
    tenant = info_root / "cliente-a"
    legacy = tenant / "cadastro_fotos" / "001.png"
    scoped = tenant / "cadastro_fotos" / "lojas" / "store-a" / "001.jpg"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    scoped.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_bytes(b"legacy")
    scoped.write_bytes(b"scoped")
    cadastro_lojas_produtos.salvar_produto_loja(
        "cliente-a", "store-a", {"sku": "001", "nome": "Produto"}
    )

    produto = cadastro_lojas_produtos._obter_produto_loja_sync(
        "cliente-a", "store-a", "001"
    )

    assert produto["foto"] == "cadastro_fotos/lojas/store-a/001.jpg"


def test_empty_store_batch_ignores_unscoped_legacy_photo(monkeypatch, tmp_path):
    info_root, _ = _configure(monkeypatch, tmp_path)
    tenant = info_root / "cliente-a"
    legacy_photo = tenant / "cadastro_fotos" / "BLING-0080.png"
    legacy_photo.parent.mkdir(parents=True, exist_ok=True)
    legacy_photo.write_bytes(b"legacy-without-store")
    itens = [
        {"sku": f"BLING-{indice:04d}", "nome": f"Produto Bling {indice}"}
        for indice in range(498)
    ]

    resultado = cadastro_lojas_produtos.salvar_produtos_loja_em_lote(
        "cliente-a",
        "store-a",
        itens,
    )

    assert resultado["incluidos"] == 498
    assert resultado["atualizados"] == 0
    assert resultado["total"] == 498
    with (tenant / "cadastro_produtos_lojas.csv").open(
        "r", encoding="utf-8-sig", newline=""
    ) as handle:
        persistidos = list(csv.DictReader(handle))
    assert len(persistidos) == 498
    produto_com_foto_legada = next(
        item for item in persistidos if item["sku"] == "BLING-0080"
    )
    assert produto_com_foto_legada.get("foto", "") == ""
    assert legacy_photo.read_bytes() == b"legacy-without-store"


@pytest.mark.parametrize(
    "stale_reference",
    [
        "cadastro_fotos/001.png",
        "cadastro_fotos/lojas/store-a/001-old.png",
    ],
    ids=["legacy-root", "old-store-scope"],
)
def test_enrichment_prefers_current_scoped_photo_over_stale_reference(
    monkeypatch,
    tmp_path,
    stale_reference,
):
    info_root, _ = _configure(monkeypatch, tmp_path)
    tenant = info_root / "cliente-a"
    config_path = tenant / cadastro_fotos.CADASTRO_FOTOS_CONFIG_ARQUIVO
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps(
            {
                "schema": cadastro_fotos.CADASTRO_FOTOS_CONFIG_SCHEMA,
                "strict_store_scope": True,
                "shared_groups": [],
            }
        ),
        encoding="utf-8",
    )
    segment = cadastro_fotos._cadastro_store_id_foto_segmento("store-a")
    current_relative = f"cadastro_fotos/lojas/{segment}/001.jpg"
    current_photo = tenant / current_relative
    current_photo.parent.mkdir(parents=True, exist_ok=True)
    current_photo.write_bytes(b"foto-atual-da-loja")
    _write_csv(
        tenant / "cadastro_produtos_lojas.csv",
        [
            {
                "store_id": "store-a",
                "loja_sync": "Loja A",
                "sku": "001",
                "sku_normalizado": "001",
                "nome": "Produto",
                "foto": stale_reference,
                "row_version": "1",
            }
        ],
    )

    produto = cadastro_lojas_produtos._obter_produto_loja_sync(
        "cliente-a",
        "store-a",
        "001",
    )

    assert produto["foto"] == current_relative


def _configure_three_store_photo_group(monkeypatch, tmp_path):
    store_ids = ("store-a", "store-b", "store-c")
    info_root, _ = _configure(
        monkeypatch,
        tmp_path,
        {
            "cliente-a": [
                {"store_id": store_id, "nome": f"Loja {store_id[-1].upper()}"}
                for store_id in store_ids
            ]
        },
    )
    tenant = info_root / "cliente-a"
    tenant.mkdir(parents=True, exist_ok=True)
    (tenant / cadastro_fotos.CADASTRO_FOTOS_CONFIG_ARQUIVO).write_text(
        json.dumps(
            {
                "schema": cadastro_fotos.CADASTRO_FOTOS_CONFIG_SCHEMA,
                "strict_store_scope": True,
                "shared_groups": [
                    {"group_id": "abc", "store_ids": list(store_ids)}
                ],
            }
        ),
        encoding="utf-8",
    )
    return info_root, tenant, store_ids


def _group_photo_paths(tenant: Path, store_ids, sku: str, extension: str):
    return [
        tenant
        / "cadastro_fotos"
        / "lojas"
        / cadastro_fotos._cadastro_store_id_foto_segmento(store_id)
        / f"{sku}.{extension}"
        for store_id in store_ids
    ]


def test_store_save_extension_change_updates_group_and_readback(monkeypatch, tmp_path):
    _info_root, tenant, store_ids = _configure_three_store_photo_group(
        monkeypatch, tmp_path
    )
    cadastro_lojas_produtos.salvar_produto_loja(
        "cliente-a",
        "store-a",
        {"sku": "EXT-1", "nome": "Produto", "__foto_data_url": _foto_data_url(b"png")},
    )

    produto_b = cadastro_lojas_produtos.salvar_produto_loja(
        "cliente-a",
        "store-b",
        {
            "sku": "EXT-1",
            "nome": "Produto",
            "__foto_data_url": "data:image/jpeg;base64,"
            + base64.b64encode(b"jpg").decode("ascii"),
        },
    )

    png_paths = _group_photo_paths(tenant, store_ids, "EXT-1", "png")
    jpg_paths = _group_photo_paths(tenant, store_ids, "EXT-1", "jpg")
    produto_a = cadastro_lojas_produtos._obter_produto_loja_sync(
        "cliente-a", "store-a", "EXT-1"
    )
    rows = {
        row["store_id"]: row
        for row in cadastro_lojas_produtos._ler_registros_persistidos("cliente-a")[0]
        if row["sku_normalizado"] == "EXT-1"
    }
    assert all(not path.exists() for path in png_paths)
    assert all(path.read_bytes() == b"jpg" for path in jpg_paths)
    assert produto_b["foto"].endswith("/EXT-1.jpg")
    assert produto_a["foto"].endswith("/EXT-1.jpg")
    assert rows["store-a"]["foto"].endswith("/EXT-1.jpg")
    assert rows["store-b"]["foto"].endswith("/EXT-1.jpg")


def test_store_save_same_extension_advances_event_for_every_active_group_member(
    monkeypatch,
    tmp_path,
):
    _info_root, tenant, store_ids = _configure_three_store_photo_group(
        monkeypatch,
        tmp_path,
    )
    for store_id in store_ids:
        cadastro_lojas_produtos.salvar_produto_loja(
            "cliente-a",
            store_id,
            {"sku": "EVT-1", "nome": "Produto"},
        )
    cadastro_lojas_produtos.salvar_produto_loja(
        "cliente-a",
        "store-a",
        {
            "sku": "EVT-1",
            "nome": "Produto",
            "__foto_data_url": _foto_data_url(b"primeira"),
        },
    )
    antes = {
        row["store_id"]: int(row["row_version"])
        for row in cadastro_lojas_produtos._ler_registros_persistidos("cliente-a")[0]
        if row["sku_normalizado"] == "EVT-1"
    }

    cadastro_lojas_produtos.salvar_produto_loja(
        "cliente-a",
        "store-a",
        {
            "sku": "EVT-1",
            "nome": "Produto",
            "__foto_data_url": _foto_data_url(b"segunda"),
        },
    )

    depois = {
        row["store_id"]: row
        for row in cadastro_lojas_produtos._ler_registros_persistidos("cliente-a")[0]
        if row["sku_normalizado"] == "EVT-1"
    }
    assert set(depois) == set(store_ids)
    assert all(int(depois[store_id]["row_version"]) > antes[store_id] for store_id in store_ids)
    assert all(depois[store_id]["foto"].endswith("/EVT-1.png") for store_id in store_ids)
    assert all(
        path.read_bytes() == b"segunda"
        for path in _group_photo_paths(tenant, store_ids, "EVT-1", "png")
    )


def test_store_save_extension_change_rolls_back_photos_and_csv(monkeypatch, tmp_path):
    _info_root, tenant, store_ids = _configure_three_store_photo_group(
        monkeypatch, tmp_path
    )
    cadastro_lojas_produtos.salvar_produto_loja(
        "cliente-a",
        "store-a",
        {"sku": "EXT-2", "nome": "Produto", "__foto_data_url": _foto_data_url(b"png")},
    )
    cadastro_path = tenant / "cadastro_produtos_lojas.csv"
    cadastro_antes = cadastro_path.read_bytes()
    png_paths = _group_photo_paths(tenant, store_ids, "EXT-2", "png")
    jpg_paths = _group_photo_paths(tenant, store_ids, "EXT-2", "jpg")

    monkeypatch.setattr(
        cadastro_lojas_produtos,
        "_salvar_registros_atomico",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("falha depois das fotos")),
    )
    with pytest.raises(OSError, match="falha depois das fotos"):
        cadastro_lojas_produtos.salvar_produto_loja(
            "cliente-a",
            "store-b",
            {
                "sku": "EXT-2",
                "nome": "Produto",
                "__foto_data_url": "data:image/jpeg;base64,"
                + base64.b64encode(b"jpg").decode("ascii"),
            },
        )

    assert cadastro_path.read_bytes() == cadastro_antes
    assert all(path.read_bytes() == b"png" for path in png_paths)
    assert all(not path.exists() for path in jpg_paths)


def test_materialized_shadow_keeps_compiled_fields_live(monkeypatch, tmp_path):
    info_root, _ = _configure(monkeypatch, tmp_path)
    _write_csv(
        info_root / "cliente-a" / "cadastro_produtos.csv",
        [{"sku": "700", "nome": "Legado", "ncm": "ncm-global-antigo"}],
    )
    compilado = info_root / "cliente-a" / "produtos_compilado.csv"
    _write_csv(
        compilado,
        [{
            "sku": "700",
            "loja_sync": "Loja A",
            "id_bling": "id-a",
            "nome_bling": "Nome A",
            "ncm_bling": "1111",
        }],
    )

    cadastro_lojas_produtos.salvar_produto_loja(
        "cliente-a", "store-a", {"sku": "700", "marca": "Marca local"}
    )
    _write_csv(
        compilado,
        [{
            "sku": "700",
            "loja_sync": "Loja A",
            "id_bling": "id-b",
            "nome_bling": "Nome B",
            "ncm_bling": "2222",
        }],
    )

    produto = cadastro_lojas_produtos._obter_produto_loja_sync(
        "cliente-a", "store-a", "700"
    )

    assert produto["id_bling"] == "id-b"
    assert produto["produto_bling"] == "Nome B"
    assert produto["ncm"] == "2222"
    assert produto["marca"] == "Marca local"


def test_unscoped_legacy_ncm_never_masks_exact_compiled_store(monkeypatch, tmp_path):
    info_root, _ = _configure(monkeypatch, tmp_path)
    tenant = info_root / "cliente-a"
    _write_csv(
        tenant / "cadastro_produtos.csv",
        [{"sku": "701", "nome": "Legado", "ncm": "ncm-global"}],
    )
    _write_csv(
        tenant / "produtos_compilado.csv",
        [
            {"sku": "701", "loja_sync": "Loja A", "ncm_bling": "ncm-a"},
            {"sku": "701", "loja_sync": "Loja B", "ncm_bling": "ncm-b"},
        ],
    )

    produto_a = cadastro_lojas_produtos._obter_produto_loja_sync(
        "cliente-a", "store-a", "701"
    )
    produto_b = cadastro_lojas_produtos._obter_produto_loja_sync(
        "cliente-a", "store-b", "701"
    )

    assert produto_a["ncm"] == "ncm-a"
    assert produto_b["ncm"] == "ncm-b"


def test_compiled_store_id_separates_homonyms_and_survives_rename(monkeypatch, tmp_path):
    stores = {
        "cliente-a": [
            {"store_id": "store-a", "nome": "Loja Homonima"},
            {"store_id": "store-b", "nome": "Loja Homonima"},
        ],
    }
    info_root, configured = _configure(monkeypatch, tmp_path, stores)
    _write_csv(
        info_root / "cliente-a" / "produtos_compilado.csv",
        [
            {
                "sku": "008",
                "store_id": "store-a",
                "loja_sync": "Nome Antigo A",
                "ncm_bling": "ncm-a",
                "id_bling": "id-a",
            },
            {
                "sku": "008",
                "store_id": "store-b",
                "loja_sync": "Nome Antigo B",
                "ncm_bling": "ncm-b",
                "id_bling": "id-b",
            },
            {
                "sku": "008",
                "store_id": "",
                "loja_sync": "Loja Homonima",
                "ncm_bling": "ncm-legado-ambiguo",
                "id_bling": "id-legado",
            },
        ],
    )

    produto_a = cadastro_lojas_produtos._obter_produto_loja_sync(
        "cliente-a", "store-a", "008"
    )
    produto_b = cadastro_lojas_produtos._obter_produto_loja_sync(
        "cliente-a", "store-b", "008"
    )
    configured["cliente-a"][0]["nome"] = "Loja A Renomeada"
    produto_a_renomeado = cadastro_lojas_produtos._obter_produto_loja_sync(
        "cliente-a", "store-a", "008"
    )

    assert (produto_a["ncm"], produto_a["id_bling"]) == ("ncm-a", "id-a")
    assert (produto_b["ncm"], produto_b["id_bling"]) == ("ncm-b", "id-b")
    assert produto_a_renomeado["ncm"] == "ncm-a"
    assert produto_a_renomeado["loja_sync"] == "Loja A Renomeada"
