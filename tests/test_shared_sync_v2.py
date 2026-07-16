import hashlib
import io
import json
from concurrent.futures import ThreadPoolExecutor
import time
import zipfile

import pytest
from fastapi import HTTPException

from backend.schemas.shared_sync import SharedSyncUserLinkCreateRequest
from backend.services import shared_sync  # configura o facade e injeta dependências entre módulos
from backend.services import integracoes
from backend.services import shared_sync_apply_scope
from backend.services import shared_sync_bundle
from backend.services import shared_sync_collect_files
from backend.services import shared_sync_config
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


def test_inicializadores_web_nao_alcancam_endpoints_automaticos():
    source = open("static/auth/shared-sync-boot.js", "r", encoding="utf-8-sig").read()
    shared_start = source.index("(function initSharedSyncAutoPull")
    shared_return = source.index("    return;", shared_start)
    assert shared_return < source.index("/api/shared-sync/auto-pull", shared_start)
    assert shared_return < source.index("/api/shared-sync/user-shares/auto-push", shared_start)
    machine_start = source.index("(function initMachineSharedSyncAuto")
    machine_return = source.index("    return;", machine_start)
    assert machine_return < source.index("/api/shared-sync/machine-sync/auto", machine_start)


def test_configuracao_automatica_e_sempre_desativada(monkeypatch):
    monkeypatch.setenv("JK_SHARED_SYNC_AUTO", "1")
    assert shared_sync_config._shared_sync_auto_enabled() is False
    sessao = {"username": "operador", "client_id": "000002", "permissions": {"integracao": True}}
    cfg = shared_sync_config._shared_sync_machine_config_normalizar(
        sessao,
        {"enabled": True, "scopes": ["lojas_integracoes"], "auto_pull": True, "auto_push": True},
    )
    assert cfg["auto_pull"] is False
    assert cfg["auto_push"] is False


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
    assert [item["nome"] for item in saved] == ["Nova"]
    assert saved[0]["store_id"] == "store-estavel"
    assert saved[0]["integracoes"]["bling"]["access_token"] == "novo"
    assert saved[0]["integracoes"]["bling"]["refresh_token"] == "refresh"
    assert saved[0]["integracoes"]["mercadoturbo"]["token"] == "turbo"


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
        results=[{"scope": "lojas_integracoes", "snapshot_hash": "abc"}],
        link_id="link",
    )
    audit = (tmp_path / "shared_sync_audit.jsonl").read_text(encoding="utf-8")
    assert '"credentials":4' in audit
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

    def document(self, doc_id):
        return _FakeDocument(self, doc_id)

    def where(self, key, _op, value):
        return _FakeQuery(self, key, value)


class _FakeFirestore:
    def __init__(self):
        self.collections = {}

    def collection(self, name):
        return self.collections.setdefault(name, _FakeCollection())


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


def test_versoes_fonte_e_electron_estao_alinhadas_em_1_0_98():
    root_package = json.loads(open("package.json", "r", encoding="utf-8").read())
    electron_package = json.loads(open("electron_app/package.json", "r", encoding="utf-8").read())
    backend_source = open("backend_api.py", "r", encoding="utf-8-sig").read()
    assert root_package["version"] == "1.0.98"
    assert electron_package["version"] == "1.0.98"
    assert 'VERSAO_MINIMA_APP_PADRAO = "1.0.98"' in backend_source
