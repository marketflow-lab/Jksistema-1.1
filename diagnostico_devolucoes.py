#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Script de diagnóstico para verificar dados de devoluções no banco de dados
"""
import sqlite3
import os
import json
from datetime import datetime
import sys

# Configurar encoding do stdout para UTF-8
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

def obter_client_id():
    """Tenta obter o client_id do localStorage salvo"""
    info_path = os.path.join(os.path.dirname(__file__), "info")
    if os.path.exists(info_path):
        # Listar pastas de clientes
        clientes = [d for d in os.listdir(info_path) if os.path.isdir(os.path.join(info_path, d))]
        return clientes
    return []

def verificar_banco_cliente(client_id):
    """Verifica o banco de dados de um cliente específico"""
    db_path = os.path.join(os.path.dirname(__file__), "info", client_id, "vendas_historico.db")
    
    if not os.path.exists(db_path):
        print(f"[ERRO] Banco de dados nao encontrado: {db_path}")
        return
    
    print(f"\nVerificando banco de dados: {db_path}")
    print(f"Tamanho: {os.path.getsize(db_path)} bytes")
    
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    
    # Verificar tabelas
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tabelas = cur.fetchall()
    print(f"\nTabelas encontradas:")
    for t in tabelas:
        print(f"  - {t[0]}")
    
    # Contar registros em cada tabela
    print(f"\nContagem de registros:")
    for tabela in ['notas_entrada', 'notas_entrada_itens']:
        try:
            cur.execute(f"SELECT COUNT(*) as cnt FROM {tabela}")
            cnt = cur.fetchone()[0]
            print(f"  {tabela}: {cnt} registros")
        except:
            print(f"  {tabela}: [ERRO] Tabela nao existe")
    
    # Verificar devoluções
    print(f"\nAnalise de devolucoes:")
    try:
        cur.execute("SELECT COUNT(*) as cnt FROM notas_entrada WHERE devolucao = 1")
        cnt_notas = cur.fetchone()[0]
        print(f"  Notas de entrada com devolucao: {cnt_notas}")
        
        cur.execute("SELECT COUNT(*) as cnt FROM notas_entrada_itens WHERE devolucao = 1")
        cnt_itens = cur.fetchone()[0]
        print(f"  Itens de devolucoes: {cnt_itens}")
        
        # Verificar se há SKUs NULL
        cur.execute("SELECT COUNT(*) as cnt FROM notas_entrada_itens WHERE devolucao = 1 AND (sku IS NULL OR sku = '')")
        cnt_null = cur.fetchone()[0]
        if cnt_null > 0:
            print(f"  [AVISO] Itens com SKU NULL ou vazio: {cnt_null}")
    except Exception as e:
        print(f"  Erro ao contar devolucoes: {e}")
    
    # Mostrar últimas devoluções
    print(f"\nUltimas devolucoes registradas:")
    try:
        cur.execute("""
            SELECT numero_nota, data_emissao, sku, descricao, quantidade, valor_total, fornecedor 
            FROM notas_entrada_itens 
            WHERE devolucao = 1 AND sku IS NOT NULL AND sku != ''
            ORDER BY date(data_emissao) DESC 
            LIMIT 10
        """)
        rows = cur.fetchall()
        if rows:
            print(f"{'NF':<12} {'Data':<12} {'SKU':<15} {'Descricao':<30} {'Qtd':<5} {'Valor':<12}")
            print("-" * 100)
            for row in rows:
                desc = row[3][:28] if row[3] else ""
                print(f"{row[0]:<12} {row[1]:<12} {row[2]:<15} {desc:<30} {row[4]:<5} R$ {float(row[5]):.2f}")
        else:
            print("Nenhuma devolucao encontrada com SKU valido")
    except Exception as e:
        print(f"Erro ao listar devolucoes: {e}")
    
    # Verificar dados brutos (para debug)
    print(f"\nUltimos 5 itens (com SKU NULL tambem):")
    try:
        cur.execute("""
            SELECT numero_nota, sku, descricao, devolucao, data_emissao
            FROM notas_entrada_itens 
            ORDER BY rowid DESC 
            LIMIT 5
        """)
        rows = cur.fetchall()
        for row in rows:
            sku_display = row[1] if row[1] else "[NULL]"
            print(f"  NF: {row[0]}, SKU: {sku_display}, Dev: {row[3]}, Data: {row[4]}")
    except Exception as e:
        print(f"Erro ao listar dados brutos: {e}")
    
    conn.close()

def main():
    print("=" * 100)
    print("DIAGNOSTICO DE DEVOLUCOES")
    print("=" * 100)
    
    clientes = obter_client_id()
    
    if not clientes:
        print("[ERRO] Nenhum cliente encontrado em info/")
        return
    
    print(f"\nClientes encontrados: {clientes}")
    
    for client_id in clientes:
        verificar_banco_cliente(client_id)
    
    print("\n" + "=" * 100)
    print("[OK] Diagnostico completo")
    print("=" * 100)

if __name__ == "__main__":
    main()
