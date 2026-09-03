import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import HTTPException

from backend.services import integracoes
from backend.services.cadastro_fotos_coordenacao import (
    CadastroFotosCoordenacaoErro,
    bloquear_transicao_fotos_tenant,
)


def _configure(tmp_path):
    info_dir = tmp_path / "info"

    def tenant_path(client_id):
        path = info_dir / str(client_id or "default")
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    integracoes.configure_integracoes_context(
        pasta_info=str(info_dir),
        get_tenant_path=tenant_path,
        normalizar_integracao_conectada=lambda servico, dados: dados,
    )
    return info_dir / "000002" / "lojas_config.json"


def _lojas(qtd):
    return [
        {"nome": f"Loja {idx}", "integracoes": {"mercadolivre": {"connected": True, "access_token": f"tok-{idx}"}}}
        for idx in range(1, qtd + 1)
    ]


def _bling_lojas():
    return [
        {
            "nome": "Loja A",
            "integracoes": {
                "bling": {
                    "id": "client-a",
                    "secret": "secret-a",
                    "access_token": "access-a-old",
                    "refresh_token": "refresh-a-old",
                    "connected": True,
                }
            },
        },
        {
            "nome": "Loja B",
            "integracoes": {
                "bling": {
                    "id": "client-b",
                    "secret": "secret-b",
                    "access_token": "access-b-old",
                    "refresh_token": "refresh-b-old",
                    "connected": True,
                }
            },
        },
    ]


def test_salvar_lojas_espera_transicao_de_fotos_do_tenant(tmp_path):
    arquivo = _configure(tmp_path)
    integracoes.salvar_lojas(
        "000002",
        [{"store_id": "store-a", "nome": "Loja A", "integracoes": {}}],
    )
    tenant = arquivo.parent
    iniciou = threading.Event()
    terminou = threading.Event()
    falhas = []

    def salvar_em_paralelo():
        iniciou.set()
        try:
            integracoes.salvar_lojas(
                "000002",
                [
                    {
                        "store_id": "store-a",
                        "nome": "Loja A renomeada",
                        "integracoes": {},
                    }
                ],
            )
        except BaseException as exc:  # pragma: no cover - evidencia diagnostica
            falhas.append(exc)
        finally:
            terminou.set()

    with bloquear_transicao_fotos_tenant(tenant, timeout_seconds=0):
        worker = threading.Thread(target=salvar_em_paralelo, daemon=True)
        worker.start()
        assert iniciou.wait(1)
        assert not terminou.wait(0.2)

    worker.join(timeout=3)
    assert terminou.is_set()
    assert falhas == []
    persistidas = json.loads(arquivo.read_text(encoding="utf-8"))
    assert persistidas[0]["nome"] == "Loja A renomeada"


@pytest.mark.parametrize(
    ("error_code", "expected_code"),
    [
        ("locked", "cadastro_photo_transition_busy"),
        ("unsafe_tenant", "cadastro_photo_transition_unavailable"),
        ("lock_unsafe", "cadastro_photo_transition_unavailable"),
        ("lock_unavailable", "cadastro_photo_transition_unavailable"),
    ],
)
def test_falha_de_coordenacao_distingue_contenda_de_indisponibilidade(
    monkeypatch,
    tmp_path,
    error_code,
    expected_code,
):
    arquivo = _configure(tmp_path)
    integracoes.salvar_lojas(
        "000002",
        [{"store_id": "store-a", "nome": "Loja A", "integracoes": {}}],
    )
    antes = arquivo.read_bytes()

    class _FalhaCoordenacao:
        def __enter__(self):
            raise CadastroFotosCoordenacaoErro(
                error_code,
                "Falha de coordenacao simulada.",
            )

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(
        integracoes,
        "bloquear_transicao_fotos_tenant",
        lambda *_args, **_kwargs: _FalhaCoordenacao(),
    )

    with pytest.raises(HTTPException) as exc_info:
        integracoes.salvar_lojas(
            "000002",
            [
                {
                    "store_id": "store-a",
                    "nome": "Nao deve gravar",
                    "integracoes": {},
                }
            ],
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == expected_code
    assert arquivo.read_bytes() == antes


def test_lojas_homonimas_sem_id_recebem_identidades_distintas(tmp_path):
    arquivo = _configure(tmp_path)
    lojas = [
        {"nome": "Loja Igual", "integracoes": {}},
        {"nome": "Loja Igual", "integracoes": {}},
    ]

    integracoes.salvar_lojas("000002", lojas)

    persistidas = json.loads(arquivo.read_text(encoding="utf-8"))
    ids = [item["store_id"] for item in persistidas]
    assert all(ids)
    assert len(set(ids)) == 2


def test_migracao_legada_ignora_nome_ou_alias_ambiguo(monkeypatch, tmp_path):
    _configure(tmp_path)
    lojas = [
        {
            "store_id": "loja-a",
            "nome": "Loja Igual",
            "nomes_anteriores": ["Alias Compartilhado"],
            "integracoes": {},
        },
        {
            "store_id": "loja-b",
            "nome": " loja igual ",
            "nomes_anteriores": [" alias compartilhado "],
            "integracoes": {},
        },
    ]
    monkeypatch.setattr(
        integracoes,
        "_integracoes_coletar_legadas",
        lambda: {
            "LOJA IGUAL": {"bling": {"access_token": "nao-migrar"}},
            "ALIAS COMPARTILHADO": {
                "mercadolivre": {"access_token": "tambem-nao-migrar"}
            },
        },
    )

    resultado, mudou = integracoes._integracoes_mesclar_legadas(
        "default",
        lojas,
        bootstrap_virgem=True,
    )

    assert mudou is False
    assert all(not loja["integracoes"] for loja in resultado)


def test_migracao_legada_por_alias_exato_exige_identidade_unica(monkeypatch, tmp_path):
    _configure(tmp_path)
    lojas = [
        {
            "store_id": "loja-a",
            "nome": "Nome Atual",
            "nomes_anteriores": ["Nome Legado"],
            "integracoes": {},
        },
        {"store_id": "loja-b", "nome": "Outra", "integracoes": {}},
    ]
    monkeypatch.setattr(
        integracoes,
        "_integracoes_coletar_legadas",
        lambda: {" nome legado ": {"bling": {"access_token": "migrado"}}},
    )

    resultado, mudou = integracoes._integracoes_mesclar_legadas(
        "default",
        lojas,
        bootstrap_virgem=True,
    )

    assert mudou is True
    assert resultado[0]["integracoes"]["bling"]["access_token"] == "migrado"
    assert resultado[1]["integracoes"] == {}


def test_carregar_loja_legada_persiste_store_id_estavel_com_sync_metadata(tmp_path):
    arquivo = _configure(tmp_path)
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    arquivo.write_text(
        json.dumps(
            [
                {
                    "nome": "Loja Legada",
                    "integracoes": {},
                    "_sync_version": 1,
                    "_sync_updated_at": "2026-08-28T12:00:00Z",
                }
            ]
        ),
        encoding="utf-8",
    )

    primeira = integracoes.carregar_lojas("000002")
    store_id = primeira[0]["store_id"]
    persistida = json.loads(arquivo.read_text(encoding="utf-8"))
    segunda = integracoes.carregar_lojas("000002")

    assert store_id
    assert persistida[0]["store_id"] == store_id
    assert segunda[0]["store_id"] == store_id


def test_store_id_duplicado_e_rejeitado_sem_alterar_arquivo(tmp_path):
    arquivo = _configure(tmp_path)
    integracoes.salvar_lojas("000002", [{"nome": "Original", "integracoes": {}}])
    antes = arquivo.read_bytes()

    with pytest.raises(HTTPException) as exc_info:
        integracoes.salvar_lojas(
            "000002",
            [
                {"store_id": "duplicado", "nome": "A", "integracoes": {}},
                {"store_id": "duplicado", "nome": "B", "integracoes": {}},
            ],
        )

    assert exc_info.value.status_code == 409
    assert arquivo.read_bytes() == antes


def test_renome_preserva_store_id_fornecido(tmp_path):
    arquivo = _configure(tmp_path)
    lojas = [{"nome": "Antes", "integracoes": {}}]
    integracoes.salvar_lojas("000002", lojas)
    store_id = lojas[0]["store_id"]

    integracoes.salvar_lojas(
        "000002",
        [{"store_id": store_id, "nome": "Depois", "integracoes": {}}],
    )

    persistida = json.loads(arquivo.read_text(encoding="utf-8"))[0]
    assert persistida["store_id"] == store_id
    assert persistida["nome"] == "Depois"


def test_store_ids_case_only_sao_identidades_distintas(tmp_path):
    arquivo = _configure(tmp_path)
    integracoes.salvar_lojas(
        "000002",
        [
            {"store_id": "StoreA", "nome": "Loja", "integracoes": {}},
            {"store_id": "storea", "nome": "Loja", "integracoes": {}},
        ],
    )

    persistidas = json.loads(arquivo.read_text(encoding="utf-8"))
    assert [item["store_id"] for item in persistidas] == ["StoreA", "storea"]


def test_refresh_bling_com_store_id_nao_atualiza_homonima(monkeypatch, tmp_path):
    _configure(tmp_path)
    lojas = [
        {
            "store_id": "store-a",
            "nome": "Loja Igual",
            "integracoes": {"bling": {
                "id": "client-a",
                "secret": "secret-a",
                "access_token": "access-a",
                "refresh_token": "refresh-a",
            }},
        },
        {
            "store_id": "store-b",
            "nome": "Loja Igual",
            "integracoes": {"bling": {
                "id": "client-b",
                "secret": "secret-b",
                "access_token": "access-b",
                "refresh_token": "refresh-b",
            }},
        },
    ]
    integracoes.salvar_lojas("000002", lojas)
    monkeypatch.setattr(
        integracoes,
        "exchange_bling_refresh_token",
        lambda client_id, _secret, refresh: {
            "access_token": f"novo-{client_id}",
            "refresh_token": f"novo-{refresh}",
        },
    )

    renovado = integracoes.renovar_token_bling_loja(
        "000002",
        "Loja Igual",
        lojas[1]["integracoes"]["bling"],
        store_id="store-b",
    )

    persistidas = integracoes.carregar_lojas("000002")
    por_id = {item["store_id"]: item for item in persistidas}
    assert renovado["access_token"] == "novo-client-b"
    assert por_id["store-a"]["integracoes"]["bling"]["access_token"] == "access-a"
    assert por_id["store-b"]["integracoes"]["bling"]["access_token"] == "novo-client-b"


def test_salvar_lojas_bloqueia_snapshot_regressivo(tmp_path):
    arquivo = _configure(tmp_path)
    boas = _lojas(4)
    integracoes.salvar_lojas("000002", boas)

    with pytest.raises(HTTPException) as exc:
        integracoes.salvar_lojas("000002", [boas[0]])

    assert exc.value.status_code == 409
    assert [loja["nome"] for loja in json.loads(arquivo.read_text(encoding="utf-8"))] == [
        "Loja 1",
        "Loja 2",
        "Loja 3",
        "Loja 4",
    ]


def test_carregar_lojas_restaurar_backup_imediato_quando_arquivo_fica_vazio(tmp_path):
    arquivo = _configure(tmp_path)
    boas = _lojas(4)
    integracoes.salvar_lojas("000002", boas)
    integracoes.salvar_lojas("000002", boas)
    arquivo.write_text("", encoding="utf-8")

    restauradas = integracoes.carregar_lojas("000002")

    assert [loja["nome"] for loja in restauradas] == ["Loja 1", "Loja 2", "Loja 3", "Loja 4"]
    assert [loja["nome"] for loja in json.loads(arquivo.read_text(encoding="utf-8"))] == [
        "Loja 1",
        "Loja 2",
        "Loja 3",
        "Loja 4",
    ]


@pytest.mark.parametrize("owner", [None, "cliente-a"])
def test_carregar_lojas_ignora_legado_global_de_outro_tenant(
    tmp_path,
    owner,
):
    info_dir = tmp_path / "info"
    info_dir.mkdir()

    def tenant_path(client_id):
        path = info_dir / str(client_id)
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    integracoes.configure_integracoes_context(
        pasta_info=str(info_dir),
        get_tenant_path=tenant_path,
        normalizar_integracao_conectada=lambda _servico, dados: dados,
    )
    nome = "Loja de origem desconhecida"
    loja = {
        "nome": nome,
        "integracoes": {"mercadolivre": {"access_token": "nao-vazar"}},
    }
    if owner:
        loja["store_id"] = integracoes._integracoes_store_id(
            owner,
            {"nome": nome},
        )
    original = json.dumps([loja]).encode("utf-8")
    global_path = info_dir / "lojas_config.json"
    global_path.write_bytes(original)

    assert integracoes.carregar_lojas("cliente-b") == []
    assert global_path.read_bytes() == original
    assert not (info_dir / "cliente-b" / "lojas_config.json").exists()


def test_carregar_lojas_nao_infere_owner_de_store_id_opaco(tmp_path):
    info_dir = tmp_path / "info"
    info_dir.mkdir()

    def tenant_path(client_id):
        path = info_dir / str(client_id)
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    integracoes.configure_integracoes_context(
        pasta_info=str(info_dir),
        get_tenant_path=tenant_path,
        normalizar_integracao_conectada=lambda _servico, dados: dados,
    )
    nome = "Loja atribuida"
    store_id = integracoes._integracoes_store_id(
        "cliente-b",
        {"nome": nome},
    )
    (info_dir / "lojas_config.json").write_text(
        json.dumps([{
            "nome": nome,
            "store_id": store_id,
            "integracoes": {"bling": {"access_token": "seguro"}},
        }]),
        encoding="utf-8",
    )

    lojas = integracoes.carregar_lojas("cliente-b")

    assert lojas == []
    assert (info_dir / "lojas_config.json").exists()
    assert not (info_dir / "cliente-b" / "lojas_config.json").exists()


def test_carregar_lojas_recupera_backup_rico_antes_de_normalizar(tmp_path):
    arquivo = _configure(tmp_path)
    tenant = arquivo.parent
    tenant.mkdir(parents=True, exist_ok=True)
    atual = [{
        "nome": "Remota",
        "store_id": "store-remota",
        "integracoes": {},
    }]
    backup = [{
        "nome": "Local recuperada",
        "store_id": "store-local",
        "integracoes": {
            "mercadolivre": {
                "app_id": "app-local",
                "client_secret": "secret-local",
                "access_token": "access-local",
                "refresh_token": "refresh-local",
                "user_id": "seller-local",
                "connected": True,
            },
        },
    }]
    arquivo.write_text(json.dumps(atual), encoding="utf-8")
    (tenant / "lojas_config.json.bak").write_text(
        json.dumps(backup),
        encoding="utf-8",
    )

    lojas = integracoes.carregar_lojas("000002")
    por_id = {loja["store_id"]: loja for loja in lojas}

    assert set(por_id) == {"store-remota", "store-local"}
    assert (
        por_id["store-local"]["integracoes"]["mercadolivre"]["user_id"]
        == "seller-local"
    )
    backup_final = json.loads(
        (tenant / "lojas_config.json.bak").read_text(encoding="utf-8")
    )
    assert {loja["store_id"] for loja in backup_final} == set(por_id)


def test_credenciais_globais_sem_owner_nao_entram_em_tenant_real(tmp_path):
    arquivo = _configure(tmp_path)
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    arquivo.write_text(
        json.dumps([{
            "nome": "Loja igual",
            "store_id": integracoes._integracoes_store_id(
                "000002",
                {"nome": "Loja igual"},
            ),
            "integracoes": {},
        }]),
        encoding="utf-8",
    )
    info_dir = arquivo.parent.parent
    (info_dir / "integracoes.json").write_text(
        json.dumps({
            "Loja igual": {
                "mercadolivre": {
                    "app_id": "app-outro",
                    "client_secret": "secret-outro",
                    "access_token": "access-outro",
                    "refresh_token": "refresh-outro",
                    "user_id": "seller-outro",
                },
            },
        }),
        encoding="utf-8",
    )
    (info_dir / "bling_conf.json").write_text(
        json.dumps([{
            "loja": "Loja igual",
            "token": "bling-outro",
            "apikey": "key-outro",
        }]),
        encoding="utf-8",
    )

    lojas = integracoes.carregar_lojas("000002")

    assert lojas[0]["integracoes"] == {}


def test_credenciais_globais_legadas_continuam_permitidas_no_default(tmp_path):
    info_dir = tmp_path / "info"
    info_dir.mkdir()

    def tenant_path(client_id):
        path = info_dir / str(client_id or "default")
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    integracoes.configure_integracoes_context(
        pasta_info=str(info_dir),
        get_tenant_path=tenant_path,
        normalizar_integracao_conectada=lambda _servico, dados: dados,
    )
    (info_dir / "integracoes.json").write_text(
        json.dumps({
            "Loja default": {
                "bling": {"access_token": "token-default"},
            },
        }),
        encoding="utf-8",
    )

    lojas = integracoes.carregar_lojas("default")

    assert lojas[0]["nome"] == "Loja default"
    assert lojas[0]["integracoes"]["bling"]["access_token"] == "token-default"


@pytest.mark.parametrize("misto", [False, True], ids=["com-id", "misto"])
def test_default_assume_exclusivamente_o_legado_global_sem_inferir_owner_por_id(
    tmp_path,
    misto,
):
    info_dir = tmp_path / "info"
    info_dir.mkdir()

    def tenant_path(client_id):
        path = info_dir / str(client_id or "default")
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    integracoes.configure_integracoes_context(
        pasta_info=str(info_dir),
        get_tenant_path=tenant_path,
        normalizar_integracao_conectada=lambda _servico, dados: dados,
    )
    nome = "Loja de outro tenant"
    payload = [{
        "nome": nome,
        "store_id": integracoes._integracoes_store_id(
            "tenant-a",
            {"nome": nome},
        ),
        "integracoes": {"bling": {"access_token": "tenant-a"}},
    }]
    if misto:
        payload.append({"nome": "Loja sem ID", "integracoes": {}})
    original = json.dumps(payload).encode("utf-8")
    global_path = info_dir / "lojas_config.json"
    global_path.write_bytes(original)

    lojas = integracoes.carregar_lojas("default")

    assert {loja["nome"] for loja in lojas} == {
        "Loja de outro tenant",
        *({"Loja sem ID"} if misto else set()),
    }
    assert lojas[0]["store_id"] == payload[0]["store_id"]
    assert all(str(loja.get("store_id") or "").strip() for loja in lojas)
    assert not global_path.exists()
    assert (info_dir / "default" / "lojas_config.json").exists()


def test_default_bloqueia_nomes_ambiguos_entre_fontes_legadas(tmp_path):
    info_dir = tmp_path / "info"
    info_dir.mkdir()

    def tenant_path(client_id):
        path = info_dir / str(client_id or "default")
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    integracoes.configure_integracoes_context(
        pasta_info=str(info_dir),
        get_tenant_path=tenant_path,
        normalizar_integracao_conectada=lambda _servico, dados: dados,
    )
    integracoes_original = json.dumps({
        "Loja A": {
            "mercadolivre": {
                "app_id": "app-a",
                "client_secret": "secret-a",
            },
        },
    }).encode("utf-8")
    bling_original = json.dumps([{
        "loja": "Loja Á",
        "token": "bling-outra",
    }]).encode("utf-8")
    (info_dir / "integracoes.json").write_bytes(integracoes_original)
    (info_dir / "bling_conf.json").write_bytes(bling_original)

    with pytest.raises(HTTPException) as bloqueado:
        integracoes.carregar_lojas("default")

    assert bloqueado.value.status_code == 409
    assert (info_dir / "integracoes.json").read_bytes() == integracoes_original
    assert (info_dir / "bling_conf.json").read_bytes() == bling_original
    assert not (info_dir / "default" / "lojas_config.json").exists()


def test_default_importa_todas_lojas_legadas_nao_excluidas(tmp_path):
    info_dir = tmp_path / "info"
    tenant = info_dir / "default"
    tenant.mkdir(parents=True)

    integracoes.configure_integracoes_context(
        pasta_info=str(info_dir),
        get_tenant_path=lambda _client_id: str(tenant),
        normalizar_integracao_conectada=lambda _servico, dados: dados,
    )
    (tenant / "lojas_config.json").write_text(
        json.dumps([{
            "nome": "Ja existente",
            "store_id": integracoes._integracoes_store_id(
                "default",
                {"nome": "Ja existente"},
            ),
            "integracoes": {},
        }]),
        encoding="utf-8",
    )
    store_id_excluida = integracoes._integracoes_store_id(
        "default",
        {"nome": "Legada excluida"},
    )
    (tenant / "lojas_sync_tombstones.json").write_text(
        json.dumps([{
            "key": f"store:{store_id_excluida}:",
            "type": "store",
            "store_id": store_id_excluida,
            "version": 2,
            "deleted_at": "2026-09-02T12:00:00Z",
        }]),
        encoding="utf-8",
    )
    (info_dir / "integracoes.json").write_text(
        json.dumps({
            "Legada 1": {"bling": {"access_token": "token-1"}},
            "Legada 2": {"bling": {"access_token": "token-2"}},
            "Legada excluida": {"bling": {"access_token": "nao-voltar"}},
        }),
        encoding="utf-8",
    )

    lojas = integracoes.carregar_lojas("default")

    assert {loja["nome"] for loja in lojas} == {"Ja existente"}
    assert lojas[0]["integracoes"] == {}
    assert (info_dir / "integracoes.json").exists()


@pytest.mark.parametrize("com_tombstone", [False, True])
def test_default_nao_mistura_legado_em_integracao_local_presente(
    tmp_path,
    com_tombstone,
):
    info_dir = tmp_path / "info"
    tenant = info_dir / "default"
    tenant.mkdir(parents=True)

    integracoes.configure_integracoes_context(
        pasta_info=str(info_dir),
        get_tenant_path=lambda _client_id: str(tenant),
        normalizar_integracao_conectada=lambda _servico, dados: dados,
    )
    nome = "Loja default"
    store_id = integracoes._integracoes_store_id("default", {"nome": nome})
    atual = {
        "connected": False,
        "app_id": "app-novo",
        "client_secret": "secret-novo",
    }
    (tenant / "lojas_config.json").write_text(
        json.dumps([{
            "nome": nome,
            "store_id": store_id,
            "integracoes": {"mercadolivre": atual},
        }]),
        encoding="utf-8",
    )
    if com_tombstone:
        (tenant / "lojas_sync_tombstones.json").write_text(
            json.dumps([{
                "key": f"integration:{store_id}:mercadolivre",
                "type": "integration",
                "store_id": store_id,
                "service": "mercadolivre",
                "version": 2,
                "deleted_at": "2026-09-02T12:00:00Z",
            }]),
            encoding="utf-8",
        )
    (info_dir / "integracoes.json").write_text(
        json.dumps({
            nome: {
                "mercadolivre": {
                    "app_id": "app-antigo",
                    "client_secret": "secret-antigo",
                    "access_token": "access-antigo",
                    "refresh_token": "refresh-antigo",
                    "user_id": "seller-antigo",
                },
            },
        }),
        encoding="utf-8",
    )

    lojas = integracoes.carregar_lojas("default")
    ml = lojas[0]["integracoes"]["mercadolivre"]

    assert ml["connected"] is False
    assert ml["app_id"] == "app-novo"
    assert ml["client_secret"] == "secret-novo"
    assert "access_token" not in ml
    assert "refresh_token" not in ml
    assert "user_id" not in ml


def test_default_bloqueia_bling_conflitante_entre_fontes_globais(tmp_path):
    info_dir = tmp_path / "info"
    info_dir.mkdir()

    def tenant_path(client_id):
        path = info_dir / str(client_id or "default")
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    integracoes.configure_integracoes_context(
        pasta_info=str(info_dir),
        get_tenant_path=tenant_path,
        normalizar_integracao_conectada=lambda _servico, dados: dados,
    )
    integracoes_original = json.dumps({
        "Loja default": {
            "bling": {
                "client_id": "app-primeiro",
                "client_secret": "secret-primeiro",
            },
        },
    }).encode("utf-8")
    bling_original = json.dumps([{
        "loja": "Loja default",
        "token": "token-outra-conta",
        "apikey": "key-outra-conta",
    }]).encode("utf-8")
    (info_dir / "integracoes.json").write_bytes(integracoes_original)
    (info_dir / "bling_conf.json").write_bytes(bling_original)

    with pytest.raises(HTTPException) as bloqueado:
        integracoes.carregar_lojas("default")

    assert bloqueado.value.status_code == 409
    assert (info_dir / "integracoes.json").read_bytes() == integracoes_original
    assert (info_dir / "bling_conf.json").read_bytes() == bling_original
    assert not (info_dir / "default" / "lojas_config.json").exists()


def test_carregar_lojas_nao_materializa_legado_global_em_tenant_nao_default(
    tmp_path,
    monkeypatch,
):
    info_dir = tmp_path / "info"
    info_dir.mkdir()

    def tenant_path(client_id):
        path = info_dir / str(client_id)
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    integracoes.configure_integracoes_context(
        pasta_info=str(info_dir),
        get_tenant_path=tenant_path,
        normalizar_integracao_conectada=lambda _servico, dados: dados,
    )
    nome = "Loja preservada"
    original = json.dumps([{
        "nome": nome,
        "store_id": integracoes._integracoes_store_id(
            "000002",
            {"nome": nome},
        ),
        "integracoes": {"bling": {"access_token": "seguro"}},
    }]).encode("utf-8")
    global_path = info_dir / "lojas_config.json"
    global_path.write_bytes(original)
    escritor_real = integracoes._integracoes_escrever_lojas_config_atomico

    def falhar_materializacao(caminho, payload):
        if str(caminho) == str(info_dir / "000002" / "lojas_config.json"):
            raise PermissionError("replace")
        return escritor_real(caminho, payload)

    monkeypatch.setattr(
        integracoes,
        "_integracoes_escrever_lojas_config_atomico",
        falhar_materializacao,
    )

    assert integracoes.carregar_lojas("000002") == []
    assert global_path.read_bytes() == original
    assert not (info_dir / "000002" / "lojas_config.json").exists()


def test_carregar_lojas_recupera_backup_quando_primario_sumiu(tmp_path):
    arquivo = _configure(tmp_path)
    nome = "Loja no backup"
    backup = [{
        "nome": nome,
        "store_id": integracoes._integracoes_store_id("000002", {"nome": nome}),
        "integracoes": {"bling": {"access_token": "backup-seguro"}},
    }]
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    (arquivo.parent / "lojas_config.json.bak").write_text(
        json.dumps(backup),
        encoding="utf-8",
    )

    lojas = integracoes.carregar_lojas("000002")

    assert lojas[0]["integracoes"]["bling"]["access_token"] == "backup-seguro"
    assert json.loads(arquivo.read_text(encoding="utf-8")) == lojas
    assert json.loads(
        (arquivo.parent / "lojas_config.json.bak").read_text(encoding="utf-8")
    ) == lojas


@pytest.mark.parametrize("tipo", ["store", "integration"])
def test_backup_restaurado_de_primario_corrompido_respeita_tombstone(
    tmp_path,
    tipo,
):
    arquivo = _configure(tmp_path)
    nome = "Loja excluida"
    store_id = integracoes._integracoes_store_id("000002", {"nome": nome})
    backup = [{
        "nome": nome,
        "store_id": store_id,
        "integracoes": {
            "mercadolivre": {
                "app_id": "app",
                "client_secret": "secret",
                "access_token": "nao-reviver",
                "refresh_token": "nao-reviver-refresh",
                "connected": True,
            },
        },
    }]
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    arquivo.write_text("{invalido", encoding="utf-8")
    (arquivo.parent / "lojas_config.json.bak").write_text(
        json.dumps(backup),
        encoding="utf-8",
    )
    tombstone = {
        "key": f"{tipo}:{store_id}:{'mercadolivre' if tipo == 'integration' else ''}",
        "type": tipo,
        "store_id": store_id,
        "version": 2,
        "deleted_at": "2026-09-02T12:00:00Z",
    }
    if tipo == "integration":
        tombstone["service"] = "mercadolivre"
    (arquivo.parent / "lojas_sync_tombstones.json").write_text(
        json.dumps([tombstone]),
        encoding="utf-8",
    )

    lojas = integracoes.carregar_lojas("000002")

    if tipo == "store":
        assert lojas == []
    else:
        assert len(lojas) == 1
        assert "mercadolivre" not in lojas[0]["integracoes"]
    assert json.loads(arquivo.read_text(encoding="utf-8")) == lojas
    assert json.loads(
        (arquivo.parent / "lojas_config.json.bak").read_text(encoding="utf-8")
    ) == lojas


def test_carregar_lojas_ignora_root_coexistente_de_tenant_nao_default(tmp_path):
    info_dir = tmp_path / "info"
    tenant = info_dir / "000002"
    tenant.mkdir(parents=True)
    integracoes.configure_integracoes_context(
        pasta_info=str(info_dir),
        get_tenant_path=lambda _client_id: str(tenant),
        normalizar_integracao_conectada=lambda _servico, dados: dados,
    )
    nome = "Loja root"
    root = [{
        "nome": nome,
        "store_id": integracoes._integracoes_store_id("000002", {"nome": nome}),
        "integracoes": {"bling": {"access_token": "root-seguro"}},
    }]
    (tenant / "lojas_config.json").write_text("[]", encoding="utf-8")
    (info_dir / "lojas_config.json").write_text(json.dumps(root), encoding="utf-8")

    lojas = integracoes.carregar_lojas("000002")

    assert lojas == []
    assert (info_dir / "lojas_config.json").exists()
    assert json.loads(
        (tenant / "lojas_config.json").read_text(encoding="utf-8")
    ) == []
    assert not (tenant / "lojas_config.json.bak").exists()


def test_root_de_outro_tenant_nao_ressuscita_loja_tombstonada(tmp_path):
    info_dir = tmp_path / "info"
    tenant = info_dir / "000002"
    tenant.mkdir(parents=True)
    integracoes.configure_integracoes_context(
        pasta_info=str(info_dir),
        get_tenant_path=lambda _client_id: str(tenant),
        normalizar_integracao_conectada=lambda _servico, dados: dados,
    )
    nome = "Loja apagada"
    store_id = integracoes._integracoes_store_id("000002", {"nome": nome})
    (info_dir / "lojas_config.json").write_text(
        json.dumps([{
            "nome": nome,
            "store_id": store_id,
            "integracoes": {"bling": {"access_token": "nao-voltar"}},
        }]),
        encoding="utf-8",
    )
    (tenant / "lojas_sync_tombstones.json").write_text(
        json.dumps([{
            "key": f"store:{store_id}:",
            "type": "store",
            "store_id": store_id,
            "version": 2,
            "deleted_at": "2026-09-02T12:00:00Z",
        }]),
        encoding="utf-8",
    )

    assert integracoes.carregar_lojas("000002") == []
    assert (info_dir / "lojas_config.json").exists()
    assert not (tenant / "lojas_config.json").exists()
    assert not (tenant / "lojas_config.json.bak").exists()


def test_migracao_root_bloqueia_tombstone_sem_evento_antes_de_gravar(tmp_path):
    info_dir = tmp_path / "info"
    tenant = info_dir / "default"
    tenant.mkdir(parents=True)
    integracoes.configure_integracoes_context(
        pasta_info=str(info_dir),
        get_tenant_path=lambda _client_id: str(tenant),
        normalizar_integracao_conectada=lambda _servico, dados: dados,
    )
    nome = "Loja preservada"
    store_id = integracoes._integracoes_store_id("default", {"nome": nome})
    root_path = info_dir / "lojas_config.json"
    root_original = json.dumps([{
        "nome": nome,
        "integracoes": {
            "mercadolivre": {
                "access_token": "nao-apagar",
                "refresh_token": "nao-apagar-refresh",
            },
        },
    }]).encode("utf-8")
    root_path.write_bytes(root_original)
    (tenant / "lojas_sync_tombstones.json").write_text(
        json.dumps([{
            "key": f"store:{store_id}:",
            "type": "store",
            "store_id": store_id,
            "service": "",
            "version": 1,
        }]),
        encoding="utf-8",
    )

    with pytest.raises(HTTPException) as bloqueado:
        integracoes.carregar_lojas("default")

    assert bloqueado.value.status_code == 409
    assert root_path.read_bytes() == root_original
    assert not (tenant / "lojas_config.json").exists()
    assert not (tenant / "_shared_sync_backups").exists()


def test_default_nao_mistura_credencial_por_nome_apenas_parecido(tmp_path):
    info_dir = tmp_path / "info"
    tenant = info_dir / "default"
    tenant.mkdir(parents=True)
    integracoes.configure_integracoes_context(
        pasta_info=str(info_dir),
        get_tenant_path=lambda _client_id: str(tenant),
        normalizar_integracao_conectada=lambda _servico, dados: dados,
    )
    (tenant / "lojas_config.json").write_text(
        json.dumps([{
            "nome": "Loja A",
            "store_id": integracoes._integracoes_store_id(
                "default",
                {"nome": "Loja A"},
            ),
            "integracoes": {"mercadolivre": {"app_id": "local"}},
        }]),
        encoding="utf-8",
    )
    (info_dir / "integracoes.json").write_text(
        json.dumps({"Loja Á": {"bling": {"access_token": "outro"}}}),
        encoding="utf-8",
    )

    lojas = integracoes.carregar_lojas("default")
    assert len(lojas) == 1
    assert lojas[0]["nome"] == "Loja A"
    assert lojas[0]["integracoes"]["mercadolivre"]["app_id"] == "local"
    assert "bling" not in lojas[0]["integracoes"]


def test_carregar_lojas_preserva_homonimas_com_store_ids_distintos(tmp_path):
    arquivo = _configure(tmp_path)
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    arquivo.write_text(
        json.dumps([
            {"nome": "Loja A", "store_id": "store-a", "integracoes": {}},
            {"nome": "Loja-A", "store_id": "store-b", "integracoes": {}},
        ]),
        encoding="utf-8",
    )

    lojas = integracoes.carregar_lojas("000002")
    assert {loja["store_id"] for loja in lojas} == {"store-a", "store-b"}
    assert integracoes.buscar_loja("000002", "Loja A")["store_id"] == "store-a"
    assert integracoes.buscar_loja("000002", "Loja-A")["store_id"] == "store-b"


def test_default_converte_aliases_operacionais_bling_legado(tmp_path):
    info_dir = tmp_path / "info"
    info_dir.mkdir()

    def tenant_path(client_id):
        path = info_dir / str(client_id or "default")
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    integracoes.configure_integracoes_context(
        pasta_info=str(info_dir),
        get_tenant_path=tenant_path,
        normalizar_integracao_conectada=lambda _servico, dados: dados,
    )
    (info_dir / "integracoes.json").write_text(
        json.dumps({
            "Loja aliases": {
                "bling": {
                    "app_id": "app-alias",
                    "secret_key": "secret-alias",
                    "apikey": "api-key-alias",
                },
            },
        }),
        encoding="utf-8",
    )

    bling = integracoes.carregar_lojas("default")[0]["integracoes"]["bling"]
    assert bling["id"] == "app-alias"
    assert bling["secret"] == "secret-alias"
    assert bling["api_key"] == "api-key-alias"


def test_default_bloqueia_aliases_ml_apontando_para_contas_diferentes(tmp_path):
    info_dir = tmp_path / "info"
    info_dir.mkdir()

    def tenant_path(client_id):
        path = info_dir / str(client_id or "default")
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    integracoes.configure_integracoes_context(
        pasta_info=str(info_dir),
        get_tenant_path=tenant_path,
        normalizar_integracao_conectada=lambda _servico, dados: dados,
    )
    original = json.dumps({
        "Loja alias": {
            "ml": {"access_token": "a", "user_id": "seller-a"},
            "Mercado Livre": {"access_token": "b", "user_id": "seller-b"},
        },
    }).encode("utf-8")
    (info_dir / "integracoes.json").write_bytes(original)

    with pytest.raises(HTTPException) as bloqueado:
        integracoes.carregar_lojas("default")
    assert bloqueado.value.status_code == 409
    assert (info_dir / "integracoes.json").read_bytes() == original
    assert not (info_dir / "default" / "lojas_config.json").exists()


@pytest.mark.parametrize(
    ("servico", "config"),
    [
        (
            "bling",
            {
                "client_id": "app-a",
                "id": "app-b",
                "client_secret": "secret-a",
                "secret": "secret-b",
            },
        ),
        (
            "mercadolivre",
            {
                "app_id": "app-a",
                "client_id": "app-b",
                "client_secret": "secret-a",
                "secret": "secret-b",
            },
        ),
    ],
)
def test_default_bloqueia_aliases_conflitantes_no_mesmo_bloco(
    tmp_path,
    servico,
    config,
):
    info_dir = tmp_path / "info"
    info_dir.mkdir()

    def tenant_path(client_id):
        path = info_dir / str(client_id or "default")
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    integracoes.configure_integracoes_context(
        pasta_info=str(info_dir),
        get_tenant_path=tenant_path,
        normalizar_integracao_conectada=lambda _servico, dados: dados,
    )
    original = json.dumps({"Loja conflito": {servico: config}}).encode("utf-8")
    (info_dir / "integracoes.json").write_bytes(original)

    with pytest.raises(HTTPException) as bloqueado:
        integracoes.carregar_lojas("default")
    assert bloqueado.value.status_code == 409
    assert (info_dir / "integracoes.json").read_bytes() == original
    assert not (info_dir / "default" / "lojas_config.json").exists()


def test_default_bloqueia_identidade_de_loja_conflitante_no_bling_conf(tmp_path):
    info_dir = tmp_path / "info"
    info_dir.mkdir()

    def tenant_path(client_id):
        path = info_dir / str(client_id or "default")
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    integracoes.configure_integracoes_context(
        pasta_info=str(info_dir),
        get_tenant_path=tenant_path,
        normalizar_integracao_conectada=lambda _servico, dados: dados,
    )
    original = json.dumps([{
        "loja": "Loja A",
        "nome": "Loja B",
        "token": "token",
    }]).encode("utf-8")
    (info_dir / "bling_conf.json").write_bytes(original)

    with pytest.raises(HTTPException) as bloqueado:
        integracoes.carregar_lojas("default")
    assert bloqueado.value.status_code == 409
    assert (info_dir / "bling_conf.json").read_bytes() == original
    assert not (info_dir / "default" / "lojas_config.json").exists()


def test_restaurar_tombstone_cria_evento_mesmo_sem_exclusao_local(tmp_path):
    arquivo = _configure(tmp_path)
    loja = {"nome": "Loja recriada", "store_id": "store-recriada"}

    integracoes.restaurar_tombstone_integracao(
        "000002",
        loja=loja,
        servico="mercadolivre",
        tipo="integration",
    )

    tombstones = json.loads(
        (arquivo.parent / "lojas_sync_tombstones.json").read_text(encoding="utf-8")
    )
    assert tombstones[0]["key"] == "integration:store-recriada:mercadolivre"
    assert tombstones[0]["restored_at"]


def test_backup_oauth_mais_forte_sobrevive_a_update_nao_relacionado(tmp_path):
    arquivo = _configure(tmp_path)
    tenant = arquivo.parent
    loja_a = {
        "nome": "Loja A",
        "store_id": "store-a",
        "integracoes": {
            "mercadolivre": {
                "app_id": "app",
                "client_secret": "secret",
                "connected": False,
            },
        },
    }
    loja_b = {
        "nome": "Loja B",
        "store_id": "store-b",
        "integracoes": {},
    }
    backup = [
        {
            **loja_a,
            "integracoes": {
                "mercadolivre": {
                    "app_id": "app",
                    "client_secret": "secret",
                    "access_token": "ultimo-access",
                    "refresh_token": "ultimo-refresh",
                    "user_id": "seller-ultimo",
                    "connected": True,
                },
            },
        },
        loja_b,
    ]
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    arquivo.write_text(json.dumps([loja_a, loja_b]), encoding="utf-8")
    backup_bytes = json.dumps(backup).encode("utf-8")
    (tenant / "lojas_config.json.bak").write_bytes(backup_bytes)

    carregadas = integracoes.carregar_lojas("000002")
    assert "access_token" not in carregadas[0]["integracoes"]["mercadolivre"]
    integracoes.atualizar_api_loja(
        "000002",
        "Loja B",
        "mercadoturbo",
        {"token": "turbo-b", "connected": True},
        store_id="store-b",
        require_existing=True,
    )

    backup_final = json.loads(
        (tenant / "lojas_config.json.bak").read_text(encoding="utf-8")
    )
    backup_por_id = {loja["store_id"]: loja for loja in backup_final}
    ml_backup = backup_por_id["store-a"]["integracoes"]["mercadolivre"]
    assert ml_backup["access_token"] == "ultimo-access"
    assert ml_backup["refresh_token"] == "ultimo-refresh"
    assert ml_backup["user_id"] == "seller-ultimo"
    assert (
        backup_por_id["store-b"]["integracoes"]["mercadoturbo"]["token"]
        == "turbo-b"
    )
    main = json.loads(arquivo.read_text(encoding="utf-8"))
    assert "access_token" not in main[0]["integracoes"]["mercadolivre"]
    assert main[1]["integracoes"]["mercadoturbo"]["token"] == "turbo-b"

    arquivo.write_text("{corrompido", encoding="utf-8")
    recuperadas = integracoes.carregar_lojas("000002")
    recuperadas_por_id = {loja["store_id"]: loja for loja in recuperadas}
    assert (
        recuperadas_por_id["store-a"]["integracoes"]["mercadolivre"][
            "access_token"
        ]
        == "ultimo-access"
    )
    assert (
        recuperadas_por_id["store-b"]["integracoes"]["mercadoturbo"]["token"]
        == "turbo-b"
    )


def test_salvar_lojas_rejeita_nomes_sem_identidade_normalizada(tmp_path):
    arquivo = _configure(tmp_path)

    with pytest.raises(HTTPException) as bloqueado:
        integracoes.salvar_lojas(
            "000002",
            [
                {"nome": "店", "integracoes": {}},
                {"nome": "商", "integracoes": {}},
            ],
        )
    assert bloqueado.value.status_code == 409
    assert not arquivo.exists()


def test_default_rejeita_nome_global_sem_identidade_normalizada(tmp_path):
    info_dir = tmp_path / "info"
    info_dir.mkdir()

    def tenant_path(client_id):
        path = info_dir / str(client_id or "default")
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    integracoes.configure_integracoes_context(
        pasta_info=str(info_dir),
        get_tenant_path=tenant_path,
        normalizar_integracao_conectada=lambda _servico, dados: dados,
    )
    original = json.dumps({"店": {"bling": {"access_token": "token"}}}).encode("utf-8")
    (info_dir / "integracoes.json").write_bytes(original)

    with pytest.raises(HTTPException) as bloqueado:
        integracoes.carregar_lojas("default")
    assert bloqueado.value.status_code == 409
    assert (info_dir / "integracoes.json").read_bytes() == original


@pytest.mark.parametrize("acao", ["registrar", "restaurar"])
@pytest.mark.parametrize("conteudo", [b"{invalido", b'{"nao":"lista"}'])
def test_mutacao_de_tombstone_invalido_falha_sem_apagar_historico(
    tmp_path,
    acao,
    conteudo,
):
    arquivo = _configure(tmp_path)
    path = arquivo.parent / "lojas_sync_tombstones.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(conteudo)
    loja = {"nome": "Loja segura", "store_id": "store-segura"}

    funcao = (
        integracoes.registrar_tombstone_integracao
        if acao == "registrar"
        else integracoes.restaurar_tombstone_integracao
    )
    with pytest.raises(HTTPException) as bloqueado:
        funcao(
            "000002",
            loja=loja,
            servico="mercadolivre",
            tipo="integration",
        )

    assert bloqueado.value.status_code == 409
    assert path.read_bytes() == conteudo


def test_primario_valido_isola_backup_corrompido_e_continua_carregando(tmp_path):
    arquivo = _configure(tmp_path)
    lojas = [{
        "nome": "Loja principal",
        "store_id": "store-principal",
        "integracoes": {},
    }]
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    arquivo.write_text(json.dumps(lojas), encoding="utf-8")
    (arquivo.parent / "lojas_config.json.bak").write_bytes(b"{corrompido")

    carregadas = integracoes.carregar_lojas("000002")

    assert [loja["store_id"] for loja in carregadas] == ["store-principal"]
    assert isinstance(
        json.loads(
            (arquivo.parent / "lojas_config.json.bak").read_text(
                encoding="utf-8"
            )
        ),
        list,
    )
    isolados = list(
        (arquivo.parent / "_shared_sync_backups").glob(
            "invalid_backup_*/lojas_config.json.bak"
        )
    )
    assert len(isolados) == 1
    assert isolados[0].read_bytes() == b"{corrompido"


def test_journal_reexecuta_restore_interrompido_sem_apagar_reconexao(
    tmp_path,
    monkeypatch,
):
    arquivo = _configure(tmp_path)
    antiga = [{
        "nome": "Loja journal",
        "store_id": "store-journal",
        "integracoes": {"mercadolivre": {"connected": False}},
    }]
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    arquivo.write_text(json.dumps(antiga), encoding="utf-8")
    integracoes.registrar_tombstone_integracao(
        "000002",
        loja=antiga[0],
        servico="mercadolivre",
        tipo="integration",
    )
    nova = json.loads(json.dumps(antiga))
    nova[0]["integracoes"]["mercadolivre"] = {
        "app_id": "app",
        "client_secret": "secret",
        "access_token": "access-novo",
        "refresh_token": "refresh-novo",
        "user_id": "seller-novo",
        "connected": True,
    }
    tombstones = integracoes._integracoes_atualizar_tombstone_payload(
        "000002",
        integracoes._integracoes_ler_tombstones_estrito("000002"),
        loja=nova[0],
        servico="mercadolivre",
        tipo="integration",
        restaurar=True,
    )
    escrever_real = integracoes._integracoes_escrever_lojas_config_atomico
    falhou = {"valor": False}

    def falhar_uma_vez(caminho, payload):
        if (
            not falhou["valor"]
            and str(caminho).lower().endswith("lojas_config.json")
        ):
            falhou["valor"] = True
            raise OSError("queda simulada antes do principal")
        return escrever_real(caminho, payload)

    monkeypatch.setattr(
        integracoes,
        "_integracoes_escrever_lojas_config_atomico",
        falhar_uma_vez,
    )
    with pytest.raises(HTTPException):
        integracoes._integracoes_commit_lojas_tombstones(
            "000002",
            nova,
            tombstones,
        )
    journal = (
        arquivo.parent
        / "_shared_sync_backups"
        / "pending_lojas_transaction.json"
    )
    assert journal.exists()

    monkeypatch.setattr(
        integracoes,
        "_integracoes_escrever_lojas_config_atomico",
        escrever_real,
    )
    recuperadas = integracoes.carregar_lojas("000002")
    assert (
        recuperadas[0]["integracoes"]["mercadolivre"]["user_id"]
        == "seller-novo"
    )
    assert not journal.exists()


def test_journal_concluido_nao_reverte_update_posterior_se_remove_falhar(
    tmp_path,
    monkeypatch,
):
    arquivo = _configure(tmp_path)
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    arquivo.write_text(
        json.dumps([{
            "nome": "Loja journal",
            "store_id": "store-journal",
            "integracoes": {},
        }]),
        encoding="utf-8",
    )
    journal = (
        arquivo.parent
        / "_shared_sync_backups"
        / "pending_lojas_transaction.json"
    )
    remover_real = integracoes.os.remove
    bloquear = {"valor": True}

    def remover(caminho):
        if bloquear["valor"] and str(caminho) == str(journal):
            raise PermissionError("journal ocupado")
        return remover_real(caminho)

    monkeypatch.setattr(integracoes.os, "remove", remover)
    integracoes.atualizar_api_loja(
        "000002",
        "Loja journal",
        "mercadoturbo",
        {"token": "token-a", "connected": True},
        store_id="store-journal",
        require_existing=True,
    )
    assert json.loads(journal.read_text(encoding="utf-8"))["committed"] is True

    carregadas = integracoes.carregar_lojas("000002")
    carregadas[0]["observacao"] = "update-mais-novo"
    integracoes.salvar_lojas("000002", carregadas)
    bloquear["valor"] = False

    finais = integracoes.carregar_lojas("000002")
    assert finais[0]["observacao"] == "update-mais-novo"
    assert not journal.exists()


def test_backup_nao_promove_oauth_antigo_sem_prova_da_mesma_conta(tmp_path):
    arquivo = _configure(tmp_path)
    atual = [{
        "nome": "Loja Bling",
        "store_id": "store-bling",
        "integracoes": {
            "bling": {
                "id": "app-compartilhado",
                "secret": "secret-compartilhado",
                "access_token": "ACCESS-NOVO",
                "connected": True,
            },
        },
    }]
    antigo = [{
        **atual[0],
        "integracoes": {
            "bling": {
                "id": "app-compartilhado",
                "secret": "secret-compartilhado",
                "access_token": "ACCESS-ANTIGO",
                "refresh_token": "REFRESH-ANTIGO",
                "connected": True,
            },
        },
    }]
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    arquivo.write_text(json.dumps(atual), encoding="utf-8")
    (arquivo.parent / "lojas_config.json.bak").write_text(
        json.dumps(antigo),
        encoding="utf-8",
    )

    integracoes.salvar_lojas("000002", atual)
    backup_final = json.loads(
        (arquivo.parent / "lojas_config.json.bak").read_text(encoding="utf-8")
    )
    assert (
        backup_final[0]["integracoes"]["bling"]["access_token"]
        == "ACCESS-NOVO"
    )
    assert "refresh_token" not in backup_final[0]["integracoes"]["bling"]


def test_refresh_bling_semeia_e_preserva_oauth_connection_id(
    tmp_path,
    monkeypatch,
):
    _configure(tmp_path)
    lojas = _bling_lojas()
    integracoes.salvar_lojas("000002", lojas)
    store_id = lojas[0]["store_id"]
    gerados = []

    def gerar_connection_id(_bytes):
        gerados.append(True)
        return "connection-semeada"

    tokens = iter(
        [
            {"access_token": "access-a-1", "refresh_token": "refresh-a-1"},
            {"access_token": "access-a-2", "refresh_token": "refresh-a-2"},
        ]
    )
    monkeypatch.setattr(integracoes.secrets, "token_urlsafe", gerar_connection_id)
    monkeypatch.setattr(
        integracoes,
        "exchange_bling_refresh_token",
        lambda *_args: next(tokens),
    )

    primeira = integracoes.renovar_token_bling_loja(
        "000002",
        "Loja A",
        dict(lojas[0]["integracoes"]["bling"]),
        store_id=store_id,
    )
    assert primeira["oauth_connection_id"] == "connection-semeada"
    persistida = integracoes.buscar_loja(
        "000002",
        "Loja A",
        store_id=store_id,
    )["integracoes"]["bling"]
    assert persistida["oauth_connection_id"] == "connection-semeada"

    segunda = integracoes.renovar_token_bling_loja(
        "000002",
        "Loja A",
        dict(persistida),
        store_id=store_id,
    )
    assert segunda["oauth_connection_id"] == "connection-semeada"
    assert gerados == [True]
    assert integracoes.buscar_loja(
        "000002",
        "Loja A",
        store_id=store_id,
    )["integracoes"]["bling"]["oauth_connection_id"] == "connection-semeada"


def test_refresh_single_flight_mesma_loja_faz_um_post(tmp_path, monkeypatch):
    _configure(tmp_path)
    lojas = _bling_lojas()
    integracoes.salvar_lojas("000002", lojas)
    snapshot = dict(lojas[0]["integracoes"]["bling"])
    chamadas = 0
    chamadas_lock = threading.Lock()

    def exchange(client_id, client_secret, refresh_token):
        nonlocal chamadas
        with chamadas_lock:
            chamadas += 1
        return {"access_token": "access-a-new", "refresh_token": "refresh-a-new"}

    monkeypatch.setattr(integracoes, "exchange_bling_refresh_token", exchange)
    with ThreadPoolExecutor(max_workers=2) as executor:
        resultados = list(
            executor.map(
                lambda _: integracoes.renovar_token_bling_loja(
                    "000002",
                    "Loja A",
                    snapshot,
                    store_id=lojas[0]["store_id"],
                    return_disposition=True,
                ),
                range(2),
            )
        )

    assert chamadas == 1
    assert {item[0]["access_token"] for item in resultados} == {"access-a-new"}
    assert {item[1] for item in resultados} == {
        "committed_by_caller",
        "reused_concurrent",
    }
    assert integracoes.buscar_loja("000002", "Loja A")["integracoes"]["bling"]["refresh_token"] == "refresh-a-new"


def test_refresh_single_flight_sem_rotacao_do_refresh_token(tmp_path, monkeypatch):
    _configure(tmp_path)
    lojas = _bling_lojas()
    integracoes.salvar_lojas("000002", lojas)
    snapshot = dict(lojas[0]["integracoes"]["bling"])
    chamadas = 0

    def exchange(client_id, client_secret, refresh_token):
        nonlocal chamadas
        chamadas += 1
        return {"access_token": "access-a-new"}

    monkeypatch.setattr(integracoes, "exchange_bling_refresh_token", exchange)
    with ThreadPoolExecutor(max_workers=2) as executor:
        resultados = list(
            executor.map(
                lambda _: integracoes.renovar_token_bling_loja(
                    "000002",
                    "Loja A",
                    snapshot,
                    store_id=lojas[0]["store_id"],
                ),
                range(2),
            )
        )

    assert chamadas == 1
    assert {item["access_token"] for item in resultados} == {"access-a-new"}
    assert {item["refresh_token"] for item in resultados} == {"refresh-a-old"}


def test_refresh_concorrente_de_duas_lojas_preserva_ambos_tokens(tmp_path, monkeypatch):
    _configure(tmp_path)
    lojas = _bling_lojas()
    integracoes.salvar_lojas("000002", lojas)
    snapshots = {loja["nome"]: dict(loja["integracoes"]["bling"]) for loja in lojas}
    barrier = threading.Barrier(2)

    def exchange(client_id, client_secret, refresh_token):
        barrier.wait(timeout=3)
        sufixo = "a" if client_id == "client-a" else "b"
        return {
            "access_token": f"access-{sufixo}-new",
            "refresh_token": f"refresh-{sufixo}-new",
        }

    monkeypatch.setattr(integracoes, "exchange_bling_refresh_token", exchange)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                integracoes.renovar_token_bling_loja,
                "000002",
                loja["nome"],
                snapshots[loja["nome"]],
                store_id=loja["store_id"],
            )
            for loja in lojas
        ]
        [future.result(timeout=5) for future in futures]

    assert integracoes.buscar_loja("000002", "Loja A")["integracoes"]["bling"]["refresh_token"] == "refresh-a-new"
    assert integracoes.buscar_loja("000002", "Loja B")["integracoes"]["bling"]["refresh_token"] == "refresh-b-new"


def test_resultado_antigo_nao_sobrescreve_reconexao(tmp_path, monkeypatch):
    _configure(tmp_path)
    lojas = _bling_lojas()
    integracoes.salvar_lojas("000002", lojas)
    snapshot = dict(lojas[0]["integracoes"]["bling"])

    def exchange(client_id, client_secret, refresh_token):
        integracoes.atualizar_api_loja(
            "000002",
            "Loja A",
            "bling",
            {
                "access_token": "access-reconnected",
                "refresh_token": "refresh-reconnected",
                "connected": True,
            },
            store_id=lojas[0]["store_id"],
        )
        return {"access_token": "access-stale", "refresh_token": "refresh-stale"}

    monkeypatch.setattr(integracoes, "exchange_bling_refresh_token", exchange)
    resultado, disposition = integracoes.renovar_token_bling_loja(
        "000002",
        "Loja A",
        snapshot,
        store_id=lojas[0]["store_id"],
        return_disposition=True,
    )

    assert disposition == "cas_lost"
    assert resultado["access_token"] == "access-reconnected"
    persistido = integracoes.buscar_loja("000002", "Loja A")["integracoes"]["bling"]
    assert persistido["refresh_token"] == "refresh-reconnected"
    assert persistido["connected"] is True


def test_resultado_antigo_nao_sobrescreve_reconexao_com_mesmo_refresh(tmp_path, monkeypatch):
    _configure(tmp_path)
    lojas = _bling_lojas()
    integracoes.salvar_lojas("000002", lojas)
    snapshot = dict(lojas[0]["integracoes"]["bling"])

    def exchange(client_id, client_secret, refresh_token):
        integracoes.atualizar_api_loja(
            "000002",
            "Loja A",
            "bling",
            {
                "access_token": "access-reconnected",
                "refresh_token": refresh_token,
                "connected": True,
                "updated_at": "reconnected-now",
            },
            store_id=lojas[0]["store_id"],
        )
        return {"access_token": "access-stale", "refresh_token": refresh_token}

    monkeypatch.setattr(integracoes, "exchange_bling_refresh_token", exchange)
    resultado = integracoes.renovar_token_bling_loja(
        "000002",
        "Loja A",
        snapshot,
        store_id=lojas[0]["store_id"],
    )

    assert resultado["access_token"] == "access-reconnected"
    assert integracoes.buscar_loja("000002", "Loja A")["integracoes"]["bling"]["access_token"] == "access-reconnected"


def test_invalid_grant_obsoleto_nao_invalida_token_reconectado(tmp_path, monkeypatch):
    _configure(tmp_path)
    lojas = _bling_lojas()
    integracoes.salvar_lojas("000002", lojas)
    snapshot = dict(lojas[0]["integracoes"]["bling"])

    def exchange(client_id, client_secret, refresh_token):
        integracoes.atualizar_api_loja(
            "000002",
            "Loja A",
            "bling",
            {
                "access_token": "access-reconnected",
                "refresh_token": "refresh-reconnected",
                "connected": True,
                "oauth_invalid": False,
            },
            store_id=lojas[0]["store_id"],
        )
        raise HTTPException(status_code=401, detail="invalid_grant")

    monkeypatch.setattr(integracoes, "exchange_bling_refresh_token", exchange)
    resultado = integracoes.renovar_token_bling_loja(
        "000002",
        "Loja A",
        snapshot,
        store_id=lojas[0]["store_id"],
    )

    assert resultado["refresh_token"] == "refresh-reconnected"
    assert resultado["connected"] is True
    assert resultado.get("oauth_invalid") is False
