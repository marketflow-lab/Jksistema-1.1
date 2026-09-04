from __future__ import annotations

import csv
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from backend.services import cadastro_fotos_migracao as migracao
from backend.services.cadastro_fotos import (
    CADASTRO_FOTOS_CONFIG_ARQUIVO,
    CADASTRO_FOTOS_CONFIG_SCHEMA,
    _cadastro_store_id_foto_segmento,
)


CLIENT_ID = "000002"
STORE_IDS = ("uai-mineirinho", "jk-pecas", "carlos-jose")
GROUP_ID = "uai-jk-carlos"
FOTOS = {
    "005.jpg": b"foto-jpg-005",
    "005.png": b"foto-png-005",
}


def _criar_contexto(
    raiz: Path,
    *,
    fotos: dict[str, bytes] | None = None,
    store_ids: tuple[str, ...] = STORE_IDS,
) -> Path:
    tenant = raiz / CLIENT_ID
    pasta_fotos = tenant / "cadastro_fotos"
    pasta_fotos.mkdir(parents=True)
    (tenant / "lojas_config.json").write_text(
        json.dumps(
            [{"store_id": store_id} for store_id in store_ids],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    for nome, conteudo in (fotos or FOTOS).items():
        (pasta_fotos / nome).write_bytes(conteudo)
    return raiz


def _pasta_fotos(raiz: Path) -> Path:
    return raiz / CLIENT_ID / "cadastro_fotos"


def _pasta_loja(raiz: Path, store_id: str) -> Path:
    return _pasta_fotos(raiz) / "lojas" / _cadastro_store_id_foto_segmento(store_id)


def _config_path(raiz: Path) -> Path:
    return raiz / CLIENT_ID / CADASTRO_FOTOS_CONFIG_ARQUIVO


def _journal_path(raiz: Path) -> Path:
    return raiz / CLIENT_ID / migracao.MIGRACAO_JOURNAL_ARQUIVO


def _quarentena_path(raiz: Path) -> Path:
    return _pasta_fotos(raiz) / migracao.MIGRACAO_QUARENTENA_PASTA


def _config_esperada() -> dict[str, object]:
    return {
        "schema": CADASTRO_FOTOS_CONFIG_SCHEMA,
        "strict_store_scope": True,
        "shared_groups": [
            {
                "group_id": GROUP_ID,
                "store_ids": list(STORE_IDS),
            }
        ],
    }


def _executar_migracao_fotos_contextos_aprovado(
    info_roots: object,
    client_id: str,
    store_ids: object,
    *,
    group_id: str = GROUP_ID,
    delete_legacy: bool = False,
) -> dict[str, object]:
    raizes = list(info_roots)  # type: ignore[arg-type]
    lojas = list(store_ids)  # type: ignore[arg-type]
    plano = migracao.planejar_migracao_fotos_contextos(
        raizes,
        client_id,
        lojas,
        group_id=group_id,
    )
    return migracao.executar_migracao_fotos_contextos(
        raizes,
        client_id,
        lojas,
        group_id=group_id,
        delete_legacy=delete_legacy,
        expected_plan_sha256=plano["report"]["plan_sha256"],
    )


def _snapshot_arvore(raiz: Path) -> tuple[tuple[str, ...], dict[str, bytes]]:
    diretorios = tuple(
        sorted(
            caminho.relative_to(raiz).as_posix()
            for caminho in raiz.rglob("*")
            if caminho.is_dir()
        )
    )
    arquivos = {
        caminho.relative_to(raiz).as_posix(): caminho.read_bytes()
        for caminho in sorted(raiz.rglob("*"))
        if caminho.is_file()
    }
    return diretorios, arquivos


def _criar_link_diretorio(link: Path, alvo: Path, tipo: str) -> None:
    if tipo == "symlink":
        try:
            link.symlink_to(alvo, target_is_directory=True)
        except (NotImplementedError, OSError) as exc:
            pytest.skip(f"symlink de diretorio indisponivel: {exc}")
        return

    if os.name != "nt" or not hasattr(link, "is_junction"):
        pytest.skip("junction de diretorio so e verificavel neste runtime do Windows")
    resultado = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(alvo.resolve())],
        capture_output=True,
        text=True,
        check=False,
    )
    if resultado.returncode != 0:
        pytest.skip("criacao de junction nao autorizada neste ambiente")
    assert link.is_junction()


def _remover_link_diretorio(link: Path) -> None:
    if not os.path.lexists(link):
        return
    is_junction = getattr(link, "is_junction", None)
    if os.name == "nt" and callable(is_junction) and is_junction():
        os.rmdir(link)
    else:
        link.unlink()


def _assert_fotos_legadas(raiz: Path, esperadas: dict[str, bytes] = FOTOS) -> None:
    for nome, conteudo in esperadas.items():
        assert (_pasta_fotos(raiz) / nome).read_bytes() == conteudo


def _assert_destinos_completos(raizes: list[Path]) -> None:
    for raiz in raizes:
        for store_id in STORE_IDS:
            pasta = _pasta_loja(raiz, store_id)
            assert {
                caminho.name: caminho.read_bytes()
                for caminho in pasta.iterdir()
                if caminho.is_file()
            } == FOTOS


def test_dry_run_planeja_dois_contextos_sem_mutar(tmp_path: Path) -> None:
    raizes = [
        _criar_contexto(tmp_path / "checkout" / "info"),
        _criar_contexto(tmp_path / "dados-operacionais" / "info"),
    ]
    antes = [_snapshot_arvore(raiz) for raiz in raizes]

    plano = migracao.planejar_migracao_fotos_contextos(
        raizes,
        CLIENT_ID,
        STORE_IDS,
        group_id=GROUP_ID,
    )

    assert plano["report"] == {
        "client_id": CLIENT_ID,
        "contexts": [
            {
                "source_files": 2,
                "source_bytes": sum(map(len, FOTOS.values())),
                "source_manifest_sha256": contexto["report"]["source_manifest_sha256"],
                "stores": 3,
                "copies_required": 6,
                "copies_equal": 0,
                "conflicts": 0,
                "legacy_files_to_delete": 2,
                "config_action": "create",
                "ready": True,
            }
            for contexto in plano["plans"]
        ],
        "total_copies_required": 12,
        "total_legacy_files_to_delete": 4,
        "ready": True,
        "plan_sha256": plano["report"]["plan_sha256"],
    }
    assert len(plano["report"]["plan_sha256"]) == 64
    assert [_snapshot_arvore(raiz) for raiz in raizes] == antes


def test_apply_exige_sha256_emitido_pelo_dry_run_sem_mutar(tmp_path: Path) -> None:
    raiz = _criar_contexto(tmp_path / "info")
    antes = _snapshot_arvore(raiz)

    with pytest.raises(migracao.CadastroFotosMigracaoErro) as erro:
        migracao.executar_migracao_fotos_contextos(
            [raiz],
            CLIENT_ID,
            STORE_IDS,
            group_id=GROUP_ID,
            delete_legacy=True,
        )

    assert erro.value.code == "plan_confirmation_required"
    assert _snapshot_arvore(raiz) == antes


def test_foto_adicionada_apos_dry_run_invalida_plano_antes_de_escrever(
    tmp_path: Path,
) -> None:
    raiz = _criar_contexto(tmp_path / "info")
    plano = migracao.planejar_migracao_fotos_contextos(
        [raiz],
        CLIENT_ID,
        STORE_IDS,
        group_id=GROUP_ID,
    )
    (_pasta_fotos(raiz) / "006.jpg").write_bytes(b"foto-adicionada-depois")
    antes_apply = _snapshot_arvore(raiz)

    with pytest.raises(migracao.CadastroFotosMigracaoErro) as erro:
        migracao.executar_migracao_fotos_contextos(
            [raiz],
            CLIENT_ID,
            STORE_IDS,
            group_id=GROUP_ID,
            delete_legacy=True,
            expected_plan_sha256=plano["report"]["plan_sha256"],
        )

    assert erro.value.code == "migration_plan_changed"
    assert _snapshot_arvore(raiz) == antes_apply
    assert not _config_path(raiz).exists()
    assert not (_pasta_fotos(raiz) / "lojas").exists()


def test_lojas_config_duplicado_apos_dry_run_bloqueia_sem_excluir(
    tmp_path: Path,
) -> None:
    raiz = _criar_contexto(tmp_path / "info")
    plano = migracao.planejar_migracao_fotos_contextos(
        [raiz],
        CLIENT_ID,
        STORE_IDS,
        group_id=GROUP_ID,
    )
    lojas_path = raiz / CLIENT_ID / "lojas_config.json"
    lojas = json.loads(lojas_path.read_text(encoding="utf-8"))
    lojas.append(dict(lojas[0]))
    lojas_path.write_text(json.dumps(lojas), encoding="utf-8")
    antes_apply = _snapshot_arvore(raiz)

    with pytest.raises(migracao.CadastroFotosMigracaoErro) as erro:
        migracao.executar_migracao_fotos_contextos(
            [raiz],
            CLIENT_ID,
            STORE_IDS,
            group_id=GROUP_ID,
            delete_legacy=True,
            expected_plan_sha256=plano["report"]["plan_sha256"],
        )

    assert erro.value.code == "stores_config_invalid"
    assert _snapshot_arvore(raiz) == antes_apply
    _assert_fotos_legadas(raiz)


def test_lojas_config_valido_alterado_apos_dry_run_muda_token(
    tmp_path: Path,
) -> None:
    raiz = _criar_contexto(tmp_path / "info")
    plano = migracao.planejar_migracao_fotos_contextos(
        [raiz],
        CLIENT_ID,
        STORE_IDS,
        group_id=GROUP_ID,
    )
    lojas_path = raiz / CLIENT_ID / "lojas_config.json"
    lojas = json.loads(lojas_path.read_text(encoding="utf-8"))
    lojas[0]["nome"] = "Nome alterado depois do dry-run"
    lojas_path.write_text(json.dumps(lojas), encoding="utf-8")
    antes_apply = _snapshot_arvore(raiz)

    with pytest.raises(migracao.CadastroFotosMigracaoErro) as erro:
        migracao.executar_migracao_fotos_contextos(
            [raiz],
            CLIENT_ID,
            STORE_IDS,
            group_id=GROUP_ID,
            delete_legacy=True,
            expected_plan_sha256=plano["report"]["plan_sha256"],
        )

    assert erro.value.code == "migration_plan_changed"
    assert _snapshot_arvore(raiz) == antes_apply


def test_lojas_config_wrapper_incompativel_com_runtime_falha_sem_mutar(
    tmp_path: Path,
) -> None:
    raiz = _criar_contexto(tmp_path / "info")
    lojas_path = raiz / CLIENT_ID / "lojas_config.json"
    lojas = json.loads(lojas_path.read_text(encoding="utf-8"))
    lojas_path.write_text(json.dumps({"lojas": lojas}), encoding="utf-8")
    antes = _snapshot_arvore(raiz)

    with pytest.raises(migracao.CadastroFotosMigracaoErro) as erro:
        migracao.planejar_migracao_fotos_contextos(
            [raiz],
            CLIENT_ID,
            STORE_IDS,
            group_id=GROUP_ID,
        )

    assert erro.value.code == "stores_config_invalid"
    assert _snapshot_arvore(raiz) == antes


def test_tenant_substituido_por_clone_identico_invalida_plano(
    tmp_path: Path,
) -> None:
    raiz = _criar_contexto(tmp_path / "info")
    plano = migracao.planejar_migracao_fotos_contextos(
        [raiz],
        CLIENT_ID,
        STORE_IDS,
        group_id=GROUP_ID,
    )
    tenant = raiz / CLIENT_ID
    clone = tmp_path / "clone-tenant"
    backup = tmp_path / "tenant-original"
    shutil.copytree(tenant, clone)
    tenant.rename(backup)
    clone.rename(tenant)
    antes_apply = _snapshot_arvore(raiz)

    with pytest.raises(migracao.CadastroFotosMigracaoErro) as erro:
        migracao.executar_migracao_fotos_contextos(
            [raiz],
            CLIENT_ID,
            STORE_IDS,
            group_id=GROUP_ID,
            delete_legacy=True,
            expected_plan_sha256=plano["report"]["plan_sha256"],
        )

    assert erro.value.code == "migration_plan_changed"
    assert _snapshot_arvore(raiz) == antes_apply


def test_troca_de_quais_destinos_estao_prontos_invalida_plano(
    tmp_path: Path,
) -> None:
    fotos = {"pequena.jpg": b"p", "grande.jpg": b"g" * 4096}
    raiz = _criar_contexto(tmp_path / "info", fotos=fotos)
    pasta = _pasta_loja(raiz, STORE_IDS[0])
    pasta.mkdir(parents=True)
    (pasta / "pequena.jpg").write_bytes(fotos["pequena.jpg"])
    plano = migracao.planejar_migracao_fotos_contextos(
        [raiz],
        CLIENT_ID,
        STORE_IDS,
        group_id=GROUP_ID,
    )
    (pasta / "pequena.jpg").unlink()
    (pasta / "grande.jpg").write_bytes(fotos["grande.jpg"])
    antes_apply = _snapshot_arvore(raiz)

    with pytest.raises(migracao.CadastroFotosMigracaoErro) as erro:
        migracao.executar_migracao_fotos_contextos(
            [raiz],
            CLIENT_ID,
            STORE_IDS,
            group_id=GROUP_ID,
            delete_legacy=True,
            expected_plan_sha256=plano["report"]["plan_sha256"],
        )

    assert erro.value.code == "migration_plan_changed"
    assert _snapshot_arvore(raiz) == antes_apply


def test_cli_dry_run_emite_sha256_sem_escrever(tmp_path: Path) -> None:
    raiz = _criar_contexto(tmp_path / "info")
    antes = _snapshot_arvore(raiz)
    script = Path(__file__).resolve().parents[1] / "scripts" / "migrate-cadastro-fotos-lojas.py"
    comando = [
        sys.executable,
        str(script),
        "--info-root",
        str(raiz),
        "--client-id",
        CLIENT_ID,
    ]
    for store_id in STORE_IDS:
        comando.extend(("--store-id", store_id))

    processo = subprocess.run(
        comando,
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    relatorio = json.loads(processo.stdout)

    assert len(relatorio["plan_sha256"]) == 64
    assert relatorio["total_copies_required"] == len(FOTOS) * len(STORE_IDS)
    assert _snapshot_arvore(raiz) == antes


@pytest.mark.parametrize(
    ("arquivo", "conteudo"),
    [
        (
            "cadastro_produtos.csv",
            "sku,foto\n009,cadastro_fotos/produto.jpg\n",
        ),
        (
            "cadastro_produtos_lojas.csv",
            "store_id,sku,sku_normalizado,foto\n"
            "uai-mineirinho,009,009,cadastro_fotos/produto.jpg\n",
        ),
        (
            "produtos_compilado.csv",
            "store_id,sku,foto\n"
            "uai-mineirinho,009,cadastro_fotos/produto.jpg\n",
        ),
    ],
)
def test_referencia_legada_com_nome_diferente_do_sku_bloqueia_sem_mutar(
    tmp_path: Path,
    arquivo: str,
    conteudo: str,
) -> None:
    raiz = _criar_contexto(
        tmp_path / "info",
        fotos={"produto.jpg": b"foto-nao-canonica"},
    )
    (raiz / CLIENT_ID / arquivo).write_text(conteudo, encoding="utf-8")
    antes = _snapshot_arvore(raiz)

    with pytest.raises(migracao.CadastroFotosMigracaoErro) as erro_plano:
        migracao.planejar_migracao_fotos_contextos(
            [raiz],
            CLIENT_ID,
            STORE_IDS,
            group_id=GROUP_ID,
        )
    assert erro_plano.value.code == "legacy_photo_filename_sku_mismatch"
    assert _snapshot_arvore(raiz) == antes

    with pytest.raises(migracao.CadastroFotosMigracaoErro) as erro_execucao:
        _executar_migracao_fotos_contextos_aprovado(
            [raiz],
            CLIENT_ID,
            STORE_IDS,
            group_id=GROUP_ID,
            delete_legacy=True,
        )
    assert erro_execucao.value.code == "legacy_photo_filename_sku_mismatch"
    assert _snapshot_arvore(raiz) == antes


@pytest.mark.parametrize("delimitador", [",", ";", "\t"])
def test_preflight_detecta_referencia_nao_canonica_em_delimitadores_suportados(
    tmp_path: Path,
    delimitador: str,
) -> None:
    raiz = _criar_contexto(
        tmp_path / "info",
        fotos={"produto.jpg": b"foto-nao-canonica"},
    )
    (raiz / CLIENT_ID / "cadastro_produtos.csv").write_text(
        f"sku{delimitador}foto\n"
        f"009{delimitador}cadastro_fotos/produto.jpg\n",
        encoding="utf-8",
    )
    antes = _snapshot_arvore(raiz)

    with pytest.raises(migracao.CadastroFotosMigracaoErro) as erro:
        migracao.planejar_migracao_fotos_contextos(
            [raiz],
            CLIENT_ID,
            STORE_IDS,
            group_id=GROUP_ID,
        )

    assert erro.value.code == "legacy_photo_filename_sku_mismatch"
    assert _snapshot_arvore(raiz) == antes


@pytest.mark.parametrize(
    "referencia",
    [
        "cadastro_fotos/009.jpg",
        "C:/dados/000002/cadastro_fotos/009.jpg",
        "file:///C:/dados/000002/cadastro_fotos/009.jpg",
        "api/cadastro/foto/000002/009.jpg",
        "api/cadastro/foto-arquivo/009.jpg",
        "api%2Fcadastro%2Ffoto%2F000002%2F009.jpg",
        "api%252Fcadastro%252Ffoto-arquivo%252F009.jpg",
        "x/../api/cadastro/foto/000002/009.jpg",
        "https://exemplo.invalid/x/../api/cadastro/foto-arquivo/009.jpg",
        "https:/api/cadastro/foto-arquivo/009.jpg",
        "https:api/cadastro/foto/000002/009.jpg",
        "%2e%2e/api/cadastro/foto/000002/009.jpg",
    ],
)
def test_referencia_legada_canonica_bloqueia_antes_de_ativar_escopo_estrito(
    tmp_path: Path,
    referencia: str,
) -> None:
    raiz = _criar_contexto(tmp_path / "info", fotos={"009.jpg": b"foto-009"})
    (raiz / CLIENT_ID / "cadastro_produtos.csv").write_text(
        f"sku,foto\n009,{referencia}\n",
        encoding="utf-8",
    )
    antes = _snapshot_arvore(raiz)

    with pytest.raises(migracao.CadastroFotosMigracaoErro) as erro:
        migracao.planejar_migracao_fotos_contextos(
            [raiz],
            CLIENT_ID,
            STORE_IDS,
            group_id=GROUP_ID,
        )

    assert erro.value.code == "legacy_photo_reference_requires_store_migration"
    assert not _config_path(raiz).exists()
    assert not (_pasta_fotos(raiz) / "lojas").exists()
    assert _snapshot_arvore(raiz) == antes


@pytest.mark.parametrize(
    "referencia",
    [
        "foo/009.jpg",
        "C:/outra/009.jpg",
        "/qualquer/009.jpg",
        "file:///C:/outra/009.jpg",
    ],
)
@pytest.mark.parametrize("coluna", ["foto", "imagem"])
@pytest.mark.parametrize("arquivo", migracao.ARQUIVOS_CADASTRO_COM_REFERENCIA_FOTO)
def test_caminho_achatado_pela_tela_bloqueia_exclusao_em_todos_os_csvs(
    tmp_path: Path,
    referencia: str,
    coluna: str,
    arquivo: str,
) -> None:
    raiz = _criar_contexto(tmp_path / "info", fotos={"009.jpg": b"foto-009"})
    caminho_csv = raiz / CLIENT_ID / arquivo
    with caminho_csv.open("w", encoding="utf-8", newline="") as saida:
        escritor = csv.writer(saida)
        escritor.writerow(["sku", coluna])
        escritor.writerow(["009", referencia])
    antes = _snapshot_arvore(raiz)

    with pytest.raises(migracao.CadastroFotosMigracaoErro) as erro:
        migracao.planejar_migracao_fotos_contextos(
            [raiz],
            CLIENT_ID,
            STORE_IDS,
            group_id=GROUP_ID,
        )

    assert erro.value.code == "legacy_photo_reference_requires_store_migration"
    assert _snapshot_arvore(raiz) == antes


@pytest.mark.parametrize(
    "coluna",
    [
        "cg_foto",
        "imagem",
        "imagem_url",
        "image_url",
        "url_imagem",
        "link_imagem",
        "image",
        "picture",
        "url foto",
        "link foto",
        "foto produto",
        "thumbnail_url",
        "URL da Imagem",
    ],
)
@pytest.mark.parametrize("arquivo", migracao.ARQUIVOS_CADASTRO_COM_REFERENCIA_FOTO)
def test_alias_de_imagem_com_referencia_legada_bloqueia_sem_mutar(
    tmp_path: Path,
    coluna: str,
    arquivo: str,
) -> None:
    raiz = _criar_contexto(tmp_path / "info", fotos={"009.jpg": b"foto-009"})
    (raiz / CLIENT_ID / arquivo).write_text(
        f"sku,{coluna}\n009,cadastro_fotos/009.jpg\n",
        encoding="utf-8",
    )
    antes = _snapshot_arvore(raiz)

    with pytest.raises(migracao.CadastroFotosMigracaoErro) as erro:
        migracao.planejar_migracao_fotos_contextos(
            [raiz],
            CLIENT_ID,
            STORE_IDS,
            group_id=GROUP_ID,
        )

    assert erro.value.code == "legacy_photo_reference_requires_store_migration"
    assert _snapshot_arvore(raiz) == antes


@pytest.mark.parametrize("wrapper", ["'", '"', "`", "%27"])
@pytest.mark.parametrize("coluna", ["foto", "imagem", "image_url"])
@pytest.mark.parametrize("arquivo", migracao.ARQUIVOS_CADASTRO_COM_REFERENCIA_FOTO)
def test_referencia_legada_com_aspas_ou_crase_bloqueia_em_todos_os_csvs(
    tmp_path: Path,
    wrapper: str,
    coluna: str,
    arquivo: str,
) -> None:
    raiz = _criar_contexto(tmp_path / "info", fotos={"009.jpg": b"foto-009"})
    caminho_csv = raiz / CLIENT_ID / arquivo
    with caminho_csv.open("w", encoding="utf-8", newline="") as saida:
        escritor = csv.writer(saida)
        escritor.writerow(["sku", coluna])
        escritor.writerow(["009", f"{wrapper}cadastro_fotos/009.jpg{wrapper}"])
    antes = _snapshot_arvore(raiz)

    with pytest.raises(migracao.CadastroFotosMigracaoErro) as erro:
        migracao.planejar_migracao_fotos_contextos(
            [raiz],
            CLIENT_ID,
            STORE_IDS,
            group_id=GROUP_ID,
        )

    assert erro.value.code == "legacy_photo_reference_requires_store_migration"
    assert _snapshot_arvore(raiz) == antes


@pytest.mark.parametrize(
    "referencia",
    [
        "https://cdn.exemplo.invalid/produtos/009.jpg",
        "//cdn.exemplo.invalid/produtos/009.jpg",
        "ftp://cdn.exemplo.invalid/produtos/009.jpg",
        "s3://bucket/produtos/009.jpg",
        "gs://bucket/produtos/009.png",
        "blob:https://cdn.exemplo.invalid/produtos/009.jpg",
        "data:image/jpeg;base64,AA==",
        "lojas/store-segment/009.jpg",
        "/api/cadastro/foto/000002/lojas/store-segment/009.jpg",
    ],
)
def test_alias_de_imagem_nao_bloqueia_referencia_externa_ou_ja_por_loja(
    tmp_path: Path,
    referencia: str,
) -> None:
    raiz = _criar_contexto(tmp_path / "info", fotos={"009.jpg": b"foto-009"})
    (raiz / CLIENT_ID / "cadastro_produtos.csv").write_text(
        f'sku,imagem_url\n009,"{referencia}"\n',
        encoding="utf-8",
    )
    antes = _snapshot_arvore(raiz)

    plano = migracao.planejar_migracao_fotos_contextos(
        [raiz],
        CLIENT_ID,
        STORE_IDS,
        group_id=GROUP_ID,
    )

    assert plano["report"]["ready"] is True
    assert _snapshot_arvore(raiz) == antes


def test_referencia_para_variante_nao_preferida_divergente_bloqueia_sem_mutar(
    tmp_path: Path,
) -> None:
    raiz = _criar_contexto(
        tmp_path / "info",
        fotos={"009.png": b"foto-antiga", "009.jpg": b"foto-nova"},
    )
    (raiz / CLIENT_ID / "cadastro_produtos.csv").write_text(
        "sku,foto\n009,cadastro_fotos/009.jpg\n",
        encoding="utf-8",
    )
    antes = _snapshot_arvore(raiz)

    with pytest.raises(migracao.CadastroFotosMigracaoErro) as erro:
        _executar_migracao_fotos_contextos_aprovado(
            [raiz],
            CLIENT_ID,
            STORE_IDS,
            group_id=GROUP_ID,
            delete_legacy=True,
        )

    assert erro.value.code == "legacy_photo_variant_ambiguous"
    assert _snapshot_arvore(raiz) == antes


def test_apply_copia_tres_lojas_nos_dois_contextos_antes_de_excluir_legadas(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raizes = [
        _criar_contexto(tmp_path / "checkout" / "info"),
        _criar_contexto(tmp_path / "dados-operacionais" / "info"),
    ]
    pastas_legadas = {_pasta_fotos(raiz).resolve() for raiz in raizes}
    replace_real = migracao.os.replace
    exclusoes_observadas = 0

    def replace_observado(origem: object, destino: object) -> None:
        nonlocal exclusoes_observadas
        origem_path = Path(origem)
        destino_path = Path(destino)
        movendo_legada = (
            origem_path.parent.resolve() in pastas_legadas
            and destino_path.parent.name.startswith(".cadastro-fotos-legadas-")
        )
        if movendo_legada:
            _assert_destinos_completos(raizes)
            exclusoes_observadas += 1
        replace_real(origem, destino)

    monkeypatch.setattr(migracao.os, "replace", replace_observado)

    resultado = _executar_migracao_fotos_contextos_aprovado(
        raizes,
        CLIENT_ID,
        STORE_IDS,
        group_id=GROUP_ID,
        delete_legacy=True,
    )

    assert resultado == {
        "success": True,
        "status": "migrated",
        "client_id": CLIENT_ID,
        "contexts": 2,
        "stores": 3,
        "copied": 12,
        "already_equal": 0,
        "legacy_deleted": 4,
        "strict_store_scope": True,
        "shared_group": GROUP_ID,
    }
    assert exclusoes_observadas == 4
    _assert_destinos_completos(raizes)
    for raiz in raizes:
        assert not any(
            caminho.is_file() and caminho.suffix.casefold() in migracao.EXTENSOES_FOTO_SUPORTADAS
            for caminho in _pasta_fotos(raiz).iterdir()
        )


def test_apply_460_origens_resulta_em_1380_destinos_sem_residuos(
    tmp_path: Path,
) -> None:
    fotos = {
        f"SKU-{indice:04d}.jpg": f"foto-{indice:04d}".encode("ascii")
        for indice in range(460)
    }
    raiz = _criar_contexto(tmp_path / "info", fotos=fotos)
    plano = migracao.planejar_migracao_fotos_contextos(
        [raiz],
        CLIENT_ID,
        STORE_IDS,
        group_id=GROUP_ID,
    )

    assert plano["report"]["contexts"][0]["source_files"] == 460
    assert plano["report"]["total_copies_required"] == 1380
    resultado = migracao.executar_migracao_fotos_contextos(
        [raiz],
        CLIENT_ID,
        STORE_IDS,
        group_id=GROUP_ID,
        delete_legacy=True,
        expected_plan_sha256=plano["report"]["plan_sha256"],
    )

    assert resultado["copied"] == 1380
    assert resultado["legacy_deleted"] == 460
    assert sum(
        1
        for store_id in STORE_IDS
        for caminho in _pasta_loja(raiz, store_id).iterdir()
        if caminho.is_file() and caminho.suffix.casefold() == ".jpg"
    ) == 1380
    assert not _journal_path(raiz).exists()
    assert not _quarentena_path(raiz).exists()
    assert not list(_pasta_fotos(raiz).rglob(".cadastro-foto-migracao.*.tmp"))
    assert not any(
        caminho.is_file() and caminho.suffix.casefold() in migracao.EXTENSOES_FOTO_SUPORTADAS
        for caminho in _pasta_fotos(raiz).iterdir()
    )


def test_apply_cria_cadastro_canonico_ausente_por_associacao_comprovada(
    tmp_path: Path,
) -> None:
    raiz = _criar_contexto(
        tmp_path / "info",
        fotos={"001.jpg": b"foto-001"},
    )
    tenant = raiz / CLIENT_ID
    lojas = [
        {"store_id": store_id, "nome": nome}
        for store_id, nome in zip(
            STORE_IDS,
            ("Uai Mineirinho", "JK Pecas", "Carlos Jose"),
        )
    ]
    (tenant / "lojas_config.json").write_text(
        json.dumps(lojas),
        encoding="utf-8",
    )
    (tenant / "cadastro_produtos.csv").write_text(
        "sku,nome\n001,Produto base\n",
        encoding="utf-8-sig",
    )
    (tenant / "produtos_compilado.csv").write_text(
        "sku,loja_sync,nome_bling\n"
        "001,Uai Mineirinho,Produto Uai\n"
        "001,JK Pecas,Produto JK\n"
        "001,Carlos Jose,Produto Carlos\n",
        encoding="utf-8-sig",
    )
    canonico = tenant / "cadastro_produtos_lojas.csv"
    assert not canonico.exists()

    _executar_migracao_fotos_contextos_aprovado(
        [raiz],
        CLIENT_ID,
        STORE_IDS,
        group_id=GROUP_ID,
        delete_legacy=True,
    )

    with canonico.open("r", encoding="utf-8-sig", newline="") as arquivo:
        linhas = list(csv.DictReader(arquivo))
    assert len(linhas) == 3
    assert {linha["store_id"] for linha in linhas} == set(STORE_IDS)
    assert {linha["row_version"] for linha in linhas} == {"1"}
    assert len({linha["updated_at_utc"] for linha in linhas}) == 1
    assert all(linha["updated_at_utc"] for linha in linhas)
    assert {
        linha["foto"]
        for linha in linhas
    } == {
        f"cadastro_fotos/lojas/{_cadastro_store_id_foto_segmento(store_id)}/001.jpg"
        for store_id in STORE_IDS
    }
    assert not (_pasta_fotos(raiz) / "001.jpg").exists()


def test_apply_materializa_blank_sem_ressuscitar_tombstone_ou_reemitir_evento(
    tmp_path: Path,
) -> None:
    raiz = _criar_contexto(
        tmp_path / "info",
        fotos={"001.jpg": b"foto-001"},
    )
    tenant = raiz / CLIENT_ID
    desejada_c = (
        "cadastro_fotos/lojas/"
        f"{_cadastro_store_id_foto_segmento(STORE_IDS[2])}/001.jpg"
    )
    (tenant / "cadastro_produtos_lojas.csv").write_text(
        "store_id,sku,sku_normalizado,loja_sync,foto,row_version,updated_at_utc,deleted_at_utc\n"
        f"{STORE_IDS[0]},001,001,Uai,,4,2026-08-01T00:00:00Z,\n"
        f"{STORE_IDS[1]},001,001,JK,,8,2026-08-02T00:00:00Z,2026-08-02T00:00:00Z\n"
        f"{STORE_IDS[2]},001,001,Carlos,{desejada_c},2,2026-08-03T00:00:00Z,\n",
        encoding="utf-8-sig",
    )

    _executar_migracao_fotos_contextos_aprovado(
        [raiz],
        CLIENT_ID,
        STORE_IDS,
        group_id=GROUP_ID,
        delete_legacy=True,
    )

    with (tenant / "cadastro_produtos_lojas.csv").open(
        "r", encoding="utf-8-sig", newline=""
    ) as arquivo:
        linhas = {linha["store_id"]: linha for linha in csv.DictReader(arquivo)}
    assert linhas[STORE_IDS[0]]["row_version"] == "5"
    assert linhas[STORE_IDS[0]]["foto"].endswith("/001.jpg")
    assert linhas[STORE_IDS[1]]["row_version"] == "8"
    assert linhas[STORE_IDS[1]]["foto"] == ""
    assert linhas[STORE_IDS[1]]["deleted_at_utc"]
    assert linhas[STORE_IDS[2]]["row_version"] == "2"
    assert linhas[STORE_IDS[2]]["updated_at_utc"] == "2026-08-03T00:00:00Z"


def test_apply_aceita_store_ids_em_generator_e_copia_todas_as_lojas(
    tmp_path: Path,
) -> None:
    raiz = _criar_contexto(tmp_path / "info")

    resultado = _executar_migracao_fotos_contextos_aprovado(
        [raiz],
        CLIENT_ID,
        (store_id for store_id in STORE_IDS),
        group_id=GROUP_ID,
        delete_legacy=True,
    )

    assert resultado["stores"] == len(STORE_IDS)
    assert resultado["copied"] == len(STORE_IDS) * len(FOTOS)
    _assert_destinos_completos([raiz])


def test_apply_grava_config_strict_exata_em_cada_contexto(tmp_path: Path) -> None:
    raizes = [
        _criar_contexto(tmp_path / "checkout" / "info"),
        _criar_contexto(tmp_path / "dados-operacionais" / "info"),
    ]

    _executar_migracao_fotos_contextos_aprovado(
        raizes,
        CLIENT_ID,
        STORE_IDS,
        group_id=GROUP_ID,
        delete_legacy=True,
    )

    esperada = _config_esperada()
    bytes_esperados = (
        json.dumps(esperada, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    for raiz in raizes:
        assert _config_path(raiz).read_bytes() == bytes_esperados
        assert json.loads(_config_path(raiz).read_text(encoding="utf-8")) == esperada


def test_conflito_no_destino_preserva_origens_e_faz_zero_mutacoes(tmp_path: Path) -> None:
    raizes = [
        _criar_contexto(tmp_path / "checkout" / "info"),
        _criar_contexto(tmp_path / "dados-operacionais" / "info"),
    ]
    pasta_conflitante = _pasta_loja(raizes[1], STORE_IDS[1])
    pasta_conflitante.mkdir(parents=True)
    (pasta_conflitante / "005.jpg").write_bytes(b"conteudo-divergente")
    antes = [_snapshot_arvore(raiz) for raiz in raizes]

    with pytest.raises(migracao.CadastroFotosMigracaoErro) as erro:
        _executar_migracao_fotos_contextos_aprovado(
            raizes,
            CLIENT_ID,
            STORE_IDS,
            group_id=GROUP_ID,
            delete_legacy=True,
        )

    assert erro.value.code == "destination_conflict"
    assert [_snapshot_arvore(raiz) for raiz in raizes] == antes
    for raiz in raizes:
        _assert_fotos_legadas(raiz)


def test_falha_de_copia_preserva_todas_as_fotos_legadas(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raizes = [
        _criar_contexto(tmp_path / "checkout" / "info"),
        _criar_contexto(tmp_path / "dados-operacionais" / "info"),
    ]
    copiar_real = migracao._copiar_foto_atomica
    chamadas = 0

    def copiar_com_falha(origem: Path, destino: Path, esperado: dict[str, object]) -> bool:
        nonlocal chamadas
        chamadas += 1
        if chamadas == 4:
            raise OSError("falha de copia injetada")
        return copiar_real(origem, destino, esperado)

    monkeypatch.setattr(migracao, "_copiar_foto_atomica", copiar_com_falha)

    with pytest.raises(OSError, match="falha de copia injetada"):
        _executar_migracao_fotos_contextos_aprovado(
            raizes,
            CLIENT_ID,
            STORE_IDS,
            group_id=GROUP_ID,
            delete_legacy=True,
        )

    for raiz in raizes:
        _assert_fotos_legadas(raiz)
        assert not _config_path(raiz).exists()


def test_mudanca_da_origem_apos_copias_preserva_fotos_legadas(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raizes = [
        _criar_contexto(tmp_path / "checkout" / "info"),
        _criar_contexto(tmp_path / "dados-operacionais" / "info"),
    ]
    copiar_real = migracao._copiar_foto_atomica
    chamadas = 0
    total_copias = len(raizes) * len(STORE_IDS) * len(FOTOS)

    def copiar_e_mudar_origem(
        origem: Path,
        destino: Path,
        esperado: dict[str, object],
    ) -> bool:
        nonlocal chamadas
        copiou = copiar_real(origem, destino, esperado)
        chamadas += 1
        if chamadas == total_copias:
            (_pasta_fotos(raizes[0]) / "005.jpg").write_bytes(b"origem-alterada")
        return copiou

    monkeypatch.setattr(migracao, "_copiar_foto_atomica", copiar_e_mudar_origem)

    with pytest.raises(migracao.CadastroFotosMigracaoErro) as erro:
        _executar_migracao_fotos_contextos_aprovado(
            raizes,
            CLIENT_ID,
            STORE_IDS,
            group_id=GROUP_ID,
            delete_legacy=True,
        )

    assert erro.value.code == "source_changed"
    assert (_pasta_fotos(raizes[0]) / "005.jpg").read_bytes() == b"origem-alterada"
    assert (_pasta_fotos(raizes[0]) / "005.png").read_bytes() == FOTOS["005.png"]
    _assert_fotos_legadas(raizes[1])
    for raiz in raizes:
        assert not _config_path(raiz).exists()


def test_store_desconhecida_falha_antes_de_qualquer_mutacao(tmp_path: Path) -> None:
    raizes = [
        _criar_contexto(tmp_path / "checkout" / "info"),
        _criar_contexto(tmp_path / "dados-operacionais" / "info"),
    ]
    antes = [_snapshot_arvore(raiz) for raiz in raizes]

    with pytest.raises(migracao.CadastroFotosMigracaoErro) as erro:
        _executar_migracao_fotos_contextos_aprovado(
            raizes,
            CLIENT_ID,
            (*STORE_IDS, "store-inexistente"),
            group_id=GROUP_ID,
            delete_legacy=True,
        )

    assert erro.value.code == "store_not_found"
    assert [_snapshot_arvore(raiz) for raiz in raizes] == antes


@pytest.mark.parametrize(
    ("local_link", "codigo_esperado"),
    [
        ("cadastro_fotos", "unsafe_photo_directory"),
        ("cadastro_fotos/lojas", "unsafe_store_photos_root"),
    ],
)
@pytest.mark.parametrize("tipo_link", ["symlink", "junction"])
def test_rejeita_raiz_de_fotos_com_link_sem_mutar_o_alvo(
    tmp_path: Path,
    local_link: str,
    codigo_esperado: str,
    tipo_link: str,
) -> None:
    raiz = tmp_path / "info"
    tenant = raiz / CLIENT_ID
    tenant.mkdir(parents=True)
    (tenant / "lojas_config.json").write_text(
        json.dumps([{"store_id": item} for item in STORE_IDS]),
        encoding="utf-8",
    )
    link = tenant.joinpath(*local_link.split("/"))
    if local_link.endswith("/lojas"):
        pasta_fotos = tenant / "cadastro_fotos"
        pasta_fotos.mkdir()
        for nome, conteudo in FOTOS.items():
            (pasta_fotos / nome).write_bytes(conteudo)
    else:
        link.parent.mkdir(parents=True, exist_ok=True)

    alvo = tmp_path / f"alvo-{tipo_link}-{codigo_esperado}"
    alvo.mkdir()
    (alvo / "sentinela.jpg").write_bytes(b"nao-mutar")
    antes_alvo = _snapshot_arvore(alvo)
    _criar_link_diretorio(link, alvo, tipo_link)

    with pytest.raises(migracao.CadastroFotosMigracaoErro) as erro:
        migracao.planejar_migracao_fotos_contextos(
            [raiz],
            CLIENT_ID,
            STORE_IDS,
            group_id=GROUP_ID,
        )

    assert erro.value.code == codigo_esperado
    assert _snapshot_arvore(alvo) == antes_alvo
    assert not _config_path(raiz).exists()


@pytest.mark.parametrize("tipo_link", ["symlink", "junction"])
def test_rejeita_info_root_sob_ancestral_reparse_sem_mutar_o_alvo(
    tmp_path: Path,
    tipo_link: str,
) -> None:
    canonical_parent = tmp_path / f"canonical-parent-{tipo_link}"
    canonical_info = _criar_contexto(canonical_parent / "info")
    antes = _snapshot_arvore(canonical_parent)
    alias_parent = tmp_path / f"alias-parent-{tipo_link}"
    _criar_link_diretorio(alias_parent, canonical_parent, tipo_link)
    try:
        with pytest.raises(migracao.CadastroFotosMigracaoErro) as erro:
            migracao.planejar_migracao_fotos_contextos(
                [alias_parent / "info"],
                CLIENT_ID,
                STORE_IDS,
                group_id=GROUP_ID,
            )

        assert erro.value.code == "unsafe_info_root"
        assert _snapshot_arvore(canonical_parent) == antes
    finally:
        _remover_link_diretorio(alias_parent)


def test_rejeita_contexto_duplicado_com_grafia_equivalente_sem_mutar(
    tmp_path: Path,
) -> None:
    raiz = _criar_contexto(tmp_path / "DadosComCase" / "info")
    equivalente = raiz / ".." / raiz.name
    if os.name == "nt":
        equivalente = Path(str(equivalente).swapcase())
    antes = _snapshot_arvore(raiz)

    with pytest.raises(migracao.CadastroFotosMigracaoErro) as erro:
        migracao.planejar_migracao_fotos_contextos(
            [raiz, equivalente],
            CLIENT_ID,
            STORE_IDS,
            group_id=GROUP_ID,
        )

    assert erro.value.code == "duplicate_context"
    assert _snapshot_arvore(raiz) == antes


def test_005_jpg_e_005_png_sao_preservados_como_arquivos_distintos(tmp_path: Path) -> None:
    raiz = _criar_contexto(tmp_path / "info")

    _executar_migracao_fotos_contextos_aprovado(
        [raiz],
        CLIENT_ID,
        STORE_IDS,
        group_id=GROUP_ID,
        delete_legacy=True,
    )

    for store_id in STORE_IDS:
        pasta = _pasta_loja(raiz, store_id)
        assert (pasta / "005.jpg").read_bytes() == FOTOS["005.jpg"]
        assert (pasta / "005.png").read_bytes() == FOTOS["005.png"]
        assert sorted(caminho.name for caminho in pasta.iterdir()) == ["005.jpg", "005.png"]


def test_copia_nao_sobrescreve_foto_por_loja_criada_na_janela_de_publicacao(
    tmp_path: Path,
    monkeypatch,
) -> None:
    raiz = _criar_contexto(tmp_path / "info")
    plano = migracao.planejar_migracao_fotos_contextos(
        [raiz],
        CLIENT_ID,
        STORE_IDS,
        group_id=GROUP_ID,
    )["plans"][0]
    esperado = plano["source_manifest"]["005.jpg"]
    destino = plano["destinations"][0]["path"] / "005.jpg"
    link_real = migracao.os.link

    def publicar_com_writer_concorrente(origem, alvo):
        if Path(alvo) == destino:
            destino.parent.mkdir(parents=True, exist_ok=True)
            destino.write_bytes(b"foto-store-concorrente")
        return link_real(origem, alvo)

    monkeypatch.setattr(migracao.os, "link", publicar_com_writer_concorrente)

    with pytest.raises(migracao.CadastroFotosMigracaoErro) as erro:
        migracao._copiar_foto_atomica(esperado["path"], destino, esperado)

    assert erro.value.code == "destination_changed"
    assert destino.read_bytes() == b"foto-store-concorrente"


def test_falha_ao_remover_temporario_de_copia_nunca_retorna_sucesso(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raiz = _criar_contexto(tmp_path / "info", fotos={"009.jpg": b"foto-009"})
    plano = migracao.planejar_migracao_fotos_contextos(
        [raiz],
        CLIENT_ID,
        STORE_IDS,
        group_id=GROUP_ID,
    )["plans"][0]
    esperado = plano["source_manifest"]["009.jpg"]
    destino = plano["destinations"][0]["path"] / "009.jpg"
    unlink_real = migracao.os.unlink

    def unlink_com_falha(caminho: object, *args: object, **kwargs: object) -> None:
        if Path(caminho).name.startswith(".cadastro-foto-migracao."):
            raise OSError("falha de cleanup injetada")
        unlink_real(caminho, *args, **kwargs)

    monkeypatch.setattr(migracao.os, "unlink", unlink_com_falha)

    with pytest.raises(migracao.CadastroFotosMigracaoErro) as erro:
        migracao._copiar_foto_atomica(esperado["path"], destino, esperado)

    assert erro.value.code == "migration_temp_cleanup_failed"
    assert destino.read_bytes() == b"foto-009"
    assert list(destino.parent.glob(".cadastro-foto-migracao.*.tmp"))
    monkeypatch.undo()
    with pytest.raises(migracao.CadastroFotosMigracaoErro) as erro_retomada:
        migracao.planejar_migracao_fotos_contextos(
            [raiz],
            CLIENT_ID,
            STORE_IDS,
            group_id=GROUP_ID,
        )
    assert erro_retomada.value.code == "migration_temp_cleanup_pending"


def test_temporario_de_config_bloqueia_retomada_em_vez_de_reportar_sucesso(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raiz = _criar_contexto(tmp_path / "info")
    plano = migracao.planejar_migracao_fotos_contextos(
        [raiz],
        CLIENT_ID,
        STORE_IDS,
        group_id=GROUP_ID,
    )
    unlink_real = migracao.os.unlink

    def unlink_com_falha(caminho: object, *args: object, **kwargs: object) -> None:
        if Path(caminho).name.endswith(".create.tmp"):
            raise OSError("falha de cleanup de config injetada")
        unlink_real(caminho, *args, **kwargs)

    monkeypatch.setattr(migracao.os, "unlink", unlink_com_falha)
    with pytest.raises(migracao.CadastroFotosMigracaoErro) as erro:
        migracao.executar_migracao_fotos_contextos(
            [raiz],
            CLIENT_ID,
            STORE_IDS,
            group_id=GROUP_ID,
            delete_legacy=True,
            expected_plan_sha256=plano["report"]["plan_sha256"],
        )

    assert erro.value.code == "migration_temp_cleanup_failed"
    assert _config_path(raiz).is_file()
    assert _journal_path(raiz).is_file()
    assert list((raiz / CLIENT_ID).glob(".cadastro_fotos_config.json.*.create.tmp"))
    _assert_fotos_legadas(raiz)

    monkeypatch.undo()
    with pytest.raises(migracao.CadastroFotosMigracaoErro) as erro_retomada:
        migracao.planejar_migracao_fotos_contextos(
            [raiz],
            CLIENT_ID,
            STORE_IDS,
            group_id=GROUP_ID,
        )
    assert erro_retomada.value.code == "migration_temp_cleanup_pending"
    _assert_fotos_legadas(raiz)


def test_segunda_execucao_apos_exclusao_retorna_already_migrated_sem_mutar(
    tmp_path: Path,
) -> None:
    raizes = [
        _criar_contexto(tmp_path / "checkout" / "info"),
        _criar_contexto(tmp_path / "dados-operacionais" / "info"),
    ]
    _executar_migracao_fotos_contextos_aprovado(
        raizes,
        CLIENT_ID,
        STORE_IDS,
        group_id=GROUP_ID,
        delete_legacy=True,
    )
    antes = [_snapshot_arvore(raiz) for raiz in raizes]
    for raiz in raizes:
        _quarentena_path(raiz).mkdir()

    resultado = _executar_migracao_fotos_contextos_aprovado(
        raizes,
        CLIENT_ID,
        STORE_IDS,
        group_id=GROUP_ID,
        delete_legacy=True,
    )

    assert resultado["status"] == "already_migrated"
    assert resultado["copied"] == 0
    assert resultado["legacy_deleted"] == 0
    assert [_snapshot_arvore(raiz) for raiz in raizes] == antes
    _assert_destinos_completos(raizes)


def test_already_migrated_nao_reporta_sucesso_se_quarentena_nao_for_removida(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raiz = _criar_contexto(tmp_path / "info")
    _executar_migracao_fotos_contextos_aprovado(
        [raiz],
        CLIENT_ID,
        STORE_IDS,
        group_id=GROUP_ID,
        delete_legacy=True,
    )
    quarentena = _quarentena_path(raiz)
    quarentena.mkdir()
    rmdir_real = Path.rmdir

    def rmdir_com_falha(caminho: Path) -> None:
        if caminho == quarentena:
            raise OSError("falha de rmdir injetada")
        rmdir_real(caminho)

    monkeypatch.setattr(Path, "rmdir", rmdir_com_falha)

    with pytest.raises(migracao.CadastroFotosMigracaoErro) as erro:
        _executar_migracao_fotos_contextos_aprovado(
            [raiz],
            CLIENT_ID,
            STORE_IDS,
            group_id=GROUP_ID,
            delete_legacy=True,
        )

    assert erro.value.code == "legacy_cleanup_pending"
    assert quarentena.is_dir()


def test_apply_nao_reporta_sucesso_se_quarentena_for_recriada_apos_rmdir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raiz = _criar_contexto(tmp_path / "info")
    quarentena = _quarentena_path(raiz)
    rmdir_real = Path.rmdir
    recriada = False

    def rmdir_com_recriacao(caminho: Path) -> None:
        nonlocal recriada
        rmdir_real(caminho)
        if caminho == quarentena and not recriada:
            caminho.mkdir()
            recriada = True

    with monkeypatch.context() as contexto:
        contexto.setattr(Path, "rmdir", rmdir_com_recriacao)
        with pytest.raises(migracao.CadastroFotosMigracaoErro) as erro:
            _executar_migracao_fotos_contextos_aprovado(
                [raiz],
                CLIENT_ID,
                STORE_IDS,
                group_id=GROUP_ID,
                delete_legacy=True,
            )

    assert erro.value.code == "legacy_cleanup_pending"
    assert recriada is True
    assert quarentena.is_dir()
    assert list(quarentena.iterdir()) == []
    assert _journal_path(raiz).is_file()
    _assert_destinos_completos([raiz])

    resultado = _executar_migracao_fotos_contextos_aprovado(
        [raiz],
        CLIENT_ID,
        STORE_IDS,
        group_id=GROUP_ID,
        delete_legacy=True,
    )

    assert resultado["status"] == "recovered"
    assert resultado["legacy_deleted"] == 0
    assert not quarentena.exists()
    assert not _journal_path(raiz).exists()


def test_apply_nao_reporta_sucesso_se_quarentena_surgir_ao_remover_journal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raiz = _criar_contexto(tmp_path / "info")
    quarentena = _quarentena_path(raiz)
    journal = _journal_path(raiz)
    unlink_real = Path.unlink
    recriada = False

    def unlink_com_recriacao(caminho: Path, *args: object, **kwargs: object) -> None:
        nonlocal recriada
        unlink_real(caminho, *args, **kwargs)
        if caminho == journal and not recriada:
            quarentena.mkdir()
            recriada = True

    with monkeypatch.context() as contexto:
        contexto.setattr(Path, "unlink", unlink_com_recriacao)
        with pytest.raises(migracao.CadastroFotosMigracaoErro) as erro:
            _executar_migracao_fotos_contextos_aprovado(
                [raiz],
                CLIENT_ID,
                STORE_IDS,
                group_id=GROUP_ID,
                delete_legacy=True,
            )

    assert erro.value.code == "legacy_cleanup_pending"
    assert recriada is True
    assert not journal.exists()
    assert quarentena.is_dir()
    assert list(quarentena.iterdir()) == []
    _assert_destinos_completos([raiz])

    resultado = _executar_migracao_fotos_contextos_aprovado(
        [raiz],
        CLIENT_ID,
        STORE_IDS,
        group_id=GROUP_ID,
        delete_legacy=True,
    )

    assert resultado["status"] == "already_migrated"
    assert resultado["legacy_deleted"] == 0
    assert not quarentena.exists()


class _CrashSimulado(BaseException):
    pass


def _interromper_depois_do_primeiro_move(
    raizes: list[Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replace_real = migracao.os.replace
    movimentos = 0

    def replace_com_crash(origem: object, destino: object) -> None:
        nonlocal movimentos
        origem_path = Path(origem)
        destino_path = Path(destino)
        movendo_legada = any(
            origem_path.parent.resolve() == _pasta_fotos(raiz).resolve()
            and destino_path.parent == _quarentena_path(raiz)
            for raiz in raizes
        )
        if movendo_legada:
            movimentos += 1
            if movimentos == 2:
                raise _CrashSimulado()
        replace_real(origem, destino)

    with monkeypatch.context() as contexto:
        contexto.setattr(migracao.os, "replace", replace_com_crash)
        with pytest.raises(_CrashSimulado):
            _executar_migracao_fotos_contextos_aprovado(
                raizes,
                CLIENT_ID,
                STORE_IDS,
                group_id=GROUP_ID,
                delete_legacy=True,
            )


def test_crash_apos_primeiro_move_deixa_journal_portavel_e_retomavel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raizes = [
        _criar_contexto(tmp_path / "checkout" / "info"),
        _criar_contexto(tmp_path / "dados-operacionais" / "info"),
    ]

    _interromper_depois_do_primeiro_move(raizes, monkeypatch)

    for raiz in raizes:
        journal = _journal_path(raiz)
        assert journal.is_file()
        texto = journal.read_text(encoding="utf-8")
        assert str(tmp_path) not in texto
        payload = json.loads(texto)
        assert payload["schema"] == migracao.MIGRACAO_JOURNAL_SCHEMA
        assert set(payload["source_manifest"]) == set(FOTOS)
    assert _quarentena_path(raizes[0]).is_dir()
    assert sorted(caminho.name for caminho in _quarentena_path(raizes[0]).iterdir()) == [
        "005.jpg"
    ]
    _assert_destinos_completos(raizes)

    resultado = _executar_migracao_fotos_contextos_aprovado(
        raizes,
        CLIENT_ID,
        STORE_IDS,
        group_id=GROUP_ID,
        delete_legacy=True,
    )

    assert resultado["status"] == "recovered"
    assert resultado["legacy_deleted"] == len(FOTOS) * len(raizes)
    for raiz in raizes:
        assert not _journal_path(raiz).exists()
        assert not _quarentena_path(raiz).exists()
        assert not any(
            caminho.is_file() and caminho.suffix.casefold() in migracao.EXTENSOES_FOTO_SUPORTADAS
            for caminho in _pasta_fotos(raiz).iterdir()
        )


def test_estado_origem_quarentena_alterado_apos_dry_run_invalida_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raiz = _criar_contexto(tmp_path / "info")
    _interromper_depois_do_primeiro_move([raiz], monkeypatch)
    plano = migracao.planejar_migracao_fotos_contextos(
        [raiz],
        CLIENT_ID,
        STORE_IDS,
        group_id=GROUP_ID,
    )
    restante = next(
        caminho
        for caminho in _pasta_fotos(raiz).iterdir()
        if caminho.is_file() and caminho.suffix.casefold() in migracao.EXTENSOES_FOTO_SUPORTADAS
    )
    os.replace(restante, _quarentena_path(raiz) / restante.name)
    antes_apply = _snapshot_arvore(raiz)

    with pytest.raises(migracao.CadastroFotosMigracaoErro) as erro:
        migracao.executar_migracao_fotos_contextos(
            [raiz],
            CLIENT_ID,
            STORE_IDS,
            group_id=GROUP_ID,
            delete_legacy=True,
            expected_plan_sha256=plano["report"]["plan_sha256"],
        )

    assert erro.value.code == "migration_plan_changed"
    assert _snapshot_arvore(raiz) == antes_apply


def test_rerun_com_journal_e_destino_divergente_nao_move_nem_exclui(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raizes = [
        _criar_contexto(tmp_path / "checkout" / "info"),
        _criar_contexto(tmp_path / "dados-operacionais" / "info"),
    ]
    _interromper_depois_do_primeiro_move(raizes, monkeypatch)
    destino_divergente = _pasta_loja(raizes[1], STORE_IDS[2]) / "005.png"
    destino_divergente.write_bytes(b"destino-divergente")
    antes_legado = [
        (
            _snapshot_arvore(_pasta_fotos(raiz)),
            _journal_path(raiz).read_bytes(),
        )
        for raiz in raizes
    ]

    with pytest.raises(migracao.CadastroFotosMigracaoErro) as erro:
        _executar_migracao_fotos_contextos_aprovado(
            raizes,
            CLIENT_ID,
            STORE_IDS,
            group_id=GROUP_ID,
            delete_legacy=True,
        )

    assert erro.value.code == "destination_changed"
    assert [
        (
            _snapshot_arvore(_pasta_fotos(raiz)),
            _journal_path(raiz).read_bytes(),
        )
        for raiz in raizes
    ] == antes_legado
    assert destino_divergente.read_bytes() == b"destino-divergente"


def test_falha_de_unlink_mantem_journal_e_rerun_conclui(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raizes = [
        _criar_contexto(tmp_path / "checkout" / "info"),
        _criar_contexto(tmp_path / "dados-operacionais" / "info"),
    ]
    unlink_real = Path.unlink
    falhou = False

    def unlink_com_falha(caminho: Path, *args: object, **kwargs: object) -> None:
        nonlocal falhou
        if caminho.parent.name == migracao.MIGRACAO_QUARENTENA_PASTA and not falhou:
            falhou = True
            raise OSError("falha de unlink injetada")
        unlink_real(caminho, *args, **kwargs)

    with monkeypatch.context() as contexto:
        contexto.setattr(Path, "unlink", unlink_com_falha)
        with pytest.raises(migracao.CadastroFotosMigracaoErro) as erro:
            _executar_migracao_fotos_contextos_aprovado(
                raizes,
                CLIENT_ID,
                STORE_IDS,
                group_id=GROUP_ID,
                delete_legacy=True,
            )

    assert erro.value.code == "legacy_cleanup_pending"
    assert all(_journal_path(raiz).is_file() for raiz in raizes)
    assert sum(
        len(list(_quarentena_path(raiz).glob("*")))
        for raiz in raizes
        if _quarentena_path(raiz).is_dir()
    ) == 1

    resultado = _executar_migracao_fotos_contextos_aprovado(
        raizes,
        CLIENT_ID,
        STORE_IDS,
        group_id=GROUP_ID,
        delete_legacy=True,
    )

    assert resultado["status"] == "recovered"
    assert resultado["legacy_deleted"] == 1
    assert all(not _journal_path(raiz).exists() for raiz in raizes)


def test_lock_de_writer_cross_process_rejeita_migracao_sem_mutar_tenant(
    tmp_path: Path,
) -> None:
    raiz = _criar_contexto(tmp_path / "info")
    antes = _snapshot_arvore(raiz / CLIENT_ID)
    codigo = "\n".join(
        [
            "import sys",
            "from backend.services import cadastro_fotos_coordenacao as coordenacao",
            "with coordenacao.bloquear_transicao_fotos_tenant(sys.argv[1], timeout_seconds=0):",
            "    print('LOCKED', flush=True)",
            "    sys.stdin.readline()",
        ]
    )
    processo = subprocess.Popen(
        [sys.executable, "-c", codigo, str(raiz / CLIENT_ID)],
        cwd=Path(__file__).resolve().parents[1],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert processo.stdout is not None
        pronto = processo.stdout.readline().strip()
        if pronto != "LOCKED":
            assert processo.stderr is not None
            pytest.fail(f"processo de lock falhou: {processo.stderr.read()}")
        with pytest.raises(migracao.CadastroFotosMigracaoErro) as erro:
            _executar_migracao_fotos_contextos_aprovado(
                [raiz],
                CLIENT_ID,
                STORE_IDS,
                group_id=GROUP_ID,
                delete_legacy=True,
            )
    finally:
        if processo.stdin is not None:
            processo.stdin.write("\n")
            processo.stdin.flush()
            processo.stdin.close()
        processo.wait(timeout=10)

    assert erro.value.code == "migration_locked"
    assert _snapshot_arvore(raiz / CLIENT_ID) == antes


def test_lock_cross_process_independe_de_temp_e_tmp(tmp_path: Path) -> None:
    raiz = _criar_contexto(tmp_path / "info")
    tenant = raiz / CLIENT_ID
    temp_a = tmp_path / "temp-a"
    temp_b = tmp_path / "temp-b"
    temp_a.mkdir()
    temp_b.mkdir()
    codigo_a = "\n".join(
        [
            "import sys",
            "from backend.services import cadastro_fotos_coordenacao as c",
            "with c.bloquear_transicao_fotos_tenant(sys.argv[1], timeout_seconds=0):",
            "    print('LOCKED_A', flush=True)",
            "    sys.stdin.readline()",
        ]
    )
    codigo_b = "\n".join(
        [
            "import sys",
            "from backend.services import cadastro_fotos_coordenacao as c",
            "try:",
            "    with c.bloquear_transicao_fotos_tenant(sys.argv[1], timeout_seconds=0):",
            "        print('LOCKED_B_SIMULTANEOUSLY')",
            "except c.CadastroFotosCoordenacaoErro as exc:",
            "    print(exc.code)",
        ]
    )
    env_a = {**os.environ, "TEMP": str(temp_a), "TMP": str(temp_a), "TMPDIR": str(temp_a)}
    env_b = {**os.environ, "TEMP": str(temp_b), "TMP": str(temp_b), "TMPDIR": str(temp_b)}
    processo_a = subprocess.Popen(
        [sys.executable, "-c", codigo_a, str(tenant)],
        cwd=Path(__file__).resolve().parents[1],
        env=env_a,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert processo_a.stdout is not None
        pronto = processo_a.stdout.readline().strip()
        if pronto != "LOCKED_A":
            assert processo_a.stderr is not None
            pytest.fail(f"processo A de lock falhou: {processo_a.stderr.read()}")
        processo_b = subprocess.run(
            [sys.executable, "-c", codigo_b, str(tenant)],
            cwd=Path(__file__).resolve().parents[1],
            env=env_b,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
    finally:
        if processo_a.stdin is not None:
            processo_a.stdin.write("\n")
            processo_a.stdin.flush()
            processo_a.stdin.close()
        processo_a.wait(timeout=10)

    assert processo_b.stdout.strip() == "locked"


def test_criacao_concorrente_de_config_divergente_nao_e_sobrescrita(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raiz = _criar_contexto(tmp_path / "info")
    config_path = _config_path(raiz)
    conteudo_externo = b'{"configuracao": "externa"}\n'
    link_real = migracao.os.link
    injetou = False

    def link_com_corrida(origem: object, destino: object, *args: object, **kwargs: object) -> None:
        nonlocal injetou
        if Path(destino) == config_path and not injetou:
            injetou = True
            config_path.write_bytes(conteudo_externo)
        link_real(origem, destino, *args, **kwargs)

    monkeypatch.setattr(migracao.os, "link", link_com_corrida)

    with pytest.raises(migracao.CadastroFotosMigracaoErro) as erro:
        _executar_migracao_fotos_contextos_aprovado(
            [raiz],
            CLIENT_ID,
            STORE_IDS,
            group_id=GROUP_ID,
            delete_legacy=True,
        )

    assert erro.value.code == "photo_config_conflict"
    assert config_path.read_bytes() == conteudo_externo
    assert _journal_path(raiz).is_file()
    assert not _quarentena_path(raiz).exists()
    _assert_fotos_legadas(raiz)
    _assert_destinos_completos([raiz])


def test_destino_alterado_apos_config_falha_antes_do_primeiro_move(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raiz = _criar_contexto(tmp_path / "info")
    criar_real = migracao._criar_config_atomica_se_ausente
    alterou = False

    def criar_e_alterar(caminho: Path, desejada: dict[str, object]) -> bool:
        nonlocal alterou
        resultado = criar_real(caminho, desejada)
        if not alterou:
            alterou = True
            (_pasta_loja(raiz, STORE_IDS[1]) / "005.png").write_bytes(b"mudou")
        return resultado

    monkeypatch.setattr(migracao, "_criar_config_atomica_se_ausente", criar_e_alterar)

    with pytest.raises(migracao.CadastroFotosMigracaoErro) as erro:
        _executar_migracao_fotos_contextos_aprovado(
            [raiz],
            CLIENT_ID,
            STORE_IDS,
            group_id=GROUP_ID,
            delete_legacy=True,
        )

    assert erro.value.code == "destination_changed"
    assert _journal_path(raiz).is_file()
    assert not _quarentena_path(raiz).exists()
    _assert_fotos_legadas(raiz)


@pytest.mark.parametrize(
    ("modo", "codigo"),
    [
        ("missing", "stores_config_missing"),
        ("directory", "stores_config_unsafe"),
    ],
)
def test_lojas_config_ausente_ou_nao_regular_falha_sem_mutar(
    tmp_path: Path,
    modo: str,
    codigo: str,
) -> None:
    raiz = _criar_contexto(tmp_path / "info")
    lojas_config = raiz / CLIENT_ID / "lojas_config.json"
    lojas_config.unlink()
    if modo == "directory":
        lojas_config.mkdir()
    antes = _snapshot_arvore(raiz)

    with pytest.raises(migracao.CadastroFotosMigracaoErro) as erro:
        _executar_migracao_fotos_contextos_aprovado(
            [raiz],
            CLIENT_ID,
            STORE_IDS,
            group_id=GROUP_ID,
            delete_legacy=True,
        )

    assert erro.value.code == codigo
    assert _snapshot_arvore(raiz) == antes


@pytest.mark.parametrize(
    ("alvo", "codigo"),
    [
        ("config", "photo_config_unsafe"),
        ("journal", "migration_journal_unsafe"),
        ("stores", "stores_config_unsafe"),
    ],
)
def test_broken_symlink_de_controle_falha_fechado(
    tmp_path: Path,
    alvo: str,
    codigo: str,
) -> None:
    raiz = _criar_contexto(tmp_path / "info")
    if alvo == "config":
        caminho = _config_path(raiz)
    elif alvo == "journal":
        caminho = _journal_path(raiz)
    else:
        caminho = raiz / CLIENT_ID / "lojas_config.json"
        caminho.unlink()
    try:
        caminho.symlink_to(tmp_path / f"inexistente-{alvo}.json")
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symlink de arquivo indisponivel: {exc}")
    assert os.path.lexists(caminho) and not caminho.exists()

    with pytest.raises(migracao.CadastroFotosMigracaoErro) as erro:
        migracao.planejar_migracao_fotos_contextos(
            [raiz],
            CLIENT_ID,
            STORE_IDS,
            group_id=GROUP_ID,
        )

    assert erro.value.code == codigo


@pytest.mark.parametrize(
    ("mutacao", "codigo"),
    [
        ("destination", "destination_changed"),
        ("config", "photo_config_conflict"),
        ("stores", "stores_config_changed"),
    ],
)
def test_revalidacao_final_falha_antes_do_unlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutacao: str,
    codigo: str,
) -> None:
    raiz = _criar_contexto(tmp_path / "info")
    revalidar_real = migracao._revalidar_plano_antes_exclusao
    chamadas = 0

    def revalidar_com_mutacao(plano: dict[str, object], **kwargs: object) -> None:
        nonlocal chamadas
        chamadas += 1
        if chamadas == 3:
            if mutacao == "destination":
                (_pasta_loja(raiz, STORE_IDS[0]) / "005.jpg").write_bytes(b"mudou")
            elif mutacao == "config":
                _config_path(raiz).write_text("{}", encoding="utf-8")
            else:
                (raiz / CLIENT_ID / "lojas_config.json").write_text(
                    json.dumps([{"store_id": STORE_IDS[0]}]),
                    encoding="utf-8",
                )
        revalidar_real(plano, **kwargs)

    monkeypatch.setattr(migracao, "_revalidar_plano_antes_exclusao", revalidar_com_mutacao)

    with pytest.raises(migracao.CadastroFotosMigracaoErro) as erro:
        _executar_migracao_fotos_contextos_aprovado(
            [raiz],
            CLIENT_ID,
            STORE_IDS,
            group_id=GROUP_ID,
            delete_legacy=True,
        )

    assert erro.value.code == codigo
    assert _journal_path(raiz).is_file()
    assert {
        caminho.name: caminho.read_bytes()
        for caminho in _quarentena_path(raiz).iterdir()
        if caminho.is_file()
    } == FOTOS


def test_journal_adulterado_falha_fechado_sem_excluir_quarentena(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raiz = _criar_contexto(tmp_path / "info")
    _interromper_depois_do_primeiro_move([raiz], monkeypatch)
    payload = json.loads(_journal_path(raiz).read_text(encoding="utf-8"))
    payload["source_manifest"]["005.jpg"]["sha256"] = "0" * 64
    _journal_path(raiz).write_text(json.dumps(payload), encoding="utf-8")
    antes = _snapshot_arvore(_pasta_fotos(raiz))

    with pytest.raises(migracao.CadastroFotosMigracaoErro) as erro:
        _executar_migracao_fotos_contextos_aprovado(
            [raiz],
            CLIENT_ID,
            STORE_IDS,
            group_id=GROUP_ID,
            delete_legacy=True,
        )

    assert erro.value.code == "quarantine_conflict"
    assert _snapshot_arvore(_pasta_fotos(raiz)) == antes
