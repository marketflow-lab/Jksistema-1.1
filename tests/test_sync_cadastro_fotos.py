import pytest

import sync_cadastro_fotos


def test_utilitario_global_descontinuado_falha_fechado_mesmo_sem_configuracao(
    tmp_path,
):
    tenant = tmp_path / "info" / "000002"
    tenant.mkdir(parents=True)
    csv_path = tenant / "cadastro_produtos.csv"
    csv_path.write_bytes(b"sku,nome,foto\n001,Produto,\n")
    csv_antes = csv_path.read_bytes()

    with pytest.raises(RuntimeError, match="global descontinuado"):
        sync_cadastro_fotos.sincronizar_csv(
            csv_path,
            {"001": (b"foto-global-proibida", "png")},
        )

    assert csv_path.read_bytes() == csv_antes
    assert not (tenant / "cadastro_fotos").exists()


def test_main_descontinuado_falha_antes_de_baixar_planilha(monkeypatch):
    monkeypatch.setattr(
        sync_cadastro_fotos,
        "baixar_planilha_xlsx",
        lambda: (_ for _ in ()).throw(AssertionError("nao deve baixar")),
    )

    with pytest.raises(RuntimeError, match="global descontinuado"):
        sync_cadastro_fotos.main()
