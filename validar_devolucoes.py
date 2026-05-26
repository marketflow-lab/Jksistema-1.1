#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Script para validar as alterações na sincronização de devoluções
"""
import os
import sqlite3

def verificar_status():
    """Verifica o status atual da sincronização"""
    print("=" * 80)
    print("VALIDACAO DE DEVOLUCOES - STATUS")
    print("=" * 80)
    
    client_id = "000002"
    db_path = os.path.join(os.path.dirname(__file__), "info", client_id, "vendas_historico.db")
    
    if not os.path.exists(db_path):
        print(f"[ERRO] Banco nao encontrado: {db_path}")
        return
    
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    
    # Contar notas e itens
    cur.execute("SELECT COUNT(*) as cnt FROM notas_entrada WHERE devolucao = 1")
    notas_dev = cur.fetchone()[0]
    
    cur.execute("SELECT COUNT(*) as cnt FROM notas_entrada_itens WHERE devolucao = 1")
    itens_dev = cur.fetchone()[0]
    
    print(f"\nNotas de entrada com devolucao: {notas_dev}")
    print(f"Itens de devolucao encontrados: {itens_dev}")
    
    if itens_dev == 0:
        print("\n[ATENCAO] Ainda nao ha itens salvos!")
        print("Motivos possiveis:")
        print("  1. A API do Bling nao retorna itens na listagem")
        print("  2. Os itens sao retornados em estrutura diferente")
        print("  3. E necessario fazer requisicoes individuais por NF")
        print("\nSolucao implementada:")
        print("  - Funcao _bling_obter_detalhes_nf() adicionada")
        print("  - Requisicoes individuais serao feitas apenas para devoluções")
        print("  - Delay de 1s a cada 5 requisicoes (protecao rate limit)")
        return
    
    # Mostrar SKUs encontrados
    print(f"\nSKUs encontrados nas devolucoes:")
    cur.execute("""
        SELECT DISTINCT sku, COUNT(*) as qtd 
        FROM notas_entrada_itens 
        WHERE devolucao = 1 AND sku IS NOT NULL AND sku != ''
        GROUP BY sku
        LIMIT 10
    """)
    rows = cur.fetchall()
    for row in rows:
        print(f"  - {row[0]}: {row[1]} itens")
    
    print("\n[SUCESSO] Devoluções foram sincronizadas com sucesso!")
    conn.close()

if __name__ == "__main__":
    verificar_status()
