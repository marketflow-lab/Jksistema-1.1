import json

from scripts.sync_store_catalog_fiches import inventory_and_sync


def test_initial_inventory_and_idempotent_load_preserve_manual_notes(tmp_path):
    root = tmp_path / "info"
    tenant = root / "000002"
    curated = tenant / "ContextVault" / "80_Curadoria"
    curated.mkdir(parents=True)
    note = curated / "manual.md"
    note.write_text("Conteúdo manual", encoding="utf-8")
    (tenant / "lojas_config.json").write_text(json.dumps([
        {"store_id": "uai", "nome": "Uai Mineirinho", "integracoes": {"mercadolivre": {"user_id": "123", "site_id": "MLB"}}},
        {"store_id": "jk", "nome": "JK Peças", "integracoes": {"mercadolivre": {"user_id": "456", "site_id": "MLB"}}}
    ]), encoding="utf-8")
    (tenant / "cadastro_produtos_lojas.csv").write_text(
        "store_id;sku;produto_bling;descricao\nuai;001;Sensor;Descrição\njk;001;Produto distinto;Original\nuai;002;;\n", encoding="utf-8")
    preview = inventory_and_sync("000002", ["uai", "jk"], info_root=root)
    assert preview["stores"][0]["catalog_skus"] == 2
    assert preview["stores"][0]["without_technical_data"] == 1
    assert not (tenant / "context_hub" / "context_hub.db").exists()
    report = inventory_and_sync("000002", ["uai", "jk"], info_root=root, apply=True)
    assert report["curated_preserved"] and note.read_text(encoding="utf-8") == "Conteúdo manual"
    assert all(store["idempotent_repeat"] for store in report["stores"])
    assert report["stores"][0]["sku_001"]["found"]
    assert report["stores"][0]["sku_001"]["sku"] == "001"
    assert report["stores"][0]["verified_fiches"] == 2
