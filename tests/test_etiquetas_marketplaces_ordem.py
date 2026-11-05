from backend.services.etiquetas_marketplaces_common import ordenar_etiquetas


def test_ordem_mantem_skus_e_pedidos_em_blocos():
    # Os dois volumes de B contam como um pedido. Sufixos distintos não
    # alteram o desempate pelo prefixo nem são combinados no mesmo grupo.
    etiquetas = [
        ("A1", "10-Z", 1),
        ("D1", "8-B", 3),
        ("B1", "20-B", 5),
        ("C1", "2-A", 3),
        ("A2", "10-Z", 5),
        ("E1", "8-A", 3),
        ("B1", "20-B", 5),
        ("F1", "N/D", 1),
        ("M1", "50-A", 2),
        ("G1", "10-Y", 5),
        ("M1", "60-A", 1),
        ("N1", "N/D", 1),
    ]

    resultado = ordenar_etiquetas(
        etiquetas, pedido=lambda item: item[0], sku=lambda item: item[1],
        quantidade=lambda item: item[2],
    )

    assert resultado == [etiquetas[i] for i in (4, 0, 9, 2, 6, 3, 1, 5, 7, 8, 10, 11)]
