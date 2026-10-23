import copy

import pytest

from backend.services.fornecedor_origem import identificar_pais_fornecedor


@pytest.mark.parametrize("endereco,esperado", [
    ("Industrial Park. Hubei Province. Tianmen City.", "CHINA"),
    ("Guangzhou, Guangdong", "CHINA"),
    ("Industrial Zone, Zhejiang, China", "CHINA"),
    ("Industrial Zone, Jiangsu Province", "CHINA"),
    ("Inner Mongolia, Hohhot", "CHINA"),
    ("Hohhot, Inner Mongolia, China", "CHINA"),
    ("HUBEI", "CHINA"),
    ("Porto, Portugal", "PORTUGAL"),
    ("São Paulo, Brasil", "BRAZIL"),
    ("São Paulo, Brazil", "BRAZIL"),
    ("Tokyo, Japan", "JAPAN"),
    ("Berlin, Alemanha", "GERMANY"),
    ("Paris, France", "FRANCE"),
    ("Toronto, Canada", "CANADA"),
    ("New York, United States of America", "UNITED STATES"),
    ("New York, U.S.A.", "UNITED STATES"),
    ("London, Reino Unido", "UNITED KINGDOM"),
    ("London, UK", "UNITED KINGDOM"),
    ("Dubai, U.A.E.", "UNITED ARAB EMIRATES"),
    ("Malabo, Equatorial Guinea", "EQUATORIAL GUINEA"),
    ("Bissau, Guinea-Bissau", "GUINEA-BISSAU"),
    ("South Georgia and South Sandwich Islands", "SOUTH GEORGIA & SOUTH SANDWICH ISLANDS"),
    ("Industrial Park, Singapore", "SINGAPORE"),
    ("Hong Kong", "HONG KONG SAR CHINA"),
    ("China / Vietnam", ""),
    ("Toronto, Canada; China", ""),
    ("Rua de Exemplo, 100", ""),
    ("Hubeiland Industrial Park", ""),
    ("Business Park, Suite UK9", ""),
    ("", ""),
    (None, ""),
])
def test_identifica_pais_no_endereco_cadastrado(endereco, esperado):
    cadastro = {"endereco_empresa": endereco}
    original = copy.deepcopy(cadastro)
    assert identificar_pais_fornecedor(cadastro) == esperado
    assert cadastro == original


@pytest.mark.parametrize("endereco,esperado", [("Hubei Province", "CHINA"), ("", "")])
def test_identificacao_ignora_nome_banco_telefone_e_campo_manual(endereco, esperado):
    cadastro = {
        "nome_empresa": "Canada Exemplo Ltd.", "endereco_empresa": endereco,
        "pais_regiao_beneficiario": "Singapore", "endereco_banco": "Singapore",
        "endereco_beneficiario": "Canada", "pais_origem": "VIETNAM", "telefone": "+0000000",
    }
    original = copy.deepcopy(cadastro)
    assert identificar_pais_fornecedor(cadastro) == esperado
    assert cadastro == original


def test_cadastro_ausente_nao_inventa_pais():
    assert identificar_pais_fornecedor(None) == ""
