"""Cubagem exercised through the real list enrichment and summaries."""
import copy
from decimal import Decimal
import pandas as pd
import pytest
from backend.services import medias_compras_fiscal as fiscal
from backend.services import medias_compras_common as common
from backend.services import cadastro_compatibilidade as compat

@pytest.fixture
def ambiente(monkeypatch, tmp_path):
    source = tmp_path / 'cadastro_produtos.backup_classificacao.csv'
    source.write_text('sku,cg_m³ individual,store_id\n001,99,loja-b\nX,88,loja-b\n', encoding='utf-8')
    monkeypatch.setattr(fiscal, '_carregar_cadastro_para_impostos', lambda _: (
        pd.DataFrame([{'sku': '001', 'm3 individual': '50'}, {'sku':'X','m3 individual':'66'}]), ''))
    monkeypatch.setattr(fiscal, '_caminhos_cadastros_meta', lambda _: [str(source)])
    monkeypatch.setattr(fiscal, '_resolver_foto_cadastro_sku', lambda *a, **k: '')
    monkeypatch.setattr(fiscal, '_indice_ncm_referencia_aliquotas', lambda _: {})
    monkeypatch.setattr(fiscal, '_indice_ncm_referencia_por_ncm', lambda _: {})
    rows = [
        {'sku':'001','store_id':'loja-a','m3 individual':'0,00003125','m3_unidade':'par','m3_origem':'Packing A'},
        {'sku':'1','store_id':'loja-a','m3 individual':'0.0004','m3_unidade':'peca'},
        {'sku':'001','store_id':'loja-b','m3 individual':'0.5'},
        {'sku':'DEL','store_id':'loja-a','m3 individual':'0.7','deleted_at_utc':'2026-10-13'},
    ]
    def context(client, loja, **kw):
        assert client == 'tenant-a'
        if loja not in ('loja-a','loja-b'):
            return {'scope':'unresolved','store_id':'','produtos':[]}
        return {'scope':'store','store_id':loja,'produtos':copy.deepcopy(rows),'skus_controlados':{'001','1','X','DEL'}}
    monkeypatch.setattr(compat, 'visao_produtos_cadastro_contexto_loja', context)
    return rows

def test_current_store_volume_precedes_all_legacy_sources(ambiente):
    result = fiscal._enriquecer_itens_lista_pedido_com_impostos('tenant-a', [
        {'SKU':'001','Quantidade':80,'M3 individual':9,'M3':720},
        {'SKU':'1','Quantidade':2},
    ], incluir_impostos=False, loja='loja-a')
    assert result[0]['M3 individual'] == 0.00003125
    assert result[0]['M3'] == 0.0025
    assert result[0]['Cubagem status'] == 'cadastro'
    assert result[0]['Cubagem fonte'] == 'Packing A'
    assert result[0]['Cubagem unidade'] == 'par'
    assert result[1]['M3'] == 0.0008
    other = fiscal._enriquecer_itens_lista_pedido_com_impostos('tenant-a', [{'SKU':'001','Quantidade':2}], incluir_impostos=False, loja='loja-b')
    assert other[0]['M3'] == 1.0

def test_missing_and_deleted_never_use_backup_or_residual_totals(ambiente):
    result = fiscal._enriquecer_itens_lista_pedido_com_impostos('tenant-a', [
        {'SKU':'X','Quantidade':4,'M3 individual':66,'M3':264},
        {'SKU':'DEL','Quantidade':1},
        {'SKU':'X','Quantidade':0,'M3':99},
    ], incluir_impostos=False, loja='loja-a')
    assert [i['Cubagem status'] for i in result] == ['pendente'] * 3
    assert result[0]['M3'] == result[0]['M3 individual'] == ''
    assert result[1]['M3'] == ''
    assert result[2]['M3'] == 0

def test_unresolved_store_fails_closed(ambiente):
    result = fiscal._enriquecer_itens_lista_pedido_com_impostos('tenant-a', [{'SKU':'001','Quantidade':5}], incluir_impostos=False, loja='ambigua')
    assert result[0]['Cubagem status'] == 'pendente'
    assert result[0]['M3'] == ''

def test_history_preserves_snapshot_instead_of_today_catalog(ambiente):
    original = {'SKU':'001','Quantidade':10,'M3 individual':'0.00009','M3':'0.00347'}
    result = fiscal._enriquecer_itens_lista_pedido_com_impostos('tenant-a', [original], incluir_impostos=False, loja='loja-a', preservar_cubagem=True)[0]
    assert result['M3 individual'] == 0.00009
    assert result['M3'] == 0.00347
    assert result['Cubagem status'] == 'historico'
    assert original['M3'] == '0.00347'
    empty = fiscal._enriquecer_itens_lista_pedido_com_impostos('tenant-a', [{'SKU':'001','Quantidade':10}], incluir_impostos=False, loja='loja-a', preservar_cubagem=True)[0]
    assert empty['Cubagem status'] == 'pendente'

def test_recalculate_preserves_small_volume_and_marks_partial_cost(ambiente):
    valid = fiscal._recalcular_frete_internacional_itens_lista('tenant-a', [{'SKU':'001','Quantidade':80,'Valor unidade':2}], loja='loja-a')[0]
    assert valid['M3 individual'] == 0.00003125
    assert valid['M3'] == 0.0025
    partial = fiscal._recalcular_frete_internacional_itens_lista('tenant-a', [
        {'SKU':'001','Quantidade':80,'Valor unidade':2},
        {'SKU':'X','Quantidade':2,'Valor unidade':7,'Frete Internacional':44},
    ], loja='loja-a')
    assert partial[1]['M3'] == ''
    assert partial[1]['Frete Internacional'] == ''
    assert partial[0]['Frete Internacional'] == ''

def test_map_is_exact_store_bound_and_does_not_scan_backups(ambiente, monkeypatch):
    monkeypatch.setattr(fiscal, '_caminhos_cadastros_meta', lambda _: pytest.fail('operational m3 scanned backups'))
    assert fiscal._construir_mapa_m3_sku('tenant-a') == {}
    assert fiscal._construir_mapa_m3_sku('tenant-a', store_id='loja-a') == {'001':0.00003125,'1':0.0004}
    assert fiscal._construir_mapa_m3_sku('tenant-a', store_id='loja-b') == {'001':0.5}

def test_summary_uses_same_store_map_and_reports_partial():
    lista = {'status':'Lista gerada','itens':[
        {'SKU':'001','Quantidade':80,'M3 individual':99},
        {'SKU':'1','Quantidade':2,'M3 individual':99},
        {'SKU':'X','Quantidade':0},
    ]}
    summary = common._resumo_lista_pedido(lista, {'001':0.00003125})
    assert summary['total_m3'] == 0.0025
    assert summary['m3_pendente_itens'] == 1
    assert summary['m3_completo'] is False
    lista['status'] = 'Recebido'
    lista['itens'] = [{'SKU':'001','Quantidade':80,'M3':'0.004123456','M3 individual':'0.00003125'}]
    summary = common._resumo_lista_pedido(lista, {'001':9})
    assert summary['total_m3'] == 0.004123456
    assert summary['m3_completo'] is True

def test_malformed_aliases_preserved_only_as_historical_snapshot(ambiente):
    old_key = "M\u00c3\u201a\u00c2\u00b3"
    original = {"SKU":"X", "Quantidade":4, old_key:99, "CBM individual":"0.12"}
    historic = fiscal._recalcular_frete_internacional_itens_lista("tenant-a",[original],loja="loja-a",preservar_cubagem=True)[0]
    assert historic["M3"] == 99
    assert historic["M3 individual"] == 0.12
    assert old_key not in historic and "CBM individual" not in historic
    fresh = fiscal._recalcular_frete_internacional_itens_lista("tenant-a",[original],loja="loja-a")[0]
    assert fresh["M3"] == "" and fresh["M3 individual"] == ""
    assert old_key not in fresh and "CBM individual" not in fresh
    normalized = common._normalizar_item_lista_pedido(fresh)
    assert normalized["Estimed CBM"] == ""

def test_float_overflow_total_is_pending_not_invalid_json():
    volume={"status":"confirmado", "m3_individual":Decimal("1e308")}
    result=fiscal._aplicar_cubagem_item_lista({"Quantidade":10},volume,"loja-a")
    assert result["Cubagem status"] == "pendente"
    assert result["M3"] == ""
    summary=common._resumo_lista_pedido({"itens":[{"SKU":"A","Quantidade":10}]},{"A":1e308})
    assert summary["m3_completo"] is False and summary["total_m3"] == 0

def test_real_catalog_csv_preserves_raw_identity_and_blocks_client_forgery(tmp_path):
    from backend.services import cadastro_lojas_produtos as cadastro
    from backend.services.cadastro_cubagem import resolver_cubagem_produtos
    path=tmp_path/"cadastro_produtos_lojas.csv"
    path.write_text("store_id,sku,sku_normalizado,row_version,m3 individual\nloja-a,1,001,1,0.1\nloja-b,001,001,1,0.8\n",encoding="utf-8")
    rows,_=cadastro._ler_registros_persistidos_caminho(path)
    assert rows[0]["sku"] == "001"
    assert rows[0]["cubagem_sku"] == "1"
    first=resolver_cubagem_produtos(rows,"loja-a")
    assert list(first)==["1"] and first["1"]["m3_individual"] == Decimal("0.1")
    second=resolver_cubagem_produtos(rows,"loja-b")
    assert list(second)==["001"]
    payload,_,_=cadastro._dados_mutacao({"sku":"001","cubagem_sku":"forged","m3 individual":"0.1"})
    assert "cubagem_sku" not in payload

def test_partial_m3_change_preserves_already_registered_pair_unit():
    from backend.services import cadastro_lojas_produtos as cadastro
    payload,_,_=cadastro._dados_mutacao({"m3 individual":"0,000032"})
    existing={"m3_unidade":"par", "m3 individual":"0.000030"}
    existing.update(payload)
    assert existing["m3_unidade"] == "par"
    assert existing["m3 individual"] == "0.000032"

def test_landed_cost_cannot_use_partial_volume(monkeypatch):
    from backend.services import medias_compras_listas as listas
    from backend.services import codex_reports_advanced as reports
    from fastapi import HTTPException
    monkeypatch.setattr(listas,"_api_medias_compras_lista_pedido_detalhe_sync",lambda *a,**k:{"lista":{"m3_completo":False}})
    monkeypatch.setattr(reports,"build_profile_context",lambda **k:pytest.fail("partial volume entered financial calculation"))
    with pytest.raises(HTTPException) as exc:
        listas._api_medias_compras_lista_pedido_custo_posto_sync("pedido","tenant-a")
    assert exc.value.status_code == 409

def test_landed_cost_keeps_public_response_when_volume_complete(monkeypatch):
    from backend.services import medias_compras_listas as listas
    from backend.services import codex_reports_advanced as reports
    monkeypatch.setattr(listas,"_api_medias_compras_lista_pedido_detalhe_sync",lambda *a,**k:{"lista":{"m3_completo":True}})
    monkeypatch.setattr(reports,"build_profile_context",lambda **k:{"import_analysis":{"ok":True},"scope":{"store_id":"loja-a"}})
    result=listas._api_medias_compras_lista_pedido_custo_posto_sync("pedido","tenant-a")
    assert result["success"] is True and result["analysis"] == {"ok":True}
    assert result["scope"] == {"store_id":"loja-a"}
