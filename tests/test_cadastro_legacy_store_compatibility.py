import asyncio
import csv
import io
import json
import logging
import subprocess
import sys
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi import HTTPException, UploadFile
from pydantic import ValidationError

from backend.schemas import CadastroProdutoRequest
from backend.services import (
    cadastro_custos,
    cadastro_compatibilidade,
    cadastro_fotos,
    cadastro_importacao,
    cadastro_listagem,
    cadastro_lojas_produtos,
    cadastro_produtos,
    ia_tools_produtos,
    integracoes,
    medias_compras_excel,
    medias_compras_sugestoes,
)
from backend.services.whatsapp import artifacts as whatsapp_artifacts


_DELETE_STORE_SCRIPT = r"""
import json
import logging
import os
import sys

from fastapi import HTTPException
from backend.services import integracoes

info_root, client_id, store_name, store_id = sys.argv[1:5]

def tenant_path(current_client_id):
    return os.path.join(info_root, str(current_client_id))

integracoes.configure_integracoes_context(
    logger_ref=logging.getLogger("cadastro-import-delete-test"),
    pasta_info=info_root,
    get_tenant_path=tenant_path,
)
print("START", flush=True)
try:
    integracoes.excluir_loja(client_id, store_name, store_id=store_id)
except HTTPException as exc:
    print(
        json.dumps({"status": exc.status_code, "detail": exc.detail}),
        flush=True,
    )
else:
    print(json.dumps({"status": 200, "detail": {}}), flush=True)
"""


def _spawn_store_delete(info_root: Path):
    return subprocess.Popen(
        [
            sys.executable,
            "-c",
            _DELETE_STORE_SCRIPT,
            str(info_root),
            "cliente-a",
            "Loja A",
            "store-a",
        ],
        cwd=Path(__file__).resolve().parents[1],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _finish_store_delete(process: subprocess.Popen, timeout: float = 15.0) -> dict:
    stdout, stderr = process.communicate(timeout=timeout)
    assert process.returncode == 0, stderr
    lines = [line for line in stdout.splitlines() if line.strip()]
    assert lines, stderr
    return json.loads(lines[-1])


def _configurar(monkeypatch, tmp_path, stores_by_client=None):
    info_root = tmp_path / "info"
    stores_by_client = stores_by_client or {
        "cliente-a": [
            {"store_id": "store-a", "nome": "Loja A"},
            {"store_id": "store-b", "nome": "Loja B"},
        ],
        "cliente-single": [{"store_id": "store-a", "nome": "Loja A"}],
        "cliente-b": [{"store_id": "store-a", "nome": "Loja A"}],
    }

    def tenant_path(client_id):
        path = info_root / str(client_id)
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    for module in (
        cadastro_lojas_produtos,
        cadastro_custos,
        cadastro_fotos,
        cadastro_produtos,
        cadastro_importacao,
        cadastro_listagem,
        ia_tools_produtos,
        medias_compras_excel,
    ):
        monkeypatch.setattr(module, "get_tenant_path", tenant_path)

    monkeypatch.setattr(cadastro_fotos, "PASTA_INFO", str(info_root), raising=False)
    monkeypatch.setattr(ia_tools_produtos, "PASTA_INFO", str(info_root), raising=False)
    monkeypatch.setattr(medias_compras_excel, "PASTA_INFO", str(info_root), raising=False)
    monkeypatch.setattr(
        integracoes,
        "carregar_lojas",
        lambda client_id: stores_by_client.get(str(client_id), []),
    )

    def migrar(client_id, filename, _legacy_path):
        return str(Path(tenant_path(client_id)) / filename)

    monkeypatch.setattr(
        cadastro_listagem, "_migrar_arquivo_legado_para_tenant", migrar, raising=False
    )
    monkeypatch.setattr(
        ia_tools_produtos, "_migrar_arquivo_legado_para_tenant", migrar, raising=False
    )
    for module in (cadastro_listagem, ia_tools_produtos):
        monkeypatch.setattr(
            module,
            "ARQUIVO_DB_CADASTRO_PRODUTOS",
            "legacy-cadastro",
            raising=False,
        )
        monkeypatch.setattr(
            module, "ARQUIVO_DB_PRODUTOS", "legacy-estoque", raising=False
        )
    monkeypatch.setattr(
        ia_tools_produtos, "logger", logging.getLogger(__name__), raising=False
    )
    monkeypatch.setattr(
        ia_tools_produtos,
        "_normalizar_sku_mes",
        cadastro_lojas_produtos._normalizar_sku_mes,
        raising=False,
    )
    monkeypatch.setattr(
        whatsapp_artifacts, "_info_dir", lambda: info_root, raising=False
    )
    return info_root, stores_by_client


def _configurar_importacao_com_integracoes_reais(monkeypatch, tmp_path):
    info_root = tmp_path / "info"

    def tenant_path(client_id):
        path = info_root / str(client_id)
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    for module in (
        cadastro_lojas_produtos,
        cadastro_custos,
        cadastro_fotos,
        cadastro_importacao,
    ):
        monkeypatch.setattr(module, "get_tenant_path", tenant_path)
    monkeypatch.setattr(cadastro_fotos, "PASTA_INFO", str(info_root), raising=False)
    monkeypatch.setattr(integracoes, "PASTA_INFO", str(info_root), raising=False)
    monkeypatch.setattr(
        integracoes,
        "ARQUIVO_LOJAS",
        str(info_root / "lojas_config.json"),
        raising=False,
    )
    monkeypatch.setattr(
        integracoes,
        "ARQUIVO_TEMP_AUTH",
        str(info_root / "temp_integracao.json"),
        raising=False,
    )
    monkeypatch.setattr(integracoes, "_get_tenant_path", tenant_path, raising=False)

    tenant = Path(tenant_path("cliente-a"))
    (tenant / "lojas_config.json").write_text(
        json.dumps(
            [
                {
                    "store_id": "store-a",
                    "nome": "Loja A",
                    "integracoes": {},
                }
            ]
        ),
        encoding="utf-8",
    )
    return info_root, tenant


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


def _upload(conteudo: str, nome="importacao.csv"):
    return UploadFile(filename=nome, file=io.BytesIO(conteudo.encode("utf-8")))


def _assert_store_id_required(exc_info):
    erro = exc_info.value
    assert erro.status_code == 409
    assert isinstance(erro.detail, dict)
    assert erro.detail["code"] == "store_id_required"
    assert erro.detail["skus"] == ["001"]


_PHOTO_ALIAS_FIELDS = (
    "imagem",
    "image_url",
    "link_imagem",
    "picture",
    "thumbnail_url",
    "link_foto",
)


def _ativar_escopo_fotos_estrito(tenant: Path) -> None:
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


def test_mutadores_globais_rejeitam_sku_controlado_sem_alterar_arquivos(monkeypatch, tmp_path):
    info_root, _ = _configurar(monkeypatch, tmp_path)
    tenant = info_root / "cliente-a"
    legado = tenant / "cadastro_produtos.csv"
    _write_csv(legado, [{"sku": "001", "nome": "Legado", "categoria": "Antiga"}])
    cadastro_lojas_produtos.salvar_produto_loja(
        "cliente-a", "store-a", {"sku": "001", "nome": "Por loja"}
    )
    canonico = tenant / "cadastro_produtos_lojas.csv"
    legado_antes = legado.read_bytes()
    canonico_antes = canonico.read_bytes()

    chamadas = [
        lambda: cadastro_produtos.salvar_produto_cadastro(
            CadastroProdutoRequest(sku="001", nome="Global"), "cliente-a"
        ),
        lambda: cadastro_produtos.atualizar_produto_cadastro_completo(
            "001", {"nome": "Global editado"}, "cliente-a"
        ),
        lambda: cadastro_produtos.incluir_produto_cadastro_completo(
            {"sku": "001", "nome": "Global incluido"}, "cliente-a"
        ),
        lambda: cadastro_importacao.importar_colunas_cadastro_por_sku(
            _upload("sku;nome\n001;Global importado\n"), None, None, "cliente-a"
        ),
    ]
    for chamada in chamadas:
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(chamada())
        _assert_store_id_required(exc_info)
        assert legado.read_bytes() == legado_antes
        assert canonico.read_bytes() == canonico_antes


@pytest.mark.parametrize(
    "campo",
    [
        "store_id",
        "loja_sync",
        "loja",
        "sku_normalizado",
        "row_version",
        "updated_at_utc",
        "deleted_at_utc",
        "scope_source",
    ],
)
def test_importacao_global_rejeita_novo_vinculo_de_loja(
    monkeypatch,
    tmp_path,
    campo,
):
    info_root, _ = _configurar(monkeypatch, tmp_path)
    legado = info_root / "cliente-a" / "cadastro_produtos.csv"
    _write_csv(legado, [{"sku": "001", "nome": "Legado"}])
    antes = legado.read_bytes()

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            cadastro_importacao.importar_colunas_cadastro_por_sku(
                _upload(f"sku;nome;{campo}\n001;Alterado;Loja A\n"),
                None,
                None,
                "cliente-a",
            )
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "store_id_required"
    assert exc_info.value.detail["campos"] == [campo]
    assert legado.read_bytes() == antes


@pytest.mark.parametrize(
    "campo",
    [
        "store_id",
        "loja_sync",
        "loja",
        "sku_normalizado",
        "row_version",
        "updated_at_utc",
        "deleted_at_utc",
        "scope_source",
    ],
)
@pytest.mark.parametrize("operacao", ["atualizar", "incluir"])
def test_crud_global_rejeita_novo_vinculo_de_loja(
    monkeypatch,
    tmp_path,
    campo,
    operacao,
):
    info_root, _ = _configurar(monkeypatch, tmp_path)
    legado = info_root / "cliente-a" / "cadastro_produtos.csv"
    _write_csv(legado, [{"sku": "001", "nome": "Legado"}])
    antes = legado.read_bytes()

    with pytest.raises(HTTPException) as exc_info:
        if operacao == "atualizar":
            asyncio.run(
                cadastro_produtos.atualizar_produto_cadastro_completo(
                    "001",
                    {"nome": "Alterado", campo: "Loja A"},
                    "cliente-a",
                )
            )
        else:
            asyncio.run(
                cadastro_produtos.incluir_produto_cadastro_completo(
                    {"sku": "002", "nome": "Novo", campo: "Loja A"},
                    "cliente-a",
                )
            )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "store_id_required"
    assert exc_info.value.detail["campos"] == [campo]
    assert legado.read_bytes() == antes


@pytest.mark.parametrize("photo_column", _PHOTO_ALIAS_FIELDS)
@pytest.mark.parametrize("operacao", ["atualizar", "incluir"])
def test_crud_global_strict_rejeita_alias_de_foto_local_sem_mutar_csv(
    monkeypatch,
    tmp_path,
    photo_column,
    operacao,
):
    info_root, _ = _configurar(monkeypatch, tmp_path)
    tenant = info_root / "cliente-a"
    legado = tenant / "cadastro_produtos.csv"
    _write_csv(legado, [{"sku": "001", "nome": "Original"}])
    antes = legado.read_bytes()
    _ativar_escopo_fotos_estrito(tenant)
    payload = {
        "nome": "Alterado",
        photo_column: "cadastro_fotos/001.png",
    }

    with pytest.raises(HTTPException) as exc_info:
        if operacao == "atualizar":
            asyncio.run(
                cadastro_produtos.atualizar_produto_cadastro_completo(
                    "001",
                    payload,
                    "cliente-a",
                )
            )
        else:
            asyncio.run(
                cadastro_produtos.incluir_produto_cadastro_completo(
                    {"sku": "002", **payload},
                    "cliente-a",
                )
            )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "store_id_required"
    assert legado.read_bytes() == antes


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
@pytest.mark.parametrize("operacao", ["atualizar", "incluir"])
def test_crud_global_strict_preserva_alias_de_url_externa_ou_data_url(
    monkeypatch,
    tmp_path,
    photo_column,
    photo_ref,
    operacao,
):
    info_root, _ = _configurar(monkeypatch, tmp_path)
    tenant = info_root / "cliente-a"
    legado = tenant / "cadastro_produtos.csv"
    _write_csv(legado, [{"sku": "001", "nome": "Original"}])
    _ativar_escopo_fotos_estrito(tenant)

    if operacao == "atualizar":
        asyncio.run(
            cadastro_produtos.atualizar_produto_cadastro_completo(
                "001",
                {"nome": "Alterado", photo_column: photo_ref},
                "cliente-a",
            )
        )
        sku_esperado = "001"
    else:
        asyncio.run(
            cadastro_produtos.incluir_produto_cadastro_completo(
                {"sku": "002", "nome": "Novo", photo_column: photo_ref},
                "cliente-a",
            )
        )
        sku_esperado = "002"

    rows = list(csv.DictReader(io.StringIO(legado.read_text(encoding="utf-8-sig"))))
    row = next(item for item in rows if item["sku"] == sku_esperado)
    assert row[photo_column] == photo_ref


@pytest.mark.parametrize(
    "generic_column",
    ["url", "link", "permalink", "produto_url", "anuncio_link", "callback_url", "manual_link"],
)
@pytest.mark.parametrize("operacao", ["atualizar", "incluir"])
def test_crud_global_strict_preserva_campo_generico_com_valor_que_parece_imagem(
    monkeypatch,
    tmp_path,
    generic_column,
    operacao,
):
    info_root, _ = _configurar(monkeypatch, tmp_path)
    tenant = info_root / "cliente-a"
    legado = tenant / "cadastro_produtos.csv"
    _write_csv(legado, [{"sku": "001", "nome": "Original"}])
    _ativar_escopo_fotos_estrito(tenant)
    valor = "/produto/001.jpg"

    if operacao == "atualizar":
        asyncio.run(
            cadastro_produtos.atualizar_produto_cadastro_completo(
                "001",
                {"nome": "Alterado", generic_column: valor},
                "cliente-a",
            )
        )
        sku_esperado = "001"
    else:
        asyncio.run(
            cadastro_produtos.incluir_produto_cadastro_completo(
                {"sku": "002", "nome": "Novo", generic_column: valor},
                "cliente-a",
            )
        )
        sku_esperado = "002"

    rows = list(csv.DictReader(io.StringIO(legado.read_text(encoding="utf-8-sig"))))
    row = next(item for item in rows if item["sku"] == sku_esperado)
    assert row[generic_column] == valor


@pytest.mark.parametrize(
    "campo",
    [
        "store_id",
        "loja_sync",
        "loja",
        "sku_normalizado",
        "row_version",
        "updated_at_utc",
        "deleted_at_utc",
        "scope_source",
    ],
)
def test_schema_crud_global_nao_ignora_vinculo_de_loja(campo):
    with pytest.raises(ValidationError):
        CadastroProdutoRequest(
            sku="001",
            nome="Global",
            **{campo: "Loja A"},
        )


def test_upload_foto_global_rejeita_sku_controlado_sem_criar_fallback(monkeypatch, tmp_path):
    info_root, _ = _configurar(monkeypatch, tmp_path)
    cadastro_lojas_produtos.salvar_produto_loja(
        "cliente-a", "store-a", {"sku": "001", "nome": "Por loja"}
    )
    tenant = info_root / "cliente-a"
    canonico = tenant / "cadastro_produtos_lojas.csv"
    canonico_antes = canonico.read_bytes()

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            cadastro_fotos.upload_foto_cadastro(
                "001", _upload("imagem-global", "001.png"), "cliente-a"
            )
        )

    _assert_store_id_required(exc_info)
    assert canonico.read_bytes() == canonico_antes
    assert not (tenant / "cadastro_fotos" / "001.png").exists()


@pytest.mark.parametrize("rota", ["foto", "importacao"])
def test_mutador_global_revalida_sku_criado_durante_leitura_async(
    monkeypatch,
    tmp_path,
    rota,
):
    info_root, _ = _configurar(monkeypatch, tmp_path)
    tenant = info_root / "cliente-a"

    class UploadComCorrida:
        filename = "001.png" if rota == "foto" else "importacao.csv"
        content_type = "image/png" if rota == "foto" else "text/csv"

        async def read(self):
            cadastro_lojas_produtos.salvar_produto_loja(
                "cliente-a",
                "store-a",
                {"sku": "001", "nome": "Criado durante await"},
            )
            return b"imagem" if rota == "foto" else b"sku;nome\n001;Global\n"

    with pytest.raises(HTTPException) as exc_info:
        if rota == "foto":
            asyncio.run(
                cadastro_fotos.upload_foto_cadastro(
                    "001",
                    UploadComCorrida(),
                    "cliente-a",
                )
            )
        else:
            asyncio.run(
                cadastro_importacao.importar_colunas_cadastro_por_sku(
                    UploadComCorrida(),
                    None,
                    None,
                    "cliente-a",
                )
            )

    _assert_store_id_required(exc_info)
    assert not (tenant / "cadastro_produtos.csv").exists()
    assert not (tenant / "cadastro_fotos" / "001.png").exists()


def test_mutacao_legada_e_scoped_compartilham_o_mesmo_lock_canonico(
    monkeypatch,
    tmp_path,
):
    _configurar(monkeypatch, tmp_path)
    pronto_para_lock = threading.Event()
    terminou = threading.Event()
    dados_real = cadastro_lojas_produtos._dados_mutacao

    def dados_observados(payload):
        resultado = dados_real(payload)
        pronto_para_lock.set()
        return resultado

    monkeypatch.setattr(cadastro_lojas_produtos, "_dados_mutacao", dados_observados)

    def salvar_scoped():
        cadastro_lojas_produtos.salvar_produto_loja(
            "cliente-a",
            "store-a",
            {"sku": "001", "nome": "Scoped"},
        )
        terminou.set()

    with cadastro_compatibilidade.bloquear_mutacao_legada_sem_sku_controlado(
        "cliente-a",
        ["001"],
    ):
        worker = threading.Thread(target=salvar_scoped, daemon=True)
        worker.start()
        assert pronto_para_lock.wait(2)
        assert not terminou.wait(0.1)

    worker.join(2)
    assert not worker.is_alive()
    assert terminou.is_set()
    with pytest.raises(HTTPException) as exc_info:
        with cadastro_compatibilidade.bloquear_mutacao_legada_sem_sku_controlado(
            "cliente-a",
            ["001"],
        ):
            pass
    _assert_store_id_required(exc_info)


def test_tombstone_continua_bloqueando_mutacao_global(monkeypatch, tmp_path):
    info_root, _ = _configurar(monkeypatch, tmp_path)
    tenant = info_root / "cliente-a"
    legado = tenant / "cadastro_produtos.csv"
    _write_csv(legado, [{"sku": "001", "nome": "Legado"}])
    criado = cadastro_lojas_produtos.salvar_produto_loja(
        "cliente-a", "store-a", {"sku": "001", "nome": "Por loja"}
    )
    asyncio.run(
        cadastro_lojas_produtos.excluir_produto_loja(
            "store-a", "001", criado["row_version"], "cliente-a"
        )
    )
    canonico = tenant / "cadastro_produtos_lojas.csv"
    assert "deleted_at_utc" in canonico.read_text(encoding="utf-8-sig")
    legado_antes = legado.read_bytes()
    canonico_antes = canonico.read_bytes()

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            cadastro_produtos.incluir_produto_cadastro_completo(
                {"sku": "001", "nome": "Ressuscitado global"}, "cliente-a"
            )
        )

    _assert_store_id_required(exc_info)
    assert legado.read_bytes() == legado_antes
    assert canonico.read_bytes() == canonico_antes


def test_importacao_global_controlada_retorna_409_mesmo_sem_arquivo_legado(
    monkeypatch, tmp_path
):
    info_root, _ = _configurar(monkeypatch, tmp_path)
    cadastro_lojas_produtos.salvar_produto_loja(
        "cliente-a", "store-a", {"sku": "001", "nome": "Por loja"}
    )
    canonico = info_root / "cliente-a" / "cadastro_produtos_lojas.csv"
    canonico_antes = canonico.read_bytes()

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            cadastro_importacao.importar_colunas_cadastro_por_sku(
                _upload("sku;nome\n001;Importacao global\n"),
                None,
                None,
                "cliente-a",
            )
        )

    _assert_store_id_required(exc_info)
    assert canonico.read_bytes() == canonico_antes
    assert not (info_root / "cliente-a" / "cadastro_produtos.csv").exists()


def test_get_global_controlado_e_read_only_e_syncs_falham_antes_de_efeitos(monkeypatch, tmp_path):
    info_root, _ = _configurar(monkeypatch, tmp_path)
    tenant = info_root / "cliente-a"
    legado = tenant / "cadastro_produtos.csv"
    legado.parent.mkdir(parents=True, exist_ok=True)
    legado.write_bytes(
        b"SKU,Nome,cg_foto,Coluna Indesejada\r\n001,Legado,001.jpg,lixo\r\n"
    )
    cadastro_lojas_produtos.salvar_produto_loja(
        "cliente-a", "store-a", {"sku": "001", "nome": "Por loja"}
    )
    legado_antes = legado.read_bytes()

    produtos = asyncio.run(cadastro_listagem.listar_produtos_cadastro("cliente-a"))

    assert legado.read_bytes() == legado_antes
    assert len([item for item in produtos if item.get("sku") == "001"]) == 1

    def efeito_proibido(*_args, **_kwargs):
        raise AssertionError("efeito global executado antes do bloqueio")

    monkeypatch.setattr(
        cadastro_listagem, "_migrar_arquivo_legado_para_tenant", efeito_proibido
    )
    for flags in ({"sync_fotos": True}, {"sync_ncm": True}):
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(
                cadastro_listagem.listar_produtos_cadastro(
                    "cliente-a", **flags
                )
            )
        _assert_store_id_required(exc_info)
        assert legado.read_bytes() == legado_antes


def test_sync_fotos_global_bloqueia_com_escopo_estrito_mesmo_sem_sku_scoped(
    monkeypatch,
    tmp_path,
):
    info_root, _ = _configurar(monkeypatch, tmp_path)
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
    assert not (tenant / "cadastro_produtos_lojas.csv").exists()

    def efeito_proibido(*_args, **_kwargs):
        raise AssertionError("writer global executado antes do bloqueio")

    monkeypatch.setattr(
        cadastro_listagem, "_migrar_arquivo_legado_para_tenant", efeito_proibido
    )
    monkeypatch.setattr(
        cadastro_listagem, "autenticar_google_sheets", efeito_proibido, raising=False
    )
    monkeypatch.setattr(
        cadastro_listagem,
        "_extrair_imagens_planilha_por_sku",
        efeito_proibido,
        raising=False,
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            cadastro_listagem.listar_produtos_cadastro(
                "cliente-a", sync_fotos=True
            )
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "store_id_required"
    assert exc_info.value.detail["skus"] == []
    assert not (tenant / "cadastro_fotos").exists()


@pytest.mark.parametrize(
    "photo_column",
    [
        "foto",
        "cg_foto",
        "imagem",
        "imagem_url",
        "image_url",
        "url_imagem",
        "link_imagem",
        "picture",
        "thumbnail_url",
        "link_foto",
    ],
)
def test_importacao_global_strict_rejeita_referencia_local_sem_mutar_csv(
    monkeypatch,
    tmp_path,
    photo_column,
):
    info_root, _ = _configurar(monkeypatch, tmp_path)
    tenant = info_root / "cliente-a"
    legado = tenant / "cadastro_produtos.csv"
    _write_csv(legado, [{"sku": "001", "nome": "Original", "foto": ""}])
    legado_antes = legado.read_bytes()
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

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            cadastro_importacao.importar_colunas_cadastro_por_sku(
                _upload(
                    f"sku;nome;{photo_column}\n"
                    "001;Alterado;cadastro_fotos/001.png\n"
                ),
                None,
                None,
                "cliente-a",
            )
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "store_id_required"
    assert exc_info.value.detail["skus"] == ["001"]
    assert legado.read_bytes() == legado_antes
    assert not (tenant / "cadastro_fotos").exists()


@pytest.mark.parametrize(
    "photo_column",
    ("foto", "cg_foto", *_PHOTO_ALIAS_FIELDS),
)
@pytest.mark.parametrize(
    "photo_ref",
    [
        "",
        "https://cdn.example/cadastro_fotos/001.png",
        "//cdn.example/cadastro_fotos/001.png",
        "blob:https://cdn.example/cadastro_fotos/001.png",
    ],
)
def test_importacao_global_strict_preserva_foto_vazia_ou_url_externa(
    monkeypatch,
    tmp_path,
    photo_column,
    photo_ref,
):
    info_root, _ = _configurar(monkeypatch, tmp_path)
    tenant = info_root / "cliente-a"
    legado = tenant / "cadastro_produtos.csv"
    _write_csv(legado, [{"sku": "001", "nome": "Original", "foto": "anterior"}])
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

    asyncio.run(
        cadastro_importacao.importar_colunas_cadastro_por_sku(
            _upload(f"sku;nome;{photo_column}\n001;Alterado;{photo_ref}\n"),
            None,
            None,
            "cliente-a",
        )
    )

    rows = list(csv.DictReader(io.StringIO(legado.read_text(encoding="utf-8-sig"))))
    assert rows[0]["nome"] == "Alterado"
    assert rows[0][photo_column] == photo_ref


def test_sync_fotos_global_revalida_escopo_estrito_no_instante_do_commit(
    monkeypatch,
    tmp_path,
):
    info_root, _ = _configurar(monkeypatch, tmp_path)
    tenant = info_root / "cliente-a"
    legado = tenant / "cadastro_produtos.csv"
    _write_csv(legado, [{"sku": "001", "nome": "Produto", "foto": ""}])
    legado_antes = legado.read_bytes()
    config_path = tenant / cadastro_fotos.CADASTRO_FOTOS_CONFIG_ARQUIVO

    monkeypatch.setattr(
        cadastro_listagem,
        "autenticar_google_sheets",
        lambda: None,
        raising=False,
    )

    def ativar_escopo_estrito_durante_preparo():
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
        return {"001": {"ext": "png", "bytes": b"foto-planilha"}}

    monkeypatch.setattr(
        cadastro_listagem,
        "_extrair_imagens_planilha_por_sku",
        ativar_escopo_estrito_durante_preparo,
        raising=False,
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            cadastro_listagem.listar_produtos_cadastro(
                "cliente-a", sync_fotos=True
            )
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "store_id_required"
    assert exc_info.value.detail["skus"] == []
    assert legado.read_bytes() == legado_antes
    assert not (tenant / "cadastro_fotos" / "001.png").exists()


def test_upload_foto_global_troca_extensao_sem_deixar_variante_antiga(
    monkeypatch,
    tmp_path,
):
    info_root, _ = _configurar(monkeypatch, tmp_path)
    pasta = info_root / "cliente-a" / "cadastro_fotos"
    pasta.mkdir(parents=True, exist_ok=True)
    antiga = pasta / "009.png"
    antiga.write_bytes(b"imagem-antiga")

    resultado = asyncio.run(
        cadastro_fotos.upload_foto_cadastro(
            "009",
            _upload("imagem-nova", "009.jpg"),
            "cliente-a",
        )
    )

    nova = pasta / "009.jpg"
    assert resultado["success"] is True
    assert resultado["foto"] == "cadastro_fotos/009.jpg"
    assert resultado["url"].endswith("/cliente-a/009.jpg")
    assert nova.read_bytes() == b"imagem-nova"
    assert not antiga.exists()
    mapa = cadastro_fotos._cadastro_mapa_fotos_locais("cliente-a")
    assert cadastro_fotos._cadastro_resolver_foto_local(mapa, "009") == (
        "cadastro_fotos/009.jpg"
    )


def test_listagem_global_rejeita_sku_criado_durante_sync_sem_efeitos(
    monkeypatch, tmp_path
):
    info_root, _ = _configurar(monkeypatch, tmp_path)
    tenant = info_root / "cliente-a"
    legado = tenant / "cadastro_produtos.csv"
    _write_csv(
        legado,
        [{"sku": "001", "nome": "Legado", "cg_foto": "foto-antiga.jpg"}],
    )
    legado_antes = legado.read_bytes()

    def criar_scoped_durante_sheets():
        cadastro_lojas_produtos.salvar_produto_loja(
            "cliente-a", "store-a", {"sku": "001", "nome": "Por loja"}
        )
        return None

    monkeypatch.setattr(
        cadastro_listagem,
        "autenticar_google_sheets",
        criar_scoped_durante_sheets,
        raising=False,
    )
    monkeypatch.setattr(
        cadastro_listagem,
        "_extrair_imagens_planilha_por_sku",
        lambda: {},
        raising=False,
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            cadastro_listagem.listar_produtos_cadastro(
                "cliente-a", sync_fotos=True
            )
        )

    _assert_store_id_required(exc_info)
    assert legado.read_bytes() == legado_antes
    assert (tenant / "cadastro_produtos_lojas.csv").exists()


def test_listagem_global_aborta_se_cadastro_legado_muda_durante_sync(
    monkeypatch, tmp_path
):
    info_root, _ = _configurar(monkeypatch, tmp_path)
    legado = info_root / "cliente-a" / "cadastro_produtos.csv"
    _write_csv(
        legado,
        [{"sku": "001", "nome": "Inicial", "cg_foto": "foto-antiga.jpg"}],
    )

    def editar_durante_sheets():
        _write_csv(
            legado,
            [{"sku": "001", "nome": "Edicao concorrente", "cg_foto": "nova.jpg"}],
        )
        return None

    monkeypatch.setattr(
        cadastro_listagem,
        "autenticar_google_sheets",
        editar_durante_sheets,
        raising=False,
    )
    monkeypatch.setattr(
        cadastro_listagem,
        "_extrair_imagens_planilha_por_sku",
        lambda: {},
        raising=False,
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            cadastro_listagem.listar_produtos_cadastro(
                "cliente-a", sync_fotos=True
            )
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "legacy_catalog_changed"
    resultado = legado.read_text(encoding="utf-8-sig")
    assert "Edicao concorrente" in resultado
    assert "Inicial" not in resultado


def test_listagem_global_nao_sobrescreve_foto_alterada_antes_do_commit(
    monkeypatch, tmp_path
):
    info_root, _ = _configurar(monkeypatch, tmp_path)
    tenant = info_root / "cliente-a"
    legado = tenant / "cadastro_produtos.csv"
    _write_csv(legado, [{"sku": "001", "nome": "Produto", "foto": ""}])
    foto = tenant / "cadastro_fotos" / "001.png"
    foto.parent.mkdir(parents=True, exist_ok=True)
    foto.write_bytes(b"foto-antiga")

    monkeypatch.setattr(
        cadastro_listagem,
        "autenticar_google_sheets",
        lambda: None,
        raising=False,
    )
    monkeypatch.setattr(
        cadastro_listagem,
        "_extrair_imagens_planilha_por_sku",
        lambda: {"001": {"ext": "png", "bytes": b"foto-planilha"}},
        raising=False,
    )
    bloquear_real = cadastro_compatibilidade.bloquear_mutacao_legada_sem_sku_controlado

    @contextmanager
    def alterar_foto_antes_do_commit(client_id, skus):
        with bloquear_real(client_id, skus):
            foto.write_bytes(b"foto-concorrente")
            yield

    monkeypatch.setattr(
        cadastro_compatibilidade,
        "bloquear_mutacao_legada_sem_sku_controlado",
        alterar_foto_antes_do_commit,
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            cadastro_listagem.listar_produtos_cadastro(
                "cliente-a", sync_fotos=True
            )
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "legacy_photo_changed"
    assert foto.read_bytes() == b"foto-concorrente"


def test_listagem_global_troca_extensao_da_foto_sem_manter_variante_antiga(
    monkeypatch,
    tmp_path,
):
    info_root, _ = _configurar(monkeypatch, tmp_path)
    tenant = info_root / "cliente-a"
    legado = tenant / "cadastro_produtos.csv"
    _write_csv(legado, [{"sku": "009", "nome": "Produto", "foto": ""}])
    antiga = tenant / "cadastro_fotos" / "009.png"
    antiga.parent.mkdir(parents=True, exist_ok=True)
    antiga.write_bytes(b"foto-antiga")
    monkeypatch.setattr(
        cadastro_listagem,
        "autenticar_google_sheets",
        lambda: None,
        raising=False,
    )
    monkeypatch.setattr(
        cadastro_listagem,
        "_extrair_imagens_planilha_por_sku",
        lambda: {"009": {"ext": "jpg", "bytes": b"foto-nova"}},
        raising=False,
    )

    asyncio.run(
        cadastro_listagem.listar_produtos_cadastro(
            "cliente-a",
            sync_fotos=True,
        )
    )

    nova = tenant / "cadastro_fotos" / "009.jpg"
    assert nova.read_bytes() == b"foto-nova"
    assert not antiga.exists()
    mapa = cadastro_fotos._cadastro_mapa_fotos_locais("cliente-a")
    assert cadastro_fotos._cadastro_resolver_foto_local(mapa, "009") == (
        "cadastro_fotos/009.jpg"
    )


def test_listagem_global_reverte_foto_se_commit_do_csv_falhar(
    monkeypatch,
    tmp_path,
):
    info_root, _ = _configurar(monkeypatch, tmp_path)
    tenant = info_root / "cliente-a"
    legado = tenant / "cadastro_produtos.csv"
    _write_csv(
        legado,
        [{"sku": "009", "nome": "Produto", "foto": "cadastro_fotos/009.png"}],
    )
    legado_antes = legado.read_bytes()
    antiga = tenant / "cadastro_fotos" / "009.png"
    antiga.parent.mkdir(parents=True, exist_ok=True)
    antiga.write_bytes(b"foto-antiga")
    monkeypatch.setattr(
        cadastro_listagem,
        "autenticar_google_sheets",
        lambda: None,
        raising=False,
    )
    monkeypatch.setattr(
        cadastro_listagem,
        "_extrair_imagens_planilha_por_sku",
        lambda: {"009": {"ext": "jpg", "bytes": b"foto-nova"}},
        raising=False,
    )
    monkeypatch.setattr(
        cadastro_listagem,
        "_sync_ncm_salvar_estoque_atomico",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("falha de CSV injetada")
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            cadastro_listagem.listar_produtos_cadastro(
                "cliente-a",
                sync_fotos=True,
            )
        )

    assert exc_info.value.status_code == 500
    assert "falha de CSV injetada" in str(exc_info.value.detail)
    assert legado.read_bytes() == legado_antes
    assert antiga.read_bytes() == b"foto-antiga"
    assert not (tenant / "cadastro_fotos" / "009.jpg").exists()


def test_criar_e_editar_por_loja_alimenta_fachada_e_ia_sem_base_legada(monkeypatch, tmp_path):
    _configurar(monkeypatch, tmp_path)
    cadastro_lojas_produtos.salvar_produto_loja(
        "cliente-single", "store-a", {"sku": "001", "nome": "Versao inicial"}
    )
    atualizado = cadastro_lojas_produtos.salvar_produto_loja(
        "cliente-single", "store-a", {"sku": "001", "nome": "Versao atual"}
    )

    produtos = asyncio.run(cadastro_listagem.listar_produtos_cadastro("cliente-single"))
    df_ia = ia_tools_produtos._ia_carregar_produtos_tool_df("cliente-single")

    assert atualizado["nome"] == "Versao atual"
    assert [item["nome"] for item in produtos] == ["Versao atual"]
    assert df_ia is not None
    assert df_ia.loc[df_ia["sku_norm"] == "001", "nome_tool"].tolist() == [
        "Versao atual"
    ]


def test_ia_remove_legado_e_compilado_de_sku_controlado_ambiguo(monkeypatch, tmp_path):
    info_root, _ = _configurar(monkeypatch, tmp_path)
    tenant = info_root / "cliente-a"
    cadastro_lojas_produtos.salvar_produto_loja(
        "cliente-a", "store-a", {"sku": "001", "nome": "Somente Loja A"}
    )
    _write_csv(
        tenant / "cadastro_produtos.csv",
        [{"sku": "001", "nome": "NOME LEGADO PROIBIDO"}],
    )
    _write_csv(
        tenant / "produtos_compilado.csv",
        [{"sku": "001", "nome_bling": "NOME COMPILADO PROIBIDO"}],
    )

    produtos = asyncio.run(cadastro_listagem.listar_produtos_cadastro("cliente-a"))
    df_ia = ia_tools_produtos._ia_carregar_produtos_tool_df("cliente-a")

    assert len(produtos) == 1
    assert produtos[0]["store_scope_ambiguous"] is True
    assert produtos[0].get("nome", "") == ""
    assert df_ia is not None
    linhas = df_ia[df_ia["sku_norm"] == "001"]
    assert len(linhas) == 1
    assert bool(linhas.iloc[0]["store_scope_ambiguous"]) is True
    assert linhas.iloc[0]["nome_tool"] == ""
    assert "PROIBIDO" not in " ".join(str(valor) for valor in linhas.iloc[0].tolist())


def test_ia_consolida_sku_divergente_sem_escolher_loja(monkeypatch, tmp_path):
    _configurar(monkeypatch, tmp_path)
    cadastro_lojas_produtos.salvar_produto_loja(
        "cliente-a", "store-a", {"sku": "001", "nome": "Produto A"}
    )
    cadastro_lojas_produtos.salvar_produto_loja(
        "cliente-a", "store-b", {"sku": "001", "nome": "Produto B"}
    )

    df_ia = ia_tools_produtos._ia_carregar_produtos_tool_df("cliente-a")

    assert df_ia is not None
    linhas = df_ia[df_ia["sku_norm"] == "001"]
    assert len(linhas) == 1
    assert linhas.iloc[0]["nome_tool"] == ""
    assert bool(linhas.iloc[0]["store_scope_ambiguous"]) is True
    assert linhas.iloc[0]["store_ids"] == "store-a|store-b"


def test_ia_nao_reintroduz_foto_global_em_sku_ambiguo(monkeypatch, tmp_path):
    info_root, _ = _configurar(monkeypatch, tmp_path)
    cadastro_lojas_produtos.salvar_produto_loja(
        "cliente-a",
        "store-a",
        {"sku": "001", "nome": "Produto", "foto": "https://a.invalid/001.png"},
    )
    cadastro_lojas_produtos.salvar_produto_loja(
        "cliente-a",
        "store-b",
        {"sku": "001", "nome": "Produto", "foto": "https://b.invalid/001.png"},
    )
    foto_global = info_root / "cliente-a" / "cadastro_fotos" / "001.png"
    foto_global.parent.mkdir(parents=True, exist_ok=True)
    foto_global.write_bytes(b"fallback-global-proibido")
    monkeypatch.setattr(
        ia_tools_produtos.marketplace_images,
        "find_listing_image",
        lambda *_args, **_kwargs: pytest.fail(
            "SKU ambiguo nao deve buscar imagem global no marketplace"
        ),
    )

    produto = ia_tools_produtos._ia_tool_get_product_data(
        "cliente-a", "SKU 001", limite=1
    )
    match = produto["result"]["matches"][0]
    imagem = ia_tools_produtos._ia_tool_get_product_image(
        "cliente-a", "SKU 001", produto_tool=produto
    )

    assert match["store_scope_ambiguous"] is True
    assert match["imagem_url"] == ""
    assert imagem["result"]["store_scope_ambiguous"] is True
    assert imagem["result"]["imagem_encontrada"] is False
    assert imagem["result"]["imagem_url"] == ""


def test_tombstone_remove_fallback_legado_e_compilado_da_fachada_e_ia(
    monkeypatch, tmp_path
):
    info_root, _ = _configurar(monkeypatch, tmp_path)
    tenant = info_root / "cliente-single"
    criado = cadastro_lojas_produtos.salvar_produto_loja(
        "cliente-single", "store-a", {"sku": "001", "nome": "Por loja"}
    )
    asyncio.run(
        cadastro_lojas_produtos.excluir_produto_loja(
            "store-a", "001", criado["row_version"], "cliente-single"
        )
    )
    _write_csv(
        tenant / "cadastro_produtos.csv", [{"sku": "001", "nome": "Legado proibido"}]
    )
    _write_csv(
        tenant / "produtos_compilado.csv",
        [{"sku": "001", "nome_bling": "Compilado proibido"}],
    )

    produtos = asyncio.run(cadastro_listagem.listar_produtos_cadastro("cliente-single"))
    df_ia = ia_tools_produtos._ia_carregar_produtos_tool_df("cliente-single")

    assert produtos == []
    assert df_ia is not None
    assert df_ia[df_ia["sku_norm"] == "001"].empty


def test_referencias_de_imagem_preservam_caminho_scoped(monkeypatch, tmp_path):
    _configurar(monkeypatch, tmp_path)

    assert ia_tools_produtos._ia_normalizar_imagem_cadastro_url(
        "cadastro_fotos/lojas/store-a/001.png", "cliente-a"
    ) == "/api/cadastro/foto-arquivo/lojas/store-a/001.png"
    assert ia_tools_produtos._ia_normalizar_imagem_cadastro_url(
        "001.png", "cliente-a"
    ) == "/api/cadastro/foto-arquivo/001.png"
    assert ia_tools_produtos._ia_normalizar_imagem_cadastro_url(
        "pasta-desconhecida/001.png", "cliente-a"
    ) == ""


def test_adaptador_ia_preserva_protocolos_externos_e_fecha_caminhos_locais(
    monkeypatch, tmp_path
):
    _configurar(monkeypatch, tmp_path)

    externas = [
        "https://cdn.exemplo/foto.png",
        "http://cdn.exemplo/foto.png",
        "//cdn.exemplo/foto.png",
        "ftp://cdn.exemplo/foto.png",
        "s3://bucket/foto.png",
        "gs://bucket/foto.png",
        "blob:https://app.exemplo/id",
        "data:image/png;base64,AAAA",
    ]
    for referencia in externas:
        assert ia_tools_produtos._ia_normalizar_imagem_cadastro_url(
            referencia, "cliente-a"
        ) == referencia

    assert ia_tools_produtos._ia_normalizar_imagem_cadastro_url(
        "file:///C:/fora/foto.png", "cliente-a"
    ) == ""
    assert ia_tools_produtos._ia_normalizar_imagem_cadastro_url(
        r"C:\fora\foto.png", "cliente-a"
    ) == ""
    assert ia_tools_produtos._ia_normalizar_imagem_cadastro_url(
        "/api/cadastro/foto-arquivo/lojas/store-a/001.png", "cliente-a"
    ) == "/api/cadastro/foto-arquivo/lojas/store-a/001.png"


def test_medias_e_whatsapp_nunca_escolhem_mesmo_basename_de_outra_loja_ou_tenant(
    monkeypatch, tmp_path
):
    info_root, _ = _configurar(monkeypatch, tmp_path)
    foto_a = info_root / "cliente-a" / "cadastro_fotos" / "lojas" / "store-a" / "001.png"
    foto_b = info_root / "cliente-a" / "cadastro_fotos" / "lojas" / "store-b" / "001.png"
    foto_outro_tenant = (
        info_root / "cliente-b" / "cadastro_fotos" / "lojas" / "store-a" / "001.png"
    )
    foto_legada = info_root / "cliente-a" / "cadastro_fotos" / "001.png"
    foto_default_legada = info_root / "default" / "cadastro_fotos" / "legacy.png"
    foto_default_scoped = (
        info_root / "default" / "cadastro_fotos" / "lojas" / "store-a" / "001.png"
    )
    for caminho, conteudo in (
        (foto_a, b"store-a"),
        (foto_b, b"store-b"),
        (foto_outro_tenant, b"tenant-b"),
        (foto_legada, b"legacy"),
        (foto_default_legada, b"default-legacy"),
        (foto_default_scoped, b"default-scoped-proibido"),
    ):
        caminho.parent.mkdir(parents=True, exist_ok=True)
        caminho.write_bytes(conteudo)

    resolver_excel = medias_compras_excel._resolver_caminho_foto_cadastro_seguro
    resolver_sugestoes = medias_compras_sugestoes._resolver_caminho_foto_cadastro_seguro
    for resolver in (resolver_excel, resolver_sugestoes):
        assert Path(
            resolver("cliente-a", "cadastro_fotos/lojas/store-a/001.png", "001")
        ).read_bytes() == b"store-a"
        assert Path(
            resolver("cliente-a", "cadastro_fotos/lojas/store-b/001.png", "001")
        ).read_bytes() == b"store-b"
        assert resolver(
            "cliente-a", "cadastro_fotos/lojas/store-inexistente/001.png", "001"
        ) is None
        assert Path(resolver("cliente-a", "001.png", "001")).read_bytes() == b"legacy"
        assert Path(resolver("cliente-single", "legacy.png", "001")).read_bytes() == b"default-legacy"
        assert resolver(
            "cliente-single", "cadastro_fotos/lojas/store-a/001.png", "001"
        ) is None
        assert resolver("cliente-single", str(foto_default_scoped), "001") is None

    assert whatsapp_artifacts._whatsapp_resolve_image_reference(
        "/api/cadastro/foto-arquivo/lojas/store-a/001.png", "cliente-a"
    ).read_bytes() == b"store-a"
    assert whatsapp_artifacts._whatsapp_resolve_image_reference(
        "cadastro_fotos/lojas/store-b/001.png", "cliente-a"
    ).read_bytes() == b"store-b"
    assert whatsapp_artifacts._whatsapp_resolve_image_reference(
        "/api/cadastro/foto/cliente-a/lojas/store-a/001.png", "cliente-a"
    ).read_bytes() == b"store-a"
    assert whatsapp_artifacts._whatsapp_resolve_image_reference(
        "/api/cadastro/foto/cliente-b/lojas/store-a/001.png", "cliente-a"
    ) is None
    assert whatsapp_artifacts._whatsapp_resolve_image_reference(
        "cadastro_fotos/lojas/store-inexistente/001.png", "cliente-a"
    ) is None
    assert whatsapp_artifacts._whatsapp_resolve_image_reference(
        "cadastro_fotos/lojas/store-a/001.png", "cliente-b"
    ).read_bytes() == b"tenant-b"
