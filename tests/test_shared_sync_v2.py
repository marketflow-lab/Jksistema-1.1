import base64
import asyncio
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
from backend.services import integracoes_api
from backend.services import shared_sync_apply_scope
from backend.services import shared_sync_common
from backend.services import shared_sync_bundle
from backend.services import shared_sync_collect_files
from backend.services import shared_sync_config
from backend.services import shared_sync_machine
from backend.services import shared_sync_machine_endpoints
from backend.services import shared_sync_keyring
from backend.services import shared_sync_merge_integracoes
from backend.services import shared_sync_merge_sqlite
from backend.services import shared_sync_merge_user_data
from backend.services import shared_sync_operations
from backend.services import shared_sync_remote
from backend.services import shared_sync_user_endpoints
from backend.services import shared_sync_user_pairs


def _entry(relative_path: str, data: bytes) -> dict:
    return {
        "relative_path": relative_path,
        "data": data,
        "size": len(data),
        "mtime": 1,
        "sha256": hashlib.sha256(data).hexdigest(),
        "item_keys": [],
    }


def _scope_bundle(scope: str, arquivos: list[tuple[str, bytes]]) -> bytes:
    manifest_files = [
        {
            "relative_path": rel,
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
        for rel, data in arquivos
    ]
    manifest = {
        "schema": 2,
        "scope": scope,
        "file_count": len(manifest_files),
        "snapshot_hash": shared_sync_bundle._shared_sync_snapshot_hash(manifest_files),
        "files": manifest_files,
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as arquivo:
        arquivo.writestr("manifest.json", json.dumps(manifest))
        for rel, data in arquivos:
            arquivo.writestr(f"files/{rel}", data)
    return buffer.getvalue()


def _scope_bundle_v1(scope: str, arquivos: list[tuple[str, bytes]]) -> tuple[bytes, str]:
    manifest_files = [
        {
            "relative_path": rel,
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
        for rel, data in arquivos
    ]
    snapshot_hash = shared_sync_bundle._shared_sync_snapshot_hash(manifest_files)
    manifest = {
        "schema": 1,
        "scope": scope,
        "file_count": len(manifest_files),
        "snapshot_hash": snapshot_hash,
        "files": manifest_files,
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as arquivo:
        arquivo.writestr("manifest.json", json.dumps(manifest))
        for rel, data in arquivos:
            arquivo.writestr(f"files/{rel}", data)
    return buffer.getvalue(), snapshot_hash


def _lojas_bundle(lojas: list[dict]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as arquivo:
        arquivo.writestr("files/lojas_config.json", json.dumps(lojas))
    return buffer.getvalue()


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
        lambda _sessao, scope, **_kwargs: (
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


def test_machine_config_save_returns_status_bound_to_current_machine(monkeypatch):
    from backend.schemas.shared_sync import SharedSyncMachineConfigRequest
    session = {"username": "operador", "client_id": "000002"}
    monkeypatch.setattr(shared_sync_machine_endpoints, "_shared_sync_session", lambda *args: session)
    monkeypatch.setattr(shared_sync_machine_endpoints, "_shared_sync_machine_config_save", lambda *args: {})
    received = []
    monkeypatch.setattr(shared_sync_machine_endpoints, "_shared_sync_machine_status_payload",
                        lambda _session, machine_id: received.append(machine_id) or {"success": True})
    payload = SharedSyncMachineConfigRequest(enabled=True, scopes=["cadastro"], machine_id="pc:atual")
    result = shared_sync_machine_endpoints.shared_sync_machine_salvar_config(
        payload, authorization="Bearer fixture", client_id="000002")
    assert result["success"] is True
    assert received == ["pc:atual"]


def test_machine_preview_distinguishes_missing_snapshot_from_firebase_failure(monkeypatch):
    from backend.schemas.shared_sync import SharedSyncPreviewRequest
    session = {"username": "operador", "client_id": "000002"}
    monkeypatch.setattr(shared_sync_machine_endpoints, "_shared_sync_session", lambda *args: session)
    monkeypatch.setattr(shared_sync_machine_endpoints, "_shared_sync_machine_resolver_scopes",
                        lambda *args, **kwargs: ["cadastro"])
    monkeypatch.setattr(shared_sync_machine_endpoints, "_shared_sync_machine_bundle_ids",
                        lambda *args: {"cadastro": "bundle"})
    monkeypatch.setattr(shared_sync_machine_endpoints, "_shared_sync_remote_meta_by_id",
                        lambda *_args, **_kwargs: None)
    with pytest.raises(HTTPException) as missing:
        shared_sync_machine_endpoints.shared_sync_machine_preview(
            SharedSyncPreviewRequest(direction="pull", scopes=["cadastro"], machine_id="pc:atual"),
            authorization="Bearer fixture", client_id="000002")
    assert missing.value.status_code == 404
    monkeypatch.setattr(shared_sync_machine_endpoints, "_shared_sync_remote_meta_by_id",
                        lambda *_args, **_kwargs: (_ for _ in ()).throw(
                            HTTPException(503, "Falha ao consultar o Firebase; o snapshot remoto não pôde ser verificado.")))
    with pytest.raises(HTTPException) as unavailable:
        shared_sync_machine_endpoints.shared_sync_machine_preview(
            SharedSyncPreviewRequest(direction="pull", scopes=["cadastro"], machine_id="pc:atual"),
            authorization="Bearer fixture", client_id="000002")
    assert unavailable.value.status_code == 503
    assert "Firebase" in str(unavailable.value.detail)


def test_legacy_firebase_import_label_does_not_claim_to_activate_central():
    source = open("static/admin_usuarios.html", "r", encoding="utf-8-sig").read()
    assert "Importar cópia legada do Firebase" in source
    assert "não ativa a Central de Contas" in source


def test_pacote_v2_criptografa_credenciais_sem_texto_legivel(
    tmp_path,
    monkeypatch,
):
    _configure_integracoes_sync_test(tmp_path)
    secret = b"segredo-local-de-teste-com-entropia"
    monkeypatch.setattr(integracoes, "carregar_lojas", lambda _client_id: [])
    monkeypatch.setattr(
        integracoes,
        "_integracoes_validar_estado_atual_para_envio",
        lambda _client_id: None,
    )
    monkeypatch.setattr(
        integracoes,
        "_integracoes_validar_tombstones_contra_lojas_para_envio",
        lambda _client_id, _lojas: None,
    )
    monkeypatch.setattr(shared_sync_remote, "_shared_sync_encryption_secret", lambda: secret)
    credentials = b'{"access_token":"token-super-secreto","refresh_token":"refresh-secreto","state":"temporario","code":"oauth-code"}'
    monkeypatch.setattr(
        shared_sync_bundle,
        "_shared_sync_coletar_arquivos",
        lambda *args, **kwargs: ([_entry("lojas_config.json", credentials)], []),
    )
    bundle, manifest, _ = shared_sync_bundle._shared_sync_montar_pacote_locked(
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


def test_oauth_draft_transitorio_nao_altera_fingerprint_nem_pacote(
    tmp_path,
    monkeypatch,
):
    _configure_integracoes_sync_test(tmp_path)
    monkeypatch.setattr(integracoes, "carregar_lojas", lambda _client_id: [])
    monkeypatch.setattr(
        integracoes,
        "_integracoes_validar_estado_atual_para_envio",
        lambda _client_id: None,
    )
    monkeypatch.setattr(
        integracoes,
        "_integracoes_validar_tombstones_contra_lojas_para_envio",
        lambda _client_id, _lojas: None,
    )
    base = {
        "lojas": [
            {
                "nome": "Loja",
                "integracoes": {"mercadolivre": {"connected": False}},
            }
        ]
    }
    com_draft = json.loads(json.dumps(base))
    com_draft["lojas"][0]["integracoes"]["mercadolivre"]["oauth_draft"] = {
        "state": "state-transitorio",
        "app_id": "app-transitorio",
        "client_secret": "secret-transitorio",
    }
    raw_base = json.dumps(base, ensure_ascii=False).encode("utf-8")
    raw_draft = json.dumps(com_draft, ensure_ascii=False).encode("utf-8")
    sessao = {"client_id": "000002", "username": "operador"}

    def fingerprint(data):
        monkeypatch.setattr(
            shared_sync_operations,
            "_shared_sync_coletar_arquivos",
            lambda *_args, **_kwargs: ([_entry("lojas_config.json", data)], []),
        )
        return shared_sync_operations._shared_sync_local_fingerprint(
            sessao,
            ["lojas_integracoes"],
        )

    hash_base, files_base = fingerprint(raw_base)
    hash_draft, files_draft = fingerprint(raw_draft)
    assert hash_draft == hash_base
    assert files_draft == files_base

    monkeypatch.setattr(
        shared_sync_bundle,
        "_shared_sync_coletar_arquivos",
        lambda *_args, **_kwargs: ([_entry("lojas_config.json", raw_draft)], []),
    )
    monkeypatch.setattr(
        shared_sync_bundle,
        "_shared_sync_coletar_arquivos_delta",
        lambda *_args, **_kwargs: ([_entry("lojas_config.json", raw_draft)], [], []),
    )
    full_bundle, full_manifest, _ = shared_sync_bundle._shared_sync_montar_pacote_locked(
        "000002", "lojas_integracoes", "operador", user_only=True,
    )
    delta_bundle, delta_manifest, _ = shared_sync_bundle._shared_sync_montar_pacote_locked(
        "000002", "lojas_integracoes", "operador", user_only=True, known_keys=set(),
    )
    assert full_manifest["snapshot_hash"] == hash_base["lojas_integracoes"]
    assert delta_manifest["snapshot_hash"] == hash_base["lojas_integracoes"]
    for bundle in (full_bundle, delta_bundle):
        with zipfile.ZipFile(io.BytesIO(bundle), "r") as zf:
            persisted = zf.read("files/lojas_config.json")
        assert b"oauth_draft" not in persisted
        assert b"state-transitorio" not in persisted
        assert b"secret-transitorio" not in persisted
    assert b"oauth_draft" in raw_draft


@pytest.mark.parametrize(
    ("servico", "transitorio", "chave_transitoria"),
    [
        (
            "mercadolivre",
            {
                "oauth_draft": {
                    "state": "state-transitorio",
                    "app_id": "app-transitorio",
                    "client_secret": "secret-transitorio",
                }
            },
            "oauth_draft",
        ),
        (
            "bling",
            {"oauth_pending_state": "state-transitorio"},
            "oauth_pending_state",
        ),
    ],
)
def test_oauth_transitorio_persistido_nao_altera_fingerprint_nem_pacote(
    tmp_path,
    monkeypatch,
    servico,
    transitorio,
    chave_transitoria,
):
    info = tmp_path / "info"

    def tenant_path(client_id):
        path = info / str(client_id)
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    monkeypatch.setattr(integracoes, "PASTA_INFO", str(info))
    monkeypatch.setattr(integracoes, "_get_tenant_path", tenant_path)
    monkeypatch.setattr(
        integracoes,
        "_normalizar_integracao_conectada",
        lambda _servico, dados: dados,
    )
    integracoes.salvar_lojas(
        "000002",
        [
            {
                "nome": "Loja",
                "integracoes": {servico: {"connected": False}},
            }
        ],
    )
    store_id = integracoes.carregar_lojas("000002")[0]["store_id"]
    lojas_path = info / "000002" / "lojas_config.json"

    def coletar(*_args, **_kwargs):
        return [_entry("lojas_config.json", lojas_path.read_bytes())], []

    monkeypatch.setattr(
        shared_sync_operations,
        "_shared_sync_coletar_arquivos",
        coletar,
    )
    monkeypatch.setattr(
        shared_sync_bundle,
        "_shared_sync_coletar_arquivos",
        coletar,
    )
    sessao = {"client_id": "000002", "username": "operador"}

    def capturar():
        hashes, _files = shared_sync_operations._shared_sync_local_fingerprint(
            sessao,
            ["lojas_integracoes"],
        )
        bundle, manifest, _warnings = shared_sync_bundle._shared_sync_montar_pacote(
            "000002",
            "lojas_integracoes",
            "operador",
            user_only=True,
        )
        with zipfile.ZipFile(io.BytesIO(bundle), "r") as zf:
            arquivo = zf.read("files/lojas_config.json")
        return hashes["lojas_integracoes"], manifest["snapshot_hash"], arquivo

    antes = capturar()
    integracoes.atualizar_api_loja(
        "000002",
        "Loja",
        servico,
        transitorio,
        store_id=store_id,
        require_existing=True,
    )
    depois = capturar()

    assert depois == antes
    assert chave_transitoria.encode() not in depois[2]
    assert b"state-transitorio" not in depois[2]


def test_pacote_lojas_nao_exporta_tombstones_locais(tmp_path, monkeypatch):
    _info, tenant = _configure_integracoes_sync_test(tmp_path)
    monkeypatch.setattr(integracoes, "carregar_lojas", lambda _client_id: [])
    monkeypatch.setattr(
        integracoes,
        "_integracoes_validar_estado_atual_para_envio",
        lambda _client_id: None,
    )
    monkeypatch.setattr(
        integracoes,
        "_integracoes_validar_tombstones_contra_lojas_para_envio",
        lambda _client_id, _lojas: None,
    )
    lojas_target = tenant / "lojas_config.json"
    lojas_bytes = b"[]"
    lojas_target.write_bytes(lojas_bytes)
    tombstone_target = tenant / "lojas_sync_tombstones.json"
    tombstone_bytes = b'[{"key":"store:local:","version":1}]'
    tombstone_target.write_bytes(tombstone_bytes)
    tombstone_entry = {
        "relative_path": "lojas_sync_tombstones.json",
        "abs_path": str(tombstone_target),
        "mtime": 1,
        "size": len(tombstone_bytes),
        "sha256": hashlib.sha256(tombstone_bytes).hexdigest(),
        "item_keys": [],
    }

    def coletar(*_args, **_kwargs):
        return [
            _entry("lojas_config.json", lojas_bytes),
            dict(tombstone_entry),
        ], []

    monkeypatch.setattr(shared_sync_bundle, "_shared_sync_coletar_arquivos", coletar)
    bundle, manifest, _warnings = shared_sync_bundle._shared_sync_montar_pacote(
        "000002", "lojas_integracoes", "operador",
    )
    with zipfile.ZipFile(io.BytesIO(bundle), "r") as arquivo:
        nomes = arquivo.namelist()

    assert "files/lojas_config.json" in nomes
    assert "files/lojas_sync_tombstones.json" not in nomes
    assert [item["relative_path"] for item in manifest["files"]] == [
        "lojas_config.json"
    ]


def test_pacote_lojas_recupera_backup_antes_de_congelar_snapshot(
    tmp_path,
    monkeypatch,
):
    info = tmp_path / "info"
    tenant = info / "000002"
    tenant.mkdir(parents=True)

    integracoes.configure_integracoes_context(
        pasta_info=str(info),
        get_tenant_path=lambda _client_id: str(tenant),
        normalizar_integracao_conectada=lambda _servico, dados: dados,
    )
    nome = "Loja recuperada"
    store_id = integracoes._integracoes_store_id("000002", {"nome": nome})
    (tenant / "lojas_config.json").write_text("[]", encoding="utf-8")
    (tenant / "lojas_config.json.bak").write_text(
        json.dumps([{
            "nome": nome,
            "integracoes": {
                "bling": {
                    "access_token": "access-recuperado",
                    "refresh_token": "refresh-recuperado",
                },
            },
        }]),
        encoding="utf-8",
    )

    def coletar(*_args, **_kwargs):
        data = (tenant / "lojas_config.json").read_bytes()
        return [_entry("lojas_config.json", data)], []

    monkeypatch.setattr(shared_sync_bundle, "_shared_sync_coletar_arquivos", coletar)

    bundle, _manifest, _warnings = shared_sync_bundle._shared_sync_montar_pacote(
        "000002",
        "lojas_integracoes",
        "operador",
    )

    with zipfile.ZipFile(io.BytesIO(bundle), "r") as arquivo:
        lojas = json.loads(arquivo.read("files/lojas_config.json"))
    assert lojas[0]["store_id"]
    assert lojas[0]["store_id"] != store_id
    assert lojas[0]["integracoes"]["bling"]["access_token"] == "access-recuperado"
    assert json.loads((tenant / "lojas_config.json").read_text(encoding="utf-8")) == lojas


def test_pacote_lojas_materializa_migracao_global_antes_da_coleta(
    tmp_path,
    monkeypatch,
):
    info = tmp_path / "info"
    info.mkdir()
    tenant = info / "000002"

    def tenant_path(_client_id):
        tenant.mkdir(parents=True, exist_ok=True)
        return str(tenant)

    integracoes.configure_integracoes_context(
        pasta_info=str(info),
        get_tenant_path=tenant_path,
        normalizar_integracao_conectada=lambda _servico, dados: dados,
    )
    nome = "Loja global atribuida"
    store_id = integracoes._integracoes_store_id("000002", {"nome": nome})
    (info / "lojas_config.json").write_text(
        json.dumps([{
            "nome": nome,
            "store_id": store_id,
            "integracoes": {"mercadolivre": {"access_token": "access-local"}},
        }]),
        encoding="utf-8",
    )

    def coletar(*_args, **_kwargs):
        data = (
            (tenant / "lojas_config.json").read_bytes()
            if (tenant / "lojas_config.json").exists()
            else b"[]"
        )
        return [_entry("lojas_config.json", data)], []

    monkeypatch.setattr(shared_sync_bundle, "_shared_sync_coletar_arquivos", coletar)

    bundle, _manifest, _warnings = shared_sync_bundle._shared_sync_montar_pacote(
        "000002",
        "lojas_integracoes",
        "operador",
    )

    with zipfile.ZipFile(io.BytesIO(bundle), "r") as arquivo:
        lojas = json.loads(arquivo.read("files/lojas_config.json"))
    assert lojas == []
    assert (info / "lojas_config.json").exists()


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
    remote = {"lojas_integracoes": "snapshot-a|hash-remoto|bundle-a"}
    monkeypatch.setattr(
        shared_sync_operations,
        "_shared_sync_remote_fingerprint",
        lambda *args: (dict(remote), {"lojas_integracoes": {}}),
    )
    preview_remoto = shared_sync_operations._shared_sync_create_preview(
        sessao,
        kind="machine",
        resource_id="self",
        direction="push",
        scopes=["lojas_integracoes"],
        bundle_ids={"lojas_integracoes": "bundle"},
    )
    remote["lojas_integracoes"] = "snapshot-b|hash-novo|bundle-b"
    with pytest.raises(HTTPException) as remoto_changed:
        shared_sync_operations._shared_sync_require_operation(
            preview_remoto["operation_id"],
            sessao,
            kind="machine",
            resource_id="self",
            direction="push",
            scopes=["lojas_integracoes"],
            bundle_ids={"lojas_integracoes": "bundle"},
        )
    assert remoto_changed.value.status_code == 409

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


def test_mesma_operacao_manual_nao_pode_ser_confirmada_duas_vezes(monkeypatch):
    sessao = {"username": "operador", "client_id": "000002"}
    local = {"lojas_integracoes": "hash-a"}
    remote = {"lojas_integracoes": "snapshot-a|hash-remoto|bundle-a"}
    monkeypatch.setattr(
        shared_sync_operations,
        "_shared_sync_local_fingerprint",
        lambda *args: (dict(local), {"lojas_integracoes": []}),
    )
    monkeypatch.setattr(
        shared_sync_operations,
        "_shared_sync_remote_fingerprint",
        lambda *args: (dict(remote), {"lojas_integracoes": {}}),
    )
    preview = shared_sync_operations._shared_sync_create_preview(
        sessao,
        kind="machine",
        resource_id="self",
        direction="push",
        scopes=["lojas_integracoes"],
        bundle_ids={"lojas_integracoes": "bundle"},
    )

    def confirmar():
        try:
            shared_sync_operations._shared_sync_require_operation(
                preview["operation_id"],
                sessao,
                kind="machine",
                resource_id="self",
                direction="push",
                scopes=["lojas_integracoes"],
                bundle_ids={"lojas_integracoes": "bundle"},
            )
            return "ok"
        except HTTPException as exc:
            return exc.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        resultados = list(pool.map(lambda _item: confirmar(), range(2)))

    assert sorted(resultados, key=str) == [409, "ok"]


def test_importacao_manual_mescla_integracoes_sem_remover_lojas_locais(tmp_path, monkeypatch):
    info = tmp_path / "info"

    def tenant_path(client_id):
        path = info / str(client_id)
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    integracoes.configure_integracoes_context(
        pasta_info=str(info), get_tenant_path=tenant_path, normalizar_integracao_conectada=lambda _s, data: data,
    )
    monkeypatch.setattr(shared_sync_apply_scope, "get_tenant_path", tenant_path, raising=False)
    integracoes.salvar_lojas(
        "000002",
        [{
            "nome": "Antiga",
            "store_id": "store-local",
            "integracoes": {"bling": {"access_token": "antigo"}},
        }],
    )
    source = [{
        "nome": "Nova",
        "store_id": "store-estavel",
        "integracoes": {
            "bling": {"id": "id", "secret": "secret", "access_token": "novo", "refresh_token": "refresh", "connected": True},
            "mercadoturbo": {"token": "turbo", "connected": True},
        },
    }]
    raw = json.dumps(source).encode("utf-8")
    tombstone_raw = b"[]"
    monkeypatch.setattr(
        shared_sync_bundle,
        "_shared_sync_coletar_arquivos",
        lambda *args, **kwargs: (
            [
                _entry("lojas_config.json", raw),
                _entry("lojas_sync_tombstones.json", tombstone_raw),
            ],
            [],
        ),
    )
    bundle, _, _ = shared_sync_bundle._shared_sync_montar_pacote(
        "000008", "lojas_integracoes", "origem", user_only=True,
    )
    result = shared_sync_apply_scope._shared_sync_aplicar_pacote(
        "000002", "lojas_integracoes", bundle, "destino", {"user_share": True},
    )
    saved = json.loads((info / "000002" / "lojas_config.json").read_text(encoding="utf-8"))
    assert result["file_count"] == 1
    assert result["stores_count"] == 2
    assert [item["nome"] for item in saved] == ["Antiga", "Nova"]
    assert saved[0]["store_id"] == "store-local"
    assert saved[0]["integracoes"]["bling"]["access_token"] == "antigo"
    assert saved[1]["store_id"] == "store-estavel"
    assert saved[1]["integracoes"]["bling"]["access_token"] == "novo"
    assert saved[1]["integracoes"]["bling"]["refresh_token"] == "refresh"
    assert saved[1]["integracoes"]["mercadoturbo"]["token"] == "turbo"


def test_pacote_lojas_rejeita_casing_nao_canonico_antes_de_escrever(tmp_path, monkeypatch):
    info = tmp_path / "info"

    def tenant_path(client_id):
        path = info / str(client_id)
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    integracoes.configure_integracoes_context(
        pasta_info=str(info),
        get_tenant_path=tenant_path,
        normalizar_integracao_conectada=lambda _s, data: data,
    )
    monkeypatch.setattr(shared_sync_apply_scope, "get_tenant_path", tenant_path, raising=False)
    integracoes.salvar_lojas(
        "000002",
        [{"nome": "Local", "store_id": "store-local", "integracoes": {}}],
    )
    target = info / "000002" / "lojas_config.json"
    before = target.read_bytes()
    bundle = _scope_bundle(
        "lojas_integracoes",
        [("Lojas_Config.json", json.dumps([
            {"nome": "Remota", "store_id": "store-remota", "integracoes": {}},
        ]).encode("utf-8"))],
    )

    with pytest.raises(HTTPException) as exc_info:
        shared_sync_apply_scope._shared_sync_aplicar_pacote(
            "000002", "lojas_integracoes", bundle,
        )

    assert exc_info.value.status_code == 502
    assert target.read_bytes() == before


def test_pacote_lojas_restaura_todos_os_arquivos_se_ultima_escrita_falha(tmp_path, monkeypatch):
    info = tmp_path / "info"

    def tenant_path(client_id):
        path = info / str(client_id)
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    integracoes.configure_integracoes_context(
        pasta_info=str(info),
        get_tenant_path=tenant_path,
        normalizar_integracao_conectada=lambda _s, data: data,
    )
    monkeypatch.setattr(shared_sync_apply_scope, "get_tenant_path", tenant_path, raising=False)
    integracoes.salvar_lojas(
        "000002",
        [{"nome": "Local", "store_id": "store-local", "integracoes": {}}],
    )
    tenant = info / "000002"
    (tenant / "integracoes.json").write_text(
        json.dumps({"local": {"bling": {"access_token": "local"}}}),
        encoding="utf-8",
    )
    (tenant / "lojas_sync_tombstones.json").write_text(
        json.dumps([{
            "key": "store:store-deleted-local:",
            "type": "store",
            "store_id": "store-deleted-local",
            "service": "",
            "version": 1,
            "deleted_at": "2026-09-02T12:00:00Z",
        }]), encoding="utf-8",
    )
    (tenant / "lojas_config.json.bak").write_text(
        json.dumps([{
            "nome": "Copia recuperavel",
            "store_id": "store-recuperavel",
            "integracoes": {"bling": {"access_token": "preservar"}},
        }]),
        encoding="utf-8",
    )
    targets = [
        tenant / "lojas_config.json",
        tenant / "lojas_config.json.bak",
        tenant / "integracoes.json",
        tenant / "lojas_sync_tombstones.json",
    ]
    before = {path.name: path.read_bytes() for path in targets}
    bundle = _scope_bundle(
        "lojas_integracoes",
        [
            ("lojas_config.json", json.dumps([
                {"nome": "Remota", "store_id": "store-remota", "integracoes": {}},
            ]).encode("utf-8")),
            (
                "integracoes.json",
                json.dumps({
                    "remoto": {
                        "mercadolivre": {"access_token": "remoto"},
                    },
                }).encode("utf-8"),
            ),
            ("lojas_sync_tombstones.json", json.dumps([
                {
                        "key": "store:store-deleted-remote:",
                        "type": "store",
                        "store_id": "store-deleted-remote",
                    "service": "",
                    "version": 2,
                    "deleted_at": "2026-09-02T12:00:00Z",
                },
            ]).encode("utf-8")),
        ],
    )
    real_atomic_write = shared_sync_apply_scope._shared_sync_atomic_write
    failed = False

    def fail_last_write(target_abs, data):
        nonlocal failed
        if target_abs.endswith("integracoes.json") and not failed:
            failed = True
            raise OSError("falha simulada")
        return real_atomic_write(target_abs, data)

    monkeypatch.setattr(shared_sync_apply_scope, "_shared_sync_atomic_write", fail_last_write)
    with pytest.raises(HTTPException) as exc_info:
        shared_sync_apply_scope._shared_sync_aplicar_pacote(
            "000002", "lojas_integracoes", bundle,
        )

    assert exc_info.value.status_code == 500
    assert "estado anterior foi restaurado" in str(exc_info.value.detail)
    assert {path.name: path.read_bytes() for path in targets} == before


def test_merge_lojas_preserva_nomes_homonimos_com_store_ids_distintos(tmp_path):
    target = tmp_path / "lojas_config.json"
    target.write_text(
        json.dumps([
            {
                "nome": "Nome igual",
                "store_id": "store-local",
                "integracoes": {"bling": {"access_token": "token-local"}},
            }
        ]),
        encoding="utf-8",
    )
    remoto = json.dumps([
        {
            "nome": "Nome igual",
            "store_id": "store-remoto",
            "integracoes": {"mercadolivre": {"access_token": "token-remoto"}},
        }
    ]).encode("utf-8")

    merged = json.loads(
        shared_sync._shared_sync_merge_lojas_integracoes_bytes(
            str(target),
            remoto,
        )
    )

    assert {loja["store_id"] for loja in merged} == {
        "store-local",
        "store-remoto",
    }


def test_merge_lojas_usa_store_id_para_unir_loja_renomeada(tmp_path):
    target = tmp_path / "lojas_config.json"
    target.write_text(
        json.dumps([
            {
                "nome": "Nome local",
                "store_id": "store-compartilhada",
                "integracoes": {"bling": {"access_token": "token-local"}},
            }
        ]),
        encoding="utf-8",
    )
    remoto = json.dumps([
        {
            "nome": "Nome remoto",
            "store_id": "store-compartilhada",
            "integracoes": {"mercadolivre": {"access_token": "token-remoto"}},
        }
    ]).encode("utf-8")

    merged = json.loads(
        shared_sync._shared_sync_merge_lojas_integracoes_bytes(str(target), remoto).decode("utf-8")
    )

    assert len(merged) == 1
    assert merged[0]["store_id"] == "store-compartilhada"
    assert merged[0]["integracoes"]["bling"]["access_token"] == "token-local"
    assert merged[0]["integracoes"]["mercadolivre"]["access_token"] == "token-remoto"


def test_merge_lojas_preserva_conta_ml_local_quando_seller_remoto_diverge(tmp_path):
    target = tmp_path / "lojas_config.json"
    target.write_text(
        json.dumps([
            {
                "nome": "Loja",
                "store_id": "store-compartilhada",
                "integracoes": {
                    "mercadolivre": {
                        "user_id": "seller-local",
                        "app_id": "app-local",
                        "client_secret": "secret-local",
                        "access_token": "access-local",
                        "refresh_token": "refresh-local",
                        "updated_at": "10",
                    }
                },
            }
        ]),
        encoding="utf-8",
    )
    remoto = json.dumps([
        {
            "nome": "Loja",
            "store_id": "store-compartilhada",
            "integracoes": {
                "mercadolivre": {
                    "user_id": "seller-remoto",
                    "app_id": "app-remoto",
                    "client_secret": "secret-remoto",
                    "access_token": "access-remoto",
                    "refresh_token": "refresh-remoto",
                    "updated_at": "20",
                },
                "bling": {"access_token": "bling-remoto"},
            },
        }
    ]).encode("utf-8")

    merged = json.loads(
        shared_sync._shared_sync_merge_lojas_integracoes_bytes(str(target), remoto).decode("utf-8")
    )
    ml = merged[0]["integracoes"]["mercadolivre"]

    assert len(merged) == 1
    assert ml["user_id"] == "seller-local"
    assert ml["app_id"] == "app-local"
    assert ml["client_secret"] == "secret-local"
    assert ml["access_token"] == "access-local"
    assert ml["refresh_token"] == "refresh-local"
    assert merged[0]["integracoes"]["bling"]["access_token"] == "bling-remoto"


def test_merge_bling_preserva_bloco_local_diante_de_oauth_divergente():
    local = {
        "id": "app-local",
        "secret": "secret-local",
        "access_token": "access-local",
        "refresh_token": "refresh-local",
        "updated_at": "10",
        "marcador_local": "preservar",
    }
    remoto_incompleto = {
        "id": "app-remoto",
        "secret": "secret-remoto",
        "refresh_token": "refresh-remoto",
        "updated_at": "20",
    }

    preservado = shared_sync._shared_sync_merge_integracao_loja(
        local,
        remoto_incompleto,
        servico_key="bling",
    )
    assert preservado["id"] == "app-local"
    assert preservado["access_token"] == "access-local"
    assert preservado["refresh_token"] == "refresh-local"

    remoto_completo = {
        **remoto_incompleto,
        "access_token": "access-remoto",
    }
    atualizado = shared_sync._shared_sync_merge_integracao_loja(
        local,
        remoto_completo,
        servico_key="bling",
    )
    assert atualizado["id"] == "app-local"
    assert atualizado["secret"] == "secret-local"
    assert atualizado["access_token"] == "access-local"
    assert atualizado["refresh_token"] == "refresh-local"
    assert atualizado["marcador_local"] == "preservar"


@pytest.mark.parametrize(
    "lojas",
    [
        [
            {"nome": "A", "store_id": "store-duplicada", "integracoes": {}},
            {"nome": "B", "store_id": "store-duplicada", "integracoes": {}},
        ],
        [
            {
                "nome": "A",
                "store_id": "store-a",
                "integracoes": {"ML": {}, "mercadolivre": {}},
            },
        ],
    ],
)
def test_merge_lojas_bloqueia_identidade_ambigua_sem_colapsar(tmp_path, lojas):
    target = tmp_path / "lojas_config.json"

    with pytest.raises(HTTPException) as exc_info:
        shared_sync_merge_integracoes._shared_sync_merge_lojas_integracoes_bytes(
            str(target),
            json.dumps(lojas).encode("utf-8"),
        )

    assert exc_info.value.status_code == 502
    assert not target.exists()


def test_merge_ml_preserva_bloco_local_quando_identidade_nao_pode_ser_provada():
    local = {
        "app_id": "app-local",
        "client_secret": "secret-local",
        "access_token": "access-local",
        "updated_at": "10",
    }
    remoto = {
        "user_id": "seller-remoto",
        "app_id": "app-remoto",
        "client_secret": "secret-remoto",
        "access_token": "access-remoto",
        "refresh_token": "refresh-remoto",
        "updated_at": "20",
    }

    merged = shared_sync_merge_integracoes._shared_sync_merge_integracao_loja(
        local,
        remoto,
        servico_key="mercadolivre",
    )

    assert merged["app_id"] == "app-local"
    assert merged["access_token"] == "access-local"
    assert "user_id" not in merged


@pytest.mark.parametrize(
    ("service", "pending_key", "pending_value"),
    [
        ("mercadolivre", "oauth_draft", {"state": "state-local"}),
        ("bling", "oauth_pending_state", "state-local"),
    ],
)
def test_merge_oauth_preserva_fluxo_local_em_andamento(service, pending_key, pending_value):
    local = {
        "id": "app-local",
        "secret": "secret-local",
        "access_token": "access-local",
        "refresh_token": "refresh-local",
        pending_key: pending_value,
        "updated_at": "10",
    }
    if service == "mercadolivre":
        local["app_id"] = local.pop("id")
        local["client_secret"] = local.pop("secret")
        local["user_id"] = "seller-a"
    remoto = dict(local)
    remoto.pop(pending_key)
    remoto["access_token"] = "access-remoto"
    remoto["refresh_token"] = "refresh-remoto"
    remoto["updated_at"] = "20"

    merged = shared_sync_merge_integracoes._shared_sync_merge_integracao_loja(
        local,
        remoto,
        servico_key=service,
    )

    assert merged[pending_key] == pending_value
    assert merged["access_token"] == "access-local"


def test_merge_oauth_tres_vias_aplica_so_a_alteracao_causal():
    base = {
        "id": "app",
        "secret": "secret",
        "access_token": "access-base",
        "refresh_token": "refresh-base",
    }
    remoto_novo = {
        **base,
        "access_token": "access-remoto",
        "refresh_token": "refresh-remoto",
    }
    local_novo = {
        **base,
        "access_token": "access-local",
        "refresh_token": "refresh-local",
    }

    recebido = shared_sync_merge_integracoes._shared_sync_merge_integracao_loja(
        base,
        remoto_novo,
        servico_key="bling",
        base=base,
        strict_oauth_conflicts=True,
    )
    assert recebido["access_token"] == "access-remoto"

    preservado = shared_sync_merge_integracoes._shared_sync_merge_integracao_loja(
        local_novo,
        base,
        servico_key="bling",
        base=base,
        strict_oauth_conflicts=True,
    )
    assert preservado["access_token"] == "access-local"

    with pytest.raises(HTTPException) as conflict_exc:
        shared_sync_merge_integracoes._shared_sync_merge_integracao_loja(
            local_novo,
            remoto_novo,
            servico_key="bling",
            base=base,
            strict_oauth_conflicts=True,
        )
    assert conflict_exc.value.status_code == 409


@pytest.mark.parametrize(
    ("service", "completo", "parcial", "token_key"),
    [
        (
            "bling",
            {
                "id": "app",
                "secret": "secret",
                "access_token": "access",
                "refresh_token": "refresh",
                "connected": True,
            },
            {
                "id": "app-novo",
                "secret": "secret-novo",
                "refresh_token": "refresh-novo",
                "connected": False,
            },
            "access_token",
        ),
        (
            "bling",
            {
                "access_token": "legacy-access",
                "api_key": "legacy-api-key",
                "connected": True,
            },
            {
                "id": "draft-id",
                "secret": "draft-secret",
                "connected": False,
            },
            "access_token",
        ),
        (
            "bling",
            {
                "token": "legacy-token-alias",
                "apikey": "legacy-apikey-alias",
                "connected": True,
            },
            {
                "id": "draft-id",
                "secret": "draft-secret",
                "connected": False,
            },
            "token",
        ),
        (
            "mercadolivre",
            {
                "app_id": "app",
                "client_secret": "secret",
                "access_token": "access",
                "refresh_token": "refresh",
                "user_id": "seller-1",
                "connected": True,
            },
            {
                "app_id": "app",
                "client_secret": "secret",
                "user_id": "seller-1",
                "connected": False,
            },
            "access_token",
        ),
        (
            "mercadoturbo",
            {"token": "token-completo", "connected": True},
            {"refresh_token": "parcial", "connected": False},
            "token",
        ),
    ],
)
def test_merge_causal_nunca_rebaixa_oauth_completo_para_parcial(
    service,
    completo,
    parcial,
    token_key,
):
    with pytest.raises(HTTPException) as bloqueado:
        shared_sync_merge_integracoes._shared_sync_merge_integracao_loja(
            completo,
            parcial,
            servico_key=service,
            base=completo,
            strict_oauth_conflicts=True,
        )
    assert bloqueado.value.status_code == 409

    preservado = shared_sync_merge_integracoes._shared_sync_merge_integracao_loja(
        completo,
        parcial,
        servico_key=service,
        base=completo,
        strict_oauth_conflicts=False,
    )
    assert preservado[token_key] == completo[token_key]


def test_merge_integracoes_json_bloqueia_oauth_completo_para_parcial(tmp_path):
    target = tmp_path / "integracoes.json"
    completo = {
        "Loja": {
            "bling": {
                "id": "app",
                "secret": "secret",
                "access_token": "access",
                "refresh_token": "refresh",
                "connected": True,
            },
        },
    }
    parcial = {
        "Loja": {
            "bling": {
                "id": "app-novo",
                "secret": "secret-novo",
                "refresh_token": "refresh-novo",
                "connected": False,
            },
        },
    }
    target.write_text(json.dumps(completo), encoding="utf-8")

    with pytest.raises(HTTPException) as bloqueado:
        shared_sync_merge_integracoes._shared_sync_merge_integracoes_legacy_bytes(
            str(target),
            json.dumps(parcial).encode("utf-8"),
            base_bytes=json.dumps(completo).encode("utf-8"),
            strict_oauth_conflicts=True,
        )

    assert bloqueado.value.status_code == 409


def test_merge_integracoes_json_bloqueia_troca_de_seller_ml_mesmo_com_base(
    tmp_path,
):
    target = tmp_path / "integracoes.json"

    def payload(seller):
        return {
            "Loja": {
                "mercadolivre": {
                    "app_id": "app",
                    "client_secret": "secret",
                    "access_token": "access",
                    "refresh_token": "refresh",
                    "user_id": seller,
                    "connected": True,
                },
            },
        }

    remoto = payload("seller-a")
    target.write_text(json.dumps(payload("seller-b")), encoding="utf-8")

    with pytest.raises(HTTPException) as bloqueado:
        shared_sync_merge_integracoes._shared_sync_merge_integracoes_legacy_bytes(
            str(target),
            json.dumps(remoto).encode("utf-8"),
            base_bytes=json.dumps(remoto).encode("utf-8"),
            strict_oauth_conflicts=False,
        )

    assert bloqueado.value.status_code == 409


def test_merge_integracoes_json_bloqueia_contas_ml_sem_seller_divergentes(
    tmp_path,
):
    target = tmp_path / "integracoes.json"

    def payload(sufixo):
        return {
            "Loja": {
                "mercadolivre": {
                    "app_id": f"app-{sufixo}",
                    "client_secret": f"secret-{sufixo}",
                    "access_token": f"access-{sufixo}",
                    "refresh_token": f"refresh-{sufixo}",
                    "connected": True,
                },
            },
        }

    remoto = payload("a")
    target.write_text(json.dumps(payload("b")), encoding="utf-8")

    with pytest.raises(HTTPException) as bloqueado:
        shared_sync_merge_integracoes._shared_sync_merge_integracoes_legacy_bytes(
            str(target),
            json.dumps(remoto).encode("utf-8"),
            base_bytes=json.dumps(remoto).encode("utf-8"),
            strict_oauth_conflicts=False,
        )

    assert bloqueado.value.status_code == 409


def test_merge_integracoes_json_preserva_bling_legado_operacional(tmp_path):
    target = tmp_path / "integracoes.json"
    operacional = {
        "Loja": {
            "bling": {
                "access_token": "legacy-access",
                "api_key": "legacy-api-key",
                "connected": True,
            },
        },
    }
    draft = {
        "Loja": {
            "bling": {
                "id": "draft-id",
                "secret": "draft-secret",
                "connected": False,
            },
        },
    }
    target.write_text(json.dumps(operacional), encoding="utf-8")

    with pytest.raises(HTTPException) as bloqueado:
        shared_sync_merge_integracoes._shared_sync_merge_integracoes_legacy_bytes(
            str(target),
            json.dumps(draft).encode("utf-8"),
            base_bytes=json.dumps(operacional).encode("utf-8"),
            strict_oauth_conflicts=True,
        )

    assert bloqueado.value.status_code == 409


def test_merge_lojas_snapshot_vazio_preserva_destino_e_e_idempotente(tmp_path):
    target = tmp_path / "lojas_config.json"
    local = [{
        "nome": "Somente local",
        "store_id": "store-local",
        "integracoes": {"bling": {"access_token": "local"}},
    }]
    target.write_text(json.dumps(local), encoding="utf-8")

    primeira = shared_sync._shared_sync_merge_lojas_integracoes_bytes(
        str(target),
        b"[]",
    )
    target.write_bytes(primeira)
    segunda = shared_sync._shared_sync_merge_lojas_integracoes_bytes(
        str(target),
        b"[]",
    )

    assert json.loads(primeira) == local
    assert json.loads(segunda) == local


def test_merge_integracoes_legadas_e_tombstones_preserva_registros_locais(tmp_path):
    legacy = tmp_path / "integracoes.json"
    legacy.write_text(
        json.dumps({"Loja local": {"bling": {"access_token": "local"}}}),
        encoding="utf-8",
    )
    legacy_merged = json.loads(
        shared_sync._shared_sync_merge_integracoes_legacy_bytes(
            str(legacy),
            json.dumps({"Loja remota": {"mercadolivre": {"access_token": "remoto"}}}).encode("utf-8"),
        )
    )

    assert set(legacy_merged) == {"Loja local", "Loja remota"}
    assert legacy_merged["Loja local"]["bling"]["access_token"] == "local"

    tombstones = tmp_path / "lojas_sync_tombstones.json"
    tombstones.write_text(
        json.dumps([{
            "key": "store:local:",
            "type": "store",
            "store_id": "local",
            "version": 4,
            "deleted_at": "2026-09-02T10:00:00Z",
        }]),
        encoding="utf-8",
    )
    tombstones_merged = json.loads(
        shared_sync._shared_sync_merge_tombstones_integracoes_bytes(
            str(tombstones),
            json.dumps([
                {
                    "key": "store:local:",
                    "type": "store",
                    "store_id": "local",
                    "version": 2,
                    "deleted_at": "2026-09-01T10:00:00Z",
                },
                {
                    "key": "store:remota:",
                    "type": "store",
                    "store_id": "remota",
                    "version": 1,
                    "deleted_at": "2026-09-02T11:00:00Z",
                },
            ]).encode("utf-8"),
        )
    )
    por_chave = {item["key"]: item for item in tombstones_merged}

    assert set(por_chave) == {"store:local:", "store:remota:"}
    assert por_chave["store:local:"]["version"] == 4


def test_push_lojas_bloqueia_qualquer_perda_do_snapshot_remoto(monkeypatch):
    def bundle(lojas):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as arquivo:
            arquivo.writestr("files/lojas_config.json", json.dumps(lojas))
        return buffer.getvalue()

    remoto = bundle([
        {"nome": "Loja A", "store_id": "store-a", "integracoes": {}},
        {"nome": "Loja B", "store_id": "store-b", "integracoes": {}},
    ])
    local_reduzido = bundle([
        {"nome": "Loja A", "store_id": "store-a", "integracoes": {}},
    ])
    monkeypatch.setattr(
        shared_sync_remote,
        "_shared_sync_obter_bundle_remoto_para_guard",
        lambda *args, **kwargs: (remoto, {"snapshot_id": "snapshot-remoto"}),
    )

    with pytest.raises(HTTPException) as exc_info:
        shared_sync_merge_integracoes._shared_sync_validar_push_lojas_integracoes(
            "bundle-lojas",
            local_reduzido,
        )

    assert exc_info.value.status_code == 409
    assert "parece remover lojas" in str(exc_info.value.detail)

    remoto_legado = bundle([{
        "nome": "Loja A",
        "integracoes": {
            "mercadolivre": {"connected": True, "user_id": "seller-a"},
        },
    }])
    local_com_id = bundle([{
        "nome": "Loja A",
        "store_id": "store-a",
        "integracoes": {
            "mercadolivre": {"connected": True, "user_id": "seller-a"},
        },
    }])
    monkeypatch.setattr(
        shared_sync_remote,
        "_shared_sync_obter_bundle_remoto_para_guard",
        lambda *args, **kwargs: (remoto_legado, {"snapshot_id": "snapshot-remoto"}),
    )
    shared_sync_merge_integracoes._shared_sync_validar_push_lojas_integracoes(
        "bundle-lojas",
        local_com_id,
    )

    remoto_outro_seller = bundle([{
        "nome": "Loja A",
        "store_id": "store-a",
        "integracoes": {
            "mercadolivre": {"connected": True, "user_id": "seller-remoto"},
        },
    }])
    monkeypatch.setattr(
        shared_sync_remote,
        "_shared_sync_obter_bundle_remoto_para_guard",
        lambda *args, **kwargs: (remoto_outro_seller, {"snapshot_id": "snapshot-remoto"}),
    )
    with pytest.raises(HTTPException) as seller_exc:
        shared_sync_merge_integracoes._shared_sync_validar_push_lojas_integracoes(
            "bundle-lojas",
            local_com_id,
        )
    assert seller_exc.value.status_code == 409

    monkeypatch.setattr(
        shared_sync_remote,
        "_shared_sync_obter_bundle_remoto_para_guard",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("indisponivel")),
    )
    with pytest.raises(HTTPException) as unavailable_exc:
        shared_sync_merge_integracoes._shared_sync_validar_push_lojas_integracoes(
            "bundle-lojas",
            local_com_id,
        )
    assert unavailable_exc.value.status_code == 503


@pytest.mark.parametrize("tipo", ["store", "integration"])
@pytest.mark.parametrize(
    ("base_snapshot_id", "com_tombstone", "permitido"),
    [
        ("snapshot-remoto", True, True),
        ("", True, False),
        ("snapshot-remoto", False, False),
    ],
)
def test_push_aceita_exclusao_somente_com_base_causal_e_tombstone_ativo(
    monkeypatch,
    tipo,
    base_snapshot_id,
    com_tombstone,
    permitido,
):
    integracao_remota = {
        "app_id": "app",
        "client_secret": "secret",
        "access_token": "access-remoto",
        "refresh_token": "refresh-remoto",
        "user_id": "seller-remoto",
        "connected": True,
    }
    remoto = _lojas_bundle([{
        "nome": "Loja A",
        "store_id": "store-a",
        "integracoes": {"mercadolivre": integracao_remota},
    }])
    monkeypatch.setattr(
        shared_sync_remote,
        "_shared_sync_obter_bundle_remoto_para_guard",
        lambda *args, **kwargs: (remoto, {"snapshot_id": "snapshot-remoto"}),
    )

    lojas_locais = [] if tipo == "store" else [{
        "nome": "Loja A",
        "store_id": "store-a",
        "integracoes": {"mercadolivre": {"connected": False}},
    }]
    arquivos = [("lojas_config.json", json.dumps(lojas_locais).encode("utf-8"))]
    if com_tombstone:
        servico = "mercadolivre" if tipo == "integration" else ""
        tombstone = {
            "key": f"{tipo}:store-a:{servico}",
            "type": tipo,
            "store_id": "store-a",
            "service": servico,
            "version": 2,
            "deleted_at": "2026-09-02T12:00:00Z",
        }
        arquivos.append(
            (
                "lojas_sync_tombstones.json",
                json.dumps([tombstone]).encode("utf-8"),
            )
        )
    local = _scope_bundle("lojas_integracoes", arquivos)

    if permitido:
        assert shared_sync_merge_integracoes._shared_sync_validar_push_lojas_integracoes(
            "bundle-lojas",
            local,
            base_snapshot_id=base_snapshot_id,
        ) == "snapshot-remoto"
    else:
        with pytest.raises(HTTPException) as bloqueado:
            shared_sync_merge_integracoes._shared_sync_validar_push_lojas_integracoes(
                "bundle-lojas",
                local,
                base_snapshot_id=base_snapshot_id,
            )
        assert bloqueado.value.status_code == 409


@pytest.mark.parametrize("tipo", ["store", "integration"])
@pytest.mark.parametrize("com_base", [True, False])
def test_push_exclusao_de_snapshot_legado_sem_id_falha_fechado(
    monkeypatch,
    tipo,
    com_base,
):
    client_id = "000002"
    nome = "Loja legada"
    store_id = shared_sync_merge_integracoes._shared_sync_store_id_deterministico(
        client_id,
        nome,
    )
    remoto = _lojas_bundle([{
        "nome": nome,
        "integracoes": {
            "mercadolivre": {
                "access_token": "access-remoto",
                "refresh_token": "refresh-remoto",
                "connected": True,
            },
        },
    }])
    monkeypatch.setattr(
        shared_sync_remote,
        "_shared_sync_obter_bundle_remoto_para_guard",
        lambda *args, **kwargs: (remoto, {"snapshot_id": "snapshot-remoto"}),
    )
    lojas_locais = [] if tipo == "store" else [{
        "nome": nome,
        "store_id": store_id,
        "integracoes": {"mercadolivre": {"connected": False}},
    }]
    servico = "mercadolivre" if tipo == "integration" else ""
    tombstone = {
        "key": f"{tipo}:{store_id}:{servico}",
        "type": tipo,
        "store_id": store_id,
        "service": servico,
        "version": 1,
        "deleted_at": "2026-09-02T12:00:00Z",
    }
    local = _scope_bundle(
        "lojas_integracoes",
        [
            ("lojas_config.json", json.dumps(lojas_locais).encode("utf-8")),
            (
                "lojas_sync_tombstones.json",
                json.dumps([tombstone]).encode("utf-8"),
            ),
        ],
    )

    with pytest.raises(HTTPException) as bloqueado:
        shared_sync_merge_integracoes._shared_sync_validar_push_lojas_integracoes(
            "bundle-lojas",
            local,
            base_snapshot_id="snapshot-remoto" if com_base else "",
            client_id=client_id,
        )
    assert bloqueado.value.status_code == 409


@pytest.mark.parametrize("tipo", ["store", "integration"])
@pytest.mark.parametrize("evento_remoto", ["restored_at", "deleted_at"])
def test_push_exclusao_exige_tombstone_posterior_ao_evento_remoto(
    monkeypatch,
    tipo,
    evento_remoto,
):
    loja_remota = {
        "nome": "Loja A",
        "store_id": "store-a",
        "integracoes": {
            "mercadolivre": {
                "app_id": "app",
                "client_secret": "secret",
                "access_token": "access-remoto",
                "refresh_token": "refresh-remoto",
                "user_id": "seller-remoto",
                "connected": True,
            },
        },
    }
    servico = "mercadolivre" if tipo == "integration" else ""
    chave = f"{tipo}:store-a:{servico}"
    tombstone_remoto = {
        "key": chave,
        "type": tipo,
        "store_id": "store-a",
        "service": servico,
        "version": 5,
        evento_remoto: "2026-09-02T12:00:00Z",
    }
    remoto = _scope_bundle(
        "lojas_integracoes",
        [
            ("lojas_config.json", json.dumps([loja_remota]).encode("utf-8")),
            (
                "lojas_sync_tombstones.json",
                json.dumps([tombstone_remoto]).encode("utf-8"),
            ),
        ],
    )
    monkeypatch.setattr(
        shared_sync_remote,
        "_shared_sync_obter_bundle_remoto_para_guard",
        lambda *args, **kwargs: (remoto, {"snapshot_id": "snapshot-remoto"}),
    )
    lojas_locais = [] if tipo == "store" else [{
        "nome": "Loja A",
        "store_id": "store-a",
        "integracoes": {"mercadolivre": {"connected": False}},
    }]

    def local_bundle(*, version, deleted_at):
        tombstone_local = {
            "key": chave,
            "type": tipo,
            "store_id": "store-a",
            "service": servico,
            "version": version,
            "deleted_at": deleted_at,
        }
        return _scope_bundle(
            "lojas_integracoes",
            [
                (
                    "lojas_config.json",
                    json.dumps(lojas_locais).encode("utf-8"),
                ),
                (
                    "lojas_sync_tombstones.json",
                    json.dumps([tombstone_local]).encode("utf-8"),
                ),
            ],
        )

    stale_version = 5
    stale_deleted_at = (
        "2026-09-02T13:00:00Z"
        if evento_remoto == "restored_at"
        else "2026-09-02T11:00:00Z"
    )
    with pytest.raises(HTTPException) as stale:
        shared_sync_merge_integracoes._shared_sync_validar_push_lojas_integracoes(
            "bundle-lojas",
            local_bundle(
                version=stale_version,
                deleted_at=stale_deleted_at,
            ),
            base_snapshot_id="snapshot-remoto",
        )
    assert stale.value.status_code == 409

    causal_version = 6 if evento_remoto == "restored_at" else 5
    assert shared_sync_merge_integracoes._shared_sync_validar_push_lojas_integracoes(
        "bundle-lojas",
        local_bundle(
            version=causal_version,
            deleted_at="2026-09-02T13:00:00Z",
        ),
        base_snapshot_id="snapshot-remoto",
    ) == "snapshot-remoto"


@pytest.mark.parametrize("tipo", ["store", "integration"])
def test_push_bloqueia_tombstone_ativo_contradito_por_payload_local(
    monkeypatch,
    tipo,
):
    loja = {
        "nome": "Loja A",
        "store_id": "store-a",
        "integracoes": {
            "mercadolivre": {
                "access_token": "access",
                "refresh_token": "refresh",
                "connected": True,
            },
        },
    }
    remoto = _lojas_bundle([loja])
    monkeypatch.setattr(
        shared_sync_remote,
        "_shared_sync_obter_bundle_remoto_para_guard",
        lambda *args, **kwargs: (remoto, {"snapshot_id": "snapshot-remoto"}),
    )
    servico = "mercadolivre" if tipo == "integration" else ""
    tombstone = {
        "key": f"{tipo}:store-a:{servico}",
        "type": tipo,
        "store_id": "store-a",
        "service": servico,
        "version": 2,
        "deleted_at": "2026-09-02T13:00:00Z",
    }
    local = _scope_bundle(
        "lojas_integracoes",
        [
            ("lojas_config.json", json.dumps([loja]).encode("utf-8")),
            (
                "lojas_sync_tombstones.json",
                json.dumps([tombstone]).encode("utf-8"),
            ),
        ],
    )

    with pytest.raises(HTTPException) as bloqueado:
        shared_sync_merge_integracoes._shared_sync_validar_push_lojas_integracoes(
            "bundle-lojas",
            local,
            base_snapshot_id="snapshot-remoto",
        )
    assert bloqueado.value.status_code == 409


def test_push_lojas_bloqueia_oauth_local_antigo_e_identidade_ml_incerta(monkeypatch):
    remoto = _lojas_bundle([{
        "nome": "Loja A",
        "store_id": "store-a",
        "integracoes": {
            "mercadolivre": {
                "connected": True,
                "user_id": "seller-a",
                "app_id": "app",
                "client_secret": "secret",
                "access_token": "access-remoto",
                "refresh_token": "refresh-remoto",
                "updated_at": "20",
            },
        },
    }])
    monkeypatch.setattr(
        shared_sync_remote,
        "_shared_sync_obter_bundle_remoto_para_guard",
        lambda *args, **kwargs: (remoto, {"snapshot_id": "snapshot-remoto"}),
    )
    local_antigo = _lojas_bundle([{
        "nome": "Loja A",
        "store_id": "store-a",
        "integracoes": {
            "mercadolivre": {
                "connected": True,
                "user_id": "seller-a",
                "app_id": "app",
                "client_secret": "secret",
                "access_token": "access-local",
                "refresh_token": "refresh-local",
                "updated_at": "10",
            },
        },
    }])
    with pytest.raises(HTTPException) as stale_exc:
        shared_sync_merge_integracoes._shared_sync_validar_push_lojas_integracoes(
            "bundle-lojas", local_antigo,
        )
    assert stale_exc.value.status_code == 409

    assert shared_sync_merge_integracoes._shared_sync_validar_push_lojas_integracoes(
        "bundle-lojas",
        local_antigo,
        base_snapshot_id="snapshot-remoto",
    ) == "snapshot-remoto"

    local_parcial = _lojas_bundle([{
        "nome": "Loja A",
        "store_id": "store-a",
        "integracoes": {
            "mercadolivre": {
                "connected": False,
                "user_id": "seller-a",
                "app_id": "app",
                "client_secret": "secret",
                "refresh_token": "refresh-parcial",
                "updated_at": "30",
            },
        },
    }])
    with pytest.raises(HTTPException) as parcial_exc:
        shared_sync_merge_integracoes._shared_sync_validar_push_lojas_integracoes(
            "bundle-lojas",
            local_parcial,
            base_snapshot_id="snapshot-remoto",
        )
    assert parcial_exc.value.status_code == 409

    local_sem_identidade = _lojas_bundle([{
        "nome": "Loja A",
        "store_id": "store-a",
        "integracoes": {
            "mercadolivre": {
                "connected": True,
                "app_id": "app-diferente",
                "client_secret": "secret",
                "access_token": "access-local",
                "refresh_token": "refresh-local",
                "updated_at": "30",
            },
        },
    }])
    with pytest.raises(HTTPException) as identity_exc:
        shared_sync_merge_integracoes._shared_sync_validar_push_lojas_integracoes(
            "bundle-lojas", local_sem_identidade,
        )
    assert identity_exc.value.status_code == 409

    local_sem_seller_mesmo_oauth = _lojas_bundle([{
        "nome": "Loja A",
        "store_id": "store-a",
        "integracoes": {
            "mercadolivre": {
                "connected": True,
                "app_id": "app",
                "client_secret": "secret",
                "access_token": "access-remoto",
                "refresh_token": "refresh-remoto",
                "updated_at": "30",
            },
        },
    }])
    with pytest.raises(HTTPException) as seller_missing_exc:
        shared_sync_merge_integracoes._shared_sync_validar_push_lojas_integracoes(
            "bundle-lojas", local_sem_seller_mesmo_oauth,
        )
    assert seller_missing_exc.value.status_code == 409


def test_push_ml_legado_sem_user_id_so_e_permitido_com_mesmo_bloco_oauth(monkeypatch):
    oauth = {
        "connected": True,
        "app_id": "app",
        "client_secret": "secret",
        "access_token": "access",
        "refresh_token": "refresh",
        "updated_at": "20",
    }
    remoto = _lojas_bundle([{
        "nome": "Loja A",
        "store_id": "store-a",
        "integracoes": {"mercadolivre": dict(oauth)},
    }])
    monkeypatch.setattr(
        shared_sync_remote,
        "_shared_sync_obter_bundle_remoto_para_guard",
        lambda *args, **kwargs: (remoto, {"snapshot_id": "snapshot-remoto"}),
    )
    local = _lojas_bundle([{
        "nome": "Loja A",
        "store_id": "store-a",
        "integracoes": {
            "mercadolivre": {**oauth, "user_id": "seller-a"},
        },
    }])

    expected = shared_sync_merge_integracoes._shared_sync_validar_push_lojas_integracoes(
        "bundle-lojas", local,
    )

    assert expected == "snapshot-remoto"


def test_push_preserva_integracoes_json_legado_e_aceita_upgrade_causal(monkeypatch):
    remoto = _scope_bundle(
        "lojas_integracoes",
        [
            ("lojas_config.json", b"[]"),
            (
                "integracoes.json",
                json.dumps({
                    "Loja A": {
                        "mercadolivre": {
                            "access_token": "token-remoto",
                            "user_id": "seller-a",
                        },
                    },
                }).encode("utf-8"),
            ),
        ],
    )
    meta = {"snapshot_id": "snapshot-remoto", "snapshot_hash": "hash-base"}
    monkeypatch.setattr(
        shared_sync_remote,
        "_shared_sync_obter_bundle_remoto_para_guard",
        lambda *args, **kwargs: (remoto, dict(meta)),
    )
    local_sem_conta = _scope_bundle(
        "lojas_integracoes",
        [
            ("lojas_config.json", b"[]"),
            (
                "integracoes.json",
                json.dumps({
                    "Loja B": {
                        "bling": {"access_token": "token-local"},
                    },
                }).encode("utf-8"),
            ),
        ],
    )

    with pytest.raises(HTTPException) as perda:
        shared_sync_merge_integracoes._shared_sync_validar_push_lojas_integracoes(
            "bundle-lojas",
            local_sem_conta,
            base_snapshot_id="snapshot-remoto",
        )
    assert perda.value.status_code == 409
    assert "dados legados" in str(perda.value.detail)

    local_sem_arquivo_legado = _scope_bundle(
        "lojas_integracoes",
        [("lojas_config.json", b"[]")],
    )
    with pytest.raises(HTTPException) as arquivo_omitido:
        shared_sync_merge_integracoes._shared_sync_validar_push_lojas_integracoes(
            "bundle-lojas",
            local_sem_arquivo_legado,
            base_snapshot_id="snapshot-remoto",
        )
    assert arquivo_omitido.value.status_code == 409

    local_atualizado = _scope_bundle(
        "lojas_integracoes",
        [
            ("lojas_config.json", b"[]"),
            (
                "integracoes.json",
                json.dumps({
                    "Loja A": {
                        "mercadolivre": {
                            "access_token": "token-renovado",
                            "user_id": "seller-a",
                        },
                    },
                }).encode("utf-8"),
            ),
        ],
    )
    assert shared_sync_merge_integracoes._shared_sync_validar_push_lojas_integracoes(
        "bundle-lojas",
        local_atualizado,
        base_snapshot_id="snapshot-remoto",
    ) == "snapshot-remoto"


def test_push_legado_bloqueia_troca_de_seller_ml_mesmo_com_base(monkeypatch):
    def legacy(seller):
        return {
            "Loja": {
                "mercadolivre": {
                    "app_id": "app",
                    "client_secret": "secret",
                    "access_token": "access",
                    "refresh_token": "refresh",
                    "user_id": seller,
                    "connected": True,
                },
            },
        }

    remoto = _scope_bundle(
        "lojas_integracoes",
        [
            ("lojas_config.json", b"[]"),
            (
                "integracoes.json",
                json.dumps(legacy("seller-a")).encode("utf-8"),
            ),
        ],
    )
    local = _scope_bundle(
        "lojas_integracoes",
        [
            ("lojas_config.json", b"[]"),
            (
                "integracoes.json",
                json.dumps(legacy("seller-b")).encode("utf-8"),
            ),
        ],
    )
    monkeypatch.setattr(
        shared_sync_remote,
        "_shared_sync_obter_bundle_remoto_para_guard",
        lambda *args, **kwargs: (
            remoto,
            {
                "snapshot_id": "snapshot-remoto",
                "schema": 2,
                "encrypted": True,
            },
        ),
    )

    with pytest.raises(HTTPException) as bloqueado:
        shared_sync_merge_integracoes._shared_sync_validar_push_lojas_integracoes(
            "bundle-lojas",
            local,
            base_snapshot_id="snapshot-remoto",
        )

    assert bloqueado.value.status_code == 409


def test_push_legado_bloqueia_contas_ml_sem_seller_divergentes(monkeypatch):
    def legacy(sufixo):
        return {
            "Loja": {
                "mercadolivre": {
                    "app_id": f"app-{sufixo}",
                    "client_secret": f"secret-{sufixo}",
                    "access_token": f"access-{sufixo}",
                    "refresh_token": f"refresh-{sufixo}",
                    "connected": True,
                },
            },
        }

    remoto = _scope_bundle(
        "lojas_integracoes",
        [
            ("lojas_config.json", b"[]"),
            ("integracoes.json", json.dumps(legacy("a")).encode("utf-8")),
        ],
    )
    local = _scope_bundle(
        "lojas_integracoes",
        [
            ("lojas_config.json", b"[]"),
            ("integracoes.json", json.dumps(legacy("b")).encode("utf-8")),
        ],
    )
    monkeypatch.setattr(
        shared_sync_remote,
        "_shared_sync_obter_bundle_remoto_para_guard",
        lambda *args, **kwargs: (
            remoto,
            {
                "snapshot_id": "snapshot-remoto",
                "schema": 2,
                "encrypted": True,
            },
        ),
    )

    with pytest.raises(HTTPException) as bloqueado:
        shared_sync_merge_integracoes._shared_sync_validar_push_lojas_integracoes(
            "bundle-lojas",
            local,
            base_snapshot_id="snapshot-remoto",
        )

    assert bloqueado.value.status_code == 409


def test_push_legado_bloqueia_oauth_completo_para_campos_vazios(monkeypatch):
    completo = {
        "Loja": {
            "bling": {
                "id": "app",
                "secret": "secret",
                "access_token": "access",
                "refresh_token": "refresh",
                "connected": True,
            },
        },
    }
    parcial = {
        "Loja": {
            "bling": {
                "id": "app-novo",
                "secret": "secret-novo",
                "access_token": "",
                "refresh_token": "refresh-novo",
                "connected": False,
            },
        },
    }
    remoto = _scope_bundle(
        "lojas_integracoes",
        [
            ("lojas_config.json", b"[]"),
            ("integracoes.json", json.dumps(completo).encode("utf-8")),
        ],
    )
    local = _scope_bundle(
        "lojas_integracoes",
        [
            ("lojas_config.json", b"[]"),
            ("integracoes.json", json.dumps(parcial).encode("utf-8")),
        ],
    )
    monkeypatch.setattr(
        shared_sync_remote,
        "_shared_sync_obter_bundle_remoto_para_guard",
        lambda *args, **kwargs: (
            remoto,
            {
                "snapshot_id": "snapshot-remoto",
                "schema": 2,
                "encrypted": True,
            },
        ),
    )

    with pytest.raises(HTTPException) as bloqueado:
        shared_sync_merge_integracoes._shared_sync_validar_push_lojas_integracoes(
            "bundle-lojas",
            local,
            base_snapshot_id="snapshot-remoto",
        )

    assert bloqueado.value.status_code == 409


@pytest.mark.parametrize(
    ("service", "completo", "parcial"),
    [
        (
            "mercadolivre",
            {
                "app_id": "app",
                "secret_key": "secret",
                "access_token": "access",
                "refresh_token": "refresh",
                "user_id": "seller-1",
                "connected": True,
            },
            {
                "app_id": "app",
                "secret_key": "secret",
                "access_token": "",
                "refresh_token": "refresh",
                "user_id": "seller-1",
                "connected": False,
            },
        ),
        (
            "bling",
            {
                "app_id": "app",
                "secret_key": "secret",
                "access_token": "access",
                "refresh_token": "refresh",
                "connected": True,
            },
            {
                "app_id": "app",
                "secret_key": "secret",
                "access_token": "",
                "refresh_token": "refresh",
                "connected": False,
            },
        ),
    ],
)
def test_push_canonico_bloqueia_downgrade_com_aliases_oauth(
    monkeypatch,
    service,
    completo,
    parcial,
):
    remoto = _lojas_bundle([{
        "nome": "Loja A",
        "store_id": "store-a",
        "integracoes": {service: completo},
    }])
    local = _lojas_bundle([{
        "nome": "Loja A",
        "store_id": "store-a",
        "integracoes": {service: parcial},
    }])
    monkeypatch.setattr(
        shared_sync_remote,
        "_shared_sync_obter_bundle_remoto_para_guard",
        lambda *args, **kwargs: (
            remoto,
            {
                "snapshot_id": "snapshot-remoto",
                "schema": 2,
                "encrypted": True,
            },
        ),
    )

    with pytest.raises(HTTPException) as bloqueado:
        shared_sync_merge_integracoes._shared_sync_validar_push_lojas_integracoes(
            "bundle-lojas",
            local,
            base_snapshot_id="snapshot-remoto",
        )

    assert bloqueado.value.status_code == 409


def test_push_bloqueia_downgrade_de_bling_legado_operacional(monkeypatch):
    operacional = {
        "access_token": "legacy-access",
        "api_key": "legacy-api-key",
        "connected": True,
    }
    draft = {
        "id": "draft-id",
        "secret": "draft-secret",
        "connected": False,
    }
    remoto = _lojas_bundle([{
        "nome": "Loja A",
        "store_id": "store-a",
        "integracoes": {"bling": operacional},
    }])
    local = _lojas_bundle([{
        "nome": "Loja A",
        "store_id": "store-a",
        "integracoes": {"bling": draft},
    }])
    monkeypatch.setattr(
        shared_sync_remote,
        "_shared_sync_obter_bundle_remoto_para_guard",
        lambda *args, **kwargs: (
            remoto,
            {
                "snapshot_id": "snapshot-remoto",
                "schema": 2,
                "encrypted": True,
            },
        ),
    )

    with pytest.raises(HTTPException) as bloqueado:
        shared_sync_merge_integracoes._shared_sync_validar_push_lojas_integracoes(
            "bundle-lojas",
            local,
            base_snapshot_id="snapshot-remoto",
        )

    assert bloqueado.value.status_code == 409

    remoto_legado = _scope_bundle(
        "lojas_integracoes",
        [
            ("lojas_config.json", b"[]"),
            (
                "integracoes.json",
                json.dumps({"Loja": {"bling": operacional}}).encode("utf-8"),
            ),
        ],
    )
    local_legado = _scope_bundle(
        "lojas_integracoes",
        [
            ("lojas_config.json", b"[]"),
            (
                "integracoes.json",
                json.dumps({
                    "Loja": {
                        "bling": {
                            **draft,
                            "access_token": "",
                            "api_key": "",
                        },
                    },
                }).encode("utf-8"),
            ),
        ],
    )
    monkeypatch.setattr(
        shared_sync_remote,
        "_shared_sync_obter_bundle_remoto_para_guard",
        lambda *args, **kwargs: (
            remoto_legado,
            {
                "snapshot_id": "snapshot-remoto",
                "schema": 2,
                "encrypted": True,
            },
        ),
    )

    with pytest.raises(HTTPException) as bloqueado_legado:
        shared_sync_merge_integracoes._shared_sync_validar_push_lojas_integracoes(
            "bundle-lojas",
            local_legado,
            base_snapshot_id="snapshot-remoto",
        )

    assert bloqueado_legado.value.status_code == 409


def test_estado_legado_recebe_snapshot_id_e_skip_nao_apaga_base(monkeypatch):
    estado = {
        "scopes": {
            "machine-sync:lojas_integracoes": {
                "snapshot_hash": "hash-base",
                "direction": "pull",
            },
        },
    }

    def ler(*_args):
        return json.loads(json.dumps(estado))

    def salvar(_client_id, _username, payload):
        estado.clear()
        estado.update(json.loads(json.dumps(payload)))

    monkeypatch.setattr(shared_sync_config, "_shared_sync_state_read", ler)
    monkeypatch.setattr(shared_sync_config, "_shared_sync_state_write", salvar)
    meta = {
        "snapshot_hash": "hash-base",
        "snapshot_id": "snapshot-base",
        "schema": 2,
        "encrypted": True,
        "updated_at": "2026-09-02T12:00:00Z",
    }

    assert shared_sync_config._shared_sync_pull_already_current(
        "000002",
        "operador",
        "machine-sync:lojas_integracoes",
        meta,
    ) is False
    scope_state = estado["scopes"]["machine-sync:lojas_integracoes"]
    assert "snapshot_id" not in scope_state

    shared_sync_config._shared_sync_state_update(
        "000002",
        "operador",
        "machine-sync:lojas_integracoes",
        meta,
        "pull",
    )
    shared_sync_config._shared_sync_state_mark_skipped(
        "000002",
        "operador",
        "machine-sync:lojas_integracoes",
        direction="push",
        reason="already_current",
    )

    scope_state = estado["scopes"]["machine-sync:lojas_integracoes"]
    assert scope_state["snapshot_hash"] == "hash-base"
    assert scope_state["snapshot_id"] == "snapshot-base"
    assert scope_state["skipped"] is True

    shared_sync_config._shared_sync_state_update(
        "000002",
        "operador",
        "machine-sync:lojas_integracoes",
        meta,
        "pull",
    )
    scope_state = estado["scopes"]["machine-sync:lojas_integracoes"]
    assert "skipped" not in scope_state
    assert "reason" not in scope_state

    meta_v1 = {
        "id": "ponteiro-mutavel-v1",
        "snapshot_id": "nao-confiavel",
        "snapshot_hash": "hash-v1",
        "schema": 1,
    }
    shared_sync_config._shared_sync_state_update(
        "000002",
        "operador",
        "machine-sync:lojas_integracoes",
        meta_v1,
        "pull",
    )
    scope_state = estado["scopes"]["machine-sync:lojas_integracoes"]
    assert scope_state["snapshot_hash"] == "hash-v1"
    assert scope_state["snapshot_id"] == ""


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
    monkeypatch.setattr(shared_sync_machine, "_shared_sync_state_snapshot_id", lambda *args: "")
    monkeypatch.setattr(
        shared_sync_machine,
        "_shared_sync_obter_bundle_remoto_para_guard",
        lambda _bundle_id, **_kwargs: (b"pacote-confirmado", dict(meta)),
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
        "_shared_sync_obter_bundle_remoto_para_guard",
        lambda *args, **kwargs: pytest.fail("o pacote automatico ja atual nao deveria ser baixado"),
    )

    result = shared_sync_machine._shared_sync_machine_pull_scope(sessao, "lojas_integracoes")

    assert result["skipped"] is True
    assert result["reason"] == "already_current"


def test_importacao_de_maquina_recupera_base_v2_para_estado_legado_com_apenas_hash(monkeypatch):
    sessao = {"username": "operador", "client_id": "000002"}
    hash_base = "a" * 64
    hash_atual = "b" * 64
    meta = {
        "id": "bundle-lojas",
        "pointer_id": "bundle-lojas",
        "snapshot_id": "snapshot-atual",
        "snapshot_hash": hash_atual,
        "schema": 2,
        "encrypted": True,
        "updated_at": "2026-09-03T12:15:21Z",
    }
    aplicacoes = []
    recuperacoes = []

    monkeypatch.setattr(shared_sync_machine, "_shared_sync_machine_doc_id", lambda *args: "bundle-lojas")
    monkeypatch.setattr(shared_sync_machine, "_shared_sync_remote_meta_by_id", lambda _bundle_id: dict(meta))
    monkeypatch.setattr(shared_sync_machine, "_shared_sync_pull_already_current", lambda *args: False)
    monkeypatch.setattr(shared_sync_machine, "_shared_sync_state_snapshot_id", lambda *args: "")
    monkeypatch.setattr(shared_sync_machine, "_shared_sync_state_snapshot_hash", lambda *args: hash_base)
    monkeypatch.setattr(
        shared_sync_machine,
        "_shared_sync_obter_bundle_remoto_para_guard",
        lambda *args, **kwargs: (b"snapshot-atual", dict(meta)),
    )

    def recuperar(bundle_id, snapshot_hash, **kwargs):
        recuperacoes.append((bundle_id, snapshot_hash, kwargs))
        return b"snapshot-base", {"snapshot_id": "snapshot-base"}

    monkeypatch.setattr(
        shared_sync_machine,
        "_shared_sync_obter_base_causal_por_hash",
        recuperar,
    )
    monkeypatch.setattr(
        shared_sync_machine,
        "_shared_sync_aplicar_pacote",
        lambda *args, **kwargs: aplicacoes.append((args, kwargs)) or {"file_count": 2, "stores_count": 11},
    )
    monkeypatch.setattr(shared_sync_machine, "_shared_sync_state_update", lambda *args, **kwargs: None)

    result = shared_sync_machine._shared_sync_machine_pull_scope(
        sessao,
        "lojas_integracoes",
        force=True,
    )

    assert result["success"] is True
    assert result["stores_count"] == 11
    assert recuperacoes == [(
        "bundle-lojas",
        hash_base,
        {
            "expected_client_id": "000002",
            "expected_scope": "lojas_integracoes",
            "key_context": {"sessao": sessao, "machine_id": ""},
        },
    )]
    assert aplicacoes[0][0][-1]["base_bundle"] == b"snapshot-base"
    assert aplicacoes[0][0][-1]["strict_oauth_conflicts"] is True


def test_importacao_de_maquina_sem_base_legada_confiavel_continua_fechada(monkeypatch):
    sessao = {"username": "operador", "client_id": "000002"}
    meta = {
        "id": "bundle-lojas",
        "pointer_id": "bundle-lojas",
        "snapshot_id": "snapshot-atual",
        "snapshot_hash": "b" * 64,
        "schema": 2,
        "encrypted": True,
    }
    configuracoes = []
    monkeypatch.setattr(shared_sync_machine, "_shared_sync_machine_doc_id", lambda *args: "bundle-lojas")
    monkeypatch.setattr(shared_sync_machine, "_shared_sync_remote_meta_by_id", lambda _bundle_id: dict(meta))
    monkeypatch.setattr(shared_sync_machine, "_shared_sync_pull_already_current", lambda *args: False)
    monkeypatch.setattr(shared_sync_machine, "_shared_sync_state_snapshot_id", lambda *args: "")
    monkeypatch.setattr(shared_sync_machine, "_shared_sync_state_snapshot_hash", lambda *args: "a" * 64)
    monkeypatch.setattr(
        shared_sync_machine,
        "_shared_sync_obter_bundle_remoto_para_guard",
        lambda *args, **kwargs: (b"snapshot-atual", dict(meta)),
    )
    monkeypatch.setattr(
        shared_sync_machine,
        "_shared_sync_obter_base_causal_por_hash",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        shared_sync_machine,
        "_shared_sync_aplicar_pacote",
        lambda *args, **kwargs: configuracoes.append(args[-1]) or {"file_count": 2},
    )
    monkeypatch.setattr(shared_sync_machine, "_shared_sync_state_update", lambda *args, **kwargs: None)

    shared_sync_machine._shared_sync_machine_pull_scope(
        sessao,
        "lojas_integracoes",
        force=True,
    )

    assert configuracoes[0]["base_bundle"] is None
    assert configuracoes[0]["strict_oauth_conflicts"] is True


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
    monkeypatch.setattr(
        shared_sync_machine_endpoints,
        "_shared_sync_require_operation",
        lambda *args, **kwargs: {
            "id": "op",
                "totals": {"stores": 1},
                "remote_snapshot_ids": {"lojas_integracoes": "snapshot-previa"},
                "remote_hashes": {"lojas_integracoes": "fingerprint-previa"},
                "remote_bundle_hashes": {"lojas_integracoes": "bundle-hash-previa"},
        },
    )
    monkeypatch.setattr(
        shared_sync_machine_endpoints,
        "_shared_sync_machine_pull_scope",
        lambda _sessao, scope, *, force=False, machine_id="", expected_snapshot_id="", expected_remote_fingerprint="", expected_bundle_hash="": chamadas.append((scope, force, expected_snapshot_id, expected_remote_fingerprint, expected_bundle_hash)) or {
            "scope": scope,
            "success": True,
            "file_count": 1,
            "stores_count": 2,
            "snapshot_stores_count": 1,
        },
    )
    monkeypatch.setattr(shared_sync_machine_endpoints, "_shared_sync_audit", lambda *args, **kwargs: None)

    result = shared_sync_machine_endpoints.shared_sync_machine_pull(
        SharedSyncRunRequest(scopes=["lojas_integracoes"], operation_id="op"),
        authorization="Bearer teste",
        client_id="000002",
    )

    assert result["success"] is True
    assert chamadas == [(
        "lojas_integracoes",
        True,
        "snapshot-previa",
        "fingerprint-previa",
        "bundle-hash-previa",
    )]


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
            "stores_count": 7,
            "snapshot_stores_count": 3,
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
    assert "previa continha 4 loja(s), mas o pacote recebido continha 3" in result["results"][0]["message"]


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

    def pull_scope(
        _sessao,
        scope,
        *,
        force=False,
        machine_id="",
        expected_snapshot_id="",
        expected_remote_fingerprint="",
        expected_bundle_hash="",
    ):
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


def test_base_causal_por_hash_escolhe_apenas_snapshot_v2_completo_do_mesmo_ponteiro(monkeypatch):
    db = _FakeFirestore()
    coll = db.collection("shared_sync")
    hash_base = "a" * 64
    base = {
        "id": "bundle-lojas",
        "pointer_id": "bundle-lojas",
        "schema": 2,
        "encrypted": True,
        "status": "complete",
        "snapshot_hash": hash_base,
        "client_id": "000002",
        "scope": "lojas_integracoes",
    }
    coll.data["snapshot-antigo"] = {
        **base,
        "snapshot_id": "snapshot-antigo",
        "updated_ts": 10,
    }
    coll.data["snapshot-recente"] = {
        **base,
        "snapshot_id": "snapshot-recente",
        "updated_ts": 20,
    }
    coll.data["snapshot-uploading"] = {
        **base,
        "snapshot_id": "snapshot-uploading",
        "status": "uploading",
        "updated_ts": 30,
    }
    coll.data["snapshot-outro-tenant"] = {
        **base,
        "snapshot_id": "snapshot-outro-tenant",
        "client_id": "000003",
        "updated_ts": 40,
    }
    coll.data["snapshot-outro-ponteiro"] = {
        **base,
        "pointer_id": "bundle-outro",
        "snapshot_id": "snapshot-outro-ponteiro",
        "updated_ts": 50,
    }
    monkeypatch.setattr(shared_sync_remote, "_shared_sync_firestore_required", lambda: db)
    monkeypatch.setattr(shared_sync_remote, "_firebase_shared_sync_collection_name", lambda: "shared_sync")

    meta = shared_sync_remote._shared_sync_remote_snapshot_meta_by_hash(
        "bundle-lojas",
        hash_base,
        expected_client_id="000002",
        expected_scope="lojas_integracoes",
    )

    assert meta is not None
    assert meta["snapshot_id"] == "snapshot-recente"
    assert meta["_guard_pointer_id"] == "snapshot-recente"
    assert shared_sync_remote._shared_sync_remote_snapshot_meta_by_hash(
        "bundle-lojas",
        "hash-invalido",
        expected_client_id="000002",
        expected_scope="lojas_integracoes",
    ) is None


def test_base_causal_por_hash_revalida_manifest_antes_de_usar_snapshot(monkeypatch):
    bundle = _scope_bundle(
        "lojas_integracoes",
        [("lojas_config.json", b"[]")],
    )
    manifest = shared_sync_remote._shared_sync_manifest_from_bundle(bundle)
    snapshot_hash = str(manifest["snapshot_hash"])
    meta = {
        "snapshot_id": "snapshot-base",
        "snapshot_hash": snapshot_hash,
        "scope": "lojas_integracoes",
    }
    monkeypatch.setattr(
        shared_sync_remote,
        "_shared_sync_remote_snapshot_meta_by_hash",
        lambda *args, **kwargs: dict(meta),
    )
    monkeypatch.setattr(
        shared_sync_remote,
        "_shared_sync_obter_bundle_por_id",
        lambda *args, **kwargs: (bundle, dict(meta)),
    )

    recovered = shared_sync_remote._shared_sync_obter_base_causal_por_hash(
        "bundle-lojas",
        snapshot_hash,
        expected_client_id="000002",
        expected_scope="lojas_integracoes",
    )
    rejected = shared_sync_remote._shared_sync_obter_base_causal_por_hash(
        "bundle-lojas",
        "f" * 64,
        expected_client_id="000002",
        expected_scope="lojas_integracoes",
    )

    assert recovered is not None
    assert recovered[0] == bundle
    assert rejected is None


def test_pointer_v2_autoritativo_isola_writer_legado_e_mantem_cas(monkeypatch):
    db = _FakeFirestore()
    monkeypatch.setattr(
        shared_sync_remote,
        "_firebase_shared_sync_collection_name",
        lambda: "shared_sync",
    )
    pointer = db.collection("shared_sync").document("bundle-lojas")
    meta_a = {
        "id": "bundle-lojas",
        "snapshot_id": "snapshot-a",
        "schema": 2,
        "encrypted": True,
        "chunk_count": 1,
    }
    pointer.set(meta_a)
    expected_a = shared_sync_remote._shared_sync_guard_expectation_from_meta(
        {**meta_a, "_guard_pointer_id": "bundle-lojas"},
        "bundle-lojas",
    )

    shared_sync_remote._shared_sync_publicar_pointer_cas(
        db,
        "bundle-lojas",
        {**meta_a, "snapshot_id": "snapshot-b"},
        expected_a,
    )
    assert pointer.get().to_dict()["snapshot_id"] == "snapshot-b"
    assert pointer.get().to_dict()["chunk_count"] == 0
    authority = db.collection("shared_sync").document(
        shared_sync_remote._shared_sync_v2_authority_id("bundle-lojas")
    )
    assert authority.get().to_dict()["snapshot_id"] == "snapshot-b"
    assert authority.get().to_dict()["chunk_count"] == 1
    expected_b = shared_sync_remote._shared_sync_guard_expectation_from_meta(
        {
            **authority.get().to_dict(),
            "_guard_pointer_id": authority.id,
        },
        "bundle-lojas",
    )

    # Um writer antigo ainda consegue tocar o ponteiro conhecido por ele, mas
    # nao altera mais a autoridade consumida pelas versoes novas.
    pointer.set({
        "id": "bundle-lojas",
        "schema": 1,
        "encrypted": False,
        "snapshot_hash": "c" * 64,
        "chunk_count": 1,
    })
    shared_sync_remote._shared_sync_publicar_pointer_cas(
        db,
        "bundle-lojas",
        {**meta_a, "snapshot_id": "snapshot-d"},
        expected_b,
    )
    assert pointer.get().to_dict()["snapshot_id"] == "snapshot-d"
    assert authority.get().to_dict()["snapshot_id"] == "snapshot-d"

    with pytest.raises(HTTPException) as exc_info:
        shared_sync_remote._shared_sync_publicar_pointer_cas(
            db,
            "bundle-lojas",
            {**meta_a, "snapshot_id": "snapshot-e"},
            expected_b,
        )

    assert exc_info.value.status_code == 409
    assert pointer.get().to_dict()["snapshot_id"] == "snapshot-d"
    assert authority.get().to_dict()["snapshot_id"] == "snapshot-d"


def test_leitor_novo_prefere_autoridade_v2_apos_writer_legado(monkeypatch):
    db = _FakeFirestore()
    coll = db.collection("shared_sync")
    coll.data["bundle-lojas"] = {
        "id": "bundle-lojas",
        "schema": 1,
        "encrypted": False,
        "snapshot_hash": "b" * 64,
    }
    authority_id = shared_sync_remote._shared_sync_v2_authority_id(
        "bundle-lojas"
    )
    coll.data[authority_id] = {
        "id": "bundle-lojas",
        "snapshot_id": "snapshot-c",
        "schema": 2,
        "encrypted": True,
        "snapshot_hash": "c" * 64,
        "chunk_count": 1,
    }
    monkeypatch.setattr(shared_sync_remote, "_firebase_db", lambda: db, raising=False)
    monkeypatch.setattr(shared_sync_remote, "_firebase_deve_usar", lambda: True, raising=False)
    monkeypatch.setattr(shared_sync_remote, "_firebase_shared_sync_collection_name", lambda: "shared_sync")

    meta = shared_sync_remote._shared_sync_remote_meta_by_id("bundle-lojas")

    assert meta["snapshot_id"] == "snapshot-c"
    assert meta["snapshot_hash"] == "c" * 64
    assert meta["_guard_pointer_id"] == authority_id


def test_pointer_v1_com_mesmo_id_bloqueia_revisao_concorrente(monkeypatch):
    db = _FakeFirestore()
    monkeypatch.setattr(
        shared_sync_remote,
        "_firebase_shared_sync_collection_name",
        lambda: "shared_sync",
    )
    pointer = db.collection("shared_sync").document("bundle-lojas")
    meta_a = {
        "id": "bundle-lojas",
        "schema": 1,
        "encrypted": False,
        "snapshot_hash": "a" * 64,
        "updated_at": "2026-09-02T10:00:00Z",
        "chunk_count": 1,
        "bundle_bytes": 100,
    }
    pointer.set(meta_a)
    revision_a = shared_sync_remote._shared_sync_guard_expectation_from_meta(
        {**meta_a, "_guard_pointer_id": "bundle-lojas"},
        "bundle-lojas",
    )
    pointer.set({
        **meta_a,
        "snapshot_hash": "b" * 64,
        "updated_at": "2026-09-02T10:01:00Z",
    })

    with pytest.raises(HTTPException) as exc_info:
        shared_sync_remote._shared_sync_publicar_pointer_cas(
            db,
            "bundle-lojas",
            {"id": "bundle-lojas", "snapshot_id": "snapshot-v2"},
            revision_a,
        )

    assert exc_info.value.status_code == 409
    assert pointer.get().to_dict()["snapshot_hash"] == "b" * 64


def test_guard_le_e_valida_snapshot_v1_para_migracao_segura(monkeypatch):
    db = _FakeFirestore()
    bundle, snapshot_hash = _scope_bundle_v1(
        "lojas_integracoes",
        [("lojas_config.json", json.dumps([{
            "nome": "Loja v1",
            "store_id": "store-v1",
            "integracoes": {},
        }]).encode("utf-8"))],
    )
    db.collection("shared_sync").data["bundle-v1"] = {
        "id": "bundle-v1",
        "scope": "lojas_integracoes",
        "schema": 1,
        "encrypted": False,
        "chunk_count": 1,
        "bundle_bytes": len(bundle),
        "snapshot_hash": snapshot_hash,
    }
    db.collection("shared_sync_chunks").data["bundle-v1_00000"] = {
        "bundle_id": "bundle-v1",
        "data": base64.b64encode(bundle).decode("ascii"),
    }
    monkeypatch.setattr(
        shared_sync_remote,
        "_shared_sync_firestore_required",
        lambda: db,
    )
    monkeypatch.setattr(
        shared_sync_remote,
        "_firebase_shared_sync_collection_name",
        lambda: "shared_sync",
    )
    monkeypatch.setattr(
        shared_sync_remote,
        "_firebase_shared_sync_chunks_collection_name",
        lambda: "shared_sync_chunks",
    )

    opened, meta = (
        shared_sync_remote._shared_sync_obter_bundle_remoto_para_guard(
            "bundle-v1"
        )
    )

    assert opened == bundle
    assert meta["legacy_plaintext"] is True
    assert meta.get("snapshot_id") in (None, "")


def test_guard_v1_rejeita_manifesto_com_snapshot_hash_nao_recalculado(monkeypatch):
    db = _FakeFirestore()
    arquivos = [("lojas_config.json", b'[{"nome":"Loja alterada"}]')]
    bundle, snapshot_hash = _scope_bundle_v1("lojas_integracoes", arquivos)
    with zipfile.ZipFile(io.BytesIO(bundle), "r") as source:
        manifest = json.loads(source.read("manifest.json"))
        data = source.read("files/lojas_config.json")
    manifest["snapshot_hash"] = "0" * 64
    adulterado = io.BytesIO()
    with zipfile.ZipFile(adulterado, "w", compression=zipfile.ZIP_DEFLATED) as target:
        target.writestr("manifest.json", json.dumps(manifest))
        target.writestr("files/lojas_config.json", data)
    payload = adulterado.getvalue()
    db.collection("shared_sync").data["bundle-v1"] = {
        "id": "bundle-v1",
        "scope": "lojas_integracoes",
        "schema": 1,
        "encrypted": False,
        "chunk_count": 1,
        "bundle_bytes": len(payload),
        "snapshot_hash": snapshot_hash,
    }
    db.collection("shared_sync_chunks").data["bundle-v1_00000"] = {
        "bundle_id": "bundle-v1",
        "data": base64.b64encode(payload).decode("ascii"),
    }
    monkeypatch.setattr(shared_sync_remote, "_shared_sync_firestore_required", lambda: db)
    monkeypatch.setattr(shared_sync_remote, "_firebase_shared_sync_collection_name", lambda: "shared_sync")
    monkeypatch.setattr(shared_sync_remote, "_firebase_shared_sync_chunks_collection_name", lambda: "shared_sync_chunks")

    with pytest.raises(HTTPException) as exc_info:
        shared_sync_remote._shared_sync_obter_bundle_remoto_para_guard("bundle-v1")

    assert exc_info.value.status_code == 502


def test_leitura_autoritativa_da_guarda_distingue_ausencia_de_falha(monkeypatch):
    db = _FakeFirestore()
    monkeypatch.setattr(shared_sync_remote, "_shared_sync_firestore_required", lambda: db)
    monkeypatch.setattr(
        shared_sync_remote,
        "_firebase_shared_sync_collection_name",
        lambda: "shared_sync",
    )
    assert shared_sync_remote._shared_sync_obter_bundle_remoto_para_guard("ausente") is None

    class FailingDocument:
        def get(self):
            raise RuntimeError("firestore indisponivel")

    class FailingCollection:
        def document(self, _doc_id):
            return FailingDocument()

    class FailingFirestore:
        def collection(self, _name):
            return FailingCollection()

    monkeypatch.setattr(
        shared_sync_remote,
        "_shared_sync_firestore_required",
        lambda: FailingFirestore(),
    )
    with pytest.raises(HTTPException) as exc_info:
        shared_sync_remote._shared_sync_obter_bundle_remoto_para_guard("bundle-lojas")
    assert exc_info.value.status_code == 503


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
    lojas_destino = [{
        "nome": "Loja antiga do destino",
        "store_id": "store-exclusiva-destino",
        "integracoes": {"bling": {"access_token": "token-exclusivo-destino"}},
    }]
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
    monkeypatch.setattr(shared_sync_remote, "_shared_sync_state_snapshot_id", lambda *args, **kwargs: "")
    monkeypatch.setattr(shared_sync_remote, "_shared_sync_state_snapshot_hash", lambda *args, **kwargs: "")
    monkeypatch.setattr(shared_sync_machine, "_shared_sync_state_update", lambda *args, **kwargs: None)
    monkeypatch.setattr(shared_sync_machine, "_shared_sync_state_snapshot_id", lambda *args, **kwargs: "")
    monkeypatch.setattr(shared_sync_machine, "_shared_sync_pull_already_current", lambda *args, **kwargs: False)

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
    assert recebido["stores_count"] == 5
    assert recebido["snapshot_stores_count"] == 4
    assert [loja["nome"] for loja in lojas_recebidas] == [
        "Loja antiga do destino",
        *[loja["nome"] for loja in lojas_origem],
    ]
    assert lojas_recebidas[0]["integracoes"]["bling"]["access_token"] == "token-exclusivo-destino"
    assert lojas_recebidas[1]["integracoes"]["mercadolivre"]["access_token"] == "token-1"


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
        admin_usuarios_login_core,
        "_firebase_obter_usuario",
        lambda username: dict(users.data.get(username) or {}) or None,
    )
    monkeypatch.setattr(admin_usuarios_login_core, "_firebase_user_cache_invalidate", lambda _username: None)
    monkeypatch.setattr(admin_usuarios_login_core, "_firebase_call_timeout_seconds", lambda: 5.0)
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


def test_push_igual_so_pula_depois_de_criar_autoridade_v2(monkeypatch):
    db = _FakeFirestore()
    bundle = _scope_bundle(
        "lojas_integracoes",
        [("lojas_config.json", b"[]")],
    )
    manifest = {
        "schema": 2,
        "created_at": "2026-09-02T12:00:00Z",
        "snapshot_hash": "hash-igual",
        "file_count": 1,
        "item_count": 0,
        "item_keys": [],
        "delta": False,
        "files": [],
    }
    monkeypatch.setattr(shared_sync_remote, "_shared_sync_firestore_required", lambda: db)
    monkeypatch.setattr(
        shared_sync_remote,
        "_shared_sync_montar_pacote",
        lambda *args, **kwargs: (bundle, manifest, []),
    )
    monkeypatch.setattr(shared_sync_remote, "_shared_sync_state_snapshot_id", lambda *args: "")
    monkeypatch.setattr(shared_sync_remote, "_shared_sync_state_update", lambda *args: None)
    chamadas_guard = []

    def bloquear_para_provar_chamada(*args, **kwargs):
        chamadas_guard.append(True)
        raise HTTPException(status_code=418, detail="guard chamado")

    monkeypatch.setattr(
        shared_sync_remote,
        "_shared_sync_validar_push_lojas_integracoes",
        bloquear_para_provar_chamada,
        raising=False,
    )
    monkeypatch.setattr(
        shared_sync_remote,
        "_shared_sync_remote_meta_by_id",
        lambda *_args: {
            "id": "bundle-lojas",
            "snapshot_hash": "hash-igual",
            "_guard_pointer_id": "bundle-lojas",
        },
    )

    with pytest.raises(HTTPException) as sem_autoridade:
        shared_sync_remote._shared_sync_push_scope(
            "000002",
            "lojas_integracoes",
            {"username": "origem"},
            bundle_id="bundle-lojas",
            skip_if_remote_hash_matches=True,
        )
    assert sem_autoridade.value.status_code == 418
    assert chamadas_guard == [True]

    authority_id = shared_sync_remote._shared_sync_v2_authority_id(
        "bundle-lojas"
    )
    monkeypatch.setattr(
        shared_sync_remote,
        "_shared_sync_remote_meta_by_id",
        lambda *_args: {
            "id": "bundle-lojas",
            "snapshot_id": "snapshot-v2",
            "schema": 2,
            "encrypted": True,
            "snapshot_hash": "hash-igual",
            "_guard_pointer_id": authority_id,
        },
    )
    result = shared_sync_remote._shared_sync_push_scope(
        "000002",
        "lojas_integracoes",
        {"username": "origem"},
        bundle_id="bundle-lojas",
        skip_if_remote_hash_matches=True,
    )

    assert result["skipped"] is True
    assert result["reason"] == "already_current"
    assert chamadas_guard == [True]


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


def test_retencao_nunca_remove_autoridade_nem_candidato_pending(monkeypatch):
    db = _FakeFirestore()
    monkeypatch.setattr(shared_sync_remote, "_firebase_shared_sync_collection_name", lambda: "shared_sync")
    monkeypatch.setattr(shared_sync_remote, "_firebase_shared_sync_chunks_collection_name", lambda: "shared_sync_chunks")
    now = int(time.time())
    old = now - (10 * 24 * 60 * 60)
    metas = db.collection("shared_sync").data
    authority_id = shared_sync_remote._shared_sync_v2_authority_id("bundle")
    metas[authority_id] = {
        "id": "bundle",
        "snapshot_id": "snapshot-b",
        "schema": 2,
        "encrypted": True,
    }
    metas["snapshot-a"] = {
        "pointer_id": "bundle",
        "status": "complete",
        "updated_ts": old - 1,
    }
    metas["snapshot-b"] = {
        "pointer_id": "bundle",
        "status": "complete",
        "updated_ts": old,
    }
    metas["snapshot-c"] = {
        "pointer_id": "bundle",
        "status": "complete",
        "updated_ts": now,
    }
    metas["snapshot-d"] = {
        "pointer_id": "bundle",
        "status": "complete",
        "updated_ts": now - 1,
    }
    metas["snapshot-pending"] = {
        "pointer_id": "bundle",
        "status": "pending",
        "updated_ts": old - 2,
    }
    chunks = db.collection("shared_sync_chunks").data
    for snapshot_id in (
        "snapshot-a",
        "snapshot-b",
        "snapshot-pending",
    ):
        chunks[f"{snapshot_id}_00000"] = {
            "bundle_id": snapshot_id,
            "data": "cipher",
        }

    shared_sync_remote._shared_sync_cleanup_old_snapshots(
        db,
        "bundle",
        "snapshot-a",
    )

    assert "snapshot-a" in metas
    assert "snapshot-b" in metas
    assert "snapshot-pending" in metas
    assert "snapshot-b_00000" in chunks
    assert "snapshot-pending_00000" in chunks


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
            {
                "store_id": f"store-{item}",
                "nome": f"Loja {index}-{item}",
                "integracoes": {
                    "mercadolivre": {"access_token": f"token-{index}-{item}"}
                },
            }
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
    lojas = [{"nome": "Loja", "integracoes": {"bling": {"access_token": "token", "connected": True}}}]
    integracoes.salvar_lojas("000002", lojas)
    integracoes.desconectar_api_loja(
        "000002",
        "Loja",
        "bling",
        store_id=lojas[0]["store_id"],
    )
    lojas = json.loads((info / "000002" / "lojas_config.json").read_text(encoding="utf-8"))
    tombstones = json.loads((info / "000002" / "lojas_sync_tombstones.json").read_text(encoding="utf-8"))
    assert lojas[0]["store_id"]
    assert lojas[0]["integracoes"]["bling"]["connected"] is False
    assert tombstones[-1]["type"] == "integration"
    assert tombstones[-1]["store_id"] == lojas[0]["store_id"]
    assert tombstones[-1]["version"] == lojas[0]["integracoes"]["bling"]["_sync_version"]


def test_pull_machine_serializa_aplicacao_e_estado_causal(monkeypatch):
    sessao = {"username": "operador", "client_id": "000002"}
    iniciou_primeiro_apply = threading.Event()
    liberar_primeiro_apply = threading.Event()
    chamadas_meta = []
    aplicados = []
    estados = []
    controle = threading.Lock()

    monkeypatch.setattr(
        shared_sync_machine,
        "_shared_sync_machine_doc_id",
        lambda *args: "bundle-cadastro",
    )

    def remote_meta(_bundle_id):
        with controle:
            indice = len(chamadas_meta)
            snapshot = "snapshot-a" if indice == 0 else "snapshot-b"
            chamadas_meta.append(snapshot)
        return {
            "snapshot_id": snapshot,
            "snapshot_hash": f"hash-{snapshot}",
        }

    monkeypatch.setattr(
        shared_sync_machine,
        "_shared_sync_remote_meta_by_id",
        remote_meta,
    )
    monkeypatch.setattr(
        shared_sync_machine,
        "_shared_sync_pull_already_current",
        lambda *args: False,
    )
    monkeypatch.setattr(
        shared_sync_machine,
        "_shared_sync_obter_bundle_por_id",
        lambda _bundle_id, meta, **kwargs: (
            str(meta["snapshot_id"]).encode("utf-8"),
            meta,
        ),
    )

    def aplicar(_client_id, _scope, bundle, *_args, **_kwargs):
        snapshot = bundle.decode("utf-8")
        aplicados.append(snapshot)
        if snapshot == "snapshot-a":
            iniciou_primeiro_apply.set()
            assert liberar_primeiro_apply.wait(timeout=5)
        return {"file_count": 1}

    monkeypatch.setattr(shared_sync_machine, "_shared_sync_aplicar_pacote", aplicar)
    monkeypatch.setattr(
        shared_sync_machine,
        "_shared_sync_state_update",
        lambda _client, _user, _scope, meta, _direction: estados.append(
            meta["snapshot_id"]
        ),
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        primeiro = pool.submit(
            shared_sync_machine._shared_sync_machine_pull_scope,
            sessao,
            "cadastro",
        )
        assert iniciou_primeiro_apply.wait(timeout=5)
        segundo = pool.submit(
            shared_sync_machine._shared_sync_machine_pull_scope,
            sessao,
            "cadastro",
        )
        time.sleep(0.05)
        assert chamadas_meta == ["snapshot-a"]
        liberar_primeiro_apply.set()
        assert primeiro.result(timeout=5)["success"] is True
        assert segundo.result(timeout=5)["success"] is True

    assert aplicados == ["snapshot-a", "snapshot-b"]
    assert estados == ["snapshot-a", "snapshot-b"]


def test_merge_integracoes_legadas_usa_base_e_bloqueia_conflito_duplo(tmp_path):
    target = tmp_path / "integracoes.json"
    bloco_a = {
        "id": "app",
        "secret": "secret",
        "access_token": "access-a",
        "refresh_token": "refresh-a",
    }
    bloco_b = {**bloco_a, "access_token": "access-b", "refresh_token": "refresh-b"}
    bloco_c = {**bloco_a, "access_token": "access-c", "refresh_token": "refresh-c"}
    base = {"Loja": {"bling": bloco_a}}
    remoto = {"Loja": {"bling": bloco_b}}
    target.write_text(json.dumps(base), encoding="utf-8")

    merged = json.loads(
        shared_sync_merge_integracoes._shared_sync_merge_integracoes_legacy_bytes(
            str(target),
            json.dumps(remoto).encode("utf-8"),
            base_bytes=json.dumps(base).encode("utf-8"),
            strict_oauth_conflicts=True,
        )
    )
    assert merged["Loja"]["bling"]["access_token"] == "access-b"
    assert merged["Loja"]["bling"]["refresh_token"] == "refresh-b"

    target.write_text(
        json.dumps({"Loja": {"bling": bloco_c}}),
        encoding="utf-8",
    )
    with pytest.raises(HTTPException) as conflito:
        shared_sync_merge_integracoes._shared_sync_merge_integracoes_legacy_bytes(
            str(target),
            json.dumps(remoto).encode("utf-8"),
            base_bytes=json.dumps(base).encode("utf-8"),
            strict_oauth_conflicts=True,
        )
    assert conflito.value.status_code == 409

    with pytest.raises(HTTPException) as sem_base:
        shared_sync_merge_integracoes._shared_sync_merge_integracoes_legacy_bytes(
            str(target),
            json.dumps(remoto).encode("utf-8"),
            strict_oauth_conflicts=True,
        )
    assert sem_base.value.status_code == 409


def test_merge_integracoes_legadas_nao_combina_nomes_crus_ambiguos(tmp_path):
    target = tmp_path / "integracoes.json"
    local = {
        "Loja A": {
            "mercadolivre": {
                "app_id": "app-local",
                "client_secret": "secret-local",
            },
        },
    }
    remoto = {
        "Loja Á": {
            "bling": {
                "id": "app-remoto",
                "secret": "secret-remoto",
            },
        },
    }
    target.write_text(json.dumps(local), encoding="utf-8")

    with pytest.raises(HTTPException) as bloqueado:
        shared_sync_merge_integracoes._shared_sync_merge_integracoes_legacy_bytes(
            str(target),
            json.dumps(remoto).encode("utf-8"),
            strict_oauth_conflicts=True,
        )
    assert bloqueado.value.status_code == 409

    preservado = json.loads(
        shared_sync_merge_integracoes._shared_sync_merge_integracoes_legacy_bytes(
            str(target),
            json.dumps(remoto).encode("utf-8"),
            strict_oauth_conflicts=False,
        )
    )
    assert set(preservado) == {"Loja A", "Loja Á"}
    assert set(preservado["Loja A"]) == {"mercadolivre"}
    assert set(preservado["Loja Á"]) == {"bling"}


def test_merge_ml_enriquece_user_id_legado_sem_trocar_oauth():
    oauth = {
        "app_id": "app",
        "client_secret": "secret",
        "access_token": "access",
        "refresh_token": "refresh",
    }
    legado = dict(oauth)
    identificado = {**oauth, "user_id": "seller-1"}

    enriquecido = shared_sync_merge_integracoes._shared_sync_merge_integracao_loja(
        legado,
        identificado,
        servico_key="mercadolivre",
        strict_oauth_conflicts=True,
    )
    preservado = shared_sync_merge_integracoes._shared_sync_merge_integracao_loja(
        identificado,
        legado,
        servico_key="mercadolivre",
        strict_oauth_conflicts=True,
    )

    assert enriquecido["user_id"] == "seller-1"
    assert preservado["user_id"] == "seller-1"
    assert enriquecido["access_token"] == preservado["access_token"] == "access"


@pytest.mark.parametrize(
    "arquivos",
    [
        [("lojas_config.json", json.dumps([{}]).encode("utf-8"))],
        [
            ("lojas_config.json", b"[]"),
            ("lojas_sync_tombstones.json", json.dumps([42]).encode("utf-8")),
        ],
        [
            ("lojas_config.json", b"[]"),
            ("integracoes.json", json.dumps({"Loja": {"bling": "invalido"}}).encode("utf-8")),
        ],
    ],
)
def test_primeiro_push_bloqueia_snapshot_local_invalido(monkeypatch, arquivos):
    monkeypatch.setattr(
        shared_sync_remote,
        "_shared_sync_obter_bundle_remoto_para_guard",
        lambda *args, **kwargs: None,
    )
    bundle = _scope_bundle("lojas_integracoes", arquivos)

    with pytest.raises(HTTPException) as invalido:
        shared_sync_merge_integracoes._shared_sync_validar_push_lojas_integracoes(
            "bundle-lojas",
            bundle,
        )

    assert invalido.value.status_code == 409


def test_preview_conta_credenciais_do_integracoes_json_legado():
    bundle = _scope_bundle(
        "lojas_integracoes",
        [
            ("lojas_config.json", b"[]"),
            (
                "integracoes.json",
                json.dumps({
                    "Loja": {
                        "mercadolivre": {
                            "app_id": "app",
                            "client_secret": "secret",
                            "access_token": "access",
                            "refresh_token": "refresh",
                            "connected": False,
                        },
                    },
                }).encode("utf-8"),
            ),
        ],
    )

    credentials, disconnects, stores = (
        shared_sync_operations._shared_sync_bundle_sensitive_counts(bundle)
    )

    assert credentials == 3
    assert disconnects == 1
    assert stores == 0


def test_push_bloqueia_regressao_de_campos_da_loja_sem_base_causal(monkeypatch):
    remoto = _lojas_bundle([{
        "nome": "Nome remoto",
        "store_id": "store-a",
        "integracoes": {},
    }])
    local = _lojas_bundle([{
        "nome": "Nome local",
        "store_id": "store-a",
        "integracoes": {},
    }])
    monkeypatch.setattr(
        shared_sync_remote,
        "_shared_sync_obter_bundle_remoto_para_guard",
        lambda *args, **kwargs: (
            remoto,
            {"snapshot_id": "snapshot-remoto"},
        ),
    )

    with pytest.raises(HTTPException) as sem_base:
        shared_sync_merge_integracoes._shared_sync_validar_push_lojas_integracoes(
            "bundle-lojas",
            local,
        )
    assert sem_base.value.status_code == 409

    assert shared_sync_merge_integracoes._shared_sync_validar_push_lojas_integracoes(
        "bundle-lojas",
        local,
        base_snapshot_id="snapshot-remoto",
    ) == "snapshot-remoto"


def test_recriacao_explicita_supera_tombstone_sem_apaga_loja_futura(tmp_path):
    info = tmp_path / "info"

    def tenant_path(client_id):
        path = info / str(client_id)
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    integracoes.configure_integracoes_context(
        pasta_info=str(info),
        get_tenant_path=tenant_path,
        normalizar_integracao_conectada=lambda _s, data: data,
    )
    integracoes.salvar_lojas(
        "000002",
        [{"nome": "Loja", "integracoes": {}}],
    )
    loja = integracoes.carregar_lojas("000002")[0]
    integracoes.registrar_tombstone_integracao(
        "000002",
        loja=loja,
        tipo="store",
    )
    integracoes.salvar_lojas(
        "000002",
        [],
        permitir_reducao_confirmada=True,
    )
    nova = integracoes.criar_loja("000002", "Loja")

    tombstones = json.loads(
        (info / "000002" / "lojas_sync_tombstones.json").read_text(
            encoding="utf-8"
        )
    )
    tombstone_antigo = next(
        item
        for item in tombstones
        if item.get("type") == "store"
        and item.get("store_id") == loja["store_id"]
    )
    assert tombstone_antigo["deleted_at"]
    assert not tombstone_antigo.get("restored_at")
    remoto = json.dumps([{
        "nome": "Loja",
        "store_id": loja["store_id"],
        "integracoes": {},
    }]).encode("utf-8")
    target_vazio = tmp_path / "destino" / "lojas_config.json"
    target_vazio.parent.mkdir()
    target_vazio.write_text(json.dumps([nova]), encoding="utf-8")
    merged = json.loads(
        shared_sync_merge_integracoes._shared_sync_merge_lojas_integracoes_bytes(
            str(target_vazio),
            remoto,
            local_tombstones_bytes=json.dumps(tombstones).encode("utf-8"),
        )
    )
    assert [item["store_id"] for item in merged] == [nova["store_id"]]


def test_push_rejeita_hash_local_diferente_da_previa_antes_do_upload(monkeypatch):
    db = _FakeFirestore()
    _configure_remote_push_for_test(monkeypatch, db, b"pacote-local")

    with pytest.raises(HTTPException) as mudou:
        shared_sync_remote._shared_sync_push_scope(
            "000002",
            "cadastro",
            {"username": "origem"},
            bundle_id="bundle",
            expected_snapshot_hash="hash-da-previa",
        )

    assert mudou.value.status_code == 409
    assert db.collection("shared_sync_chunks").data == {}
    assert db.collection("shared_sync").data == {}


def test_aliases_oauth_equivalentes_nao_criam_falso_conflito():
    ml_canonico = {
        "app_id": "app",
        "client_secret": "secret",
        "access_token": "access",
        "refresh_token": "refresh",
        "user_id": "123",
    }
    ml_legado = {
        "id": "app",
        "secret": "secret",
        "access_token": "access",
        "refresh_token": "refresh",
        "user_id": 123,
    }
    nos_dois_sentidos = [
        (ml_canonico, ml_legado),
        (ml_legado, ml_canonico),
    ]
    for local, remoto in nos_dois_sentidos:
        merged = shared_sync_merge_integracoes._shared_sync_merge_integracao_loja(
            local,
            remoto,
            servico_key="mercadolivre",
            strict_oauth_conflicts=True,
        )
        assert str(merged["user_id"]) == "123"
        assert merged["access_token"] == "access"
        assert merged["refresh_token"] == "refresh"

    bling_canonico = {
        "id": "app",
        "secret": "secret",
        "access_token": "access",
        "refresh_token": "refresh",
    }
    bling_alias = {
        "client_id": "app",
        "client_secret": "secret",
        "access_token": "access",
        "refresh_token": "refresh",
    }
    for local, remoto in [
        (bling_canonico, bling_alias),
        (bling_alias, bling_canonico),
    ]:
        merged = shared_sync_merge_integracoes._shared_sync_merge_integracao_loja(
            local,
            remoto,
            servico_key="bling",
            strict_oauth_conflicts=True,
        )
        assert merged["access_token"] == "access"
        assert merged["refresh_token"] == "refresh"


def test_todas_as_rotas_de_pull_usam_mesma_chave_de_destino(monkeypatch):
    chamadas = []

    class Contexto:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def registrar(*partes):
        chamadas.append(partes)
        return Contexto()

    monkeypatch.setattr(shared_sync_machine, "_shared_sync_pull_lock", registrar)
    monkeypatch.setattr(
        shared_sync_machine,
        "_shared_sync_machine_pull_scope_serialized",
        lambda *args, **kwargs: {"success": True},
    )
    monkeypatch.setattr(shared_sync_user_pairs, "_shared_sync_pull_lock", registrar)
    monkeypatch.setattr(
        shared_sync_user_pairs,
        "_shared_sync_pull_pair_scope_serialized",
        lambda *args, **kwargs: {"success": True},
    )
    monkeypatch.setattr(shared_sync_apply_scope, "_shared_sync_pull_lock", registrar)
    monkeypatch.setattr(
        shared_sync_apply_scope,
        "_shared_sync_pull_scope_serialized",
        lambda *args, **kwargs: {"success": True},
    )
    sessao = {"client_id": "000002", "username": "operador"}

    shared_sync_machine._shared_sync_machine_pull_scope(sessao, "cadastro")
    shared_sync_user_pairs._shared_sync_pull_pair_scope(
        sessao,
        {"id": "link"},
        "cadastro",
    )
    shared_sync_apply_scope._shared_sync_pull_scope(
        "000002",
        "cadastro",
        sessao,
    )

    assert chamadas == [
        ("destination", "000002", "cadastro"),
        ("destination", "000002", "cadastro"),
        ("destination", "000002", "cadastro"),
    ]


def test_pull_migra_snapshot_v1_parcial_sem_perder_loja_local(tmp_path, monkeypatch):
    info = tmp_path / "info"

    def tenant_path(client_id):
        path = info / str(client_id)
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    integracoes.configure_integracoes_context(
        pasta_info=str(info),
        get_tenant_path=tenant_path,
        normalizar_integracao_conectada=lambda _s, data: data,
    )
    monkeypatch.setattr(
        shared_sync_apply_scope,
        "get_tenant_path",
        tenant_path,
        raising=False,
    )
    integracoes.salvar_lojas(
        "000002",
        [{
            "nome": "Loja local",
            "store_id": "store-local",
            "integracoes": {
                "bling": {
                    "id": "local",
                    "secret": "secret-local",
                    "access_token": "access-local",
                    "refresh_token": "refresh-local",
                },
            },
        }],
    )
    remoto = [{
        "nome": "Loja remota",
        "store_id": "store-remota",
        "integracoes": {
            "mercadolivre": {
                "app_id": "app-remoto",
                "client_secret": "secret-remoto",
                "access_token": "access-remoto",
                "refresh_token": "refresh-remoto",
                "user_id": "seller-remoto",
            },
        },
    }]
    bundle = _scope_bundle(
        "lojas_integracoes",
        [("lojas_config.json", json.dumps(remoto).encode("utf-8"))],
    )
    meta = {
        "id": "bundle-lojas",
        "schema": 1,
        "encrypted": False,
        "snapshot_hash": "hash-v1",
        "chunk_count": 1,
        "bundle_bytes": len(bundle),
    }
    atualizacoes = []
    leituras_meta = []
    monkeypatch.setattr(
        shared_sync_machine,
        "_shared_sync_machine_doc_id",
        lambda *args: "bundle-lojas",
    )
    def remote_meta(bundle_id):
        leituras_meta.append(bundle_id)
        assert bundle_id == "bundle-lojas"
        return dict(meta)

    monkeypatch.setattr(
        shared_sync_machine,
        "_shared_sync_remote_meta_by_id",
        remote_meta,
    )
    monkeypatch.setattr(
        shared_sync_machine,
        "_shared_sync_pull_already_current",
        lambda *args: False,
    )
    monkeypatch.setattr(
        shared_sync_machine,
        "_shared_sync_obter_bundle_remoto_para_guard",
        lambda *args, **kwargs: (bundle, dict(meta, legacy_plaintext=True)),
    )
    monkeypatch.setattr(
        shared_sync_machine,
        "_shared_sync_state_snapshot_id",
        lambda *args: "snapshot-v2-anterior",
    )
    monkeypatch.setattr(
        shared_sync_machine,
        "_shared_sync_state_update",
        lambda *args: atualizacoes.append(args),
    )

    result = shared_sync_machine._shared_sync_machine_pull_scope(
        {"client_id": "000002", "username": "operador"},
        "lojas_integracoes",
        force=True,
        expected_snapshot_id="bundle-lojas",
        expected_remote_fingerprint=(
            shared_sync_common._shared_sync_remote_fingerprint_from_meta(meta)
        ),
        expected_bundle_hash=hashlib.sha256(bundle).hexdigest(),
    )

    lojas = integracoes.carregar_lojas("000002")
    assert result["success"] is True
    assert result["stores_count"] == 2
    assert result["snapshot_stores_count"] == 1
    assert {loja["store_id"] for loja in lojas} == {
        "store-local",
        "store-remota",
    }
    assert atualizacoes
    assert atualizacoes[-1][3].get("snapshot_id") in (None, "")
    assert leituras_meta == ["bundle-lojas"]


def test_apply_ignora_loja_global_em_tenant_nao_default(tmp_path):
    info = tmp_path / "info"
    info.mkdir()

    def tenant_path(client_id):
        path = info / str(client_id)
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    integracoes.configure_integracoes_context(
        pasta_info=str(info),
        get_tenant_path=tenant_path,
        normalizar_integracao_conectada=lambda _s, data: data,
    )
    nome_local = "Loja local legada"
    store_id_local = integracoes._integracoes_store_id(
        "000002",
        {"nome": nome_local},
    )
    local = [{
        "nome": nome_local,
        "store_id": store_id_local,
        "integracoes": {
            "bling": {
                "id": "app-local",
                "secret": "secret-local",
                "access_token": "access-local",
                "refresh_token": "refresh-local",
            },
        },
    }]
    remoto = [{
        "nome": "Loja remota",
        "store_id": "store-remota",
        "integracoes": {},
    }]
    (info / "lojas_config.json").write_text(
        json.dumps(local),
        encoding="utf-8",
    )
    tenant_abs = tenant_path("000002")

    result = shared_sync_apply_scope._shared_sync_aplicar_lojas_integracoes(
        "000002",
        [("lojas_config.json", json.dumps(remoto).encode("utf-8"))],
        tenant_abs,
        str(tmp_path / "backup"),
        strict_oauth_conflicts=True,
    )

    lojas = integracoes.carregar_lojas("000002")
    assert result["stores_count"] == 1
    assert {loja["store_id"] for loja in lojas} == {"store-remota"}
    assert (info / "lojas_config.json").exists()


def test_apply_nao_recupera_global_quando_tenant_nao_default_ja_existe(tmp_path):
    info = tmp_path / "info"
    tenant = info / "000002"
    tenant.mkdir(parents=True)

    def tenant_path(_client_id):
        return str(tenant)

    integracoes.configure_integracoes_context(
        pasta_info=str(info),
        get_tenant_path=tenant_path,
        normalizar_integracao_conectada=lambda _s, data: data,
    )
    nome_legado = "Loja local ainda no global"
    store_id_legado = integracoes._integracoes_store_id(
        "000002",
        {"nome": nome_legado},
    )
    legado = [{
        "nome": nome_legado,
        "store_id": store_id_legado,
        "integracoes": {
            "bling": {
                "access_token": "legacy-access",
                "api_key": "legacy-key",
                "connected": True,
            },
        },
    }]
    tenant_remoto = [{
        "nome": "Loja do pull antigo",
        "store_id": "store-remota-antiga",
        "integracoes": {},
    }]
    remoto_novo = [{
        "nome": "Loja remota nova",
        "store_id": "store-remota-nova",
        "integracoes": {},
    }]
    (info / "lojas_config.json").write_text(
        json.dumps(legado),
        encoding="utf-8",
    )
    (tenant / "lojas_config.json").write_text(
        json.dumps(tenant_remoto),
        encoding="utf-8",
    )
    backup_dir = tmp_path / "backup"

    result = shared_sync_apply_scope._shared_sync_aplicar_lojas_integracoes(
        "000002",
        [("lojas_config.json", json.dumps(remoto_novo).encode("utf-8"))],
        str(tenant),
        str(backup_dir),
        strict_oauth_conflicts=True,
    )

    lojas = integracoes.carregar_lojas("000002")
    assert result["stores_count"] == 2
    assert {loja["store_id"] for loja in lojas} == {
        "store-remota-antiga",
        "store-remota-nova",
    }
    assert (info / "lojas_config.json").exists()
    assert not (backup_dir / "legacy_root_lojas_config.json").exists()


def test_apply_recupera_backup_imediato_sem_ressuscitar_integracao_excluida(
    tmp_path,
):
    info = tmp_path / "info"
    tenant = info / "000002"
    tenant.mkdir(parents=True)

    integracoes.configure_integracoes_context(
        pasta_info=str(info),
        get_tenant_path=lambda _client_id: str(tenant),
        normalizar_integracao_conectada=lambda _s, data: data,
    )
    atual = [{
        "nome": "Loja atual",
        "store_id": "store-atual",
        "integracoes": {"mercadolivre": {"connected": False}},
    }]
    backup = [
        {
            "nome": "Loja recuperada",
            "store_id": "store-recuperada",
            "integracoes": {"bling": {"access_token": "recuperado"}},
        },
        {
            "nome": "Loja excluida",
            "store_id": "store-excluida",
            "integracoes": {"bling": {"access_token": "nao-ressuscitar"}},
        },
        {
            "nome": "Loja atual",
            "store_id": "store-atual",
            "integracoes": {
                "mercadolivre": {
                    "access_token": "credencial-antiga",
                    "connected": True,
                },
            },
        },
    ]
    backup_original = json.dumps(backup).encode("utf-8")
    (tenant / "lojas_config.json").write_text(json.dumps(atual), encoding="utf-8")
    (tenant / "lojas_config.json.bak").write_bytes(backup_original)
    (tenant / "lojas_sync_tombstones.json").write_text(
        json.dumps([
            {
                "key": "store:store-excluida:",
                "type": "store",
                "store_id": "store-excluida",
                "version": 2,
                "deleted_at": "2026-09-02T12:00:00Z",
            },
            {
                "key": "integration:store-atual:mercadolivre",
                "type": "integration",
                "store_id": "store-atual",
                "service": "mercadolivre",
                "version": 2,
                "deleted_at": "2026-09-02T12:00:00Z",
            },
        ]),
        encoding="utf-8",
    )
    backup_dir = tmp_path / "backup"

    result = shared_sync_apply_scope._shared_sync_aplicar_lojas_integracoes(
        "000002",
        [(
            "lojas_config.json",
            b'[{"nome":"Remota","store_id":"store-remota","integracoes":{}}]',
        )],
        str(tenant),
        str(backup_dir),
        strict_oauth_conflicts=True,
    )

    lojas = json.loads((tenant / "lojas_config.json").read_text(encoding="utf-8"))
    por_id = {loja["store_id"]: loja for loja in lojas}
    assert result["stores_count"] == 3
    assert set(por_id) == {"store-atual", "store-recuperada", "store-remota"}
    assert por_id["store-recuperada"]["integracoes"]["bling"]["access_token"] == "recuperado"
    ml_atual = por_id["store-atual"]["integracoes"]["mercadolivre"]
    assert ml_atual["connected"] is False
    assert "access_token" not in ml_atual
    assert (backup_dir / "lojas_config.json.bak").read_bytes() == backup_original
    backup_final = json.loads(
        (tenant / "lojas_config.json.bak").read_text(encoding="utf-8")
    )
    assert {loja["store_id"] for loja in backup_final} == set(por_id)

    (tenant / "lojas_config.json").write_bytes(b"{invalido")
    restauradas = integracoes.carregar_lojas("000002")
    por_id_restaurado = {loja["store_id"]: loja for loja in restauradas}
    assert set(por_id_restaurado) == set(por_id)
    assert (
        por_id_restaurado["store-recuperada"]["integracoes"]["bling"][
            "access_token"
        ]
        == "recuperado"
    )


@pytest.mark.parametrize(
    "legado",
    [
        [{
            "nome": "Loja sem owner",
            "integracoes": {"bling": {"access_token": "nao-vazar"}},
        }],
        [{
            "nome": "Loja de outro cliente",
            "store_id": integracoes._integracoes_store_id(
                "cliente-a",
                {"nome": "Loja de outro cliente"},
            ),
            "integracoes": {"bling": {"access_token": "nao-vazar"}},
        }],
        [
            {
                "nome": "Loja atribuida",
                "store_id": integracoes._integracoes_store_id(
                    "000002",
                    {"nome": "Loja atribuida"},
                ),
                "integracoes": {},
            },
            {
                "nome": "Loja sem owner",
                "integracoes": {"bling": {"access_token": "nao-vazar"}},
            },
        ],
    ],
    ids=["sem-store-id", "outro-cliente", "payload-misto"],
)
def test_apply_ignora_recuperacao_global_sem_ownership_comprovado(
    tmp_path,
    legado,
):
    info = tmp_path / "info"
    tenant = info / "000002"
    tenant.mkdir(parents=True)

    integracoes.configure_integracoes_context(
        pasta_info=str(info),
        get_tenant_path=lambda _client_id: str(tenant),
        normalizar_integracao_conectada=lambda _s, data: data,
    )
    tenant_original = json.dumps([{
        "nome": "Loja do tenant",
        "store_id": "store-tenant",
        "integracoes": {},
    }]).encode("utf-8")
    legacy_original = json.dumps(legado).encode("utf-8")
    legacy_path = info / "lojas_config.json"
    tenant_path = tenant / "lojas_config.json"
    legacy_path.write_bytes(legacy_original)
    tenant_path.write_bytes(tenant_original)

    result = shared_sync_apply_scope._shared_sync_aplicar_lojas_integracoes(
        "000002",
        [(
            "lojas_config.json",
            b'[{"nome":"Remota","store_id":"remote","integracoes":{}}]',
        )],
        str(tenant),
        str(tmp_path / "backup"),
        strict_oauth_conflicts=True,
    )

    assert result["stores_count"] == 2
    assert legacy_path.read_bytes() == legacy_original
    persistidas = json.loads(tenant_path.read_text(encoding="utf-8"))
    assert {loja["store_id"] for loja in persistidas} == {
        "store-tenant",
        "remote",
    }


def test_apply_cancela_se_migracao_legada_nao_puder_ser_materializada(
    tmp_path,
    monkeypatch,
):
    info = tmp_path / "info"
    info.mkdir()
    tenant = info / "000002"

    def tenant_path(_client_id):
        tenant.mkdir(parents=True, exist_ok=True)
        return str(tenant)

    integracoes.configure_integracoes_context(
        pasta_info=str(info),
        get_tenant_path=tenant_path,
        normalizar_integracao_conectada=lambda _s, data: data,
    )
    nome_local = "Loja local protegida"
    (info / "lojas_config.json").write_text(
        json.dumps([{
            "nome": nome_local,
            "store_id": integracoes._integracoes_store_id(
                "000002",
                {"nome": nome_local},
            ),
            "integracoes": {},
        }]),
        encoding="utf-8",
    )
    escritor_real = integracoes._integracoes_escrever_lojas_config_atomico

    def falhar_materializacao(caminho, payload):
        if str(caminho) == str(tenant / "lojas_config.json"):
            raise PermissionError("replace")
        return escritor_real(caminho, payload)

    monkeypatch.setattr(
        integracoes,
        "_integracoes_escrever_lojas_config_atomico",
        falhar_materializacao,
    )

    with pytest.raises(HTTPException) as bloqueado:
        shared_sync_apply_scope._shared_sync_aplicar_lojas_integracoes(
            "000002",
            [("lojas_config.json", b'[{"nome":"Remota","store_id":"remote","integracoes":{}}]')],
            str(tenant),
            str(tmp_path / "backup"),
            strict_oauth_conflicts=True,
        )

    assert bloqueado.value.status_code == 500
    assert (info / "lojas_config.json").exists()
    assert not (tenant / "lojas_config.json").exists()


def test_apply_ignora_global_sem_owner_antes_de_criar_tenant(
    tmp_path,
):
    info = tmp_path / "info"
    info.mkdir()
    tenant = info / "000002"

    def tenant_path(_client_id):
        tenant.mkdir(parents=True, exist_ok=True)
        return str(tenant)

    integracoes.configure_integracoes_context(
        pasta_info=str(info),
        get_tenant_path=tenant_path,
        normalizar_integracao_conectada=lambda _s, data: data,
    )
    legacy_original = json.dumps([{
        "nome": "Loja de cliente desconhecido",
        "integracoes": {"mercadolivre": {"access_token": "nao-vazar"}},
    }]).encode("utf-8")
    legacy_path = info / "lojas_config.json"
    legacy_path.write_bytes(legacy_original)

    result = shared_sync_apply_scope._shared_sync_aplicar_lojas_integracoes(
        "000002",
        [(
            "lojas_config.json",
            b'[{"nome":"Remota","store_id":"remote","integracoes":{}}]',
        )],
        tenant_path("000002"),
        str(tmp_path / "backup"),
        strict_oauth_conflicts=True,
    )

    assert result["stores_count"] == 1
    assert legacy_path.read_bytes() == legacy_original
    persistidas = json.loads(
        (tenant / "lojas_config.json").read_text(encoding="utf-8")
    )
    assert [loja["store_id"] for loja in persistidas] == ["remote"]


def test_pull_bloqueia_se_pointer_muda_entre_meta_e_download(monkeypatch):
    sessao = {"client_id": "000002", "username": "operador"}
    meta_a = {
        "id": "bundle-lojas",
        "snapshot_id": "snapshot-a",
        "snapshot_hash": "hash-a",
        "bundle_sha256": "cipher-a",
    }
    meta_b = {
        "id": "bundle-lojas",
        "snapshot_id": "snapshot-b",
        "snapshot_hash": "hash-b",
        "bundle_sha256": "cipher-b",
    }
    monkeypatch.setattr(
        shared_sync_machine,
        "_shared_sync_machine_doc_id",
        lambda *args: "bundle-lojas",
    )
    monkeypatch.setattr(
        shared_sync_machine,
        "_shared_sync_remote_meta_by_id",
        lambda *_args: dict(meta_a),
    )
    monkeypatch.setattr(
        shared_sync_machine,
        "_shared_sync_pull_already_current",
        lambda *args: False,
    )
    monkeypatch.setattr(
        shared_sync_machine,
        "_shared_sync_obter_bundle_remoto_para_guard",
        lambda *args, **kwargs: (b"snapshot-b", dict(meta_b)),
    )
    monkeypatch.setattr(
        shared_sync_machine,
        "_shared_sync_aplicar_pacote",
        lambda *args, **kwargs: pytest.fail(
            "snapshot trocado nao pode ser aplicado"
        ),
    )
    monkeypatch.setattr(
        shared_sync_machine,
        "_shared_sync_state_update",
        lambda *args, **kwargs: pytest.fail(
            "snapshot trocado nao pode atualizar estado"
        ),
    )

    with pytest.raises(HTTPException) as mudou:
        shared_sync_machine._shared_sync_machine_pull_scope(
            sessao,
            "lojas_integracoes",
            force=True,
        )

    assert mudou.value.status_code == 409
    assert "mudou durante a importacao" in str(mudou.value.detail)


def _configure_integracoes_sync_test(tmp_path, client_id="000002"):
    info = tmp_path / "info"
    tenant = info / client_id
    tenant.mkdir(parents=True)
    integracoes.configure_integracoes_context(
        pasta_info=str(info),
        get_tenant_path=lambda _client_id: str(tenant),
        normalizar_integracao_conectada=lambda _servico, dados: dados,
    )
    return info, tenant


@pytest.mark.parametrize("tipo", ["store", "integration"])
def test_apply_backup_legado_sem_store_id_respeita_tombstone(tmp_path, tipo):
    _info, tenant = _configure_integracoes_sync_test(tmp_path)
    nome = "Loja antiga"
    store_id = integracoes._integracoes_store_id("000002", {"nome": nome})
    atual = [] if tipo == "store" else [{
        "nome": nome,
        "store_id": store_id,
        "integracoes": {},
    }]
    backup = [{
        "nome": nome,
        "integracoes": {
            "mercadolivre": {
                "access_token": "nao-voltar",
                "refresh_token": "nao-voltar-refresh",
            },
        },
    }]
    tombstone = {
        "key": f"{tipo}:{store_id}:{'mercadolivre' if tipo == 'integration' else ''}",
        "type": tipo,
        "store_id": store_id,
        "version": 3,
        "deleted_at": "2026-09-02T12:00:00Z",
    }
    if tipo == "integration":
        tombstone["service"] = "mercadolivre"
    (tenant / "lojas_config.json").write_text(json.dumps(atual), encoding="utf-8")
    (tenant / "lojas_config.json.bak").write_text(json.dumps(backup), encoding="utf-8")
    (tenant / "lojas_sync_tombstones.json").write_text(
        json.dumps([tombstone]),
        encoding="utf-8",
    )

    main_antes = (tenant / "lojas_config.json").read_bytes()
    with pytest.raises(HTTPException) as bloqueado:
        shared_sync_apply_scope._shared_sync_aplicar_lojas_integracoes(
            "000002",
            [("lojas_config.json", b"[]")],
            str(tenant),
            str(tmp_path / "backup-operacao"),
            strict_oauth_conflicts=True,
        )

    assert bloqueado.value.status_code == 409
    assert (tenant / "lojas_config.json").read_bytes() == main_antes


def test_apply_backup_legado_com_nome_ambiguo_falha_sem_alterar_bytes(tmp_path):
    _info, tenant = _configure_integracoes_sync_test(tmp_path)
    nome_atual = "Loja A"
    main = json.dumps([{
        "nome": nome_atual,
        "store_id": integracoes._integracoes_store_id(
            "000002",
            {"nome": nome_atual},
        ),
        "integracoes": {"bling": {"access_token": "local"}},
    }]).encode("utf-8")
    backup = json.dumps([{
        "nome": "Loja Á",
        "integracoes": {"mercadolivre": {"access_token": "ambiguo"}},
    }]).encode("utf-8")
    (tenant / "lojas_config.json").write_bytes(main)
    (tenant / "lojas_config.json.bak").write_bytes(backup)

    with pytest.raises(HTTPException) as bloqueado:
        shared_sync_apply_scope._shared_sync_aplicar_lojas_integracoes(
            "000002",
            [("lojas_config.json", main)],
            str(tenant),
            str(tmp_path / "backup-operacao"),
            strict_oauth_conflicts=True,
        )

    assert bloqueado.value.status_code == 409
    assert (tenant / "lojas_config.json").read_bytes() == main
    assert (tenant / "lojas_config.json.bak").read_bytes() == backup
    assert (tmp_path / "backup-operacao" / "lojas_config.json.bak").read_bytes() == backup


@pytest.mark.parametrize("tipo", ["store", "integration"])
def test_apply_snapshot_v1_sem_store_id_respeita_tombstone_local(tmp_path, tipo):
    _info, tenant = _configure_integracoes_sync_test(tmp_path)
    nome = "Loja v1"
    store_id = integracoes._integracoes_store_id("000002", {"nome": nome})
    atual = [] if tipo == "store" else [{
        "nome": nome,
        "store_id": store_id,
        "integracoes": {},
    }]
    remoto = [{
        "nome": nome,
        "integracoes": {"mercadolivre": {"access_token": "v1-antigo"}},
    }]
    tombstone = {
        "key": f"{tipo}:{store_id}:{'mercadolivre' if tipo == 'integration' else ''}",
        "type": tipo,
        "store_id": store_id,
        "version": 4,
        "deleted_at": "2026-09-02T12:00:00Z",
    }
    if tipo == "integration":
        tombstone["service"] = "mercadolivre"
    (tenant / "lojas_config.json").write_text(json.dumps(atual), encoding="utf-8")
    (tenant / "lojas_sync_tombstones.json").write_text(
        json.dumps([tombstone]),
        encoding="utf-8",
    )

    main_antes = (tenant / "lojas_config.json").read_bytes()
    with pytest.raises(HTTPException) as bloqueado:
        shared_sync_apply_scope._shared_sync_aplicar_lojas_integracoes(
            "000002",
            [("lojas_config.json", json.dumps(remoto).encode("utf-8"))],
            str(tenant),
            str(tmp_path / "backup-operacao"),
            strict_oauth_conflicts=True,
        )

    assert bloqueado.value.status_code == 409
    assert (tenant / "lojas_config.json").read_bytes() == main_antes


@pytest.mark.parametrize("tipo", ["store", "integration"])
def test_apply_tombstone_remoto_nao_exclui_loja_do_mesmo_bundle(tmp_path, tipo):
    _info, tenant = _configure_integracoes_sync_test(tmp_path)
    nome = "Loja remota apagada"
    store_id = "store-remota"
    remoto = [{
        "nome": nome,
        "store_id": store_id,
        "integracoes": {"mercadolivre": {"access_token": "stale"}},
    }]
    tombstone = {
        "key": f"{tipo}:{store_id}:{'mercadolivre' if tipo == 'integration' else ''}",
        "type": tipo,
        "store_id": store_id,
        "version": 5,
        "deleted_at": "2026-09-02T12:00:00Z",
    }
    if tipo == "integration":
        tombstone["service"] = "mercadolivre"
    (tenant / "lojas_config.json").write_text("[]", encoding="utf-8")

    shared_sync_apply_scope._shared_sync_aplicar_lojas_integracoes(
        "000002",
        [
            ("lojas_config.json", json.dumps(remoto).encode("utf-8")),
            ("lojas_sync_tombstones.json", json.dumps([tombstone]).encode("utf-8")),
        ],
        str(tenant),
        str(tmp_path / "backup-operacao"),
        strict_oauth_conflicts=True,
    )

    lojas = json.loads((tenant / "lojas_config.json").read_text(encoding="utf-8"))
    assert len(lojas) == 1
    assert lojas[0]["store_id"] == store_id
    assert "mercadolivre" in lojas[0]["integracoes"]
    assert not (tenant / "lojas_sync_tombstones.json").exists()


def test_apply_tombstone_remoto_conflitante_e_ignorado_localmente(tmp_path):
    _info, tenant = _configure_integracoes_sync_test(tmp_path)
    local = [{
        "nome": "Loja local",
        "store_id": "store-local",
        "integracoes": {
            "mercadolivre": {
                "app_id": "app",
                "client_secret": "secret",
                "access_token": "local-novo",
                "refresh_token": "local-refresh",
                "connected": True,
            },
        },
    }]
    main = json.dumps(local).encode("utf-8")
    (tenant / "lojas_config.json").write_bytes(main)
    tombstone = [{
        "key": "integration:store-local:mercadolivre",
        "type": "integration",
        "store_id": "store-local",
        "service": "mercadolivre",
        "version": 99,
        "deleted_at": "2026-09-02T12:00:00Z",
    }]

    shared_sync_apply_scope._shared_sync_aplicar_lojas_integracoes(
        "000002",
        [
            ("lojas_config.json", b"[]"),
            ("lojas_sync_tombstones.json", json.dumps(tombstone).encode("utf-8")),
        ],
        str(tenant),
        str(tmp_path / "backup-operacao"),
        strict_oauth_conflicts=True,
    )

    preservada = json.loads(
        (tenant / "lojas_config.json").read_text(encoding="utf-8")
    )
    assert preservada[0]["store_id"] == "store-local"
    assert preservada[0]["integracoes"]["mercadolivre"]["connected"] is True
    assert preservada[0]["integracoes"]["mercadolivre"]["access_token"] == "local-novo"
    assert not (tenant / "lojas_sync_tombstones.json").exists()


def test_bundle_bloqueia_main_conectado_com_tombstone_ativo(tmp_path, monkeypatch):
    _info, tenant = _configure_integracoes_sync_test(tmp_path)
    lojas = [{
        "nome": "Loja contraditoria",
        "store_id": "store-x",
        "integracoes": {"mercadolivre": {"access_token": "preservar", "connected": True}},
    }]
    main = json.dumps(lojas).encode("utf-8")
    tombstones = json.dumps([{
        "key": "integration:store-x:mercadolivre",
        "type": "integration",
        "store_id": "store-x",
        "service": "mercadolivre",
        "version": 2,
        "deleted_at": "2026-09-02T12:00:00Z",
    }]).encode("utf-8")
    (tenant / "lojas_config.json").write_bytes(main)
    (tenant / "lojas_sync_tombstones.json").write_bytes(tombstones)

    def coletar(*_args, **_kwargs):
        return [
            _entry("lojas_config.json", (tenant / "lojas_config.json").read_bytes()),
            _entry("lojas_sync_tombstones.json", (tenant / "lojas_sync_tombstones.json").read_bytes()),
        ], []

    monkeypatch.setattr(shared_sync_bundle, "_shared_sync_coletar_arquivos", coletar)

    with pytest.raises(HTTPException) as bloqueado:
        shared_sync_bundle._shared_sync_montar_pacote(
            "000002",
            "lojas_integracoes",
            "operador",
        )
    assert bloqueado.value.status_code == 409
    assert (tenant / "lojas_config.json").read_bytes() == main
    assert (tenant / "lojas_sync_tombstones.json").read_bytes() == tombstones


def test_bundle_ignora_root_coexistente_de_tenant_nao_default(tmp_path, monkeypatch):
    info, tenant = _configure_integracoes_sync_test(tmp_path)
    nome = "Loja coexistente"
    store_id = integracoes._integracoes_store_id("000002", {"nome": nome})
    (tenant / "lojas_config.json").write_text("[]", encoding="utf-8")
    (info / "lojas_config.json").write_text(
        json.dumps([{
            "nome": nome,
            "store_id": store_id,
            "integracoes": {"bling": {"access_token": "root-only"}},
        }]),
        encoding="utf-8",
    )

    def coletar(*_args, **_kwargs):
        data = (tenant / "lojas_config.json").read_bytes()
        return [_entry("lojas_config.json", data)], []

    monkeypatch.setattr(shared_sync_bundle, "_shared_sync_coletar_arquivos", coletar)
    bundle, _manifest, _warnings = shared_sync_bundle._shared_sync_montar_pacote(
        "000002",
        "lojas_integracoes",
        "operador",
    )

    with zipfile.ZipFile(io.BytesIO(bundle), "r") as arquivo:
        lojas = json.loads(arquivo.read("files/lojas_config.json"))
    assert lojas == []
    assert (info / "lojas_config.json").exists()


def test_apply_rejeita_tombstone_remoto_malformado_sem_escrever(tmp_path):
    _info, tenant = _configure_integracoes_sync_test(tmp_path)
    main = b"[]"
    (tenant / "lojas_config.json").write_bytes(main)

    with pytest.raises(HTTPException) as bloqueado:
        shared_sync_apply_scope._shared_sync_aplicar_lojas_integracoes(
            "000002",
            [
                ("lojas_config.json", b"[]"),
                ("lojas_sync_tombstones.json", b"[42]"),
            ],
            str(tenant),
            str(tmp_path / "backup-operacao"),
            strict_oauth_conflicts=True,
        )
    assert bloqueado.value.status_code == 502
    assert (tenant / "lojas_config.json").read_bytes() == main
    assert not (tenant / "lojas_sync_tombstones.json").exists()


def test_merge_legacy_base_com_aliases_equivalentes_aceita_refresh_remoto(tmp_path):
    target = tmp_path / "integracoes.json"
    base = {"Loja": {"bling": {
        "id": "app",
        "secret": "secret",
        "access_token": "old",
        "refresh_token": "refresh-old",
    }}}
    local = {"Loja": {"bling": {
        "client_id": "app",
        "client_secret": "secret",
        "token": "old",
        "refresh_token": "refresh-old",
    }}}
    remoto = {"Loja": {"bling": {
        "id": "app",
        "secret": "secret",
        "access_token": "new",
        "refresh_token": "refresh-new",
    }}}
    target.write_text(json.dumps(local), encoding="utf-8")

    merged = json.loads(
        shared_sync_merge_integracoes._shared_sync_merge_integracoes_legacy_bytes(
            str(target),
            json.dumps(remoto).encode("utf-8"),
            base_bytes=json.dumps(base).encode("utf-8"),
            strict_oauth_conflicts=True,
        )
    )
    assert merged["Loja"]["bling"]["access_token"] == "new"
    assert merged["Loja"]["bling"]["refresh_token"] == "refresh-new"


@pytest.mark.parametrize("local_restaurado", [False, True])
def test_merge_tombstones_bloqueia_conflito_delete_restore(
    tmp_path,
    local_restaurado,
):
    target = tmp_path / "lojas_sync_tombstones.json"
    base = {
        "key": "store:store-x:",
        "type": "store",
        "store_id": "store-x",
        "version": 2,
    }
    local = dict(base)
    remoto = dict(base, version=100)
    if local_restaurado:
        local["restored_at"] = "2026-09-02T10:00:00Z"
        remoto["deleted_at"] = "2026-09-02T11:00:00Z"
    else:
        local["deleted_at"] = "2026-09-02T11:00:00Z"
        remoto["restored_at"] = "2026-09-02T10:00:00Z"
    target.write_text(json.dumps([local]), encoding="utf-8")

    with pytest.raises(HTTPException) as bloqueado:
        shared_sync_merge_integracoes._shared_sync_merge_tombstones_integracoes_bytes(
            str(target),
            json.dumps([remoto]).encode("utf-8"),
        )
    assert bloqueado.value.status_code == 409
    assert not shared_sync_merge_integracoes._shared_sync_tombstones_preservados(
        [local],
        [remoto],
    )


def test_merge_tombstone_aceita_recriacao_com_base_causal(tmp_path):
    target = tmp_path / "lojas_sync_tombstones.json"
    excluido = {
        "key": "store:store-x:",
        "type": "store",
        "store_id": "store-x",
        "version": 1,
        "deleted_at": "2026-09-02T10:00:00Z",
    }
    restaurado = {
        "key": "store:store-x:",
        "type": "store",
        "store_id": "store-x",
        "version": 2,
        "restored_at": "2026-09-02T11:00:00Z",
    }
    target.write_text(json.dumps([excluido]), encoding="utf-8")

    merged = json.loads(
        shared_sync_merge_integracoes._shared_sync_merge_tombstones_integracoes_bytes(
            str(target),
            json.dumps([restaurado]).encode("utf-8"),
            base_bytes=json.dumps([excluido]).encode("utf-8"),
        )
    )

    assert merged == [restaurado]
    assert shared_sync_merge_integracoes._shared_sync_tombstones_preservados(
        [restaurado],
        [excluido],
        permitir_atualizacao=True,
    )


def test_merge_rejeita_aliases_oauth_conflitantes_no_snapshot_canonico(tmp_path):
    remoto = [{
        "nome": "Loja conflito",
        "store_id": "store-x",
        "integracoes": {
            "mercadolivre": {
                "app_id": "app-a",
                "client_id": "app-b",
                "client_secret": "secret",
            },
        },
    }]

    with pytest.raises(HTTPException) as bloqueado:
        shared_sync_merge_integracoes._shared_sync_merge_lojas_integracoes_bytes(
            str(tmp_path / "lojas_config.json"),
            json.dumps(remoto).encode("utf-8"),
            client_id="000002",
            strict_oauth_conflicts=True,
        )
    assert bloqueado.value.status_code == 502


def test_merge_rejeita_aliases_oauth_conflitantes_no_integracoes_legado(tmp_path):
    remoto = {
        "Loja conflito": {
            "bling": {
                "access_token": "token-a",
                "token": "token-b",
            },
        },
    }

    with pytest.raises(HTTPException) as bloqueado:
        shared_sync_merge_integracoes._shared_sync_merge_integracoes_legacy_bytes(
            str(tmp_path / "integracoes.json"),
            json.dumps(remoto).encode("utf-8"),
            strict_oauth_conflicts=True,
        )
    assert bloqueado.value.status_code == 502


def test_apply_preserva_backup_oauth_mais_forte_em_pull_nao_relacionado(tmp_path):
    _info, tenant = _configure_integracoes_sync_test(tmp_path)
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
    backup = [{
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
    }]
    backup_bytes = json.dumps(backup).encode("utf-8")
    (tenant / "lojas_config.json").write_text(json.dumps([loja_a]), encoding="utf-8")
    (tenant / "lojas_config.json.bak").write_bytes(backup_bytes)
    remoto = [{
        "nome": "Loja C",
        "store_id": "store-c",
        "integracoes": {},
    }]

    shared_sync_apply_scope._shared_sync_aplicar_lojas_integracoes(
        "000002",
        [("lojas_config.json", json.dumps(remoto).encode("utf-8"))],
        str(tenant),
        str(tmp_path / "backup-operacao"),
        strict_oauth_conflicts=True,
    )

    backup_final = json.loads(
        (tenant / "lojas_config.json.bak").read_text(encoding="utf-8")
    )
    backup_por_id = {loja["store_id"]: loja for loja in backup_final}
    assert set(backup_por_id) == {"store-a", "store-c"}
    assert (
        backup_por_id["store-a"]["integracoes"]["mercadolivre"][
            "access_token"
        ]
        == "ultimo-access"
    )
    assert (
        backup_por_id["store-a"]["integracoes"]["mercadolivre"]["user_id"]
        == "seller-ultimo"
    )
    main = json.loads((tenant / "lojas_config.json").read_text(encoding="utf-8"))
    por_id = {loja["store_id"]: loja for loja in main}
    assert set(por_id) == {"store-a", "store-c"}
    assert "access_token" not in por_id["store-a"]["integracoes"]["mercadolivre"]


@pytest.mark.parametrize("tipo", ["store", "integration"])
def test_apply_exclusao_remota_com_base_causal_preserva_estado_local(tmp_path, tipo):
    _info, tenant = _configure_integracoes_sync_test(tmp_path)
    base = [{
        "nome": "Loja causal",
        "store_id": "store-causal",
        "integracoes": {
            "mercadolivre": {
                "app_id": "app",
                "client_secret": "secret",
                "access_token": "access",
                "refresh_token": "refresh",
                "connected": True,
            },
        },
    }]
    local = json.loads(json.dumps(base))
    local[0]["_sync_version"] = 8
    local[0]["_sync_updated_at"] = "2026-09-02T13:00:00Z"
    local[0]["integracoes"]["mercadolivre"].update({
        "status": "conectado",
        "motivo": "",
        "oauth_invalid": False,
        "shared_without_oauth_tokens": False,
        "_sync_version": 8,
        "_sync_updated_at": "2026-09-02T13:00:00Z",
    })
    (tenant / "lojas_config.json").write_text(json.dumps(local), encoding="utf-8")
    tombstone = {
        "key": f"{tipo}:store-causal:{'mercadolivre' if tipo == 'integration' else ''}",
        "type": tipo,
        "store_id": "store-causal",
        "version": 2,
        "deleted_at": "2026-09-02T12:00:00Z",
    }
    if tipo == "integration":
        tombstone["service"] = "mercadolivre"

    shared_sync_apply_scope._shared_sync_aplicar_lojas_integracoes(
        "000002",
        [
            ("lojas_config.json", b"[]"),
            ("lojas_sync_tombstones.json", json.dumps([tombstone]).encode("utf-8")),
        ],
        str(tenant),
        str(tmp_path / "backup-operacao"),
        base_lojas_bytes=json.dumps(base).encode("utf-8"),
        base_tombstones_bytes=b"[]",
        strict_oauth_conflicts=True,
    )

    main = json.loads((tenant / "lojas_config.json").read_text(encoding="utf-8"))
    assert len(main) == 1
    assert main[0]["store_id"] == "store-causal"
    assert main[0]["integracoes"]["mercadolivre"]["connected"] is True
    assert main[0]["integracoes"]["mercadolivre"]["access_token"] == "access"


def test_bundle_ignora_tombstone_local_removido_durante_coleta(
    tmp_path,
    monkeypatch,
):
    _info, tenant = _configure_integracoes_sync_test(tmp_path)
    (tenant / "lojas_config.json").write_text("[]", encoding="utf-8")
    tombstone_path = tenant / "lojas_sync_tombstones.json"
    tombstone_path.write_text(
        json.dumps([{
            "key": "store:apagada:",
            "type": "store",
            "store_id": "apagada",
            "service": "",
            "version": 2,
            "deleted_at": "2026-09-02T12:00:00Z",
        }]),
        encoding="utf-8",
    )

    def coletar(*_args, **_kwargs):
        tombstone_path.unlink()
        return [
            _entry(
                "lojas_config.json",
                (tenant / "lojas_config.json").read_bytes(),
            ),
        ], []

    monkeypatch.setattr(shared_sync_bundle, "_shared_sync_coletar_arquivos", coletar)
    bundle, manifest, _warnings = shared_sync_bundle._shared_sync_montar_pacote(
        "000002",
        "lojas_integracoes",
        "operador",
    )
    with zipfile.ZipFile(io.BytesIO(bundle), "r") as arquivo:
        assert "files/lojas_sync_tombstones.json" not in arquivo.namelist()
    assert all(
        item["relative_path"] != "lojas_sync_tombstones.json"
        for item in manifest["files"]
    )


def test_apply_rejeita_tombstone_com_chave_e_identidade_contraditorias(
    tmp_path,
):
    _info, tenant = _configure_integracoes_sync_test(tmp_path)
    main = b"[]"
    (tenant / "lojas_config.json").write_bytes(main)
    remoto = [{
        "key": "store:outra:",
        "type": "store",
        "store_id": "store-real",
        "service": "",
        "version": 2,
        "deleted_at": "2026-09-02T12:00:00Z",
    }]

    with pytest.raises(HTTPException) as bloqueado:
        shared_sync_apply_scope._shared_sync_aplicar_lojas_integracoes(
            "000002",
            [
                ("lojas_config.json", b"[]"),
                (
                    "lojas_sync_tombstones.json",
                    json.dumps(remoto).encode("utf-8"),
                ),
            ],
            str(tenant),
            str(tmp_path / "backup-operacao"),
            strict_oauth_conflicts=True,
        )

    assert bloqueado.value.status_code == 502
    assert (tenant / "lojas_config.json").read_bytes() == main
    assert not (tenant / "lojas_sync_tombstones.json").exists()


@pytest.mark.parametrize("defeito", ["version", "timestamp"])
def test_apply_rejeita_tombstone_sem_metadados_causais(
    tmp_path,
    defeito,
):
    _info, tenant = _configure_integracoes_sync_test(tmp_path)
    main = b"[]"
    (tenant / "lojas_config.json").write_bytes(main)
    remoto = {
        "key": "store:store-remota:",
        "type": "store",
        "store_id": "store-remota",
        "service": "",
        "version": 2,
        "deleted_at": "2026-09-02T12:00:00Z",
    }
    if defeito == "version":
        remoto["version"] = "2"
    else:
        remoto.pop("deleted_at")

    with pytest.raises(HTTPException) as bloqueado:
        shared_sync_apply_scope._shared_sync_aplicar_lojas_integracoes(
            "000002",
            [
                ("lojas_config.json", b"[]"),
                (
                    "lojas_sync_tombstones.json",
                    json.dumps([remoto]).encode("utf-8"),
                ),
            ],
            str(tenant),
            str(tmp_path / "backup-operacao"),
            strict_oauth_conflicts=True,
        )

    assert bloqueado.value.status_code == 502
    assert (tenant / "lojas_config.json").read_bytes() == main
    assert not (tenant / "lojas_sync_tombstones.json").exists()


def test_recriar_loja_nao_ressuscita_integracoes_antigas_do_snapshot(tmp_path):
    _info, tenant = _configure_integracoes_sync_test(tmp_path)
    store_id = integracoes._integracoes_store_id(
        "000002",
        {"nome": "Loja recriada"},
    )
    original = [{
        "nome": "Loja recriada",
        "store_id": store_id,
        "integracoes": {
            "mercadolivre": {
                "app_id": "app",
                "client_secret": "secret",
                "access_token": "access-antigo",
                "refresh_token": "refresh-antigo",
                "user_id": "seller-antigo",
                "connected": True,
            },
            "bling": {"api_key": "bling-antigo", "connected": True},
            "mercadoturbo": {"token": "turbo-antigo", "connected": True},
        },
    }]
    integracoes.salvar_lojas("000002", original)

    asyncio.run(
        integracoes_api.delete_loja(
            "Loja recriada",
            store_id=store_id,
            client_id="000002",
        )
    )
    nova = integracoes.criar_loja("000002", "Loja recriada")
    tombstones_path = tenant / "lojas_sync_tombstones.json"
    tombstones = json.loads(tombstones_path.read_text(encoding="utf-8"))
    por_chave = {item["key"]: item for item in tombstones}
    assert por_chave[f"store:{store_id}:"]["deleted_at"]
    assert not por_chave[f"store:{store_id}:"].get("restored_at")

    merged = shared_sync_merge_integracoes._shared_sync_merge_lojas_integracoes_bytes(
        str(tenant / "lojas_config.json"),
        json.dumps(original).encode("utf-8"),
        local_tombstones_bytes=tombstones_path.read_bytes(),
        client_id="000002",
        strict_oauth_conflicts=True,
    )
    depois_stale = json.loads(merged)
    assert set(depois_stale[0]["integracoes"]) == {"criacao"}

    integracoes.atualizar_api_loja(
        "000002",
        "Loja recriada",
        "mercadolivre",
        {
            "app_id": "app",
            "client_secret": "secret",
            "access_token": "access-novo",
            "refresh_token": "refresh-novo",
            "user_id": "seller-novo",
            "connected": True,
        },
        store_id=nova["store_id"],
        require_existing=True,
    )
    remoto_stale = json.loads(json.dumps(original))
    merged_reconectado = (
        shared_sync_merge_integracoes._shared_sync_merge_lojas_integracoes_bytes(
            str(tenant / "lojas_config.json"),
            json.dumps(remoto_stale).encode("utf-8"),
            local_tombstones_bytes=tombstones_path.read_bytes(),
            client_id="000002",
            strict_oauth_conflicts=True,
        )
    )
    integracoes_finais = json.loads(merged_reconectado)[0]["integracoes"]
    assert integracoes_finais["mercadolivre"]["user_id"] == "seller-novo"
    assert "bling" not in integracoes_finais
    assert "mercadoturbo" not in integracoes_finais


def test_versoes_fonte_e_electron_estao_alinhadas_com_a_release():
    root_package = json.loads(open("package.json", "r", encoding="utf-8").read())
    electron_package = json.loads(open("electron_app/package.json", "r", encoding="utf-8").read())
    backend_source = open("backend_api.py", "r", encoding="utf-8-sig").read()
    assert root_package["version"] == "1.0.136"
    assert electron_package["version"] == root_package["version"]
    # O minimo do backend pode permanecer anterior para nao derrubar clientes
    # durante o rollout em duas ondas.
    assert 'VERSAO_MINIMA_APP_PADRAO = "1.0.102"' in backend_source
