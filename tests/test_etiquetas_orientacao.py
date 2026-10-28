"""Regression coverage of the geometry and contents of generated label PDFs."""

import re
import unittest

import fitz
from reportlab.lib.units import mm

from backend.schemas.etiquetas import EtiquetaAvulsaItem
from backend.services.etiquetas_pdf_avulso import (
    gerar_pdf_editor_livre,
    gerar_pdf_impressao_avulsa,
)
from backend.services.etiquetas_qrcode import gerar_pdf_qrcode_link


def _compacto(texto):
    return re.sub(r"\s+", "", texto)


def _spans(pagina):
    return [
        span
        for bloco in pagina.get_text("dict")["blocks"]
        for linha in bloco.get("lines", [])
        for span in linha["spans"]
        if span["text"].strip()
    ]


class EtiquetasOrientacaoTest(unittest.TestCase):
    def assert_dimensoes(self, pagina, largura_mm, altura_mm):
        self.assertAlmostEqual(pagina.rect.width, largura_mm * mm, places=3)
        self.assertAlmostEqual(pagina.rect.height, altura_mm * mm, places=3)

    def assert_texto_contido(self, pagina, celulas=None):
        limites = pagina.rect + (-0.1, -0.1, 0.1, 0.1)
        spans = _spans(pagina)
        self.assertTrue(spans, "O PDF deve conter texto selecionavel.")
        for span in spans:
            caixa = fitz.Rect(span["bbox"])
            self.assertTrue(limites.contains(caixa), (span["text"], caixa, limites))
            if celulas:
                self.assertTrue(
                    any(celula.contains(caixa) for celula in celulas),
                    (span["text"], caixa, celulas),
                )

    def test_avulsa_preserva_tamanhos_legados_sem_orientacao(self):
        item = EtiquetaAvulsaItem(sku="00123", sequencia="0007", quantidade=1)
        for formato, dimensoes in (("grande", (150, 80)), ("pequena", (80, 25))):
            with self.subTest(formato=formato):
                pdf, qtd = gerar_pdf_impressao_avulsa([item], formato)
                with fitz.open(stream=pdf, filetype="pdf") as doc:
                    self.assertEqual(qtd, 1)
                    self.assert_dimensoes(doc[0], *dimensoes)

    def test_avulsa_textos_longos_cabem_em_retrato_e_paisagem(self):
        sku = "000123-" + "CODIGOSEMESPACO" * 5
        sequencia = "0001234567890123456789"
        conta = "CONTA DE ARMAZENAMENTO PARA PRODUTOS COM DESCRICAO EXTENSA"
        item = EtiquetaAvulsaItem(
            sku=sku,
            sequencia=sequencia,
            quantidade=123456789,
            caixa="123456789",
            torre="12345",
            paletes="67890",
            corredor="12345",
            conta=conta,
        )
        for formato, medidas in (("grande", (150, 80)), ("pequena", (80, 25))):
            for orientacao in ("paisagem", "retrato"):
                with self.subTest(formato=formato, orientacao=orientacao):
                    pdf, qtd = gerar_pdf_impressao_avulsa([item] * 3, formato, orientacao=orientacao)
                    dimensoes = medidas if orientacao == "paisagem" else tuple(reversed(medidas))
                    with fitz.open(stream=pdf, filetype="pdf") as doc:
                        self.assertEqual(qtd, 3)
                        self.assertEqual(len(doc), 3 if formato == "grande" else 2)
                        for pagina in doc:
                            self.assert_dimensoes(pagina, *dimensoes)
                            celulas = None
                            if formato == "pequena":
                                w, h = pagina.rect.width, pagina.rect.height
                                if orientacao == "paisagem":
                                    celulas = [fitz.Rect(0, 0, w / 2, h), fitz.Rect(w / 2, 0, w, h)]
                                else:
                                    celulas = [fitz.Rect(0, 0, w, h / 2), fitz.Rect(0, h / 2, w, h)]
                                celulas = [rect + (-0.1, -0.1, 0.1, 0.1) for rect in celulas]
                            self.assert_texto_contido(pagina, celulas)
                            textos_spans = [span["text"].strip() for span in _spans(pagina)]
                            self.assertIn(sequencia, textos_spans)
                            self.assertIn("QTD:123456789", textos_spans)
                            texto = _compacto(pagina.get_text())
                            for valor in (sku, sequencia, conta, "123456789"):
                                self.assertIn(_compacto(valor), texto)

    def test_editor_quebra_codigo_longo_e_ajusta_altura_sem_perder_linhas(self):
        codigo = "0007-" + "SKUCONTINUO" * 14
        linhas = [codigo] + [f"LINHA {numero:02d} COM TEXTO DE IDENTIFICACAO" for numero in range(1, 15)]
        html = "".join(f'<div style="font-size: 48px"><b><u>{linha}</u></b></div>' for linha in linhas)
        for tamanho, medidas in (("grande", (150, 80)), ("pequena", (80, 25))):
            for orientacao in ("paisagem", "retrato"):
                with self.subTest(tamanho=tamanho, orientacao=orientacao):
                    pdf, qtd = gerar_pdf_editor_livre([html] * 3, tamanho, orientacao=orientacao)
                    dimensoes = medidas if orientacao == "paisagem" else tuple(reversed(medidas))
                    with fitz.open(stream=pdf, filetype="pdf") as doc:
                        self.assertEqual(qtd, 3)
                        self.assertEqual(len(doc), 3 if tamanho == "grande" else 2)
                        for pagina in doc:
                            self.assert_dimensoes(pagina, *dimensoes)
                            self.assert_texto_contido(pagina)
                            texto = _compacto(pagina.get_text())
                            for linha in linhas:
                                self.assertIn(_compacto(linha), texto)

    def test_qrcode_etiqueta_e_grades_orientam_pagina_e_preservam_titulo(self):
        titulo = "IDENTIFICACAO-" + "PRODUTOSEMESPACO" * 5
        link = "https://example.com/produtos/" + "000123456789" * 6
        casos = (
            ("grande", "etiqueta", (150, 100), 2),
            ("pequena", "etiqueta", (80, 25), 2),
            ("grande", "grade_15x8", (150, 100), 9),
            ("pequena", "grade_8x25", (80, 25), 3),
        )
        for tamanho, layout, medidas, quantidade in casos:
            for orientacao in ("paisagem", "retrato"):
                with self.subTest(tamanho=tamanho, layout=layout, orientacao=orientacao):
                    pdf, qtd = gerar_pdf_qrcode_link(
                        link, titulo, quantidade, tamanho, layout, orientacao=orientacao
                    )
                    dimensoes = medidas if orientacao == "paisagem" else tuple(reversed(medidas))
                    with fitz.open(stream=pdf, filetype="pdf") as doc:
                        self.assertEqual(qtd, quantidade)
                        self.assertEqual(len(doc), 2)
                        for pagina in doc:
                            self.assert_dimensoes(pagina, *dimensoes)
                            self.assert_texto_contido(pagina)
                            texto = _compacto(pagina.get_text())
                            self.assertIn(_compacto(titulo), texto)
                            if layout == "etiqueta":
                                self.assertIn(_compacto(link), texto)

    def test_qrcode_a4_orienta_folha_e_mantem_todas_etiquetas(self):
        for orientacao, dimensoes in (("paisagem", (297, 210)), ("retrato", (210, 297))):
            with self.subTest(orientacao=orientacao):
                pdf, qtd = gerar_pdf_qrcode_link(
                    "https://example.com/000123", "ETIQUETA A4", 5, "grande", "a4", orientacao=orientacao
                )
                with fitz.open(stream=pdf, filetype="pdf") as doc:
                    self.assertEqual(qtd, 5)
                    total = 0
                    for pagina in doc:
                        self.assert_dimensoes(pagina, *dimensoes)
                        self.assert_texto_contido(pagina)
                        total += _compacto(pagina.get_text()).count("ETIQUETAA4")
                    self.assertEqual(total, 5)

    def test_orientacao_invalida_e_rejeitada(self):
        chamadas = (
            lambda: gerar_pdf_impressao_avulsa([EtiquetaAvulsaItem(sku="1")], orientacao="diagonal"),
            lambda: gerar_pdf_editor_livre(["<div>Teste</div>"], orientacao="diagonal"),
            lambda: gerar_pdf_qrcode_link("https://example.com", orientacao="diagonal"),
        )
        for chamada in chamadas:
            with self.assertRaises(ValueError):
                chamada()

    def test_qrcode_link_no_limite_cabe_nas_duas_celulas_pequenas(self):
        prefixo = "https://example.com/"
        link = prefixo + ("z" * (2000 - len(prefixo)))
        for orientacao in ("paisagem", "retrato"):
            with self.subTest(orientacao=orientacao):
                pdf, qtd = gerar_pdf_qrcode_link(
                    link, "LINK NO LIMITE", 2, "pequena", "grade_8x25", orientacao=orientacao
                )
                with fitz.open(stream=pdf, filetype="pdf") as doc:
                    self.assertEqual(qtd, 2)
                    self.assertEqual(len(doc), 1)
                    pagina = doc[0]
                    self.assert_texto_contido(pagina)
                    self.assertEqual(_compacto(pagina.get_text()).count(link), 2)
                    limites = pagina.rect + (-0.1, -0.1, 0.1, 0.1)
                    for desenho in pagina.get_drawings():
                        self.assertTrue(limites.contains(desenho["rect"]))


class EtiquetasOrientacaoHttpTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from backend.routers.etiquetas import EtiquetasRouterConfig, create_etiquetas_router

        cls.storage = {}
        app = FastAPI()
        app.include_router(create_etiquetas_router(EtiquetasRouterConfig(
            get_tenant_id=lambda: "cliente-teste",
            temp_files_storage=cls.storage,
        )))
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        cls.client.close()

    def test_endpoints_encaminham_orientacao_e_aceitam_clientes_antigos(self):
        casos = (
            ("impressao-avulsa", {"itens": [{"sku": "000123"}]}, (150, 80)),
            ("impressao-editor", {"etiquetas": ["<div>000123</div>"]}, (150, 80)),
            ("qrcode-link", {"link": "https://example.com/000123", "layout": "etiqueta"}, (150, 100)),
        )
        for endpoint, payload, medidas in casos:
            for orientacao in (None, "retrato", "paisagem"):
                with self.subTest(endpoint=endpoint, orientacao=orientacao):
                    dados = dict(payload)
                    if orientacao:
                        dados["orientacao"] = orientacao
                    resposta = self.client.post(f"/api/etiquetas/{endpoint}", json=dados)
                    self.assertEqual(resposta.status_code, 200, resposta.text)
                    resultado = resposta.json()
                    self.assertTrue(resultado["success"], resultado)
                    self.assertEqual(resultado["qtd"], 1)
                    with fitz.open(stream=self.storage[resultado["id_etiquetas"]], filetype="pdf") as doc:
                        largura, altura = medidas if orientacao != "retrato" else tuple(reversed(medidas))
                        self.assertAlmostEqual(doc[0].rect.width, largura * mm, places=3)
                        self.assertAlmostEqual(doc[0].rect.height, altura * mm, places=3)

    def test_endpoints_rejeitam_orientacao_invalida_sem_gerar_pdf(self):
        casos = (
            ("impressao-avulsa", {"itens": [{"sku": "000123"}]}),
            ("impressao-editor", {"etiquetas": ["<div>000123</div>"]}),
            ("qrcode-link", {"link": "https://example.com/000123"}),
        )
        for endpoint, payload in casos:
            with self.subTest(endpoint=endpoint):
                quantidade_antes = len(self.storage)
                resposta = self.client.post(f"/api/etiquetas/{endpoint}", json={**payload, "orientacao": "diagonal"})
                self.assertEqual(resposta.status_code, 422, resposta.text)
                self.assertEqual(len(self.storage), quantidade_antes)


if __name__ == "__main__":
    unittest.main()
