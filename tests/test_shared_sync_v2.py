import base64
import hashlib
import io
import json
from concurrent.futures import ThreadPoolExecutor
import threading
import time
import zipfile

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from backend.schemas import MachinePresenceHeartbeatRequest
from backend.schemas.shared_sync import SharedSyncRunRequest, SharedSyncUserLinkCreateRequest
from backend.services import admin_usuarios  # configura o facade de autenticacao e presenca
from backend.services import admin_usuarios_auth
from backend.services import admin_usuarios_login_core
from backend.services import admin_usuarios_presence
from backend.services import admin_usuarios_presence_core
from backend.services import shared_sync  # configura o facade e injeta dependências entre módulos
from backend.services import integracoes
from backend.services import shared_sync_apply_scope
from backend.services import shared_sync_common
from backend.services import shared_sync_bundle
from backend.services import shared_sync_collect_files
from backend.services import shared_sync_config
from backend.services import shared_sync_machine
from backend.services import shared_sync_machine_endpoints
from backend.services import shared_sync_keyring
from backend.services import shared_sync_merge_sqlite
from backend.services import shared_sync_merge_user_data
from backend.services import shared_sync_operations
from backend.services import shared_sync_remote
from backend.services import shared_sync_user_endpoints


def _entry(relative_path: str, data: bytes) -> dict:
    return {
        "relative_path": relative_path,
        "data": data,
        "size": len(data),
        "mtime": 1,
        "sha256": hashlib.sha256(data).hexdigest(),
        "item_keys": [],
    }


def test_shared_sync_inicializa_depois_de_get_tenant_path():
    source = open("backend_api.py", "r", encoding="utf-8-sig").read()
    assert source.index("def get_tenant_path") < source.index("configure_shared_sync_runtime")


def test_boot_web_mantem_user_share_manual_e_ativa_somente_machine_auto_pull():
    source = open("static/auth/shared-sync-boot.js", "r", encoding="utf-8-sig").read()
    shared_start = source.index("(function initSharedSyncAutoPull")
    shared_return = source.index("    return;", shared_start)
    assert shared_return < source.index("/api/shared-sync/auto-pull", shared_start)
    assert shared_return < source.index("/api/shared-sync/user-shares/auto-push", shared_start)
    machine_start = source.index("(function initMachineSharedSyncAuto")
    machine_end = source.index("})();", machine_start)
    machine_source = source[machine_start:machine_end]
    assert "/api/shared-sync/machine-sync/auto" in machine_source
    assert "setInterval" in machine_source or "setTimeout" in machine_source
    assert "/api/shared-sync/machine-sync/push" not in machine_source


def test_auto_global_fica_desativado_mas_machine_auto_pull_respeita_opt_in(monkeypatch):
    monkeypatch.setenv("JK_SHARED_SYNC_AUTO", "1")
    assert shared_sync_config._shared_sync_auto_enabled() is False
    sessao = {"username": "operador", "client_id": "000002", "permissions": {"integracao": True}}
    cfg = shared_sync_config._shared_sync_machine_config_normalizar(
        sessao,
        {"enabled": True, "scopes": ["lojas_integracoes"], "auto_pull": True, "auto_push": True},
    )
    assert cfg["auto_pull"] is True
    assert cfg["auto_push"] is False


def test_machine_config_migra_auto_pull_legado_e_novo_save_fica_explicito(monkeypatch):
    sessao = {"username": "operador", "client_id": "000002", "permissions": {"integracao": True}}
    legado = shared_sync_config._shared_sync_machine_config_normalizar(
        sessao,
        {"enabled": True, "scopes": ["lojas_integracoes"], "auto_pull": False, "auto_push": False},
    )
    assert legado["auto_pull"] is True
    assert legado["auto_pull_explicit"] is False
    assert legado["mode_version"] == 1

    state = {"scopes": {}}
    monkeypatch.setattr(shared_sync_config, "_shared_sync_state_read", lambda *args: dict(state))

    def save_state(_client_id, _username, payload):
        state.clear()
        state.update(payload)

    monkeypatch.setattr(shared_sync_config, "_shared_sync_state_write", save_state)
    monkeypatch.setattr(shared_sync_config, "_shared_sync_now_iso", lambda: "2026-07-20T12:00:00Z")

    saved = shared_sync_config._shared_sync_machine_config_save(
        sessao,
        {"enabled": True, "scopes": ["lojas_integracoes"], "auto_pull": False, "auto_push": True},
    )

    assert saved["auto_pull"] is False
    assert saved["auto_push"] is False
    assert saved["auto_pull_explicit"] is True
    assert saved["mode_version"] == 2
    assert state["machine_sync"] == saved


def test_machine_auto_endpoint_independe_do_auto_global(monkeypatch):
    sessao = {"username": "operador", "client_id": "000002"}
    chamadas = []
    monkeypatch.setattr(shared_sync_machine_endpoints, "_shared_sync_session", lambda *args: sessao)
    monkeypatch.setattr(shared_sync_machine_endpoints, "_shared_sync_auto_enabled", lambda: False)
    rate_limits = []
    monkeypatch.setattr(
        shared_sync_machine_endpoints,
        "_shared_sync_auto_rate_limit",
        lambda *args, **kwargs: rate_limits.append((args, kwargs)) or None,
    )
    monkeypatch.setattr(shared_sync_machine_endpoints, "_shared_sync_machine_auto_interval_seconds", lambda: 120)
    monkeypatch.setattr(
        shared_sync_machine_endpoints,
        "_shared_sync_machine_auto_run",
        lambda received, machine_id, scopes: chamadas.append((received, machine_id, scopes)) or {
            "success": True,
            "direction": "machine-auto",
            "results": [{"scope": "cadastro", "direction": "pull"}],
            "skipped": [],
        },
    )

    result = shared_sync_machine_endpoints.shared_sync_machine_auto(
        SharedSyncRunRequest(scopes=["cadastro"], machine_id="pc:destino"),
        authorization="Bearer teste",
        client_id="000002",
    )

    assert result["results"] == [{"scope": "cadastro", "direction": "pull"}]
    assert chamadas == [(sessao, "pc:destino", ["cadastro"])]
    assert rate_limits[0][1]["interval_seconds"] == 120


def test_machine_auto_intervalo_padrao_alinha_com_o_scheduler(monkeypatch):
    monkeypatch.delenv("JK_MACHINE_SHARED_SYNC_AUTO_INTERVAL_S", raising=False)
    assert shared_sync_config._shared_sync_machine_auto_interval_seconds() == 120
    monkeypatch.setenv("JK_MACHINE_SHARED_SYNC_AUTO_INTERVAL_S", "5")
    assert shared_sync_config._shared_sync_machine_auto_interval_seconds() == 60
    monkeypatch.setenv("JK_MACHINE_SHARED_SYNC_AUTO_INTERVAL_S", "9999")
    assert shared_sync_config._shared_sync_machine_auto_interval_seconds() == 900


def test_machine_auto_run_puxa_apenas_hash_novo_de_outra_maquina_e_nunca_envia(monkeypatch):
    sessao = {"username": "operador", "client_id": "000002"}
    scopes = ["cadastro", "lojas_integracoes", "vendas", "favoritos_historico"]
    monkeypatch.setattr(
        shared_sync_machine,
        "_shared_sync_machine_config_read",
        lambda _sessao: {
            "enabled": True,
            "scopes": list(scopes),
            "auto_pull": True,
            # Mesmo um estado legado inconsistente nao pode reativar auto-push.
            "auto_push": True,
        },
    )
    monkeypatch.setattr(shared_sync_machine, "_shared_sync_machine_resolver_scopes", lambda *args, **kwargs: list(scopes))
    monkeypatch.setattr(
        shared_sync_machine,
        "_shared_sync_state_read",
        lambda *args: {
            "scopes": {
                "machine-sync:cadastro": {"snapshot_hash": "cadastro-antigo"},
                "machine-sync:lojas_integracoes": {"snapshot_hash": "lojas-atual"},
                "machine-sync:vendas": {"snapshot_hash": "vendas-antigo"},
            }
        },
    )
    remotos = {
        "cadastro": {"snapshot_hash": "cadastro-novo", "machine_id": "pc:origem"},
        "lojas_integracoes": {"snapshot_hash": "lojas-atual", "machine_id": "pc:origem"},
        "vendas": {"snapshot_hash": "vendas-novo", "machine_id": "pc:destino"},
        "favoritos_historico": {},
    }
    monkeypatch.setattr(shared_sync_machine, "_shared_sync_machine_remote_meta", lambda _sessao, scope: dict(remotos[scope]))
    pulls = []
    monkeypatch.setattr(
        shared_sync_machine,
        "_shared_sync_machine_pull_scope",
        lambda _sessao, scope, **_kwargs: pulls.append(scope) or {"scope": scope, "success": True, "direction": "pull"},
    )
    monkeypatch.setattr(
        shared_sync_machine,
        "_shared_sync_machine_push_scope",
        lambda *args, **kwargs: pytest.fail("machine-auto nunca pode enviar"),
    )

    result = shared_sync_machine._shared_sync_machine_auto_run(sessao, "pc:destino")

    assert pulls == ["cadastro"]
    assert result["results"] == [{"scope": "cadastro", "success": True, "direction": "pull"}]
    assert {item["scope"]: item["reason"] for item in result["skipped"]} == {
        "lojas_integracoes": "already_current",
        "vendas": "same_machine",
        "favoritos_historico": "remote_missing",
    }


def test_machine_auto_continua_outros_scopes_quando_um_pull_falha(monkeypatch):
    sessao = {"username": "operador", "client_id": "000002"}
    scopes = ["cadastro", "vendas"]
    monkeypatch.setattr(
        shared_sync_machine,
        "_shared_sync_machine_config_read",
        lambda _sessao: {"enabled": True, "scopes": scopes, "auto_pull": True, "auto_push": False},
    )
    monkeypatch.setattr(shared_sync_machine, "_shared_sync_machine_resolver_scopes", lambda *args, **kwargs: scopes)
    monkeypatch.setattr(shared_sync_machine, "_shared_sync_state_read", lambda *args: {"scopes": {}})
    monkeypatch.setattr(
        shared_sync_machine,
        "_shared_sync_machine_remote_meta",
        lambda _sessao, scope: {"snapshot_hash": f"hash-{scope}", "machine_id": "pc:origem"},
    )
    pulls = []

    def pull_scope(_sessao, scope, **_kwargs):
        pulls.append(scope)
        if scope == "cadastro":
            raise HTTPException(status_code=502, detail="snapshot de cadastro invalido")
        return {"scope": scope, "success": True, "direction": "pull"}

    monkeypatch.setattr(shared_sync_machine, "_shared_sync_machine_pull_scope", pull_scope)

    result = shared_sync_machine._shared_sync_machine_auto_run(sessao, "pc:destino")

    assert pulls == scopes
    assert result["results"] == [{"scope": "vendas", "success": True, "direction": "pull"}]
    assert result["skipped"] == [{
        "scope": "cadastro",
        "reason": "pull_failed",
        "status_code": 502,
        "message": "snapshot de cadastro invalido",
    }]


def test_machine_auto_run_sem_opt_in_nao_le_remoto_nem_transfere(monkeypatch):
    sessao = {"username": "operador", "client_id": "000002"}
    monkeypatch.setattr(
        shared_sync_machine,
        "_shared_sync_machine_config_read",
        lambda _sessao: {
            "enabled": True,
            "scopes": ["cadastro"],
            "auto_pull": False,
            "auto_push": False,
        },
    )
    monkeypatch.setattr(shared_sync_machine, "_shared_sync_machine_resolver_scopes", lambda *args, **kwargs: ["cadastro"])
    monkeypatch.setattr(shared_sync_machine, "_shared_sync_state_read", lambda *args: {"scopes": {}})
    monkeypatch.setattr(
        shared_sync_machine,
        "_shared_sync_machine_remote_meta",
        lambda *args: pytest.fail("sem opt-in nao deve consultar nem transferir snapshot"),
    )

    result = shared_sync_machine._shared_sync_machine_auto_run(sessao, "pc:destino")

    assert result["results"] == []
    assert result["skipped"] == [{"scope": "cadastro", "reason": "auto_pull_disabled"}]


def test_machine_status_expoe_recebimento_pendente_e_ultimo_pull(monkeypatch):
    sessao = {"username": "operador", "client_id": "000002"}
    monkeypatch.setattr(
        shared_sync_machine,
        "_shared_sync_machine_config_read",
        lambda _sessao: {"enabled": True, "scopes": ["cadastro"], "auto_pull": True, "auto_push": False},
    )
    monkeypatch.setattr(shared_sync_machine, "_shared_sync_machine_allowed_scopes", lambda _sessao: ["cadastro"])
    monkeypatch.setattr(
        shared_sync_machine,
        "_shared_sync_state_read",
        lambda *args: {
            "scopes": {
                "machine-sync:cadastro": {
                    "snapshot_hash": "hash-anterior",
                    "direction": "pull",
                    "synced_at": "2026-07-20T10:00:00Z",
                }
            }
        },
    )
    monkeypatch.setattr(
        shared_sync_machine,
        "_shared_sync_machine_remote_meta",
        lambda _sessao, scope: (
            {"snapshot_hash": "hash-novo", "machine_id": "pc:origem", "updated_at": "2026-07-20T11:00:00Z"}
            if scope == "cadastro" else None
        ),
    )
    monkeypatch.setattr(shared_sync_machine, "_machine_presence_list", lambda *args: [], raising=False)
    monkeypatch.setattr(shared_sync_machine, "_machine_presence_mark_current", lambda machines, _machine_id: machines, raising=False)
    monkeypatch.setattr(shared_sync_machine, "_firebase_deve_usar", lambda: True, raising=False)
    monkeypatch.setattr(
        shared_sync_machine,
        "_shared_sync_keyring_status",
        lambda *_args, **_kwargs: {"ready": True, "registered": True, "key_id": "abc123", "needs_send": False, "reason": ""},
    )

    payload = shared_sync_machine._shared_sync_machine_status_payload(sessao, "pc:destino")
    cadastro = payload["scopes"]["cadastro"]

    assert cadastro["pending_receive"] is True
    assert cadastro["last_received_at"] == "2026-07-20T10:00:00Z"
    assert cadastro["synced_at"] == "2026-07-20T10:00:00Z"
    assert cadastro["remote"]["snapshot_hash"] == "hash-novo"


def test_pacote_v2_criptografa_credenciais_sem_texto_legivel(monkeypatch):
    secret = b"segredo-local-de-teste-com-entropia"
    monkeypatch.setattr(shared_sync_remote, "_shared_sync_encryption_secret", lambda: secret)
    credentials = b'{"access_token":"token-super-secreto","refresh_token":"refresh-secreto","state":"temporario","code":"oauth-code"}'
    monkeypatch.setattr(
        shared_sync_bundle,
        "_shared_sync_coletar_arquivos",
        lambda *args, **kwargs: ([_entry("lojas_config.json", credentials)], []),
    )
    bundle, manifest, _ = shared_sync_bundle._shared_sync_montar_pacote(
        "000002", "lojas_integracoes", "operador", user_only=True,
    )
    assert manifest["schema"] == 2
    with zipfile.ZipFile(io.BytesIO(bundle), "r") as zf:
        persisted = json.loads(zf.read("files/lojas_config.json").decode("utf-8"))
    assert persisted["access_token"] == "token-super-secreto"
    assert persisted["refresh_token"] == "refresh-secreto"
    assert "state" not in persisted
    assert "code" not in persisted
    encrypted = shared_sync_remote._shared_sync_encrypt_bundle("conta-ou-vinculo", bundle)
    assert b"token-super-secreto" not in encrypted
    assert b"refresh-secreto" not in encrypted
    assert shared_sync_remote._shared_sync_decrypt_bundle("conta-ou-vinculo", encrypted) == bundle
    with pytest.raises(HTTPException):
        shared_sync_remote._shared_sync_decrypt_bundle("outro-vinculo", encrypted)


def test_coleta_integracoes_exclui_temporarios_oauth(tmp_path, monkeypatch):
    tenant = tmp_path / "000002"
    tenant.mkdir()
    (tenant / "lojas_config.json").write_text("[]", encoding="utf-8")
    (tenant / "integracoes.json").write_text("{}", encoding="utf-8")
    (tenant / "temp_integracao.json").write_text('{"code":"nao-pode-ir"}', encoding="utf-8")
    (tenant / "oauth_state.json").write_text('{"state":"nao-pode-ir"}', encoding="utf-8")
    monkeypatch.setattr(shared_sync_collect_files, "get_tenant_path", lambda _client_id: str(tenant), raising=False)
    entries, _ = shared_sync_collect_files._shared_sync_coletar_arquivos(
        "000002", "lojas_integracoes", username="operador", user_only=True,
    )
    names = {item["relative_path"] for item in entries}
    assert "lojas_config.json" in names
    assert "integracoes.json" in names
    assert "temp_integracao.json" not in names
    assert "oauth_state.json" not in names


def test_coleta_cadastro_mantem_dossies_sku_fora_do_shared_sync(tmp_path, monkeypatch):
    tenant = tmp_path / "000002"
    dossier_dir = tenant / "SKU"
    dossier_dir.mkdir(parents=True)
    (tenant / "cadastro_produtos.csv").write_text("sku,descricao\n001,Produto\n", encoding="utf-8")
    (dossier_dir / "001.json").write_text('{"sku":"001"}', encoding="utf-8")
    monkeypatch.setattr(shared_sync_collect_files, "get_tenant_path", lambda _client_id: str(tenant), raising=False)

    entries, _ = shared_sync_collect_files._shared_sync_coletar_arquivos(
        "000002", "cadastro", username="operador", user_only=True,
    )

    names = {item["relative_path"] for item in entries}
    assert "cadastro_produtos.csv" in names
    assert "SKU/001.json" not in names


def test_context_hub_e_obsidian_sao_excluidos_permanentemente(tmp_path, monkeypatch):
    tenant = tmp_path / "000002"
    (tenant / "ContextVault" / "70_Gerado").mkdir(parents=True)
    (tenant / "ContextVault" / "70_Gerado" / "mapa.json").write_text("{}", encoding="utf-8")
    (tenant / "ContextVault" / ".obsidian").mkdir(parents=True)
    (tenant / "ContextVault" / ".obsidian" / "app.json").write_text("{}", encoding="utf-8")
    (tenant / "context_hub").mkdir(parents=True)
    (tenant / "context_hub" / "generation.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(shared_sync_collect_files, "get_tenant_path", lambda _client_id: str(tenant), raising=False)

    for rel in (
        "ContextVault/70_Gerado/mapa.json",
        "ContextVault/.obsidian/app.json",
        "context_hub/generation.json",
        "SKU/001.json",
    ):
        assert shared_sync_collect_files._shared_sync_scope_match("cadastro", rel) is False

    entries, _ = shared_sync_collect_files._shared_sync_coletar_arquivos(
        "000002", "cadastro", username="operador", user_only=True,
    )
    assert entries == []


def test_shared_sync_rejeita_symlink_ou_junction_fora_do_tenant(tmp_path, monkeypatch):
    tenant = tmp_path / "000002"
    external = tmp_path / "external"
    tenant.mkdir()
    external.mkdir()
    (external / "001.json").write_text('{"sku":"001"}', encoding="utf-8")
    linked = tenant / "SKU"
    try:
        linked.symlink_to(external, target_is_directory=True)
    except OSError:
        pytest.skip("ambiente sem permissao para criar junction/symlink")
    monkeypatch.setattr(shared_sync_collect_files, "get_tenant_path", lambda _client_id: str(tenant), raising=False)

    entries, warnings = shared_sync_collect_files._shared_sync_coletar_arquivos(
        "000002", "cadastro", username="operador", user_only=True,
    )

    assert entries == []
    assert any("caminho inseguro" in warning for warning in warnings)


def test_shared_sync_rejeita_destino_sob_junction(tmp_path):
    tenant = tmp_path / "000002"
    external = tmp_path / "external"
    tenant.mkdir()
    external.mkdir()
    linked = tenant / "SKU"
    try:
        linked.symlink_to(external, target_is_directory=True)
    except OSError:
        pytest.skip("ambiente sem permissao para criar junction/symlink")

    with pytest.raises(HTTPException) as exc:
        shared_sync_collect_files._shared_sync_resolve_tenant_path(str(tenant), "SKU/001.json")
    assert exc.value.status_code == 400


def test_shared_sync_rejeita_realpath_divergente_sem_depender_de_privilegio(monkeypatch, tmp_path):
    tenant = tmp_path / "000002"
    tenant.mkdir()
    original_realpath = shared_sync_common.os.path.realpath
    candidate = tenant / "SKU" / "001.json"
    external = tmp_path / "external" / "001.json"

    def fake_realpath(value):
        absolute = shared_sync_common.os.path.abspath(value)
        if shared_sync_common.os.path.normcase(absolute) == shared_sync_common.os.path.normcase(str(candidate)):
            return str(external)
        return original_realpath(value)

    monkeypatch.setattr(shared_sync_common.os.path, "realpath", fake_realpath)

    with pytest.raises(HTTPException) as exc:
        shared_sync_common._shared_sync_resolve_tenant_path(str(tenant), "SKU/001.json")
    assert exc.value.status_code == 400


def test_shared_sync_rejeita_raiz_tenant_redirecionada(monkeypatch, tmp_path):
    tenant = tmp_path / "000002"
    tenant.mkdir()
    external = tmp_path / "external"
    original_realpath = shared_sync_common.os.path.realpath

    def fake_realpath(value):
        absolute = shared_sync_common.os.path.abspath(value)
        if shared_sync_common.os.path.normcase(absolute) == shared_sync_common.os.path.normcase(str(tenant)):
            return str(external)
        return original_realpath(value)

    monkeypatch.setattr(shared_sync_common.os.path, "realpath", fake_realpath)

    with pytest.raises(HTTPException) as exc:
        shared_sync_common._shared_sync_resolve_tenant_path(str(tenant), "SKU/001.json")
    assert exc.value.status_code == 400


def test_user_share_merge_resolve_todos_os_destinos(monkeypatch, tmp_path):
    tenant = tmp_path / "000002"
    tenant.mkdir()
    calls = []

    def resolver(root, rel):
        calls.append((root, rel))
        return str(tenant / rel)

    monkeypatch.setattr(shared_sync_merge_sqlite, "_shared_sync_resolve_tenant_path", resolver, raising=False)
    result = shared_sync_merge_sqlite._shared_sync_aplicar_user_share_add_only(
        "000002", "vendas", "destino", [("vendas_sync_state.json", b"{}")],
        str(tenant), str(tenant / "backup"),
    )

    assert calls == [(str(tenant), "vendas_sync_state.json")]
    assert result["file_count"] == 0


def test_share_between_users_bloqueia_antes_de_mesclar(monkeypatch, tmp_path):
    tenant = tmp_path / "000002"
    tenant.mkdir()

    def bloquear(_root, _rel):
        raise HTTPException(status_code=400, detail="destino inseguro")

    monkeypatch.setattr(shared_sync_merge_user_data, "_shared_sync_resolve_tenant_path", bloquear, raising=False)
    monkeypatch.setattr(
        shared_sync_merge_user_data,
        "_shared_sync_target_rel_usuario",
        lambda _scope, _username: "favoritos_historico_destino.db",
        raising=False,
    )

    with pytest.raises(HTTPException, match="destino inseguro"):
        shared_sync_merge_user_data._shared_sync_aplicar_user_scoped_share(
            "000002", "favoritos_historico", "destino", [("historico.json", b"{}")],
            str(tenant), str(tenant / "backup"),
        )


def test_vinculo_nasce_ativo_sem_admin_e_sem_aceite(monkeypatch):
    source = {
        "username": "origem",
        "client_id": "000002",
        "permissions": {"integracao": True},
        "is_admin": False,
        "usuario": {"username": "origem", "client_id": "000002", "active": True},
    }
    target = {"username": "destino", "client_id": "000008", "active": True, "name": "Destino"}
    monkeypatch.setattr(shared_sync_user_endpoints, "_shared_sync_resolver_usuario_destino", lambda *args: target)
    monkeypatch.setattr(
        shared_sync_user_endpoints,
        "_shared_sync_participant_session",
        lambda *args: {"username": "destino", "client_id": "000008", "permissions": {"integracao": True}, "is_admin": False},
    )
    monkeypatch.setattr(shared_sync_user_endpoints, "_shared_sync_links_all", lambda: [])
    monkeypatch.setattr(shared_sync_user_endpoints, "_shared_sync_save_link", lambda link: dict(link, storage="test"))
    monkeypatch.setattr(shared_sync_user_endpoints, "_shared_sync_link_public", lambda link, _sessao: link)
    payload = SharedSyncUserLinkCreateRequest(
        target_username="destino", target_client_id="000008", scopes=["lojas_integracoes"],
    )
    result = shared_sync_user_endpoints._shared_sync_create_direct_link(payload, source)
    assert result["success"] is True
    assert result["manual_only"] is True
    assert result["invite"] is None
    assert result["results"] == []
    assert result["link"]["active"] is True
    assert result["link"]["schema"] == 2


def test_vinculo_exige_permissao_normal_do_modulo_no_destino(monkeypatch):
    source = {"username": "origem", "client_id": "000002", "permissions": {"integracao": True}, "is_admin": False}
    target = {"username": "destino", "client_id": "000008"}
    monkeypatch.setattr(
        shared_sync_user_endpoints,
        "_shared_sync_participant_session",
        lambda *args: {"username": "destino", "client_id": "000008", "permissions": {}, "is_admin": False},
    )
    with pytest.raises(HTTPException) as exc:
        shared_sync_user_endpoints._shared_sync_validate_link_scopes(source, target, ["lojas_integracoes"])
    assert exc.value.status_code == 403


def test_operacao_expirada_ou_com_hash_alterado_requer_nova_previa(monkeypatch):
    sessao = {"username": "operador", "client_id": "000002"}
    local = {"lojas_integracoes": "hash-a"}
    monkeypatch.setattr(shared_sync_operations, "_shared_sync_local_fingerprint", lambda *args: (dict(local), {"lojas_integracoes": []}))
    monkeypatch.setattr(shared_sync_operations, "_shared_sync_remote_fingerprint", lambda *args: ({"lojas_integracoes": "remote-a"}, {"lojas_integracoes": {}}))
    preview = shared_sync_operations._shared_sync_create_preview(
        sessao,
        kind="machine",
        resource_id="self",
        direction="push",
        scopes=["lojas_integracoes"],
        bundle_ids={"lojas_integracoes": "bundle"},
    )
    local["lojas_integracoes"] = "hash-b"
    with pytest.raises(HTTPException) as changed:
        shared_sync_operations._shared_sync_require_operation(
            preview["operation_id"], sessao, kind="machine", resource_id="self", direction="push",
            scopes=["lojas_integracoes"], bundle_ids={"lojas_integracoes": "bundle"},
        )
    assert changed.value.status_code == 409

    local["lojas_integracoes"] = "hash-a"
    preview = shared_sync_operations._shared_sync_create_preview(
        sessao,
        kind="machine",
        resource_id="self",
        direction="push",
        scopes=["lojas_integracoes"],
        bundle_ids={"lojas_integracoes": "bundle"},
    )
    shared_sync_operations._OPERATIONS[preview["operation_id"]]["expires_ts"] = 0
    with pytest.raises(HTTPException) as expired:
        shared_sync_operations._shared_sync_require_operation(
            preview["operation_id"], sessao, kind="machine", resource_id="self", direction="push",
            scopes=["lojas_integracoes"], bundle_ids={"lojas_integracoes": "bundle"},
        )
    assert expired.value.status_code == 409


def test_importacao_manual_substitui_integracoes_pelo_snapshot_confirmado(tmp_path, monkeypatch):
    info = tmp_path / "info"

    def tenant_path(client_id):
        path = info / str(client_id)
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    integracoes.configure_integracoes_context(
        pasta_info=str(info), get_tenant_path=tenant_path, normalizar_integracao_conectada=lambda _s, data: data,
    )
    monkeypatch.setattr(shared_sync_apply_scope, "get_tenant_path", tenant_path, raising=False)
    integracoes.salvar_lojas("000002", [{"nome": "Antiga", "integracoes": {"bling": {"access_token": "antigo"}}}])
    source = [{
        "nome": "Nova",
        "store_id": "store-estavel",
        "integracoes": {
            "bling": {"id": "id", "secret": "secret", "access_token": "novo", "refresh_token": "refresh", "connected": True},
            "mercadoturbo": {"token": "turbo", "connected": True},
        },
    }]
    raw = json.dumps(source).encode("utf-8")
    monkeypatch.setattr(
        shared_sync_bundle,
        "_shared_sync_coletar_arquivos",
        lambda *args, **kwargs: ([_entry("lojas_config.json", raw)], []),
    )
    bundle, _, _ = shared_sync_bundle._shared_sync_montar_pacote(
        "000008", "lojas_integracoes", "origem", user_only=True,
    )
    result = shared_sync_apply_scope._shared_sync_aplicar_pacote(
        "000002", "lojas_integracoes", bundle, "destino", {"user_share": True},
    )
    saved = json.loads((info / "000002" / "lojas_config.json").read_text(encoding="utf-8"))
    assert result["file_count"] == 1
    assert result["stores_count"] == 1
    assert [item["nome"] for item in saved] == ["Nova"]
    assert saved[0]["store_id"] == "store-estavel"
    assert saved[0]["integracoes"]["bling"]["access_token"] == "novo"
    assert saved[0]["integracoes"]["bling"]["refresh_token"] == "refresh"
    assert saved[0]["integracoes"]["mercadoturbo"]["token"] == "turbo"


def test_importacao_manual_de_maquina_reaplica_snapshot_mesmo_com_estado_ja_atual(monkeypatch):
    sessao = {"username": "operador", "client_id": "000002"}
    meta = {
        "snapshot_hash": "snapshot-remoto",
        "updated_at": "2026-07-17T13:35:22Z",
        "updated_by": "origem",
        "machine_id": "pc:origem",
    }
    aplicacoes = []
    atualizacoes = []

    monkeypatch.setattr(shared_sync_machine, "_shared_sync_machine_doc_id", lambda *args: "bundle-lojas")
    monkeypatch.setattr(shared_sync_machine, "_shared_sync_remote_meta_by_id", lambda _bundle_id: dict(meta))
    monkeypatch.setattr(shared_sync_machine, "_shared_sync_pull_already_current", lambda *args: True)
    monkeypatch.setattr(
        shared_sync_machine,
        "_shared_sync_obter_bundle_por_id",
        lambda _bundle_id, _meta, **_kwargs: (b"pacote-confirmado", dict(meta)),
    )
    monkeypatch.setattr(
        shared_sync_machine,
        "_shared_sync_aplicar_pacote",
        lambda *args, **kwargs: aplicacoes.append((args, kwargs)) or {
            "file_count": 1,
            "backup_dir": "backup/lojas",
        },
    )
    monkeypatch.setattr(
        shared_sync_machine,
        "_shared_sync_state_update",
        lambda *args, **kwargs: atualizacoes.append((args, kwargs)),
    )

    result = shared_sync_machine._shared_sync_machine_pull_scope(
        sessao, "lojas_integracoes", force=True,
    )

    assert result["success"] is True
    assert result["file_count"] == 1
    assert result.get("skipped") is not True
    assert len(aplicacoes) == 1
    assert len(atualizacoes) == 1


def test_importacao_automatica_de_maquina_ainda_pode_pular_snapshot_ja_atual(monkeypatch):
    sessao = {"username": "operador", "client_id": "000002"}
    meta = {"snapshot_hash": "snapshot-remoto"}
    monkeypatch.setattr(shared_sync_machine, "_shared_sync_machine_doc_id", lambda *args: "bundle-lojas")
    monkeypatch.setattr(shared_sync_machine, "_shared_sync_remote_meta_by_id", lambda _bundle_id: dict(meta))
    monkeypatch.setattr(shared_sync_machine, "_shared_sync_pull_already_current", lambda *args: True)
    monkeypatch.setattr(
        shared_sync_machine,
        "_shared_sync_obter_bundle_por_id",
        lambda *args: pytest.fail("o pacote automatico ja atual nao deveria ser baixado"),
    )

    result = shared_sync_machine._shared_sync_machine_pull_scope(sessao, "lojas_integracoes")

    assert result["skipped"] is True
    assert result["reason"] == "already_current"


def test_endpoint_de_importacao_manual_forca_reaplicacao(monkeypatch):
    sessao = {"username": "operador", "client_id": "000002"}
    chamadas = []
    monkeypatch.setattr(shared_sync_machine_endpoints, "_shared_sync_session", lambda *args: sessao)
    monkeypatch.setattr(
        shared_sync_machine_endpoints,
        "_shared_sync_machine_resolver_scopes",
        lambda *args, **kwargs: ["lojas_integracoes"],
    )
    monkeypatch.setattr(
        shared_sync_machine_endpoints,
        "_shared_sync_machine_bundle_ids",
        lambda *args: {"lojas_integracoes": "bundle-lojas"},
    )
    monkeypatch.setattr(shared_sync_machine_endpoints, "_shared_sync_require_operation", lambda *args, **kwargs: {"id": "op"})
    monkeypatch.setattr(
        shared_sync_machine_endpoints,
        "_shared_sync_machine_pull_scope",
        lambda _sessao, scope, *, force=False, machine_id="": chamadas.append((scope, force)) or {
            "scope": scope,
            "success": True,
            "file_count": 1,
        },
    )
    monkeypatch.setattr(shared_sync_machine_endpoints, "_shared_sync_audit", lambda *args, **kwargs: None)

    result = shared_sync_machine_endpoints.shared_sync_machine_pull(
        SharedSyncRunRequest(scopes=["lojas_integracoes"], operation_id="op"),
        authorization="Bearer teste",
        client_id="000002",
    )

    assert result["success"] is True
    assert chamadas == [("lojas_integracoes", True)]


def test_endpoint_marca_importacao_com_quantidade_de_lojas_divergente(monkeypatch):
    sessao = {"username": "operador", "client_id": "000002"}
    monkeypatch.setattr(shared_sync_machine_endpoints, "_shared_sync_session", lambda *args: sessao)
    monkeypatch.setattr(
        shared_sync_machine_endpoints,
        "_shared_sync_machine_resolver_scopes",
        lambda *args, **kwargs: ["lojas_integracoes"],
    )
    monkeypatch.setattr(
        shared_sync_machine_endpoints,
        "_shared_sync_machine_bundle_ids",
        lambda *args: {"lojas_integracoes": "bundle-lojas"},
    )
    monkeypatch.setattr(
        shared_sync_machine_endpoints,
        "_shared_sync_require_operation",
        lambda *args, **kwargs: {"id": "op", "totals": {"stores": 4}},
    )
    monkeypatch.setattr(
        shared_sync_machine_endpoints,
        "_shared_sync_machine_pull_scope",
        lambda *args, **kwargs: {
            "scope": "lojas_integracoes",
            "success": True,
            "file_count": 1,
            "stores_count": 3,
        },
    )
    monkeypatch.setattr(shared_sync_machine_endpoints, "_shared_sync_audit", lambda *args, **kwargs: None)

    result = shared_sync_machine_endpoints.shared_sync_machine_pull(
        SharedSyncRunRequest(scopes=["lojas_integracoes"], operation_id="op"),
        authorization="Bearer teste",
        client_id="000002",
    )

    assert result["success"] is False
    assert result["partial"] is False
    assert result["results"][0]["scope"] == "lojas_integracoes"
    assert result["results"][0]["status_code"] == 502
    assert "snapshot continha 4 loja(s), mas 3 foram aplicadas" in result["results"][0]["message"]


def test_falha_em_cadastro_nao_impede_importacao_das_lojas(monkeypatch):
    sessao = {"username": "operador", "client_id": "000002"}
    chamadas = []
    monkeypatch.setattr(shared_sync_machine_endpoints, "_shared_sync_session", lambda *args: sessao)
    monkeypatch.setattr(
        shared_sync_machine_endpoints,
        "_shared_sync_machine_resolver_scopes",
        lambda *args, **kwargs: ["cadastro", "lojas_integracoes"],
    )
    monkeypatch.setattr(
        shared_sync_machine_endpoints,
        "_shared_sync_machine_bundle_ids",
        lambda *args: {"cadastro": "bundle-cadastro", "lojas_integracoes": "bundle-lojas"},
    )
    monkeypatch.setattr(
        shared_sync_machine_endpoints,
        "_shared_sync_require_operation",
        lambda *args, **kwargs: {"id": "op", "totals": {"stores": 4}},
    )

    def pull_scope(_sessao, scope, *, force=False, machine_id=""):
        chamadas.append((scope, force))
        if scope == "cadastro":
            raise HTTPException(status_code=423, detail="Cadastro temporariamente bloqueado.")
        return {
            "scope": scope,
            "success": True,
            "file_count": 1,
            "stores_count": 4,
        }

    monkeypatch.setattr(shared_sync_machine_endpoints, "_shared_sync_machine_pull_scope", pull_scope)
    monkeypatch.setattr(shared_sync_machine_endpoints, "_shared_sync_audit", lambda *args, **kwargs: None)

    result = shared_sync_machine_endpoints.shared_sync_machine_pull(
        SharedSyncRunRequest(scopes=["cadastro", "lojas_integracoes"], operation_id="op"),
        authorization="Bearer teste",
        client_id="000002",
    )

    assert chamadas == [("cadastro", True), ("lojas_integracoes", True)]
    assert result["success"] is False
    assert result["partial"] is True
    assert result["results"][0]["success"] is False
    assert result["results"][0]["status_code"] == 423
    assert result["results"][1]["success"] is True
    assert result["results"][1]["stores_count"] == 4


def test_tela_de_sincronizacao_exibe_balao_central_e_recarrega_lojas():
    admin = open("static/admin_usuarios.html", "r", encoding="utf-8-sig").read()
    integracoes_html = open("static/integracoes.html", "r", encoding="utf-8-sig").read()

    assert 'id="machineSyncProgressOverlay"' in admin
    assert admin.count("atualizarBalaoSincronizacaoMaquinas(") >= 5
    assert "Preparando o envio dos dados" in admin
    assert "Recebendo e aplicando os dados" in admin
    assert "atualizarBalaoSincronizacaoMaquinas(false);" in admin
    assert "await recarregarLojasAposSincronizacao(['lojas_integracoes']);" in admin
    assert "ignorado porque o histórico indicava que já estava atualizado" in admin
    assert 'id="sharedSyncScreenOverlay"' in integracoes_html
    assert "window.jkIntegracoesSetSyncProgress" in integracoes_html
    assert "window.jkIntegracoesReloadStores" in integracoes_html
    assert "loja(s) no snapshot" in admin
    assert "não significa exclusão de lojas" in admin
    assert "O backend não confirmou quantas lojas foram importadas" in admin
    assert 'id="machineSyncActionStatus"' in admin
    assert "Sincronização parcial." in admin
    assert "item.success === false" in admin
    assert "throw error;" in admin
    assert admin == open("admin_usuarios.html", "r", encoding="utf-8-sig").read()
    assert integracoes_html == open("integracoes.html", "r", encoding="utf-8-sig").read()


def test_auditoria_nao_grava_segredos(tmp_path, monkeypatch):
    monkeypatch.setattr(shared_sync_operations, "get_tenant_path", lambda _client_id: str(tmp_path), raising=False)
    record = {
        "id": "op",
        "machine_id": "pc",
        "direction": "pull",
        "scopes": ["lojas_integracoes"],
        "totals": {"credentials": 4, "changes": 1},
    }
    shared_sync_operations._shared_sync_audit(
        {"username": "destino", "client_id": "000002"},
        record=record,
        results=[
            {"scope": "lojas_integracoes", "snapshot_hash": "abc", "success": True},
            {
                "scope": "cadastro",
                "success": False,
                "reason": "pull_failed",
                "status_code": 423,
                "message": "mensagem que nao deve ir para a auditoria",
            },
        ],
        link_id="link",
    )
    audit = (tmp_path / "shared_sync_audit.jsonl").read_text(encoding="utf-8")
    assert '"credentials":4' in audit
    assert '"success":false' in audit
    assert '"reason":"pull_failed"' in audit
    assert '"status_code":423' in audit
    assert "mensagem que nao deve ir para a auditoria" not in audit
    for secret in ("access_token", "refresh_token", "client_secret", "secret", "turbo"):
        assert secret not in audit


class _FakeSnapshot:
    def __init__(self, doc, exists=True):
        self._doc = doc
        self.id = doc.id
        self.exists = exists
        self.reference = doc

    def to_dict(self):
        return dict(self._doc.collection.data.get(self.id) or {})


class _FakeDocument:
    def __init__(self, collection, doc_id):
        self.collection = collection
        self.id = doc_id

    def set(self, value, merge=False, **_kwargs):
        if merge:
            current = dict(self.collection.data.get(self.id) or {})
            current.update(value)
            value = current
        self.collection.data[self.id] = dict(value)

    def create(self, value, **_kwargs):
        with self.collection.lock:
            if self.id in self.collection.data:
                raise RuntimeError("already exists")
            self.collection.data[self.id] = dict(value)

    def update(self, value, **_kwargs):
        current = dict(self.collection.data.get(self.id) or {})
        for key, item in value.items():
            array_values = getattr(item, "values", None)
            if array_values is not None:
                existing = list(current.get(key) or [])
                for entry in array_values:
                    if entry not in existing:
                        existing.append(entry)
                current[key] = existing
            else:
                current[key] = item
        self.collection.data[self.id] = current

    def get(self):
        if self.collection.fail_get and self.collection.fail_get(self.id):
            return _FakeSnapshot(self, exists=False)
        return _FakeSnapshot(self, exists=self.id in self.collection.data)

    def delete(self, **_kwargs):
        self.collection.data.pop(self.id, None)


class _FakeQuery:
    def __init__(self, collection, key, value):
        self.collection = collection
        self.key = key
        self.value = value

    def stream(self):
        for doc_id, data in list(self.collection.data.items()):
            if data.get(self.key) == self.value:
                yield _FakeSnapshot(_FakeDocument(self.collection, doc_id))


class _FakeCollection:
    def __init__(self):
        self.data = {}
        self.fail_get = None
        self.lock = threading.Lock()

    def document(self, doc_id):
        return _FakeDocument(self, doc_id)

    def where(self, key, _op, value):
        return _FakeQuery(self, key, value)


class _FakeFirestore:
    def __init__(self):
        self.collections = {}

    def collection(self, name):
        return self.collections.setdefault(name, _FakeCollection())


def _configure_keyring_test(monkeypatch, db, active_device, secure_stores, approved, present=None):
    present = set(present if present is not None else approved)
    monkeypatch.setattr(shared_sync_keyring, "_firebase_db", lambda: db)
    monkeypatch.setattr(shared_sync_keyring, "_firebase_deve_usar", lambda: True)
    monkeypatch.setattr(shared_sync_keyring, "_firebase_shared_sync_keyrings_collection_name", lambda: "shared_sync_keyrings")
    monkeypatch.setattr(
        shared_sync_keyring,
        "_shared_sync_keyring_approved_machine_ids",
        lambda _sessao: set(approved),
    )
    monkeypatch.setattr(
        shared_sync_keyring,
        "_machine_presence_list",
        lambda *_args: [{"machine_id": machine_id} for machine_id in sorted(present)],
        raising=False,
    )
    monkeypatch.setattr(
        shared_sync_keyring,
        "_shared_sync_keyring_read_secret",
        lambda target: secure_stores.setdefault(active_device["id"], {}).get(target, ""),
    )
    monkeypatch.setattr(
        shared_sync_keyring,
        "_shared_sync_keyring_write_secret",
        lambda target, value: secure_stores.setdefault(active_device["id"], {}).__setitem__(target, value),
    )


def test_envio_e_importacao_entre_duas_maquinas_transfere_todas_as_lojas(tmp_path, monkeypatch):
    origem = tmp_path / "maquina-origem"
    destino = tmp_path / "maquina-destino"
    ativo = {"root": origem}

    def tenant_path(client_id):
        path = ativo["root"] / "info" / str(client_id)
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    lojas_origem = [
        {
            "nome": nome,
            "store_id": f"store-{indice}",
            "integracoes": {
                "mercadolivre": {
                    "access_token": f"token-{indice}",
                    "refresh_token": f"refresh-{indice}",
                    "connected": True,
                }
            },
        }
        for indice, nome in enumerate(("JK Peças", "Uai Mineirinho", "Carlos José", "Deckas"), start=1)
    ]
    lojas_destino = [{"nome": "Loja antiga do destino", "integracoes": {}}]
    origem_path = origem / "info" / "000002"
    destino_path = destino / "info" / "000002"
    origem_path.mkdir(parents=True)
    destino_path.mkdir(parents=True)
    (origem_path / "lojas_config.json").write_text(json.dumps(lojas_origem), encoding="utf-8")
    (destino_path / "lojas_config.json").write_text(json.dumps(lojas_destino), encoding="utf-8")

    integracoes.configure_integracoes_context(
        pasta_info=str(tmp_path / "info"),
        get_tenant_path=tenant_path,
        normalizar_integracao_conectada=lambda _servico, dados: dados,
    )
    monkeypatch.setattr(shared_sync_collect_files, "get_tenant_path", tenant_path, raising=False)
    monkeypatch.setattr(shared_sync_apply_scope, "get_tenant_path", tenant_path, raising=False)

    db = _FakeFirestore()
    monkeypatch.setattr(shared_sync_remote, "_shared_sync_firestore_required", lambda: db)
    monkeypatch.setattr(shared_sync_remote, "_firebase_db", lambda: db, raising=False)
    monkeypatch.setattr(shared_sync_remote, "_firebase_deve_usar", lambda: True, raising=False)
    monkeypatch.setattr(shared_sync_remote, "_firebase_shared_sync_collection_name", lambda: "shared_sync")
    monkeypatch.setattr(shared_sync_remote, "_firebase_shared_sync_chunks_collection_name", lambda: "shared_sync_chunks")
    monkeypatch.setattr(shared_sync_remote, "_shared_sync_state_update", lambda *args, **kwargs: None)
    monkeypatch.setattr(shared_sync_machine, "_shared_sync_state_update", lambda *args, **kwargs: None)

    active_device = {"id": "pc:destino"}
    secure_stores = {"pc:origem": {}, "pc:destino": {}}
    monkeypatch.setattr(shared_sync_keyring, "_firebase_db", lambda: db)
    monkeypatch.setattr(shared_sync_keyring, "_firebase_deve_usar", lambda: True)
    monkeypatch.setattr(shared_sync_keyring, "_firebase_shared_sync_keyrings_collection_name", lambda: "shared_sync_keyrings")
    monkeypatch.setattr(
        shared_sync_keyring,
        "_shared_sync_keyring_approved_machine_ids",
        lambda _sessao: {"pc:origem", "pc:destino"},
    )
    monkeypatch.setattr(
        shared_sync_keyring,
        "_machine_presence_list",
        lambda *_args: [{"machine_id": "pc:origem"}, {"machine_id": "pc:destino"}],
        raising=False,
    )
    monkeypatch.setattr(
        shared_sync_keyring,
        "_shared_sync_keyring_read_secret",
        lambda target: secure_stores[active_device["id"]].get(target, ""),
    )
    monkeypatch.setattr(
        shared_sync_keyring,
        "_shared_sync_keyring_write_secret",
        lambda target, value: secure_stores[active_device["id"]].__setitem__(target, value),
    )

    sessao = {"username": "operador", "client_id": "000002", "machine_id": "pc:destino"}
    assert shared_sync_keyring._shared_sync_keyring_status(sessao, "pc:destino")["registered"] is True
    active_device["id"] = "pc:origem"
    sessao["machine_id"] = "pc:origem"
    assert shared_sync_keyring._shared_sync_keyring_status(sessao, "pc:origem")["registered"] is True
    enviado = shared_sync_machine._shared_sync_machine_push_scope(
        sessao,
        "lojas_integracoes",
        "pc:origem",
    )
    assert enviado["success"] is True
    assert enviado["stores_count"] == 4
    pointer = db.collection("shared_sync").data[enviado["id"]]
    assert pointer["encryption_key_id"]

    ativo["root"] = destino
    active_device["id"] = "pc:destino"
    sessao["machine_id"] = "pc:destino"
    recebido = shared_sync_machine._shared_sync_machine_pull_scope(
        sessao,
        "lojas_integracoes",
        force=True,
        machine_id="pc:destino",
    )

    lojas_recebidas = json.loads((destino_path / "lojas_config.json").read_text(encoding="utf-8"))
    assert recebido["success"] is True
    assert recebido["stores_count"] == 4
    assert [loja["nome"] for loja in lojas_recebidas] == [loja["nome"] for loja in lojas_origem]
    assert lojas_recebidas[0]["integracoes"]["mercadolivre"]["access_token"] == "token-1"


def test_keyring_nova_maquina_so_recebe_envelope_apos_reenvio_e_ignora_membro_injetado(monkeypatch):
    db = _FakeFirestore()
    active_device = {"id": "pc:origem"}
    secure_stores = {"pc:origem": {}, "pc:destino": {}}
    _configure_keyring_test(
        monkeypatch, db, active_device, secure_stores,
        approved={"pc:origem", "pc:destino"},
    )
    sessao = {"username": "operador", "client_id": "000002", "machine_id": "pc:origem"}

    assert shared_sync_keyring._shared_sync_keyring_status(sessao, "pc:origem")["registered"] is True
    source_key, key_id = shared_sync_keyring._shared_sync_keyring_key_for_push(sessao, "pc:origem")

    active_device["id"] = "pc:destino"
    sessao["machine_id"] = "pc:destino"
    status_destino = shared_sync_keyring._shared_sync_keyring_status(sessao, "pc:destino")
    assert status_destino["registered"] is True
    assert status_destino["ready"] is False
    assert status_destino["needs_send"] is True
    with pytest.raises(HTTPException) as missing:
        shared_sync_keyring._shared_sync_keyring_key_for_pull(sessao, "pc:destino", key_id)
    assert missing.value.status_code == 409
    assert "Enviar agora" in str(missing.value.detail)

    active_device["id"] = "pc:origem"
    sessao["machine_id"] = "pc:origem"
    source_identity = shared_sync_keyring._shared_sync_keyring_identity(sessao, "pc:origem")
    attacker_private = shared_sync_keyring.X25519PrivateKey.generate()
    injected_id = shared_sync_common._shared_sync_safe_doc_id("membro-nao-aprovado")
    db.collection("shared_sync_keyrings").data[injected_id] = {
        "id": injected_id,
        "kind": "member",
        "schema": 1,
        "keyring_id": source_identity["keyring_id"],
        "owner_client_hash": source_identity["owner_client_hash"],
        "owner_user_hash": source_identity["owner_user_hash"],
        "public_key": shared_sync_keyring._shared_sync_keyring_public_b64(attacker_private),
    }
    resent_key, resent_key_id = shared_sync_keyring._shared_sync_keyring_key_for_push(sessao, "pc:origem")
    assert resent_key_id == key_id
    assert resent_key == source_key
    injected_envelope_id = shared_sync_keyring._shared_sync_keyring_envelope_id(
        source_identity, injected_id, key_id,
    )
    assert injected_envelope_id not in db.collection("shared_sync_keyrings").data

    active_device["id"] = "pc:destino"
    sessao["machine_id"] = "pc:destino"
    received_key = shared_sync_keyring._shared_sync_keyring_key_for_pull(sessao, "pc:destino", key_id)
    assert received_key == source_key
    secure_stores["pc:destino"] = {
        target: value
        for target, value in secure_stores["pc:destino"].items()
        if "/data-key/" not in target
    }
    push_key_from_destination, destination_key_id = shared_sync_keyring._shared_sync_keyring_key_for_push(
        sessao, "pc:destino",
    )
    assert destination_key_id == key_id
    assert push_key_from_destination == source_key


def test_keyring_vincula_machine_id_do_request_ao_jwt(monkeypatch):
    db = _FakeFirestore()
    active_device = {"id": "pc:a"}
    secure_stores = {"pc:a": {}}
    _configure_keyring_test(monkeypatch, db, active_device, secure_stores, approved={"pc:a", "pc:b"})
    sessao = {"username": "operador", "client_id": "000002", "machine_id": "pc:a"}
    with pytest.raises(HTTPException) as mismatch:
        shared_sync_keyring._shared_sync_keyring_register(sessao, "pc:b")
    assert mismatch.value.status_code == 403
    assert db.collection("shared_sync_keyrings").data == {}


@pytest.mark.parametrize("route_name", ["heartbeat", "realtime", "session_refresh"])
def test_rotas_autenticadas_rejeitam_machine_id_diferente_do_jwt(monkeypatch, route_name):
    request = Request({
        "type": "http", "method": "POST", "path": "/", "headers": [],
        "client": ("127.0.0.1", 12345),
    })
    sessao = {"username": "admin", "client_id": "000002", "machine_id": "pc:jwt"}
    target = admin_usuarios_auth if route_name == "session_refresh" else admin_usuarios_presence
    monkeypatch.setattr(target, "_payload_sessao_por_authorization", lambda _authorization: dict(sessao))
    if route_name == "heartbeat":
        action = lambda: admin_usuarios_presence.user_machine_heartbeat(
            MachinePresenceHeartbeatRequest(machine_id="pc:forjada"),
            request,
            authorization="Bearer teste",
        )
    elif route_name == "realtime":
        action = lambda: admin_usuarios_presence.firebase_realtime_presence_session(
            request,
            machine_id="pc:forjada",
            authorization="Bearer teste",
        )
    else:
        action = lambda: admin_usuarios_auth.minha_sessao_auth(
            request,
            authorization="Bearer teste",
            machine_id="pc:forjada",
        )
    with pytest.raises(HTTPException) as mismatch:
        action()
    assert mismatch.value.status_code == 403


def test_machine_id_autenticado_exige_jwt_vinculado_e_usa_o_id_do_token():
    assert admin_usuarios_presence_core._authenticated_session_machine_id(
        {"machine_id": "pc:jwt"}, "",
    ) == "pc:jwt"
    with pytest.raises(HTTPException) as missing:
        admin_usuarios_presence_core._authenticated_session_machine_id({}, "pc:request")
    assert missing.value.status_code == 401
    assert "novamente" in str(missing.value.detail).lower()


def test_login_admin_full_persiste_machine_allowlist_sem_aplicar_limite(monkeypatch):
    users = _FakeCollection()
    users.data["admin"] = {
        "username": "admin",
        "client_id": "000002",
        "machine_id": "pc:antiga",
        "machine_ids": ["pc:antiga"],
        "max_machines": 1,
    }
    saved = []
    monkeypatch.setattr(admin_usuarios_login_core, "_firebase_collection", lambda: users)
    monkeypatch.setattr(admin_usuarios_login_core, "_firebase_doc_id", lambda username: username)
    monkeypatch.setattr(
        admin_usuarios_login_core, "_usuario_pode_logar_em_qualquer_dispositivo", lambda *_args: True,
    )
    monkeypatch.setattr(admin_usuarios_login_core, "_firebase_user_from_data", lambda _username, data: dict(data))
    monkeypatch.setattr(
        admin_usuarios_login_core,
        "_normalizar_lista_maquinas",
        lambda machine_ids, machine_id=None: list(machine_ids or ([machine_id] if machine_id else [])),
    )
    monkeypatch.setattr(
        admin_usuarios_login_core,
        "_salvar_usuarios_sql",
        lambda usuarios, source="": saved.append((usuarios, source)),
    )
    ok, message, machine_id = admin_usuarios_login_core._firebase_validar_e_registrar_maquina(
        "admin",
        {
            "username": "admin", "client_id": "000002", "machine_id": "pc:antiga",
            "machine_ids": ["pc:antiga"], "max_machines": 1,
        },
        {"full": True},
        "pc:nova",
    )
    assert (ok, message, machine_id) == (True, "", "pc:nova")
    assert users.data["admin"]["machine_ids"] == ["pc:antiga", "pc:nova"]
    assert saved and saved[0][1] == "firebase-cache"


def test_keyring_admin_persiste_allowlist_e_ignora_presence_e_membro_forjados(monkeypatch):
    db = _FakeFirestore()
    users = db.collection("users")
    users.data["admin"] = {
        "username": "admin",
        "client_id": "000002",
        "machine_id": "pc:recebedor",
        "machine_ids": ["pc:recebedor"],
    }
    active_device = {"id": "pc:admin"}
    secure_stores = {"pc:admin": {}, "pc:recebedor": {}}
    monkeypatch.setattr(shared_sync_keyring, "_firebase_db", lambda: db)
    monkeypatch.setattr(shared_sync_keyring, "_firebase_deve_usar", lambda: True)
    monkeypatch.setattr(shared_sync_keyring, "_firebase_collection", lambda: users)
    monkeypatch.setattr(shared_sync_keyring, "_firebase_doc_id", lambda username: username)
    monkeypatch.setattr(
        shared_sync_keyring, "_firebase_shared_sync_keyrings_collection_name", lambda: "shared_sync_keyrings",
    )
    monkeypatch.setattr(
        shared_sync_keyring,
        "_machine_presence_list",
        lambda *_args: [{"machine_id": "pc:admin"}, {"machine_id": "pc:forjada"}],
        raising=False,
    )
    monkeypatch.setattr(
        shared_sync_keyring,
        "_shared_sync_keyring_read_secret",
        lambda target: secure_stores[active_device["id"]].get(target, ""),
    )
    monkeypatch.setattr(
        shared_sync_keyring,
        "_shared_sync_keyring_write_secret",
        lambda target, value: secure_stores[active_device["id"]].__setitem__(target, value),
    )
    sessao = {
        "username": "admin", "client_id": "000002", "machine_id": "pc:admin",
        "is_admin": True, "permissions": {"full": True},
    }
    assert shared_sync_keyring._shared_sync_keyring_status(sessao, "pc:admin")["registered"] is True
    assert set(users.data["admin"]["machine_ids"]) == {"pc:recebedor", "pc:admin"}

    active_device["id"] = "pc:recebedor"
    sessao["machine_id"] = "pc:recebedor"
    assert shared_sync_keyring._shared_sync_keyring_status(sessao, "pc:recebedor")["registered"] is True

    active_device["id"] = "pc:admin"
    sessao["machine_id"] = "pc:admin"
    identity = shared_sync_keyring._shared_sync_keyring_identity(sessao, "pc:admin")
    forged_member_id = shared_sync_common._shared_sync_safe_doc_id(
        "shared-sync-keyring-member", "000002", "admin", "pc:forjada",
    )
    forged_private = shared_sync_keyring.X25519PrivateKey.generate()
    db.collection("shared_sync_keyrings").data[forged_member_id] = {
        "id": forged_member_id,
        "kind": "member",
        "schema": 1,
        "keyring_id": identity["keyring_id"],
        "owner_client_hash": identity["owner_client_hash"],
        "owner_user_hash": identity["owner_user_hash"],
        "public_key": shared_sync_keyring._shared_sync_keyring_public_b64(forged_private),
    }
    authorized = shared_sync_keyring._shared_sync_keyring_authorized_machine_ids(sessao, "pc:admin")
    assert authorized == {"pc:admin", "pc:recebedor"}
    _data_key, key_id = shared_sync_keyring._shared_sync_keyring_key_for_push(sessao, "pc:admin")
    receiver_member_id = shared_sync_common._shared_sync_safe_doc_id(
        "shared-sync-keyring-member", "000002", "admin", "pc:recebedor",
    )
    receiver_envelope = shared_sync_keyring._shared_sync_keyring_envelope_id(
        identity, receiver_member_id, key_id,
    )
    forged_envelope = shared_sync_keyring._shared_sync_keyring_envelope_id(
        identity, forged_member_id, key_id,
    )
    assert receiver_envelope in db.collection("shared_sync_keyrings").data
    assert forged_envelope not in db.collection("shared_sync_keyrings").data


def test_keyring_criacao_concorrente_elege_uma_unica_chave(monkeypatch):
    db = _FakeFirestore()
    monkeypatch.setattr(shared_sync_keyring, "_firebase_shared_sync_keyrings_collection_name", lambda: "shared_sync_keyrings")
    identity = shared_sync_keyring._shared_sync_keyring_identity(
        {"username": "operador", "client_id": "000002"}, "pc:origem",
    )
    key_ids = ["a" * 16, "b" * 16]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(
            lambda key_id: shared_sync_keyring._shared_sync_keyring_create_root(db, identity, key_id),
            key_ids,
        ))
    elected = {result[0]["active_key_id"] for result in results}
    assert len(elected) == 1
    assert sum(1 for _root, won in results if won) == 1
    root = db.collection("shared_sync_keyrings").data[identity["keyring_id"]]
    assert root["active_key_id"] in key_ids


def test_keyring_rejeita_owner_adulterado_e_usuario_cruzado(monkeypatch):
    db = _FakeFirestore()
    active_device = {"id": "pc:a"}
    secure_stores = {"pc:a": {}, "pc:b": {}}
    _configure_keyring_test(monkeypatch, db, active_device, secure_stores, approved={"pc:a", "pc:b"})
    sessao_a = {"username": "usuario-a", "client_id": "000002", "machine_id": "pc:a"}
    shared_sync_keyring._shared_sync_keyring_status(sessao_a, "pc:a")
    data_key, key_id = shared_sync_keyring._shared_sync_keyring_key_for_push(sessao_a, "pc:a")
    remote_dump = json.dumps(db.collection("shared_sync_keyrings").data, sort_keys=True)
    assert shared_sync_keyring._shared_sync_keyring_encode(data_key) not in remote_dump
    assert base64.b64encode(data_key).decode("ascii") not in remote_dump
    assert data_key.hex() not in remote_dump
    status_dump = json.dumps(shared_sync_keyring._shared_sync_keyring_status(sessao_a, "pc:a"), sort_keys=True)
    assert shared_sync_keyring._shared_sync_keyring_encode(data_key) not in status_dump

    active_device["id"] = "pc:b"
    sessao_b = {"username": "usuario-b", "client_id": "000002", "machine_id": "pc:b"}
    with pytest.raises(HTTPException) as cross_user:
        shared_sync_keyring._shared_sync_keyring_key_for_pull(sessao_b, "pc:b", key_id)
    assert cross_user.value.status_code == 409

    identity_a = shared_sync_keyring._shared_sync_keyring_identity(sessao_a, "pc:a")
    db.collection("shared_sync_keyrings").data[identity_a["keyring_id"]]["owner_user_hash"] = "adulterado"
    active_device["id"] = "pc:a"
    with pytest.raises(HTTPException) as adulterado:
        shared_sync_keyring._shared_sync_keyring_key_for_push(sessao_a, "pc:a")
    assert adulterado.value.status_code == 409


def test_keyring_detecta_replay_ou_adulteracao_do_envelope(monkeypatch):
    db = _FakeFirestore()
    active_device = {"id": "pc:destino"}
    secure_stores = {"pc:origem": {}, "pc:destino": {}}
    _configure_keyring_test(monkeypatch, db, active_device, secure_stores, approved={"pc:origem", "pc:destino"})
    sessao = {"username": "operador", "client_id": "000002", "machine_id": "pc:destino"}
    shared_sync_keyring._shared_sync_keyring_status(sessao, "pc:destino")
    active_device["id"] = "pc:origem"
    sessao["machine_id"] = "pc:origem"
    shared_sync_keyring._shared_sync_keyring_status(sessao, "pc:origem")
    _data_key, key_id = shared_sync_keyring._shared_sync_keyring_key_for_push(sessao, "pc:origem")

    identity = shared_sync_keyring._shared_sync_keyring_identity(sessao, "pc:destino")
    envelope_id = shared_sync_keyring._shared_sync_keyring_envelope_id(
        identity, identity["member_id"], key_id,
    )
    record = db.collection("shared_sync_keyrings").data[envelope_id]
    record["envelope"]["ciphertext"] = shared_sync_keyring._shared_sync_keyring_encode(b"adulterado")
    active_device["id"] = "pc:destino"
    sessao["machine_id"] = "pc:destino"
    with pytest.raises(HTTPException) as tampered:
        shared_sync_keyring._shared_sync_keyring_key_for_pull(sessao, "pc:destino", key_id)
    assert tampered.value.status_code == 409


def test_snapshot_legado_sem_key_id_usa_somente_chave_legada(monkeypatch):
    db = _FakeFirestore()
    secret = b"chave-legada-configurada"
    bundle_id = "bundle-legado"
    bundle = b"pacote-v2-legado"
    encrypted = shared_sync_remote._shared_sync_encrypt_bundle(bundle_id, bundle, secret)
    snapshot_id = "snapshot-legado"
    db.collection("shared_sync_chunks").data[f"{snapshot_id}_00000"] = {
        "bundle_id": snapshot_id,
        "data": shared_sync_keyring._shared_sync_keyring_encode(encrypted),
    }
    meta = {
        "id": bundle_id,
        "pointer_id": bundle_id,
        "snapshot_id": snapshot_id,
        "schema": 2,
        "encrypted": True,
        "chunk_count": 1,
        "bundle_sha256": hashlib.sha256(encrypted).hexdigest(),
    }
    monkeypatch.setattr(shared_sync_remote, "_shared_sync_firestore_required", lambda: db)
    monkeypatch.setattr(shared_sync_remote, "_firebase_shared_sync_chunks_collection_name", lambda: "shared_sync_chunks")
    monkeypatch.setattr(shared_sync_remote, "_shared_sync_encryption_secret", lambda: secret)
    opened, _ = shared_sync_remote._shared_sync_obter_bundle_por_id(bundle_id, meta)
    assert opened == bundle

    monkeypatch.setattr(
        shared_sync_remote,
        "_shared_sync_encryption_secret",
        lambda: (_ for _ in ()).throw(HTTPException(status_code=503, detail="chave ausente")),
    )
    with pytest.raises(HTTPException) as missing:
        shared_sync_remote._shared_sync_obter_bundle_por_id(bundle_id, meta)
    assert missing.value.status_code == 503

    invalid_meta = dict(meta, encryption_key_id="fora-do-formato")
    monkeypatch.setattr(shared_sync_remote, "_shared_sync_encryption_secret", lambda: secret)
    with pytest.raises(HTTPException) as invalid_key_id:
        shared_sync_remote._shared_sync_obter_bundle_por_id(
            bundle_id,
            invalid_meta,
            key_context={"sessao": {"username": "operador"}, "machine_id": "pc"},
        )
    assert invalid_key_id.value.status_code == 409


def _configure_remote_push_for_test(monkeypatch, db, bundle):
    manifest = {
        "schema": 2,
        "created_at": "2026-07-15T12:00:00Z",
        "snapshot_hash": "snapshot-hash",
        "file_count": 1,
        "item_count": 0,
        "item_keys": [],
        "delta": False,
        "files": [{"relative_path": "lojas_config.json", "size": len(bundle), "sha256": hashlib.sha256(bundle).hexdigest()}],
    }
    monkeypatch.setattr(shared_sync_remote, "_shared_sync_firestore_required", lambda: db)
    monkeypatch.setattr(shared_sync_remote, "_firebase_shared_sync_collection_name", lambda: "shared_sync")
    monkeypatch.setattr(shared_sync_remote, "_firebase_shared_sync_chunks_collection_name", lambda: "shared_sync_chunks")
    monkeypatch.setattr(shared_sync_remote, "_shared_sync_montar_pacote", lambda *args, **kwargs: (bundle, manifest, []))
    monkeypatch.setattr(shared_sync_remote, "_shared_sync_state_update", lambda *args, **kwargs: None)
    monkeypatch.setattr(shared_sync_remote, "_shared_sync_cleanup_old_snapshots", lambda *args, **kwargs: None)
    monkeypatch.setattr(shared_sync_remote, "_shared_sync_encryption_secret", lambda: b"segredo-remoto-de-teste")
    monkeypatch.setattr(shared_sync_remote, "SHARED_SYNC_CHUNK_CHARS", 40)


def test_upload_interrompido_preserva_ultimo_snapshot_valido(monkeypatch):
    db = _FakeFirestore()
    meta_name = "shared_sync"
    chunk_name = "shared_sync_chunks"
    db.collection(meta_name).data["bundle"] = {"id": "bundle", "snapshot_id": "snapshot-anterior", "schema": 2, "encrypted": True}
    _configure_remote_push_for_test(monkeypatch, db, b"credencial-secreta" * 20)
    db.collection(chunk_name).fail_get = lambda doc_id: doc_id.endswith("_00001")
    with pytest.raises(HTTPException) as exc:
        shared_sync_remote._shared_sync_push_scope(
            "000002", "cadastro", {"username": "origem"}, bundle_id="bundle",
        )
    assert exc.value.status_code == 502
    assert db.collection(meta_name).data["bundle"]["snapshot_id"] == "snapshot-anterior"
    assert not any(doc_id.startswith("bundle__") for doc_id in db.collection(meta_name).data)


def test_upload_v2_publica_chunks_cifrados_antes_de_trocar_ponteiro(monkeypatch):
    db = _FakeFirestore()
    secret = b"access_token=token-remoto-que-nao-pode-vazar" * 8
    _configure_remote_push_for_test(monkeypatch, db, secret)
    db.collection("shared_sync_chunks").data["bundle_00000"] = {"bundle_id": "bundle", "data": "v1-legivel"}
    result = shared_sync_remote._shared_sync_push_scope(
        "000002", "cadastro", {"username": "origem"}, bundle_id="bundle",
    )
    meta = db.collection("shared_sync").data
    pointer = meta["bundle"]
    assert result["success"] is True
    assert pointer["schema"] == 2
    assert pointer["encrypted"] is True
    assert meta[pointer["snapshot_id"]]["status"] == "complete"
    remote_text = json.dumps(db.collection("shared_sync_chunks").data)
    assert "token-remoto-que-nao-pode-vazar" not in remote_text
    assert "v1-legivel" not in remote_text


def test_retencao_preserva_dois_snapshots_e_remove_antigos_apos_sete_dias(monkeypatch):
    db = _FakeFirestore()
    monkeypatch.setattr(shared_sync_remote, "_firebase_shared_sync_collection_name", lambda: "shared_sync")
    monkeypatch.setattr(shared_sync_remote, "_firebase_shared_sync_chunks_collection_name", lambda: "shared_sync_chunks")
    now = int(time.time())
    metas = db.collection("shared_sync").data
    metas["current"] = {"pointer_id": "bundle", "status": "complete", "updated_ts": now}
    metas["second"] = {"pointer_id": "bundle", "status": "complete", "updated_ts": now - 10}
    metas["old"] = {"pointer_id": "bundle", "status": "complete", "updated_ts": now - (8 * 24 * 60 * 60)}
    db.collection("shared_sync_chunks").data["old_00000"] = {"bundle_id": "old", "data": "cipher"}
    shared_sync_remote._shared_sync_cleanup_old_snapshots(db, "bundle", "current")
    assert "current" in metas
    assert "second" in metas
    assert "old" not in metas
    assert "old_00000" not in db.collection("shared_sync_chunks").data


def test_escritas_concorrentes_de_lojas_permanecem_json_atomico(tmp_path):
    info = tmp_path / "info"

    def tenant_path(client_id):
        path = info / str(client_id)
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    integracoes.configure_integracoes_context(
        pasta_info=str(info), get_tenant_path=tenant_path, normalizar_integracao_conectada=lambda _s, data: data,
    )

    def save(index):
        lojas = [
            {"nome": f"Loja {index}-{item}", "integracoes": {"mercadolivre": {"access_token": f"token-{index}-{item}"}}}
            for item in range(4)
        ]
        integracoes.salvar_lojas("000002", lojas)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(save, range(32)))
    path = info / "000002" / "lojas_config.json"
    backup = info / "000002" / "lojas_config.json.bak"
    assert isinstance(json.loads(path.read_text(encoding="utf-8")), list)
    assert isinstance(json.loads(backup.read_text(encoding="utf-8")), list)


def test_escrita_atomica_de_lojas_repete_bloqueio_transitorio(tmp_path, monkeypatch):
    destino = tmp_path / "lojas_config.json"
    real_replace = integracoes.os.replace
    calls = 0

    def transient_replace(source, target):
        nonlocal calls
        calls += 1
        if calls < 3:
            raise PermissionError(5, "bloqueio transitorio")
        return real_replace(source, target)

    monkeypatch.setattr(integracoes.os, "replace", transient_replace)

    integracoes._integracoes_escrever_lojas_config_atomico(
        str(destino),
        [{"nome": "Loja segura", "integracoes": {}}],
    )

    assert calls == 3
    assert json.loads(destino.read_text(encoding="utf-8"))[0]["nome"] == "Loja segura"


def test_desconexao_cria_tombstone_sem_remover_registro(tmp_path):
    info = tmp_path / "info"

    def tenant_path(client_id):
        path = info / str(client_id)
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    integracoes.configure_integracoes_context(
        pasta_info=str(info), get_tenant_path=tenant_path, normalizar_integracao_conectada=lambda _s, data: data,
    )
    integracoes.salvar_lojas("000002", [{"nome": "Loja", "integracoes": {"bling": {"access_token": "token", "connected": True}}}])
    integracoes.desconectar_api_loja("000002", "Loja", "bling")
    lojas = json.loads((info / "000002" / "lojas_config.json").read_text(encoding="utf-8"))
    tombstones = json.loads((info / "000002" / "lojas_sync_tombstones.json").read_text(encoding="utf-8"))
    assert lojas[0]["store_id"]
    assert lojas[0]["integracoes"]["bling"]["connected"] is False
    assert tombstones[-1]["type"] == "integration"
    assert tombstones[-1]["store_id"] == lojas[0]["store_id"]


def test_versoes_fonte_e_electron_estao_alinhadas_com_a_release():
    root_package = json.loads(open("package.json", "r", encoding="utf-8").read())
    electron_package = json.loads(open("electron_app/package.json", "r", encoding="utf-8").read())
    backend_source = open("backend_api.py", "r", encoding="utf-8-sig").read()
    assert root_package["version"] == "1.0.123"
    assert electron_package["version"] == root_package["version"]
    # O minimo do backend pode permanecer anterior para nao derrubar clientes
    # durante o rollout em duas ondas.
    assert 'VERSAO_MINIMA_APP_PADRAO = "1.0.102"' in backend_source
