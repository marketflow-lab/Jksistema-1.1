import unittest

from backend.schemas.etiquetas import EtiquetaAvulsaItem
from backend.services.etiquetas_pdf_avulso import (
    gerar_pdf_editor_livre,
    gerar_pdf_impressao_avulsa,
)


class EtiquetasPdfAvulsoTest(unittest.TestCase):
    def test_gerar_pdf_impressao_avulsa_com_sequencia(self):
        item = EtiquetaAvulsaItem(
            sequencia="001",
            sku="SKU-TESTE",
            quantidade=2,
            caixa="3",
            torre="1",
            paletes="2",
            corredor="4",
            conta="CONTA TESTE",
        )

        for formato in ("pequena", "grande"):
            with self.subTest(formato=formato):
                pdf_bytes, quantidade = gerar_pdf_impressao_avulsa([item], formato)

                self.assertEqual(quantidade, 1)
                self.assertTrue(pdf_bytes.startswith(b"%PDF"))
                self.assertGreater(len(pdf_bytes), 500)

    def test_gerar_pdf_editor_livre_com_texto(self):
        etiqueta = (
            '<div style="font-size: 36px; text-align: center">'
            "<strong><u>SKU: SKU-TESTE</u></strong>"
            "</div>"
        )

        for tamanho in ("pequena", "grande"):
            with self.subTest(tamanho=tamanho):
                pdf_bytes, quantidade = gerar_pdf_editor_livre([etiqueta], tamanho)

                self.assertEqual(quantidade, 1)
                self.assertTrue(pdf_bytes.startswith(b"%PDF"))
                self.assertGreater(len(pdf_bytes), 500)


if __name__ == "__main__":
    unittest.main()
