from backend.schemas.favoritos import FavoritosEfetivarPromocaoRequest
from backend.services import favoritos_ml
from backend.services.promocoes_core_analise import _parse_float_flex


def _req_contingencia(preco_ranking, preco_minimo_margem, preco_promocional=60.05, preco_competitivo=37.89):
    return FavoritosEfetivarPromocaoRequest(
        loja="JK Pecas",
        item_id="MLB2894843116",
        sku="001",
        preco_anuncio=79.32,
        preco_promocional=preco_promocional,
        preco_competitivo=preco_competitivo,
        campanha_id="campanha-teste",
        simulacao={
            "precoRanking": preco_ranking,
            "precoMinimoMargem": preco_minimo_margem,
        },
    )


def _configurar_dependencias(monkeypatch):
    monkeypatch.setattr(favoritos_ml, "_parse_float_flex", _parse_float_flex, raising=False)
    monkeypatch.setattr(
        favoritos_ml,
        "_favoritos_ml_preco_ranking_simulado",
        lambda req: _parse_float_flex((req.simulacao or {}).get("precoRanking")),
        raising=False,
    )


def test_contingencia_usa_preco_minimo_margem_quando_nao_da_para_competir(monkeypatch):
    _configurar_dependencias(monkeypatch)
    req = _req_contingencia(preco_ranking=37.90, preco_minimo_margem=60.05)

    preco = favoritos_ml._favoritos_ml_preco_contingencia_sem_promocao(req)

    assert preco == 60.05


def test_contingencia_preserva_faixa_competitiva_quando_margem_permitem(monkeypatch):
    _configurar_dependencias(monkeypatch)
    req = _req_contingencia(
        preco_ranking=37.90,
        preco_minimo_margem=30.00,
        preco_promocional=None,
        preco_competitivo=None,
    )

    preco = favoritos_ml._favoritos_ml_preco_contingencia_sem_promocao(req)

    assert preco == 36.40


def test_contingencia_prefere_preco_final_promocional_sem_campanha(monkeypatch):
    _configurar_dependencias(monkeypatch)
    req = _req_contingencia(preco_ranking=49.50, preco_minimo_margem=49.84, preco_promocional=49.84)

    preco = favoritos_ml._favoritos_ml_preco_contingencia_sem_promocao(req)

    assert preco == 49.84


def test_erro_generico_ml_cai_no_fallback_sem_promocao(monkeypatch):
    from backend.services.promocoes_core_parsing import normalizar_texto

    monkeypatch.setattr(favoritos_ml, "normalizar_texto", normalizar_texto, raising=False)

    assert favoritos_ml._favoritos_ml_falha_por_percentual_promocao(
        "Preco atualizado, mas falhou ao aplicar a promocao: Oops! Something went wrong..."
    )
