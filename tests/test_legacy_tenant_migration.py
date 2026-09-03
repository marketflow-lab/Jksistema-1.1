import logging
import json
import subprocess
import sys
import threading
from pathlib import Path

from backend.services import integracoes
from backend.services import legacy_tenant_migration


_HOLD_TRANSITION_LOCK_SCRIPT = r"""
import sys
from backend.services.cadastro_fotos_coordenacao import bloquear_transicao_fotos_tenant

tenant = sys.argv[1]
with bloquear_transicao_fotos_tenant(tenant, timeout_seconds=5):
    print("START", flush=True)
    sys.stdin.readline()
"""

_APP_PATHS_DEFAULT_SCRIPT = r"""
import logging
import sys
from backend.core import AppPaths

base_dir, info_dir, source = sys.argv[1:4]
paths = AppPaths.create(
    base_dir=base_dir,
    info_dir=info_dir,
    logger=logging.getLogger("app-paths-standalone-test"),
)
print(paths.migrate_legacy_file("default", "produtos_compilado.csv", source))
"""


def _configure(tmp_path: Path, monkeypatch):
    info_root = tmp_path / "info"
    info_root.mkdir()

    def tenant_path(client_id: str) -> str:
        tenant = info_root / str(client_id)
        tenant.mkdir(parents=True, exist_ok=True)
        return str(tenant)

    monkeypatch.setattr(integracoes, "PASTA_INFO", str(info_root))
    monkeypatch.setattr(integracoes, "_get_tenant_path", tenant_path)
    return info_root, tenant_path


def _migrar(client_id: str, source: Path, tenant_path) -> Path:
    destino = legacy_tenant_migration.migrar_arquivo_legado_para_tenant_seguro(
        client_id,
        source.name,
        str(source),
        get_tenant_path=tenant_path,
        logger=logging.getLogger("legacy-tenant-migration-test"),
    )
    return Path(destino)


def test_tenant_nao_default_nunca_consume_catalogo_global(tmp_path, monkeypatch):
    _info_root, tenant_path = _configure(tmp_path, monkeypatch)
    source = tmp_path / "cadastro_produtos.csv"
    source.write_bytes(b"sku,foto\n001,cadastro_fotos/001.jpg\n")

    destino = _migrar("000002", source, tenant_path)

    assert source.read_bytes() == b"sku,foto\n001,cadastro_fotos/001.jpg\n"
    assert not destino.exists()


def test_default_estrito_nao_reintroduz_referencia_local(tmp_path, monkeypatch):
    _info_root, tenant_path = _configure(tmp_path, monkeypatch)
    tenant = Path(tenant_path("default"))
    (tenant / "cadastro_fotos_config.json").write_text(
        json.dumps(
            {
                "schema": "jk.cadastro.fotos.v1",
                "strict_store_scope": True,
                "shared_groups": [
                    {"group_id": "grupo-a", "store_ids": ["store-a", "store-b"]}
                ],
            }
        ),
        encoding="utf-8",
    )
    source = tmp_path / "cadastro_produtos.csv"
    source.write_bytes(b"sku,foto\n001,cadastro_fotos/001.jpg\n")

    destino = _migrar("default", source, tenant_path)

    assert source.exists()
    assert not destino.exists()


def test_default_config_invalida_bloqueia_legado(tmp_path, monkeypatch):
    _info_root, tenant_path = _configure(tmp_path, monkeypatch)
    tenant = Path(tenant_path("default"))
    (tenant / "cadastro_fotos_config.json").write_text(
        '{"strict_store_scope": false}',
        encoding="utf-8",
    )
    source = tmp_path / "produtos_compilado.csv"
    source.write_bytes(b"sku,foto\n001,cadastro_fotos/001.jpg\n")

    destino = _migrar("default", source, tenant_path)

    assert source.exists()
    assert not destino.exists()


def test_default_grupo_nao_verificavel_bloqueia_legado(tmp_path, monkeypatch):
    _info_root, tenant_path = _configure(tmp_path, monkeypatch)
    tenant = Path(tenant_path("default"))
    (tenant / "cadastro_fotos_config.json").write_text(
        json.dumps(
            {
                "schema": "jk.cadastro.fotos.v1",
                "strict_store_scope": False,
                "shared_groups": [
                    {
                        "group_id": "nao-verificavel",
                        "store_ids": ["inexistente-a", "inexistente-b"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    source = tmp_path / "produtos_compilado.csv"
    source.write_bytes(b"sku,foto\n001,cadastro_fotos/001.jpg\n")

    destino = _migrar("default", source, tenant_path)

    assert source.exists()
    assert not destino.exists()


def test_default_publica_copia_verificada_e_remove_origem(tmp_path, monkeypatch):
    _info_root, tenant_path = _configure(tmp_path, monkeypatch)
    source = tmp_path / "cadastro_produtos.csv"
    payload = b"sku,nome\n001,Produto\n"
    source.write_bytes(payload)

    destino = _migrar("default", source, tenant_path)

    assert destino.read_bytes() == payload
    assert not source.exists()


def test_app_paths_default_funciona_em_processo_sem_cadastro_runtime(tmp_path):
    info_root = tmp_path / "info"
    source = tmp_path / "produtos_compilado.csv"
    payload = b"sku,estoque\n001,7\n"
    source.write_bytes(payload)

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            _APP_PATHS_DEFAULT_SCRIPT,
            str(tmp_path),
            str(info_root),
            str(source),
        ],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    destino = info_root / "default" / "produtos_compilado.csv"
    assert destino.read_bytes() == payload
    assert not source.exists()


def test_publicacao_concorrente_nunca_e_sobrescrita(tmp_path, monkeypatch):
    _info_root, tenant_path = _configure(tmp_path, monkeypatch)
    source = tmp_path / "cadastro_produtos.csv"
    source.write_bytes(b"origem")
    vencedor = b"destino-concorrente"

    def link_com_corrida(_temporario, destino):
        Path(destino).write_bytes(vencedor)
        raise FileExistsError(destino)

    monkeypatch.setattr(legacy_tenant_migration.os, "link", link_com_corrida)

    destino = _migrar("default", source, tenant_path)

    assert destino.read_bytes() == vencedor
    assert source.read_bytes() == b"origem"


def test_default_espera_mutex_cross_process_antes_de_mover(tmp_path, monkeypatch):
    _info_root, tenant_path = _configure(tmp_path, monkeypatch)
    source = tmp_path / "cadastro_produtos.csv"
    source.write_bytes(b"sku,nome\n001,Produto\n")
    tenant = Path(tenant_path("default"))
    process = subprocess.Popen(
        [sys.executable, "-c", _HOLD_TRANSITION_LOCK_SCRIPT, str(tenant)],
        cwd=Path(__file__).resolve().parents[1],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    resultado = []
    erros = []

    def executar():
        try:
            resultado.append(_migrar("default", source, tenant_path))
        except BaseException as exc:  # pragma: no cover - exposto pelas assercoes
            erros.append(exc)

    thread = threading.Thread(target=executar, daemon=True)
    try:
        assert process.stdout is not None
        assert process.stdout.readline().strip() == "START"
        thread.start()
        thread.join(0.5)
        assert thread.is_alive()
        assert source.exists()
        assert not (tenant / source.name).exists()
        assert process.stdin is not None
        process.stdin.write("\n")
        process.stdin.flush()
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 0, stdout + stderr
        thread.join(10)
    finally:
        if process.poll() is None:
            process.terminate()
            process.communicate(timeout=5)

    assert not thread.is_alive()
    assert erros == []
    assert resultado == [tenant / source.name]
    assert resultado[0].read_bytes() == b"sku,nome\n001,Produto\n"
    assert not source.exists()
