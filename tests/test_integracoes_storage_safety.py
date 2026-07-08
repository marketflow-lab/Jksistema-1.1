import json

import pytest
from fastapi import HTTPException

from backend.services import integracoes


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
