from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.services import admin_usuarios_common as common
from backend.services import admin_usuarios_auth as auth
from backend.services import admin_usuarios_firebase as firebase
from backend.services import admin_usuarios_login_core as login_core


@pytest.fixture(autouse=True)
def _reset_firebase_guards():
    firebase.FIREBASE_NONCRITICAL_WRITE_COOLDOWN_UNTIL = 0.0
    firebase._firebase_user_cache_invalidate()
    yield
    firebase.FIREBASE_NONCRITICAL_WRITE_COOLDOWN_UNTIL = 0.0
    firebase._firebase_user_cache_invalidate()


def test_listar_usuarios_is_read_only_and_never_rebuilds_index(monkeypatch):
    stream_calls = []

    class Snapshot:
        id = "usuario"

        @staticmethod
        def to_dict():
            return {"username": "usuario", "client_id": "000001"}

    class Collection:
        @staticmethod
        def stream(**kwargs):
            stream_calls.append(kwargs)
            return [Snapshot()]

    monkeypatch.setattr(firebase, "_firebase_collection", lambda: Collection())
    monkeypatch.setattr(
        firebase,
        "_firebase_user_from_data",
        lambda username, data, index: {**data, "username": username, "row_index": index},
    )
    monkeypatch.setattr(firebase, "_salvar_usuarios_sql", lambda *_args, **_kwargs: None, raising=False)
    monkeypatch.setattr(
        firebase,
        "_firebase_users_index_rebuild",
        lambda *_args, **_kwargs: pytest.fail("uma leitura nao pode reconstruir users_index"),
    )

    usuarios = firebase._firebase_listar_usuarios(seed_if_empty=False)

    assert usuarios == {
        "usuario": {"username": "usuario", "client_id": "000001", "row_index": 1}
    }
    assert stream_calls == [{"retry": None, "timeout": 5.0}]


def test_noncritical_write_fails_fast_and_opens_quota_cooldown(monkeypatch):
    calls = []

    def quota_failure(*args, **kwargs):
        calls.append((args, kwargs))
        raise RuntimeError("429 Quota exceeded")

    monkeypatch.setattr(firebase, "_firebase_call_timeout_seconds", lambda: 4.0)
    monkeypatch.setattr(firebase, "_firebase_quota_cooldown_seconds", lambda: 600.0)

    with pytest.raises(RuntimeError, match="Quota exceeded"):
        firebase._firebase_noncritical_write(quota_failure, {"value": 1}, merge=True)

    assert calls == [(({"value": 1},), {"merge": True, "retry": None, "timeout": 4.0})]
    assert not firebase._firebase_noncritical_write_available()
    assert firebase._firebase_noncritical_write(quota_failure, {"value": 2}) is False
    assert len(calls) == 1


def test_index_update_never_overwrites_from_an_unknown_read_state(monkeypatch):
    calls = []

    def fail_index_read(client_id, *, raise_on_error=False):
        calls.append((client_id, raise_on_error))
        raise RuntimeError("index read unavailable")

    monkeypatch.setattr(firebase, "_firebase_users_index_get", fail_index_read)
    monkeypatch.setattr(
        firebase,
        "_firebase_users_index_save",
        lambda *_args, **_kwargs: pytest.fail("indice desconhecido nao pode ser sobrescrito"),
    )

    with pytest.raises(RuntimeError, match="index read unavailable"):
        firebase._firebase_users_index_update_user(
            "usuario",
            {"username": "usuario", "client_id": "000001"},
        )

    assert calls == [("000001", True)]


def test_firebase_user_cache_defaults_to_three_hours(monkeypatch):
    monkeypatch.delenv("JK_FIREBASE_USER_CACHE_SECONDS", raising=False)
    assert firebase._firebase_user_cache_seconds() == 3 * 60 * 60

    monkeypatch.setenv("JK_FIREBASE_USER_CACHE_SECONDS", "invalid")
    assert firebase._firebase_user_cache_seconds() == 3 * 60 * 60

    monkeypatch.setenv("JK_FIREBASE_USER_CACHE_SECONDS", str(12 * 60 * 60))
    assert firebase._firebase_user_cache_seconds() == 3 * 60 * 60


def test_firebase_user_read_uses_configured_cache_and_returns_copies(monkeypatch):
    get_calls = []

    class Snapshot:
        exists = True

        @staticmethod
        def to_dict():
            return {"username": "usuario", "permissions": {"full": True}}

    class Document:
        @staticmethod
        def get(**kwargs):
            get_calls.append(kwargs)
            return Snapshot()

    class Collection:
        @staticmethod
        def document(_doc_id):
            return Document()

    monkeypatch.setattr(firebase, "_firebase_collection", lambda: Collection())
    monkeypatch.setattr(firebase, "_firebase_doc_id", lambda username: username)
    monkeypatch.setattr(
        firebase,
        "_firebase_user_from_data",
        lambda username, data: {**data, "username": username},
    )

    first = firebase._firebase_obter_usuario("usuario")
    first["permissions"]["full"] = False
    second = firebase._firebase_obter_usuario("usuario")

    assert second["permissions"]["full"] is True
    assert get_calls == [{"retry": None, "timeout": 5.0}]

    firebase._firebase_user_cache_invalidate("usuario")
    firebase._firebase_obter_usuario("usuario")
    assert len(get_calls) == 2


def test_required_permissions_read_one_remote_user_and_keep_tenant_isolation(monkeypatch):
    calls = []
    monkeypatch.setattr(common, "_firebase_deve_usar", lambda: True, raising=False)
    monkeypatch.setattr(common, "_firebase_access_obrigatorio", lambda: True, raising=False)
    monkeypatch.setattr(
        common,
        "_firebase_obter_usuario",
        lambda username: calls.append(username)
        or {"username": username, "client_id": "000001", "permissions": {"full": True}},
        raising=False,
    )
    monkeypatch.setattr(
        common,
        "carregar_usuarios_sheets",
        lambda: pytest.fail("a permissao autoritativa nao deve listar todos os usuarios"),
        raising=False,
    )
    monkeypatch.setattr(common, "_normalizar_permissoes", dict, raising=False)

    assert common._carregar_permissoes_usuario("Usuario", "000001") == {"full": True}
    assert calls == ["usuario"]

    with pytest.raises(HTTPException) as exc_info:
        common._carregar_permissoes_usuario("Usuario", "000002")
    assert exc_info.value.status_code == 403


def test_auto_permissions_serve_one_hundred_checks_with_one_document_read(monkeypatch):
    get_calls = []

    class Snapshot:
        exists = True

        @staticmethod
        def to_dict():
            return {
                "username": "usuario",
                "client_id": "000001",
                "permissions": {"full": True},
            }

    class Document:
        @staticmethod
        def get(**kwargs):
            get_calls.append(kwargs)
            return Snapshot()

    class Collection:
        @staticmethod
        def document(_doc_id):
            return Document()

    monkeypatch.setattr(firebase, "_firebase_collection", lambda: Collection())
    monkeypatch.setattr(firebase, "_firebase_doc_id", lambda username: username)
    monkeypatch.setattr(
        firebase,
        "_firebase_user_from_data",
        lambda username, data: {**data, "username": username},
    )
    monkeypatch.setattr(common, "_firebase_deve_usar", lambda: True, raising=False)
    monkeypatch.setattr(common, "_firebase_access_obrigatorio", lambda: False, raising=False)
    monkeypatch.setattr(common, "_firebase_obter_usuario", firebase._firebase_obter_usuario, raising=False)
    monkeypatch.setattr(common, "_normalizar_permissoes", dict, raising=False)
    monkeypatch.setattr(
        common,
        "carregar_usuarios_sheets",
        lambda **_kwargs: pytest.fail("o modo auto nao deve listar a colecao para validar permissoes"),
        raising=False,
    )

    for _ in range(100):
        assert common._carregar_permissoes_usuario("Usuario", "000001") == {"full": True}

    assert get_calls == [{"retry": None, "timeout": 5.0}]


def test_auto_permissions_fall_back_locally_without_another_firebase_query(monkeypatch):
    fallback_calls = []
    local = {
        "usuario": {
            "username": "usuario",
            "client_id": "000001",
            "permissions": {"vendas": True},
        }
    }
    monkeypatch.setattr(common, "_firebase_deve_usar", lambda: True, raising=False)
    monkeypatch.setattr(common, "_firebase_access_obrigatorio", lambda: False, raising=False)
    monkeypatch.setattr(common, "_firebase_obter_usuario", lambda _username: None, raising=False)
    monkeypatch.setattr(common, "_normalizar_permissoes", dict, raising=False)
    monkeypatch.setattr(
        common,
        "carregar_usuarios_sheets",
        lambda *, skip_firebase=False: fallback_calls.append(skip_firebase) or (local, None, []),
        raising=False,
    )

    assert common._carregar_permissoes_usuario("Usuario", "000001") == {"vendas": True}
    assert fallback_calls == [True]


def test_user_mutations_invalidate_the_cached_document(monkeypatch):
    invalidated = []

    class Snapshot:
        exists = False

        @staticmethod
        def to_dict():
            return {}

    class Document:
        @staticmethod
        def get(**_kwargs):
            return Snapshot()

        @staticmethod
        def set(*_args, **_kwargs):
            return None

        @staticmethod
        def delete(**_kwargs):
            return None

    class Collection:
        @staticmethod
        def document(_doc_id):
            return Document()

    monkeypatch.setattr(firebase, "_firebase_collection", lambda: Collection())
    monkeypatch.setattr(firebase, "_firebase_doc_id", lambda username: username)
    monkeypatch.setattr(firebase, "_firebase_user_cache_invalidate", lambda username="": invalidated.append(username))
    monkeypatch.setattr(firebase, "_firebase_user_cache_set", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(firebase, "_firebase_user_to_data", lambda username, usuario, source="admin": {**usuario, "username": username})
    monkeypatch.setattr(firebase, "_firebase_user_from_data", lambda username, data: {**data, "username": username})
    monkeypatch.setattr(firebase, "_salvar_usuarios_sql", lambda *_args, **_kwargs: None, raising=False)
    monkeypatch.setattr(firebase, "_backend_cache_invalidate_user_views", lambda *_args, **_kwargs: None, raising=False)
    monkeypatch.setattr(firebase, "_firebase_users_index_update_user", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(firebase, "_firebase_users_index_remove_user", lambda *_args, **_kwargs: None)

    firebase._firebase_salvar_usuario("usuario", {"client_id": "000001"}, merge=False)
    assert firebase._firebase_excluir_usuario("usuario") is True

    assert invalidated == ["usuario", "usuario"]


def test_required_permissions_do_not_fall_back_when_remote_user_is_missing(monkeypatch):
    monkeypatch.setattr(common, "_firebase_deve_usar", lambda: True, raising=False)
    monkeypatch.setattr(common, "_firebase_access_obrigatorio", lambda: True, raising=False)
    monkeypatch.setattr(common, "_firebase_obter_usuario", lambda _username: None, raising=False)
    monkeypatch.setattr(
        common,
        "_carregar_usuarios_sql",
        lambda: pytest.fail("o modo Firebase obrigatorio nao pode usar usuario local"),
        raising=False,
    )

    with pytest.raises(HTTPException) as exc_info:
        common._carregar_permissoes_usuario("usuario", "000001")
    assert exc_info.value.status_code == 401


def test_required_password_login_reads_only_requested_user(monkeypatch):
    remote = {"username": "usuario", "client_id": "000001", "source": "firebase"}
    monkeypatch.setattr(login_core, "_firebase_deve_usar", lambda: True, raising=False)
    monkeypatch.setattr(login_core, "_firebase_access_obrigatorio", lambda: True, raising=False)
    monkeypatch.setattr(login_core, "_firebase_obter_usuario", lambda username: remote if username == "usuario" else None, raising=False)
    monkeypatch.setattr(
        login_core,
        "carregar_usuarios_sheets",
        lambda: pytest.fail("login por usuario nao deve listar a colecao inteira"),
        raising=False,
    )

    usuario, worksheet, headers = login_core._carregar_usuario_login(" Usuario ")

    assert usuario is remote
    assert worksheet is None
    assert headers == []


def test_login_endpoint_uses_single_user_loader_instead_of_collection(monkeypatch):
    loaded = []
    usuario = {
        "username": "usuario",
        "password": "senha",
        "client_id": "000001",
        "permissions": {"full": True},
        "active": True,
    }
    monkeypatch.setattr(auth, "_validar_versao_minima_app_ou_426", lambda version: version, raising=False)
    remote_states = SimpleNamespace(
        REJECTED="rejected",
        INTEGRITY_FAILURE="integrity_failure",
        UNAVAILABLE="unavailable",
    )
    remote_attempt = SimpleNamespace(
        success=False,
        state=remote_states.UNAVAILABLE,
        allow_legacy_fallback=True,
        message="",
    )
    monkeypatch.setattr(auth, "RemoteAuthState", remote_states, raising=False)
    monkeypatch.setattr(auth, "_montar_machine_id_login", lambda *_args: ("machine", {}), raising=False)
    monkeypatch.setattr(auth, "attempt_remote_login", lambda **_kwargs: remote_attempt, raising=False)
    monkeypatch.setattr(
        auth,
        "_carregar_usuario_login",
        lambda username: loaded.append(username) or (usuario, None, []),
        raising=False,
    )
    monkeypatch.setattr(
        auth,
        "carregar_usuarios_sheets",
        lambda **_kwargs: pytest.fail("o endpoint de login nao deve listar a colecao"),
        raising=False,
    )
    monkeypatch.setattr(auth, "_login_senha_confere", lambda *_args: True, raising=False)
    monkeypatch.setattr(auth, "_login_usuario_ativo", lambda _usuario: True, raising=False)
    monkeypatch.setattr(auth, "_login_validade_ok", lambda _usuario: (True, ""), raising=False)
    monkeypatch.setattr(auth, "_normalizar_permissoes", dict, raising=False)
    monkeypatch.setattr(
        auth,
        "_login_validar_e_registrar_maquina",
        lambda *_args: (True, "", "machine"),
        raising=False,
    )
    monkeypatch.setattr(auth, "_registrar_login_maquina", lambda *_args: None, raising=False)
    monkeypatch.setattr(auth, "_machine_presence_record", lambda *_args: {}, raising=False)
    monkeypatch.setattr(auth, "_machine_presence_save", lambda *_args: None, raising=False)
    monkeypatch.setattr(
        auth,
        "_montar_resposta_login_sucesso",
        lambda username, *_args: {"success": True, "username": username},
        raising=False,
    )
    payload = SimpleNamespace(
        app_version="1.0.122",
        username="Usuario",
        password="senha",
        client_id="000001",
        machine_id="machine",
    )

    response = asyncio.run(auth.login_endpoint(payload, request=None))

    assert response == {"success": True, "username": "usuario"}
    assert loaded == ["usuario"]


def test_required_session_refresh_propagates_firebase_unavailability(monkeypatch):
    remote_error = HTTPException(status_code=503, detail="Firebase indisponivel")
    monkeypatch.setattr(
        auth,
        "_payload_sessao_por_authorization",
        lambda _authorization: {"username": "usuario", "client_id": "000001", "machine_id": "machine"},
        raising=False,
    )
    monkeypatch.setattr(auth, "_authenticated_session_machine_id", lambda *_args: "machine", raising=False)
    monkeypatch.setattr(
        auth,
        "_obter_usuario_sql",
        lambda _username: {"username": "usuario", "client_id": "000001", "active": True},
        raising=False,
    )
    monkeypatch.setattr(auth, "_login_usuario_ativo", lambda _usuario: True, raising=False)
    monkeypatch.setattr(auth, "_login_validade_ok", lambda _usuario: (True, ""), raising=False)
    monkeypatch.setattr(auth, "_carregar_permissoes_usuario", lambda *_args: (_ for _ in ()).throw(remote_error), raising=False)
    monkeypatch.setattr(auth, "_firebase_access_obrigatorio", lambda: True, raising=False)

    with pytest.raises(HTTPException) as exc_info:
        auth.minha_sessao_auth(request=None, authorization="Bearer token")

    assert exc_info.value is remote_error
