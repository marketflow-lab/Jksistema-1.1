import logging
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from backend.services import promocoes_core_custos as custos


def _parse_float(value):
    if value is None or value == "":
        return None
    return float(str(value).replace(",", "."))


class PromocoesCustosCacheTests(unittest.TestCase):
    def setUp(self):
        custos.invalidar_cache_custos_impostos()
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.cadastro = self.base / "cadastro_produtos.csv"
        self.lojas = self.base / "cadastro_custos_lojas.csv"
        self.cadastro.write_text("sku,custo,imposto\nABC,10,12\n", encoding="utf-8")
        self.lojas.write_text("sku,loja,custo,imposto\nABC,Loja A,8,9\n", encoding="utf-8")
        self.dataframe = pd.DataFrame(
            [
                {"sku": "ABC", "custo": "10", "imposto": "12"},
                {"sku": "XYZ", "custo": "20,5", "imposto": "0.18"},
            ]
        )
        self.load_count = 0
        self.patches = [
            patch.object(
                custos,
                "_listar_arquivos_cadastro_custos",
                return_value=[str(self.cadastro)],
            ),
            patch.object(
                custos,
                "_cadastro_custos_lojas_path",
                return_value=str(self.lojas),
            ),
            patch.object(custos, "_iterar_dfs_cadastro_custos", side_effect=self._iterar),
            patch.object(custos, "_normalizar_sku_mes", side_effect=lambda value: str(value).strip(), create=True),
            patch.object(custos, "_sku_lookup_variantes", side_effect=lambda value: [str(value).strip().upper()], create=True),
            patch.object(custos, "_parse_float_flex", side_effect=_parse_float, create=True),
            patch.object(custos, "logger", logging.getLogger("test.promocoes.custos"), create=True),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        custos.invalidar_cache_custos_impostos()
        self.tmp.cleanup()

    def _iterar(self, _client_id, incluir_custos_lojas=True):
        self.load_count += 1
        time.sleep(0.04)
        yield self.dataframe.copy(), str(self.cadastro)

    def test_second_lookup_is_cached_and_returns_independent_maps(self):
        started = time.perf_counter()
        first_custos, first_impostos = custos._carregar_custos_impostos_base("cliente", False)
        first_elapsed = time.perf_counter() - started

        first_custos["ABC"] = 999
        started = time.perf_counter()
        second_custos, second_impostos = custos._carregar_custos_impostos_base("cliente", False)
        second_elapsed = time.perf_counter() - started

        self.assertEqual(self.load_count, 1)
        self.assertEqual(second_custos["ABC"], 10.0)
        self.assertEqual(second_impostos["ABC"], 0.12)
        self.assertEqual(second_impostos["XYZ"], 0.18)
        self.assertLess(second_elapsed, first_elapsed * 0.2)

    def test_file_fingerprint_invalidates_cache(self):
        custos._carregar_custos_impostos_base("cliente", False)
        self.cadastro.write_text("sku,custo,imposto\nABC,11,12\n", encoding="utf-8")
        custos._carregar_custos_impostos_base("cliente", False)
        self.assertEqual(self.load_count, 2)

    def test_store_override_is_cached_and_not_mutable_by_caller(self):
        mapa_lojas = {"ABC": {"loja-a": {"custo": "8", "imposto": "9"}}}
        with (
            patch.object(custos, "_cadastro_norm_loja_custo", return_value="loja-a"),
            patch.object(custos, "_cadastro_mapa_custos_lojas", return_value=mapa_lojas) as read_store,
        ):
            first_custos, first_impostos = custos._carregar_custos_impostos_cadastro_por_sku_loja(
                "cliente",
                "Loja A",
            )
            first_custos["ABC"] = 777
            second_custos, second_impostos = custos._carregar_custos_impostos_cadastro_por_sku_loja(
                "cliente",
                "Loja A",
            )

        self.assertEqual(first_impostos["ABC"], 0.09)
        self.assertEqual(second_custos["ABC"], 8.0)
        self.assertEqual(second_impostos["ABC"], 0.09)
        self.assertEqual(read_store.call_count, 1)


if __name__ == "__main__":
    unittest.main()
