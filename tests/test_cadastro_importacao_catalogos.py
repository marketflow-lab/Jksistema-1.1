import asyncio
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.services import cadastro_importacao_catalogos as catalogos
from backend.services import cadastro_catalogo_common


_FINGERPRINT_INITIAL = "a" * 64
_FINGERPRINT_REFRESHED = "b" * 64
_FINGERPRINT_CONCURRENT = "c" * 64
_FINGERPRINT_RECONNECTED = "d" * 64


def _request(username="operador-a"):
    return SimpleNamespace(state=SimpleNamespace(username=username))


def _requester(username="operador-a", client_id="cliente-a"):
    return catalogos._requester_fingerprint(_request(username), client_id)


@pytest.fixture(autouse=True)
def limpar_jobs():
    with catalogos._CATALOG_IMPORT_JOBS_LOCK:
        catalogos.CATALOG_IMPORT_JOBS.clear()
    yield
    with catalogos._CATALOG_IMPORT_JOBS_LOCK:
        for job in catalogos.CATALOG_IMPORT_JOBS.values():
            event = job.get("cancel_event")
            if event is not None:
                event.set()
        catalogos.CATALOG_IMPORT_JOBS.clear()


def _job_ready(**overrides):
    now = catalogos._agora_utc()
    job = {
        "job_id": "job-seguro",
        "client_id": "cliente-a",
        "requester_fingerprint": _requester(),
        "store_id": "store-a",
        "store_name": "Loja A",
        "source": "bling",
        "status": "ready",
        "progress": {"stage": "ready", "current": 1, "total": 1, "percent": 100},
        "can_apply": True,
        "created_at_utc": now,
        "updated_at_utc": now,
        "updated_ts": time.time(),
        "cancel_event": threading.Event(),
        "config_fingerprint": _FINGERPRINT_INITIAL,
        "apply_config_fingerprint": _FINGERPRINT_INITIAL,
        "preview": {
            "coverage_complete": True,
            "sku_coverage_complete": True,
            "can_apply": True,
            "summary": {"novos": 1, "aplicaveis": 1},
            "items": [],
            "apply_rows": [
                {
                    "sku": "001",
                    "sku_normalizado": "001",
                    "row_version": 0,
                    "expected_scope": "absent",
                    "fields": {"id_bling": "10", "produto_bling": "Produto"},
                }
            ],
            "ignored": [],
            "warnings": [],
            "provider_stats": {},
        },
    }
    job.update(overrides)
    return job


def test_fingerprint_de_aplicacao_bling_ignora_so_rotacao_oauth_com_epoca_estavel():
    base = {
        "id": "app-id",
        "secret": "app-secret",
        "oauth_connection_id": "connection-a",
        "access_token": "access-a",
        "refresh_token": "refresh-a",
        "updated_at": "1",
        "_sync_version": 1,
        "_sync_updated_at": "2026-09-02T10:00:00Z",
        "connected": True,
    }
    rotated = {
        **base,
        "access_token": "access-b",
        "refresh_token": "refresh-b",
        "updated_at": "2",
        "_sync_version": 2,
        "_sync_updated_at": "2026-09-02T10:05:00Z",
    }

    exact_base = cadastro_catalogo_common.configuracao_catalogo_fingerprint(
        "bling", "store-a", "Loja A", base
    )
    exact_rotated = cadastro_catalogo_common.configuracao_catalogo_fingerprint(
        "bling", "store-a", "Loja A", rotated
    )
    stable_base = (
        cadastro_catalogo_common.configuracao_catalogo_aplicacao_fingerprint(
            "bling", "store-a", "Loja A", base
        )
    )
    stable_rotated = (
        cadastro_catalogo_common.configuracao_catalogo_aplicacao_fingerprint(
            "bling", "store-a", "Loja A", rotated
        )
    )

    assert exact_base != exact_rotated
    assert stable_base == stable_rotated
    for field, value in (
        ("oauth_connection_id", "connection-b"),
        ("id", "outro-app"),
        ("secret", "outro-secret"),
        ("connected", False),
    ):
        changed = {**rotated, field: value}
        assert (
            cadastro_catalogo_common.configuracao_catalogo_aplicacao_fingerprint(
                "bling", "store-a", "Loja A", changed
            )
            != stable_base
        )


def test_fingerprint_de_aplicacao_legado_sem_epoca_permanece_fail_closed():
    legacy = {
        "id": "app-id",
        "secret": "app-secret",
        "access_token": "access-a",
        "refresh_token": "refresh-a",
    }
    rotated = {**legacy, "access_token": "access-b", "refresh_token": "refresh-b"}

    stable_legacy = (
        cadastro_catalogo_common.configuracao_catalogo_aplicacao_fingerprint(
            "bling", "store-a", "Loja A", legacy
        )
    )
    exact_legacy = cadastro_catalogo_common.configuracao_catalogo_fingerprint(
        "bling", "store-a", "Loja A", legacy
    )
    stable_rotated = (
        cadastro_catalogo_common.configuracao_catalogo_aplicacao_fingerprint(
            "bling", "store-a", "Loja A", rotated
        )
    )

    assert stable_legacy == exact_legacy
    assert stable_rotated != stable_legacy


def test_progress_callback_preserva_percentual_global_e_mensagem_bling_sem_regredir_por_etapa():
    job = _job_ready(status="running", progress={})
    with catalogos._CATALOG_IMPORT_JOBS_LOCK:
        catalogos.CATALOG_IMPORT_JOBS[job["job_id"]] = job
    callback = catalogos._progress_callback(job["job_id"])
    observed = []

    for args in (
        ("listing", 1, 0, 5, "Listando produtos Bling."),
        ("details", 25, 100, 35, "Detalhando produtos Bling."),
        ("balances", 50, 100, 70, "Consultando saldos Bling."),
        ("categories", 1, 2, 90, "Consultando categorias Bling."),
        ("complete", 100, 100, 100, "Catálogo Bling pronto."),
    ):
        callback(*args)
        progress = catalogos.CATALOG_IMPORT_JOBS[job["job_id"]]["progress"]
        observed.append(progress["percent"])
        assert progress["message"] == args[4]

    assert observed == sorted(observed) == [5.0, 35.0, 70.0, 90.0, 100.0]

    callback({"stage": "listing", "current": 10, "total": 20, "message": "Listando ML."})
    progress = catalogos.CATALOG_IMPORT_JOBS[job["job_id"]]["progress"]
    assert progress == {
        "stage": "listing",
        "current": 10,
        "total": 20,
        "percent": None,
        "message": "Listando ML.",
    }


def test_preview_cria_ausentes_preenche_vazios_e_preserva_conflitos():
    result = catalogos._construir_preview(
        "mercadolivre",
        {
            "coverage_complete": True,
            "items": [
                {
                    "sku": "1",
                    "fields": {
                        "titulo_ml": "Titulo novo",
                        "marca": "Marca externa",
                        "descricao": "Descricao externa",
                        "preco": "campo proibido",
                    },
                },
                {"sku": "2", "fields": {"titulo_ml": "Produto novo"}},
            ],
            "skipped": [{
                "reason": "sku_ambiguo",
                "sku": "SKU-AUDIT",
                "mlb": "MLB123",
                "variation_id": "987",
                "entity": "variation",
                "candidates": ["SKU-A", "SKU-B"],
                "unsafe_raw": {"token": "nao-publicar"},
            }],
        },
        [
            {
                "sku": "001",
                "sku_normalizado": "001",
                "row_version": 7,
                "titulo_ml": "",
                "marca": "Marca manual",
                "descricao": "Descricao manual",
                "scope_source": "store_file",
            }
        ],
    )

    assert result["coverage_complete"] is True
    assert result["can_apply"] is True
    assert result["summary"] == {
        "encontrados": 2,
        "novos": 1,
        "preencher": 1,
        "inalterados": 0,
        "conflitos": 1,
        "ignorados": 1,
        "aplicaveis": 2,
        "campos": 2,
    }
    existente = next(row for row in result["apply_rows"] if row["sku_normalizado"] == "001")
    assert existente["row_version"] == 7
    assert existente["expected_scope"] == "store_file"
    assert existente["fields"] == {"titulo_ml": "Titulo novo"}
    novo = next(row for row in result["apply_rows"] if row["sku_normalizado"] == "002")
    assert novo["expected_scope"] == "absent"
    assert novo["fields"] == {"titulo_ml": "Produto novo"}
    assert all("preco" not in row["fields"] for row in result["apply_rows"])
    assert result["ignored"] == [{
        "reason": "sku_ambiguo",
        "sku": "SKU-AUDIT",
        "mlb": "MLB123",
        "variation_id": "987",
        "entity": "variation",
        "candidates": ["SKU-A", "SKU-B"],
    }]
    assert "nao-publicar" not in str(result)


def test_preview_incompleta_nunca_pode_ser_aplicada():
    result = catalogos._construir_preview(
        "bling",
        {
            "coverage_complete": False,
            "items": [{"sku": "ABC", "fields": {"id_bling": "1"}}],
        },
        [],
    )

    assert result["apply_rows"]
    assert result["sku_coverage_complete"] is False
    assert result["can_apply"] is False


def test_preview_sinaliza_quando_detalhes_ignorados_foram_limitados():
    result = catalogos._construir_preview(
        "mercadolivre",
        {
            "coverage_complete": False,
            "items": [],
            "skipped": [
                {"reason": "sku_missing", "mlb": f"MLB{index}"}
                for index in range(105)
            ],
        },
        [],
    )

    assert result["summary"]["ignorados"] == 105
    assert len(result["ignored"]) == 100
    assert result["ignored_total"] == 105
    assert result["ignored_truncated"] is True
    job = _job_ready(preview=result, can_apply=False)
    public = catalogos._public_job(job)
    assert public["ignored_total"] == 105
    assert public["ignored_truncated"] is True
    assert len(public["ignored"]) == 100


def test_sku_duplicado_bling_consolida_campos_seguros_sem_bloquear_sku_minimo():
    result = catalogos._construir_preview(
        "bling",
        {
            "coverage_complete": False,
            "sku_coverage_complete": True,
            "items": [
                {
                    "sku": "DUP",
                    "fields": {
                        "id_bling": "1",
                        "nome_bling": "Produto comum",
                        "marca_bling": "Marca A",
                        "descricao_bling": "Descricao unica",
                    },
                    "warnings": ["aviso_primeiro"],
                },
                {
                    "sku": "dup",
                    "fields": {
                        "id_bling": "2",
                        "nome_bling": "Produto comum",
                        "marca_bling": "Marca B",
                        "ncm_bling": "12345678",
                    },
                    "warnings": ["aviso_segundo"],
                },
            ],
        },
        [],
    )

    assert result["coverage_complete"] is False
    assert result["sku_coverage_complete"] is True
    assert result["can_apply"] is True
    assert len(result["apply_rows"]) == 1
    assert result["apply_rows"][0]["fields"] == {
        "descricao_bling": "Descricao unica",
        "ncm_bling": "12345678",
        "nome_bling": "Produto comum",
    }
    assert "ncm" not in result["apply_rows"][0]["fields"]
    assert "cest" not in result["apply_rows"][0]["fields"]
    assert result["summary"]["conflitos"] == 1
    assert result["items"][0]["status"] == "conflito"
    assert "sku_duplicado_bling:consolidado" in result["items"][0]["conflicts"]
    assert "id_bling:valores_divergentes_na_fonte" in result["items"][0]["conflicts"]
    assert "marca_bling:valores_divergentes_na_fonte" in result["items"][0]["conflicts"]
    assert result["items"][0]["warnings"] == [
        "aviso_primeiro",
        "aviso_segundo",
        "sku_duplicado_bling_consolidado:2",
    ]


def test_sku_only_aplicavel_com_enriquecimento_incompleto_e_exposto_publicamente():
    preview = catalogos._construir_preview(
        "bling",
        {
            "coverage_complete": False,
            "sku_coverage_complete": True,
            "items": [{"sku": "SKU-ONLY", "fields": {}}],
        },
        [],
    )

    assert preview["coverage_complete"] is False
    assert preview["sku_coverage_complete"] is True
    assert preview["can_apply"] is True
    assert preview["apply_rows"] == [
        {
            "sku": "SKU-ONLY",
            "sku_normalizado": "SKU-ONLY",
            "row_version": 0,
            "expected_scope": "absent",
            "fields": {},
        }
    ]

    public = catalogos._public_job(_job_ready(preview=preview, can_apply=True))
    assert public["coverage_complete"] is False
    assert public["sku_coverage_complete"] is True
    assert public["can_apply"] is True


@pytest.mark.parametrize("scope_source", ["store_file", "legacy_shadow", None])
def test_preview_preserva_grafia_do_sku_existente_no_payload(scope_source):
    atuais = []
    if scope_source is not None:
        existente = {
            "sku": "AbC-1",
            "sku_normalizado": "ABC-1",
            "row_version": 4 if scope_source == "store_file" else 0,
            "scope_source": scope_source,
            "produto_bling": "",
        }
        if scope_source == "legacy_shadow":
            existente["__legacy_snapshot_hash"] = "hash-legado"
        atuais.append(existente)

    result = catalogos._construir_preview(
        "bling",
        {
            "coverage_complete": True,
            "sku_coverage_complete": True,
            "items": [
                {"sku": "abc-1", "fields": {"produto_bling": "Produto Bling"}}
            ],
        },
        atuais,
    )

    assert result["apply_rows"][0]["sku"] == (
        "abc-1" if scope_source is None else "AbC-1"
    )
    assert result["apply_rows"][0]["sku_normalizado"] == "ABC-1"
    assert result["apply_rows"][0]["expected_scope"] == (
        "absent" if scope_source is None else scope_source
    )


@pytest.mark.parametrize(
    "provider_flags",
    [
        {},
        {"coverage_complete": "false", "sku_coverage_complete": "false"},
    ],
)
def test_flags_de_cobertura_ausentes_ou_textuais_falham_fechado(provider_flags):
    result = catalogos._construir_preview(
        "bling",
        {
            **provider_flags,
            "items": [{"sku": "SKU-ONLY", "fields": {}}],
        },
        [],
    )

    assert result["apply_rows"]
    assert result["coverage_complete"] is False
    assert result["sku_coverage_complete"] is False
    assert result["can_apply"] is False


def test_sku_duplicado_mercado_livre_preserva_bloqueio_anterior():
    result = catalogos._construir_preview(
        "mercadolivre",
        {
            "coverage_complete": True,
            "sku_coverage_complete": True,
            "items": [
                {"sku": "ML-DUP", "fields": {"mlb_principal": "MLB1"}},
                {"sku": "ml-dup", "fields": {"mlb_principal": "MLB2"}},
            ],
        },
        [],
    )

    assert result["sku_coverage_complete"] is True
    assert result["can_apply"] is False
    assert result["apply_rows"] == []
    assert result["items"][0]["conflicts"] == ["sku_duplicado_na_fonte"]


def test_duplicata_bling_atualiza_titulo_e_ignora_id_no_sku_existente():
    result = catalogos._construir_preview(
        "bling",
        {
            "coverage_complete": False,
            "sku_coverage_complete": True,
            "items": [
                {
                    "sku": "MANUAL",
                    "fields": {"id_bling": "10", "nome_bling": "Nome externo"},
                },
                {
                    "sku": "manual",
                    "fields": {"id_bling": "10", "nome_bling": "Nome externo"},
                },
            ],
        },
        [
            {
                "sku": "MANUAL",
                "sku_normalizado": "MANUAL",
                "row_version": 4,
                "scope_source": "store_file",
                "id_bling": "",
                "nome_bling": "Nome manual",
            }
        ],
    )

    assert result["can_apply"] is True
    assert result["apply_rows"][0]["fields"] == {
        "produto_bling": "Nome externo",
        "nome_bling": "Nome externo",
    }
    assert result["items"][0]["conflicts"] == []
    assert {change["action"] for change in result["items"][0]["changes"]} == {
        "fill",
        "overwrite",
    }


def test_bling_existente_sobrescreve_somente_titulo_ncm_e_cest():
    result = catalogos._construir_preview(
        "bling",
        {
            "coverage_complete": True,
            "sku_coverage_complete": True,
            "items": [
                {
                    "sku": "SKU-PRIORIDADE",
                    "fields": {
                        "id_bling": "999",
                        "produto_bling": "Titulo Bling novo",
                        "nome_bling": "Titulo Bling novo",
                        "ncm_bling": "12345678",
                        "cest_bling": "1234567",
                        "preco_bling": 99.9,
                        "estoque_fisico_bling": 88,
                        "marca_bling": "Marca externa",
                        "descricao_bling": "Descricao externa",
                    },
                }
            ],
        },
        [
            {
                "sku": "SKU-PRIORIDADE",
                "row_version": 7,
                "scope_source": "store_file",
                "nome": "Nome manual",
                "produto": "Produto manual",
                "id_bling": "10",
                "produto_bling": "Titulo Bling antigo",
                "nome_bling": "Titulo Bling antigo",
                "ncm_bling": "87654321",
                "ncm": "87654321",
                "cest_bling": "7654321",
                "cest": "7654321",
                "preco_bling": 10,
                "estoque_fisico_bling": 5,
                "marca_bling": "Marca manual",
                "descricao_bling": "Descricao manual",
            }
        ],
    )

    assert result["apply_rows"][0]["fields"] == {
        "produto_bling": "Titulo Bling novo",
        "nome_bling": "Titulo Bling novo",
        "ncm_bling": "12345678",
        "ncm": "12345678",
        "cest_bling": "1234567",
        "cest": "1234567",
    }
    assert result["items"][0]["conflicts"] == []
    assert {change["action"] for change in result["items"][0]["changes"]} == {
        "overwrite"
    }
    assert result["summary"]["conflitos"] == 0


def test_bling_existente_valores_vazios_nao_apagam_e_demais_campos_sao_ignorados():
    result = catalogos._construir_preview(
        "bling",
        {
            "coverage_complete": True,
            "sku_coverage_complete": True,
            "items": [
                {
                    "sku": "SKU-VAZIO",
                    "fields": {
                        "produto_bling": "  ",
                        "nome_bling": "\t",
                        "ncm_bling": "",
                        "cest_bling": None,
                        "preco_bling": 200,
                        "estoque_fisico_bling": 40,
                    },
                }
            ],
        },
        [
            {
                "sku": "SKU-VAZIO",
                "row_version": 2,
                "scope_source": "store_file",
                "produto_bling": "Titulo mantido",
                "nome_bling": "Titulo mantido",
                "ncm_bling": "11111111",
                "ncm": "11111111",
                "cest_bling": "1111111",
                "cest": "1111111",
                "preco_bling": 10,
                "estoque_fisico_bling": 5,
            }
        ],
    )

    assert result["apply_rows"] == []
    assert result["items"][0]["status"] == "inalterado"
    assert result["items"][0]["changes"] == []
    assert result["items"][0]["conflicts"] == []


def test_duplicata_bling_existente_bloqueia_so_grupos_logicos_divergentes():
    result = catalogos._construir_preview(
        "bling",
        {
            "coverage_complete": True,
            "sku_coverage_complete": True,
            "items": [
                {
                    "sku": "SKU-PARCIAL",
                    "fields": {
                        "produto_bling": "Titulo A",
                        "ncm_bling": "12345678",
                        "cest_bling": "1111111",
                        "id_bling": "1",
                        "preco_bling": 10,
                    },
                },
                {
                    "sku": "sku-parcial",
                    "fields": {
                        "nome_bling": "Titulo B",
                        "ncm_bling": "12345678",
                        "cest_bling": "2222222",
                        "id_bling": "2",
                        "preco_bling": 20,
                    },
                },
            ],
        },
        [
            {
                "sku": "SKU-PARCIAL",
                "row_version": 3,
                "scope_source": "store_file",
                "produto_bling": "Titulo antigo",
                "nome_bling": "Titulo antigo",
                "ncm_bling": "87654321",
                "ncm": "87654321",
                "cest_bling": "3333333",
                "cest": "3333333",
            }
        ],
    )

    assert result["can_apply"] is True
    assert result["apply_rows"][0]["fields"] == {
        "ncm_bling": "12345678",
        "ncm": "12345678",
    }
    assert result["items"][0]["conflicts"] == [
        "cest_bling:valores_divergentes_na_fonte",
        "produto_bling:valores_divergentes_na_fonte",
    ]
    assert result["items"][0]["warnings"] == [
        "sku_duplicado_bling_consolidado:2"
    ]


def test_duplicata_bling_existente_aplica_grupos_concordantes_e_ignora_outros():
    entries = [
        {
            "sku": "SKU-CONSENSO",
            "fields": {
                "produto_bling": "Titulo consensual",
                "ncm_bling": "12345678",
                "cest_bling": "1234567",
                "id_bling": "1",
                "preco_bling": 10,
            },
        },
        {
            "sku": "sku-consenso",
            "fields": {
                "nome_bling": "Titulo consensual",
                "ncm_bling": "12345678",
                "cest_bling": "1234567",
                "id_bling": "2",
                "preco_bling": 20,
            },
        },
    ]
    result = catalogos._construir_preview(
        "bling",
        {
            "coverage_complete": True,
            "sku_coverage_complete": True,
            "items": entries,
        },
        [
            {
                "sku": "SKU-CONSENSO",
                "row_version": 1,
                "scope_source": "store_file",
            }
        ],
    )

    assert result["apply_rows"][0]["fields"] == {
        "produto_bling": "Titulo consensual",
        "nome_bling": "Titulo consensual",
        "ncm_bling": "12345678",
        "ncm": "12345678",
        "cest_bling": "1234567",
        "cest": "1234567",
    }
    assert result["items"][0]["conflicts"] == []


def test_bling_existente_alias_de_titulo_inconsistente_bloqueia_so_titulo():
    result = catalogos._construir_preview(
        "bling",
        {
            "coverage_complete": True,
            "sku_coverage_complete": True,
            "items": [
                {
                    "sku": "SKU-ALIAS",
                    "fields": {
                        "produto_bling": "Titulo A",
                        "nome_bling": "Titulo B",
                        "ncm_bling": "12345678",
                    },
                    "conflicts": {"preco_bling": [10, 20]},
                }
            ],
        },
        [
            {
                "sku": "SKU-ALIAS",
                "row_version": 1,
                "scope_source": "store_file",
                "ncm_bling": "87654321",
                "ncm": "87654321",
            }
        ],
    )

    assert result["apply_rows"][0]["fields"] == {
        "ncm_bling": "12345678",
        "ncm": "12345678",
    }
    assert result["items"][0]["conflicts"] == [
        "produto_bling:valores_divergentes_na_fonte"
    ]


def test_multiplos_anuncios_ml_agregam_ids_e_nao_gravam_campos_divergentes():
    result = catalogos._construir_preview(
        "mercadolivre",
        {
            "coverage_complete": True,
            "items": [
                {
                    "sku": "ML-1",
                    "fields": {
                        "mlb_ids": "MLB1|MLB2",
                        "qtd_anuncios_mlb": 2,
                        "titulo_ml": "Titulo principal",
                        "marca": "Marca escolhida",
                    },
                    "conflicts": {
                        "duplicate_listing_sku": ["MLB1", "MLB2"],
                        "marca": ["Marca A", "Marca B"],
                    },
                }
            ],
        },
        [],
    )

    assert result["can_apply"] is True
    assert result["summary"]["conflitos"] == 1
    assert result["apply_rows"][0]["fields"] == {
        "mlb_ids": "MLB1|MLB2",
        "qtd_anuncios_mlb": 2,
        "titulo_ml": "Titulo principal",
    }
    assert result["items"][0]["status"] == "conflito"
    assert "marca:valores_divergentes_na_fonte" in result["items"][0]["conflicts"]


def test_legacy_shadow_preenche_so_vazios_e_preserva_campos_manuais():
    result = catalogos._construir_preview(
        "mercadolivre",
        {
            "coverage_complete": True,
            "items": [
                {
                    "sku": "1",
                    "sku_normalizado": "001",
                    "fields": {
                        "titulo_ml": "Titulo ML",
                        "marca": "Marca externa",
                        "descricao": "Descricao externa",
                    },
                }
            ],
        },
        [
            {
                "sku": "001",
                "sku_normalizado": "001",
                "scope_source": "legacy_shadow",
                "row_version": "0",
                "titulo_ml": "",
                "marca": "Marca manual",
                "descricao": "Descricao manual",
            }
        ],
    )

    assert result["apply_rows"][0]["fields"] == {"titulo_ml": "Titulo ML"}
    assert result["apply_rows"][0]["expected_scope"] == "legacy_shadow"
    assert result["apply_rows"][0]["legacy_snapshot_hash"]
    assert "marca:valor_existente_preservado" in result["items"][0]["conflicts"]
    assert "descricao:valor_existente_preservado" in result["items"][0]["conflicts"]


def test_sku_bruto_divergente_do_normalizado_falha_fechado():
    result = catalogos._construir_preview(
        "bling",
        {
            "coverage_complete": True,
            "items": [
                {
                    "sku": "SKU-A",
                    "sku_normalizado": "SKU-B",
                    "fields": {"id_bling": "10"},
                }
            ],
        },
        [],
    )

    assert result["coverage_complete"] is False
    assert result["can_apply"] is False
    assert result["apply_rows"] == []
    assert result["ignored"] == [{"reason": "sku_inconsistente", "sku": "SKU-A"}]
    assert "provider_sku_identity_mismatch" in result["warnings"]


def test_tombstone_e_conflito_expresso_e_nao_e_ressuscitado():
    result = catalogos._construir_preview(
        "bling",
        {
            "coverage_complete": True,
            "items": [{"sku": "DEL", "fields": {"id_bling": "10"}}],
        },
        [
            {
                "sku": "DEL",
                "sku_normalizado": "DEL",
                "row_version": "3",
                "deleted_at_utc": "2026-09-01T12:00:00Z",
                "scope_source": "store_file",
            }
        ],
    )

    assert result["apply_rows"] == []
    assert result["can_apply"] is False
    assert result["items"][0]["conflicts"] == ["sku_excluido_no_cadastro"]


def test_catalogo_acima_do_limite_seguro_e_rejeitado(monkeypatch):
    monkeypatch.setattr(catalogos, "CATALOG_IMPORT_MAX_PROVIDER_ITEMS", 1)

    with pytest.raises(HTTPException) as exc_info:
        catalogos._construir_preview(
            "bling",
            {
                "coverage_complete": True,
                "items": [
                    {"sku": "1", "fields": {"id_bling": "1"}},
                    {"sku": "2", "fields": {"id_bling": "2"}},
                ],
            },
            [],
        )

    assert exc_info.value.status_code == 413
    assert exc_info.value.detail["code"] == "catalog_item_limit_exceeded"


def test_worker_constroi_previa_sem_chamar_persistencia(monkeypatch):
    job = _job_ready(status="queued", can_apply=False, preview=None)
    catalogos.CATALOG_IMPORT_JOBS[job["job_id"]] = job
    monkeypatch.setattr(
        catalogos,
        "_provider_for",
        lambda _source: lambda *_args, **_kwargs: {
            "source": "bling",
            "store_id": "store-a",
            "started_config_fingerprint": _FINGERPRINT_INITIAL,
            "config_fingerprint": _FINGERPRINT_INITIAL,
            "coverage_complete": True,
            "items": [{"sku": "001", "fields": {"id_bling": "10"}}],
        },
    )
    monkeypatch.setattr(
        catalogos,
        "_listar_produtos_loja_sync",
        lambda *_args, **kwargs: []
        if kwargs.get("include_deleted") is True
        and kwargs.get("include_legacy_snapshot_hash") is True
        else pytest.fail("preview deve incluir tombstones e snapshot legado"),
    )
    monkeypatch.setattr(
        catalogos, "_configuracao_fingerprint", lambda *_args: _FINGERPRINT_INITIAL
    )
    monkeypatch.setattr(
        catalogos,
        "_configuracao_fingerprints",
        lambda *_args: (_FINGERPRINT_INITIAL, _FINGERPRINT_INITIAL),
    )
    monkeypatch.setattr(
        catalogos,
        "salvar_produtos_loja_em_lote",
        lambda *_args, **_kwargs: pytest.fail("preview nao pode persistir"),
    )

    catalogos._catalog_import_worker(job["job_id"])

    assert job["status"] == "ready"
    assert job["can_apply"] is True
    assert job["preview"]["summary"]["novos"] == 1


def test_apply_revalida_fingerprint_e_envia_somente_snapshot(monkeypatch):
    job = _job_ready()
    catalogos.CATALOG_IMPORT_JOBS[job["job_id"]] = job
    monkeypatch.setattr(
        catalogos,
        "_configuracao_aplicacao_fingerprint",
        lambda *_args: _FINGERPRINT_INITIAL,
    )
    chamadas = []

    def salvar(client_id, store_id, payloads, **kwargs):
        chamadas.append((client_id, store_id, payloads, kwargs))
        kwargs["precommit_validator"]({"store_id": store_id, "nome": "Loja A"})
        return {"incluidos": 1, "atualizados": 0, "total": 1}

    monkeypatch.setattr(catalogos, "salvar_produtos_loja_em_lote", salvar)

    response = asyncio.run(
        catalogos.aplicar_importacao_catalogo(
            "store-a", "job-seguro", _request(), "cliente-a"
        )
    )
    response_retry = asyncio.run(
        catalogos.aplicar_importacao_catalogo(
            "store-a", "job-seguro", _request(), "cliente-a"
        )
    )

    assert len(chamadas) == 1
    assert chamadas[0][2] == [
        {
            "sku": "001",
            "row_version": 0,
            "__expected_scope": "absent",
            "id_bling": "10",
            "produto_bling": "Produto",
        }
    ]
    assert chamadas[0][3]["campos_derivados_permitidos"] == catalogos._PRIVILEGED_DERIVED_FIELDS["bling"]
    assert response["status"] == "applied"
    assert response_retry["apply_result"] == {"incluidos": 1, "atualizados": 0, "total": 1}


@pytest.mark.parametrize("sku_coverage", [False, "false", None])
def test_apply_rejeita_flag_stale_sem_cobertura_completa_de_skus(
    monkeypatch,
    sku_coverage,
):
    job = _job_ready(can_apply=True)
    if sku_coverage is None:
        job["preview"].pop("sku_coverage_complete", None)
        job["preview"].pop("coverage_complete", None)
    else:
        job["preview"]["sku_coverage_complete"] = sku_coverage
    catalogos.CATALOG_IMPORT_JOBS[job["job_id"]] = job
    monkeypatch.setattr(
        catalogos,
        "_configuracao_aplicacao_fingerprint",
        lambda *_args: pytest.fail("preflight incompleto nao pode chegar ao commit"),
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            catalogos.aplicar_importacao_catalogo(
                "store-a", "job-seguro", _request(), "cliente-a"
            )
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "catalog_preview_not_applicable"
    assert job["status"] == "ready"


def test_apply_rejeita_can_apply_textual_sem_chegar_ao_commit(monkeypatch):
    job = _job_ready(can_apply="false")
    catalogos.CATALOG_IMPORT_JOBS[job["job_id"]] = job
    monkeypatch.setattr(
        catalogos,
        "_configuracao_aplicacao_fingerprint",
        lambda *_args: pytest.fail("flag textual nao pode chegar ao commit"),
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            catalogos.aplicar_importacao_catalogo(
                "store-a", "job-seguro", _request(), "cliente-a"
            )
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "catalog_preview_not_applicable"
    assert job["status"] == "ready"


def test_apply_nao_commita_se_cleanup_remove_job_entre_lookup_e_transicao(monkeypatch):
    job = _job_ready()
    catalogos.CATALOG_IMPORT_JOBS[job["job_id"]] = job
    original_lookup = catalogos._job_do_request

    def lookup_e_cleanup(*args):
        encontrado = original_lookup(*args)
        with catalogos._CATALOG_IMPORT_JOBS_LOCK:
            encontrado["updated_ts"] = (
                time.time() - catalogos.CATALOG_IMPORT_JOB_TTL_SECONDS - 1
            )
            catalogos._limpar_jobs_locked()
            assert encontrado["job_id"] not in catalogos.CATALOG_IMPORT_JOBS
        return encontrado

    monkeypatch.setattr(catalogos, "_job_do_request", lookup_e_cleanup)
    monkeypatch.setattr(
        catalogos,
        "salvar_produtos_loja_em_lote",
        lambda *_args, **_kwargs: pytest.fail("job removido nao pode commitar"),
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            catalogos.aplicar_importacao_catalogo(
                "store-a", "job-seguro", _request(), "cliente-a"
            )
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail["code"] == "catalog_job_not_found"
    assert job["status"] == "ready"


@pytest.mark.parametrize(
    ("field", "changed_value"),
    [
        ("client_id", "cliente-b"),
        ("store_id", "store-b"),
        ("requester_fingerprint", "0" * 64),
    ],
)
def test_apply_revalida_ownership_antes_da_transicao(
    monkeypatch,
    field,
    changed_value,
):
    job = _job_ready()
    catalogos.CATALOG_IMPORT_JOBS[job["job_id"]] = job
    original_lookup = catalogos._job_do_request

    def lookup_e_alterar_ownership(*args):
        encontrado = original_lookup(*args)
        encontrado[field] = changed_value
        return encontrado

    monkeypatch.setattr(catalogos, "_job_do_request", lookup_e_alterar_ownership)
    monkeypatch.setattr(
        catalogos,
        "salvar_produtos_loja_em_lote",
        lambda *_args, **_kwargs: pytest.fail("ownership divergente nao pode commitar"),
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            catalogos.aplicar_importacao_catalogo(
                "store-a", "job-seguro", _request(), "cliente-a"
            )
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail["code"] == "catalog_job_not_found"


def test_apply_persistencia_pesada_nao_bloqueia_polling_do_event_loop(monkeypatch):
    job = _job_ready()
    catalogos.CATALOG_IMPORT_JOBS[job["job_id"]] = job
    monkeypatch.setattr(
        catalogos,
        "_configuracao_aplicacao_fingerprint",
        lambda *_args: _FINGERPRINT_INITIAL,
    )
    started = threading.Event()
    release = threading.Event()

    def salvar(*_args, **_kwargs):
        started.set()
        assert release.wait(2)
        return {"incluidos": 1, "atualizados": 0, "total": 1}

    monkeypatch.setattr(catalogos, "salvar_produtos_loja_em_lote", salvar)

    async def scenario():
        timer = threading.Timer(0.5, release.set)
        timer.start()
        try:
            apply_task = asyncio.create_task(
                catalogos.aplicar_importacao_catalogo(
                    "store-a", "job-seguro", _request(), "cliente-a"
                )
            )
            while not started.is_set():
                await asyncio.sleep(0.005)
            observed = await asyncio.wait_for(
                catalogos.obter_importacao_catalogo(
                    "store-a", "job-seguro", _request(), "cliente-a"
                ),
                timeout=0.2,
            )
            assert observed["status"] == "applying"
            release.set()
            applied = await apply_task
            assert applied["status"] == "applied"
        finally:
            release.set()
            timer.cancel()
            timer.join(timeout=1)

    asyncio.run(scenario())


def test_cancelamento_http_aguarda_commit_e_registra_applied(monkeypatch):
    job = _job_ready()
    catalogos.CATALOG_IMPORT_JOBS[job["job_id"]] = job
    monkeypatch.setattr(
        catalogos,
        "_configuracao_aplicacao_fingerprint",
        lambda *_args: _FINGERPRINT_INITIAL,
    )
    started = threading.Event()
    release = threading.Event()

    def salvar(*_args, **_kwargs):
        started.set()
        assert release.wait(2)
        return {"incluidos": 1, "atualizados": 0, "total": 1}

    monkeypatch.setattr(catalogos, "salvar_produtos_loja_em_lote", salvar)

    async def scenario():
        apply_task = asyncio.create_task(
            catalogos.aplicar_importacao_catalogo(
                "store-a", "job-seguro", _request(), "cliente-a"
            )
        )
        while not started.is_set():
            await asyncio.sleep(0.005)
        apply_task.cancel()
        await asyncio.sleep(0.02)
        assert apply_task.done() is False
        assert job["status"] == "applying"
        assert job["can_apply"] is False
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await apply_task
        assert job["status"] == "applied"
        assert job["can_apply"] is False
        assert job["apply_result"] == {"incluidos": 1, "atualizados": 0, "total": 1}

    try:
        asyncio.run(scenario())
    finally:
        release.set()


def test_apply_bloqueia_previa_stale_sem_persistir(monkeypatch):
    job = _job_ready()
    catalogos.CATALOG_IMPORT_JOBS[job["job_id"]] = job
    monkeypatch.setattr(
        catalogos, "_configuracao_aplicacao_fingerprint", lambda *_args: "mudou"
    )
    monkeypatch.setattr(
        catalogos,
        "salvar_produtos_loja_em_lote",
        lambda *_args, **_kwargs: pytest.fail("preview stale nao pode persistir"),
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            catalogos.aplicar_importacao_catalogo(
                "store-a", "job-seguro", _request(), "cliente-a"
            )
        )

    assert exc_info.value.status_code == 409
    assert job["status"] == "stale"
    assert job["can_apply"] is False
    assert job["error"]["cause"] == "integration_changed"


@pytest.mark.parametrize("status_code", [400, 404])
def test_apply_invalida_previa_se_loja_ou_integracao_sumiu(monkeypatch, status_code):
    job = _job_ready()
    catalogos.CATALOG_IMPORT_JOBS[job["job_id"]] = job

    def configuracao_indisponivel(*_args):
        raise HTTPException(status_code=status_code, detail="configuracao ausente")

    monkeypatch.setattr(
        catalogos,
        "_configuracao_aplicacao_fingerprint",
        configuracao_indisponivel,
    )
    monkeypatch.setattr(
        catalogos,
        "salvar_produtos_loja_em_lote",
        lambda *_args, **_kwargs: pytest.fail("configuracao ausente nao pode persistir"),
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            catalogos.aplicar_importacao_catalogo(
                "store-a", "job-seguro", _request(), "cliente-a"
            )
        )

    assert exc_info.value.status_code == status_code
    assert job["status"] == "stale"
    assert job["can_apply"] is False
    assert job["error"]["cause"] == "integration_unavailable"


def test_apply_precommit_revalida_identidade_estavel_da_integracao(monkeypatch):
    job = _job_ready()
    catalogos.CATALOG_IMPORT_JOBS[job["job_id"]] = job
    fingerprints = iter([_FINGERPRINT_INITIAL, _FINGERPRINT_REFRESHED])
    monkeypatch.setattr(
        catalogos,
        "_configuracao_aplicacao_fingerprint",
        lambda *_args: next(fingerprints),
    )

    def salvar(*_args, **kwargs):
        kwargs["precommit_validator"]({"store_id": "store-a", "nome": "Loja A"})
        pytest.fail("a troca de conexao nao pode chegar a escrita")

    monkeypatch.setattr(catalogos, "salvar_produtos_loja_em_lote", salvar)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            catalogos.aplicar_importacao_catalogo(
                "store-a", "job-seguro", _request(), "cliente-a"
            )
        )

    assert exc_info.value.status_code == 409
    assert job["status"] == "stale"
    assert job["error"]["cause"] == "integration_changed"


def test_apply_conflito_de_row_version_identifica_mudanca_no_cadastro(monkeypatch):
    job = _job_ready()
    catalogos.CATALOG_IMPORT_JOBS[job["job_id"]] = job
    monkeypatch.setattr(
        catalogos,
        "_configuracao_aplicacao_fingerprint",
        lambda *_args: _FINGERPRINT_INITIAL,
    )

    def salvar(*_args, **_kwargs):
        raise HTTPException(
            status_code=409,
            detail="Produto foi alterado por outra operacao.",
        )

    monkeypatch.setattr(catalogos, "salvar_produtos_loja_em_lote", salvar)

    with pytest.raises(HTTPException):
        asyncio.run(
            catalogos.aplicar_importacao_catalogo(
                "store-a", "job-seguro", _request(), "cliente-a"
            )
        )

    assert job["status"] == "stale"
    assert job["error"]["cause"] == "cadastro_changed"


def test_job_nao_vaza_entre_tenants():
    job = _job_ready()
    catalogos.CATALOG_IMPORT_JOBS[job["job_id"]] = job

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            catalogos.obter_importacao_catalogo(
                "store-a", "job-seguro", _request(), "cliente-b"
            )
        )

    assert exc_info.value.status_code == 404


def test_job_so_pode_ser_lido_pelo_usuario_que_iniciou():
    job = _job_ready()
    catalogos.CATALOG_IMPORT_JOBS[job["job_id"]] = job

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            catalogos.obter_importacao_catalogo(
                "store-a", "job-seguro", _request("operador-b"), "cliente-a"
            )
        )

    assert exc_info.value.status_code == 404


@pytest.mark.parametrize("status", ["queued", "running", "ready"])
def test_cancelar_interrompe_coleta_ou_descarta_previa(status, monkeypatch):
    job = _job_ready(status=status, can_apply=status == "ready")
    catalogos.CATALOG_IMPORT_JOBS[job["job_id"]] = job

    response = asyncio.run(
        catalogos.cancelar_importacao_catalogo(
            "store-a", "job-seguro", _request(), "cliente-a"
        )
    )

    assert response["status"] == "cancelled"
    assert response["source"] == "bling"
    assert response["store_id"] == "store-a"
    assert response["can_apply"] is False
    assert job["cancel_event"].is_set()
    monkeypatch.setattr(
        catalogos,
        "_provider_for",
        lambda *_args: pytest.fail("job cancelado nao deve consultar o provedor"),
    )
    catalogos._catalog_import_worker(job["job_id"])
    assert job["status"] == "cancelled"


def test_cancelar_respeita_proprietario_do_job():
    job = _job_ready(status="running", can_apply=False)
    catalogos.CATALOG_IMPORT_JOBS[job["job_id"]] = job

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            catalogos.cancelar_importacao_catalogo(
                "store-a", "job-seguro", _request("operador-b"), "cliente-a"
            )
        )

    assert exc_info.value.status_code == 404
    assert job["status"] == "running"
    assert not job["cancel_event"].is_set()


def test_cancelar_nao_interrompe_commit_atomico_em_andamento():
    job = _job_ready(status="applying", can_apply=False)
    catalogos.CATALOG_IMPORT_JOBS[job["job_id"]] = job

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            catalogos.cancelar_importacao_catalogo(
                "store-a", "job-seguro", _request(), "cliente-a"
            )
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "catalog_apply_in_progress"
    assert job["status"] == "applying"
    assert not job["cancel_event"].is_set()


def test_envelope_publico_nao_expoe_tenant_usuario_fingerprint_ou_snapshot():
    job = _job_ready()
    job["apply_config_fingerprint"] = "c" * 64
    job["preview"]["apply_rows"][0]["legacy_snapshot_hash"] = "hash-legado"

    result = catalogos._public_job(job)

    serialized = str(result)
    assert "cliente-a" not in serialized
    assert job["requester_fingerprint"] not in serialized
    assert _FINGERPRINT_INITIAL not in serialized
    assert "c" * 64 not in serialized
    assert "hash-legado" not in serialized
    assert "cancel_event" not in result
    assert "apply_rows" not in serialized


def test_envelope_publico_nao_coage_flags_textuais_para_true():
    job = _job_ready(can_apply="false")
    job["preview"]["coverage_complete"] = "false"
    job["preview"]["sku_coverage_complete"] = "false"

    result = catalogos._public_job(job)

    assert result["coverage_complete"] is False
    assert result["sku_coverage_complete"] is False
    assert result["can_apply"] is False


def test_outro_usuario_nao_recebe_job_ativo_da_mesma_loja(monkeypatch):
    job = _job_ready(status="running", can_apply=False)
    catalogos.CATALOG_IMPORT_JOBS[job["job_id"]] = job
    monkeypatch.setattr(
        catalogos,
        "_configuracao_fonte",
        lambda *_args: ({"store_id": "store-a", "nome": "Loja A"}, {"token": "x"}),
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            catalogos.iniciar_preview_importacao_catalogo(
                "store-a", "bling", _request("operador-b"), "cliente-a"
            )
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "catalog_import_already_running"


def test_limite_de_jobs_ativos_por_tenant(monkeypatch):
    for index in range(catalogos.CATALOG_IMPORT_MAX_ACTIVE_JOBS_PER_TENANT):
        job = _job_ready(
            job_id=f"job-{index}",
            store_id=f"store-{index}",
            source="bling" if index % 2 == 0 else "mercadolivre",
            status="running",
            can_apply=False,
        )
        catalogos.CATALOG_IMPORT_JOBS[job["job_id"]] = job
    monkeypatch.setattr(
        catalogos,
        "_configuracao_fonte",
        lambda *_args: ({"store_id": "store-new", "nome": "Loja Nova"}, {"token": "x"}),
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            catalogos.iniciar_preview_importacao_catalogo(
                "store-new", "bling", _request(), "cliente-a"
            )
        )

    assert exc_info.value.status_code == 429
    assert exc_info.value.detail["code"] == "catalog_import_tenant_capacity_reached"


def test_limite_global_de_jobs_ativos(monkeypatch):
    for index in range(catalogos.CATALOG_IMPORT_MAX_ACTIVE_JOBS):
        client_id = f"cliente-{index}"
        job = _job_ready(
            job_id=f"global-{index}",
            client_id=client_id,
            requester_fingerprint=_requester(client_id=client_id),
            store_id=f"store-{index}",
            status="running",
            can_apply=False,
        )
        catalogos.CATALOG_IMPORT_JOBS[job["job_id"]] = job
    monkeypatch.setattr(
        catalogos,
        "_configuracao_fonte",
        lambda *_args: ({"store_id": "store-new", "nome": "Loja Nova"}, {"token": "x"}),
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            catalogos.iniciar_preview_importacao_catalogo(
                "store-new", "bling", _request(), "cliente-novo"
            )
        )

    assert exc_info.value.status_code == 429
    assert exc_info.value.detail["code"] == "catalog_import_capacity_reached"


def test_apenas_uma_coleta_bling_pode_consumir_o_bucket_global(monkeypatch):
    job = _job_ready(
        job_id="bling-em-andamento",
        client_id="outro-cliente",
        requester_fingerprint=_requester(client_id="outro-cliente"),
        store_id="outra-loja",
        source="bling",
        status="running",
        can_apply=False,
    )
    catalogos.CATALOG_IMPORT_JOBS[job["job_id"]] = job
    monkeypatch.setattr(
        catalogos,
        "_configuracao_fonte",
        lambda *_args: ({"store_id": "store-new", "nome": "Loja Nova"}, {"token": "x"}),
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            catalogos.iniciar_preview_importacao_catalogo(
                "store-new", "bling", _request(), "cliente-a"
            )
        )

    assert exc_info.value.status_code == 429
    assert exc_info.value.detail["code"] == "catalog_import_bling_capacity_reached"


def test_job_ativo_sem_progresso_expira_e_libera_capacidade():
    job = _job_ready(
        status="running",
        can_apply=False,
        updated_ts=time.time() - catalogos.CATALOG_IMPORT_ACTIVE_TTL_SECONDS - 1,
    )
    catalogos.CATALOG_IMPORT_JOBS[job["job_id"]] = job

    with catalogos._CATALOG_IMPORT_JOBS_LOCK:
        catalogos._limpar_jobs_locked()

    assert job["status"] == "error"
    assert job["can_apply"] is False
    assert job["cancel_event"].is_set()
    assert job["error"]["code"] == "catalog_job_timeout"


def test_ttl_ativo_supera_deadline_ml_com_margem_e_nao_cancela_antes_dele():
    from backend.services import cadastro_catalogo_mercadolivre as mercado_livre

    margem_segura = 60 * 60
    assert (
        catalogos.CATALOG_IMPORT_ACTIVE_TTL_SECONDS
        >= mercado_livre.ML_CATALOG_TIMEOUT_SECONDS + margem_segura
    )

    job = _job_ready(
        status="running",
        can_apply=False,
        updated_ts=time.time() - mercado_livre.ML_CATALOG_TIMEOUT_SECONDS,
    )
    catalogos.CATALOG_IMPORT_JOBS[job["job_id"]] = job

    with catalogos._CATALOG_IMPORT_JOBS_LOCK:
        catalogos._limpar_jobs_locked()

    assert job["status"] == "running"
    assert not job["cancel_event"].is_set()


def test_commit_atomico_antigo_nao_e_reclassificado_nem_cancelado():
    job = _job_ready(
        status="applying",
        can_apply=False,
        updated_ts=time.time() - catalogos.CATALOG_IMPORT_ACTIVE_TTL_SECONDS - 1,
    )
    catalogos.CATALOG_IMPORT_JOBS[job["job_id"]] = job

    with catalogos._CATALOG_IMPORT_JOBS_LOCK:
        catalogos._limpar_jobs_locked()

    assert job["status"] == "applying"
    assert not job["cancel_event"].is_set()
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            catalogos.cancelar_importacao_catalogo(
                "store-a", "job-seguro", _request(), "cliente-a"
            )
        )
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "catalog_apply_in_progress"


def test_dependency_proxy_resolve_runtime_configurado_depois_do_import(monkeypatch):
    seen = []

    async def runtime_dependency(request, authorization):
        seen.append((request, authorization))
        return "cliente-runtime"

    monkeypatch.setattr(catalogos, "get_tenant_id", runtime_dependency)
    request = _request()

    result = asyncio.run(catalogos._get_tenant_id_dependency(request, "Bearer token"))

    assert result == "cliente-runtime"
    assert seen == [(request, "Bearer token")]


def test_router_e_registry_publicam_as_quatro_rotas_com_dependency_proxy():
    from backend.routers.cadastro import create_cadastro_router
    from backend.services import cadastro_api

    expected = {
        "iniciar_preview_importacao_catalogo",
        "obter_importacao_catalogo",
        "cancelar_importacao_catalogo",
        "aplicar_importacao_catalogo",
    }
    routes = {route.name: route for route in create_cadastro_router().routes}

    assert expected <= set(routes)
    assert expected <= set(cadastro_api.CADASTRO_ENDPOINTS)
    for name in expected:
        dependency_calls = {dependency.call for dependency in routes[name].dependant.dependencies}
        assert catalogos._get_tenant_id_dependency in dependency_calls


@pytest.mark.parametrize(
    "provider_result",
    [
        {
            "store_id": "store-a",
            "config_fingerprint": _FINGERPRINT_INITIAL,
            "coverage_complete": True,
            "items": [],
        },
        {
            "source": "bling",
            "config_fingerprint": _FINGERPRINT_INITIAL,
            "coverage_complete": True,
            "items": [],
        },
    ],
)
def test_worker_exige_source_e_store_no_envelope(monkeypatch, provider_result):
    job = _job_ready(status="queued", can_apply=False, preview=None)
    catalogos.CATALOG_IMPORT_JOBS[job["job_id"]] = job
    monkeypatch.setattr(catalogos, "_provider_for", lambda _source: lambda *_a, **_k: provider_result)
    monkeypatch.setattr(
        catalogos, "_configuracao_fingerprint", lambda *_args: _FINGERPRINT_INITIAL
    )

    catalogos._catalog_import_worker(job["job_id"])

    assert job["status"] == "error"
    assert job["can_apply"] is False


def test_worker_rejeita_provider_que_iniciou_apos_troca_de_conta(monkeypatch):
    job = _job_ready(status="queued", can_apply=False, preview=None)
    catalogos.CATALOG_IMPORT_JOBS[job["job_id"]] = job
    monkeypatch.setattr(
        catalogos,
        "_provider_for",
        lambda _source: lambda *_a, **_k: {
            "source": "bling",
            "store_id": "store-a",
            "started_config_fingerprint": _FINGERPRINT_REFRESHED,
            "config_fingerprint": _FINGERPRINT_REFRESHED,
            "coverage_complete": True,
            "items": [],
        },
    )
    monkeypatch.setattr(
        catalogos, "_configuracao_fingerprint", lambda *_args: _FINGERPRINT_INITIAL
    )
    monkeypatch.setattr(
        catalogos,
        "_listar_produtos_loja_sync",
        lambda *_a, **_k: pytest.fail("conta trocada nao pode gerar preview"),
    )

    catalogos._catalog_import_worker(job["job_id"])

    assert job["status"] == "error"
    assert job["can_apply"] is False
    assert job["error"]["code"] == "store_config_changed"


def test_worker_aceita_refresh_concorrente_antes_do_provider_com_identidade_estavel(
    monkeypatch,
):
    stable_fingerprint = _FINGERPRINT_CONCURRENT
    job = _job_ready(
        status="queued",
        can_apply=False,
        preview=None,
        apply_config_fingerprint=stable_fingerprint,
    )
    catalogos.CATALOG_IMPORT_JOBS[job["job_id"]] = job
    provider_kwargs = {}

    def provider(_client_id, _store_id, **kwargs):
        provider_kwargs.update(kwargs)
        return {
            "source": "bling",
            "store_id": "store-a",
            "started_config_fingerprint": _FINGERPRINT_REFRESHED,
            "started_apply_config_fingerprint": stable_fingerprint,
            "config_fingerprint": _FINGERPRINT_REFRESHED,
            "apply_config_fingerprint": stable_fingerprint,
            "coverage_complete": True,
            "sku_coverage_complete": True,
            "items": [],
        }

    monkeypatch.setattr(catalogos, "_provider_for", lambda _source: provider)
    monkeypatch.setattr(
        catalogos,
        "_configuracao_fingerprint",
        lambda *_args: _FINGERPRINT_REFRESHED,
    )
    monkeypatch.setattr(
        catalogos,
        "_configuracao_aplicacao_fingerprint",
        lambda *_args: stable_fingerprint,
    )
    monkeypatch.setattr(
        catalogos,
        "_configuracao_fingerprints",
        lambda *_args: (_FINGERPRINT_REFRESHED, stable_fingerprint),
    )
    monkeypatch.setattr(catalogos, "_listar_produtos_loja_sync", lambda *_a, **_k: [])

    catalogos._catalog_import_worker(job["job_id"])

    assert job["status"] == "ready"
    assert provider_kwargs["expected_config_fingerprint"] == _FINGERPRINT_INITIAL
    assert provider_kwargs["expected_apply_config_fingerprint"] == stable_fingerprint


def test_worker_descarta_coleta_se_configuracao_mudou(monkeypatch):
    job = _job_ready(status="queued", can_apply=False, preview=None)
    catalogos.CATALOG_IMPORT_JOBS[job["job_id"]] = job
    monkeypatch.setattr(
        catalogos,
        "_provider_for",
        lambda _source: lambda *_a, **_k: pytest.fail(
            "troca de conta anterior ao provider deve ser bloqueada"
        ),
    )
    monkeypatch.setattr(
        catalogos, "_configuracao_fingerprint", lambda *_args: _FINGERPRINT_REFRESHED
    )
    monkeypatch.setattr(
        catalogos,
        "_configuracao_aplicacao_fingerprint",
        lambda *_args: _FINGERPRINT_REFRESHED,
    )
    monkeypatch.setattr(
        catalogos,
        "_listar_produtos_loja_sync",
        lambda *_a, **_k: pytest.fail("nao deve comparar Cadastro com conta divergente"),
    )

    catalogos._catalog_import_worker(job["job_id"])

    assert job["status"] == "error"
    assert job["error"]["code"] == "store_config_changed"


def test_worker_aceita_refresh_concorrente_apos_provider_e_guarda_snapshot_atual(
    monkeypatch,
):
    job = _job_ready(status="queued", can_apply=False, preview=None)
    catalogos.CATALOG_IMPORT_JOBS[job["job_id"]] = job
    monkeypatch.setattr(
        catalogos,
        "_provider_for",
        lambda _source: lambda *_a, **_k: {
            "source": "bling",
            "store_id": "store-a",
            "started_config_fingerprint": _FINGERPRINT_INITIAL,
            "started_apply_config_fingerprint": _FINGERPRINT_INITIAL,
            "config_fingerprint": _FINGERPRINT_REFRESHED,
            "apply_config_fingerprint": _FINGERPRINT_INITIAL,
            "coverage_complete": True,
            "sku_coverage_complete": True,
            "items": [],
        },
    )
    monkeypatch.setattr(
        catalogos,
        "_configuracao_fingerprint",
        lambda *_args: _FINGERPRINT_INITIAL,
    )
    monkeypatch.setattr(
        catalogos,
        "_configuracao_fingerprints",
        lambda *_args: (_FINGERPRINT_CONCURRENT, _FINGERPRINT_INITIAL),
    )
    monkeypatch.setattr(catalogos, "_listar_produtos_loja_sync", lambda *_a, **_k: [])

    catalogos._catalog_import_worker(job["job_id"])

    assert job["status"] == "ready"
    assert job["config_fingerprint"] == _FINGERPRINT_CONCURRENT
    assert job["apply_config_fingerprint"] == _FINGERPRINT_INITIAL
    assert job["can_apply"] is False


def test_worker_rejeita_reconexao_apos_retorno_do_provider(monkeypatch):
    job = _job_ready(status="queued", can_apply=False, preview=None)
    catalogos.CATALOG_IMPORT_JOBS[job["job_id"]] = job
    monkeypatch.setattr(
        catalogos,
        "_provider_for",
        lambda _source: lambda *_a, **_k: {
            "source": "bling",
            "store_id": "store-a",
            "started_config_fingerprint": _FINGERPRINT_INITIAL,
            "started_apply_config_fingerprint": _FINGERPRINT_INITIAL,
            "config_fingerprint": _FINGERPRINT_REFRESHED,
            "apply_config_fingerprint": _FINGERPRINT_INITIAL,
            "coverage_complete": True,
            "sku_coverage_complete": True,
            "items": [],
        },
    )
    monkeypatch.setattr(
        catalogos,
        "_configuracao_fingerprint",
        lambda *_args: _FINGERPRINT_INITIAL,
    )
    monkeypatch.setattr(
        catalogos,
        "_configuracao_fingerprints",
        lambda *_args: (_FINGERPRINT_CONCURRENT, _FINGERPRINT_RECONNECTED),
    )
    monkeypatch.setattr(
        catalogos,
        "_listar_produtos_loja_sync",
        lambda *_a, **_k: pytest.fail("reconexao nao pode produzir previa"),
    )

    catalogos._catalog_import_worker(job["job_id"])

    assert job["status"] == "error"
    assert job["error"]["code"] == "store_config_changed"


def test_worker_mercadolivre_nao_aceita_equivalencia_estavel_sem_snapshot_exato(
    monkeypatch,
):
    job = _job_ready(
        status="queued",
        can_apply=False,
        preview=None,
        source="mercadolivre",
    )
    catalogos.CATALOG_IMPORT_JOBS[job["job_id"]] = job
    monkeypatch.setattr(
        catalogos,
        "_provider_for",
        lambda _source: lambda *_a, **_k: {
            "source": "mercadolivre",
            "store_id": "store-a",
            "started_config_fingerprint": _FINGERPRINT_INITIAL,
            "config_fingerprint": _FINGERPRINT_REFRESHED,
            "apply_config_fingerprint": _FINGERPRINT_INITIAL,
            "coverage_complete": True,
            "sku_coverage_complete": True,
            "items": [],
        },
    )
    monkeypatch.setattr(
        catalogos,
        "_configuracao_fingerprint",
        lambda *_args: _FINGERPRINT_INITIAL,
    )
    monkeypatch.setattr(
        catalogos,
        "_configuracao_fingerprints",
        lambda *_args: (_FINGERPRINT_CONCURRENT, _FINGERPRINT_INITIAL),
    )

    catalogos._catalog_import_worker(job["job_id"])

    assert job["status"] == "error"
    assert job["error"]["code"] == "store_config_changed"


def test_erro_500_no_commit_e_terminal_e_nao_permite_retry(monkeypatch):
    job = _job_ready()
    catalogos.CATALOG_IMPORT_JOBS[job["job_id"]] = job
    monkeypatch.setattr(
        catalogos,
        "_configuracao_aplicacao_fingerprint",
        lambda *_args: _FINGERPRINT_INITIAL,
    )

    def falhar(*_args, **_kwargs):
        raise HTTPException(
            status_code=500,
            detail="Falha critica ao reverter a alteracao do cadastro por loja.",
        )

    monkeypatch.setattr(catalogos, "salvar_produtos_loja_em_lote", falhar)

    with pytest.raises(HTTPException):
        asyncio.run(
            catalogos.aplicar_importacao_catalogo(
                "store-a", "job-seguro", _request(), "cliente-a"
            )
        )

    assert job["status"] == "error"
    assert job["can_apply"] is False
