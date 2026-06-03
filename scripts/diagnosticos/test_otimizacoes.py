#!/usr/bin/env python
import backend_api as b
import os
import time

base = r'C:\Users\Trend\Desktop\2026MUlt\Jk'
files = ['Anuncios-2026_04_23-12_54.xlsx', 'mercadoturbo_anuncios_13_54 23_04_2026.xlsx', 
         'atributos-produtos.csv', 'Impulsione_suas_vendas-2026_04_23-12_53.xlsx', 'Promo_Abril-2026_04_23-12_52.xlsx']
dfs = []

for fn in files:
    p = os.path.join(base, fn)
    if not os.path.exists(p):
        print(f'Arquivo não encontrado: {fn}')
        continue
    with open(p, 'rb') as f:
        content = f.read()
    fn_low = fn.lower()
    fn_norm = b.normalizar_texto(fn_low)
    tipo_nome = b._detectar_tipo_por_nome_informado(fn_norm)
    aba = None
    if tipo_nome == 'anuncios': 
        aba = 'Anúncios'
    elif tipo_nome == 'promo_ml': 
        aba = 'Promoções'
    elif 'anuncios' in fn_norm: 
        aba = 'Anúncios'
    elif 'promo' in fn_norm: 
        aba = 'Promoções'
    
    df = b.ler_e_tratar_arquivo(content, fn_low, aba)
    tdf = b._detectar_tipo_dataframe(df, fn_norm)
    sub = b._detectar_subtipo_promo(df) if tdf == 'promo' else ''
    tipo = sub if sub else tdf
    if tipo in ('desconhecido', 'promo') and tipo_nome: 
        tipo = tipo_nome
    df.attrs['tipo_detectado'] = tipo
    print(f'✓ {fn}: {tipo} ({len(df)} linhas)')
    dfs.append(df)

print(f'\n📊 Arquivos carregados: {len(dfs)}')

# Benchmark
print('\n⏱️  Iniciando processamento...')
t1 = time.time()
out = b.montar_analise_promo_local(dfs)
t2 = time.time()

tempo_ms = (t2 - t1) * 1000
print(f'✓ Tempo de execução: {tempo_ms:.2f}ms')
print(f'✓ Registros processados: {len(out)}')

missing = [r for r in out if str(r.get('%', '')).strip().lower() in ('', 'nan', 'none', '-')]
preenchimento = (len(out) - len(missing)) / len(out) * 100 if out else 0
print(f'✓ Coluna % preenchida em: {len(out) - len(missing)}/{len(out)} ({preenchimento:.1f}%)')

print('\n📋 Primeiros 5 registros:')
for i, r in enumerate(out[:5], 1):
    print(f'  {i}. MLB: {r.get("MLB")}, Tipo: {r.get("Tipo")}, %: {r.get("%")}, SKU: {r.get("SKU")}')

print('\n✅ Teste concluído com sucesso!')
