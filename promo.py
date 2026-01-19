import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
import numpy as np
import unicodedata
import os
import glob
import json
import re
import tkinter as tk
from tkinter import filedialog
from io import BytesIO
import openpyxl
from openpyxl.cell.cell import MergedCell
import gspread
from google.oauth2.service_account import Credentials

# ==============================================================================
# --- CONFIGURAÇÕES E CAMINHOS ---
# ==============================================================================
PASTA_INFO = "info"
CREDENTIALS_FILE = os.path.join(PASTA_INFO, 'credentials.json')
CONFIG_FILE = os.path.join(PASTA_INFO, 'config_sheet.json')

# --- CONSTANTES E TAXAS ---
DB_TAXAS = {
    'molduras de estereos': {'Clássico': 0.10, 'Premium': 0.17},
    'audio para veiculos':  {'Clássico': 0.10, 'Premium': 0.17},
    'scanners':             {'Clássico': 0.12, 'Premium': 0.18},
}
TAXA_PADRAO = {'Clássico': 0.12, 'Premium': 0.17}

COLUNAS_DO_MODELO_ANUNCIOS = [
    'Agrupador de variações', 'Código do anúncio', 'Número do produto', 'Número da variação', 
    'SKU', 'Título', 'Variações', 'Preço', 'Moeda', 'Seu preço competitivo em outros canais', 
    'Preço de atacado 1 [ML]', 'Unnamed: 11', 'Preço de atacado 2 [ML]', 'Unnamed: 13', 
    'Preço de atacado 3 [ML]', 'Unnamed: 15', 'Preço de atacado 4 [ML]', 'Unnamed: 17', 
    'Preço de atacado 5 [ML]', 'Unnamed: 19', 'Forma de entrega', 'Tipo de anúncio', 
    'Tarifa de venda', 'Categoria'
]

COLUNAS_DO_MODELO_PROMO = [
    'TITLE', 'ITEM_ID', 'SKU', 'ORIGINAL_PRICE', 'DISCOUNT_PERCENTAGE', 
    'FINAL_PRICE', 'SUGGESTION', 'RECEIVES', 'LOYALTY_DISCOUNT_PERCENTAGE', 
    'LOYALTY_PRICE', 'LOYALTY_RECEIVES', 'STATUS', 'ACTION', 'ERRORS'
]

# ==============================================================================
# --- FUNÇÕES DE CONEXÃO (COM CACHE PARA VELOCIDADE) ---
# ==============================================================================

@st.cache_resource(show_spinner=False)
def autenticar_google_sheets():
    """Autenticação cacheada para evitar lentidão."""
    scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    if not os.path.exists(CREDENTIALS_FILE): return None
    try:
        creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=scopes)
        return gspread.authorize(creds)
    except Exception: return None

def carregar_id_planilha_sistema():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f:
                data = json.load(f)
                return data.get('spreadsheet_id', '').strip()
        except: return None
    return None

# ==============================================================================
# --- FUNÇÕES AUXILIARES DE PROCESSAMENTO ---
# ==============================================================================

def normalizar_texto(texto):
    if not isinstance(texto, str): return str(texto)
    return unicodedata.normalize('NFKD', texto).encode('ASCII', 'ignore').decode('ASCII').lower().strip()

def renomear_colunas_duplicadas(df):
    if df.empty: return df
    cols = pd.Series(df.columns)
    for dup in cols[cols.duplicated()].unique(): 
        cols[cols[cols == dup].index.values.tolist()] = [dup + '.' + str(i) if i != 0 else dup for i in range(sum(cols == dup))] 
    df.columns = cols
    return df

def obter_taxa_por_categoria(tipo_anuncio, nome_categoria):
    if not isinstance(tipo_anuncio, str): return 0.0
    tipo_norm = normalizar_texto(tipo_anuncio)
    cat_norm = normalizar_texto(nome_categoria)
    chave_tipo = None
    if 'classico' in tipo_norm: chave_tipo = 'Clássico'
    elif 'premium' in tipo_norm: chave_tipo = 'Premium'
    if not chave_tipo: return 0.0
    taxas_selecionadas = TAXA_PADRAO
    for termo, taxas in DB_TAXAS.items():
        if termo in cat_norm:
            taxas_selecionadas = taxas
            break 
    return taxas_selecionadas[chave_tipo]

def formatar_moeda_br(valor):
    try:
        s = str(valor).replace("R$", "").strip()
        if not s: return ""
        if '.' in s and ',' not in s: val = float(s)
        elif ',' in s and '.' not in s: val = float(s.replace(',', '.'))
        else: val = float(s.replace(',', ''))
        s_fmt = f"{val:,.2f}" 
        s_fmt = "R$ " + s_fmt.replace(',', 'X').replace('.', ',').replace('X', '.') 
        return s_fmt
    except:
        return str(valor).replace('.', ',')

# --- NORMALIZAÇÃO DE DATAFRAMES ---

def normalizar_df_anuncios(df):
    if df is None or df.empty: return df
    termos_intrusos = ["impostos incluídos", "impostos incluidos", "tax_inclusion_type"]
    colunas_para_remover = []
    for col in df.columns:
        if any(termo in str(col).lower() for termo in termos_intrusos):
            colunas_para_remover.append(col)
    if colunas_para_remover:
        df = df.drop(columns=colunas_para_remover)
    if len(df.columns) == len(COLUNAS_DO_MODELO_ANUNCIOS):
        df.columns = COLUNAS_DO_MODELO_ANUNCIOS
    return df

def normalizar_df_promo(df):
    if df is None or df.empty: return df
    for col in COLUNAS_DO_MODELO_PROMO:
        if col not in df.columns:
            df[col] = "" 
    df = df[COLUNAS_DO_MODELO_PROMO]
    return df

def ler_arquivo_bruto(caminho, aba_alvo=None):
    """
    Leitura robusta baseada no App Final.py.
    Procura cabeçalhos dinamicamente usando palavras-chave.
    """
    nome_arquivo = os.path.basename(caminho).lower()
    df = pd.DataFrame()
    try:
        if nome_arquivo.endswith(('.xlsx', '.xls')):
            xls = pd.ExcelFile(caminho)
            abas = xls.sheet_names
            nome_aba = None
            if aba_alvo:
                alvo = normalizar_texto(aba_alvo)
                for a in abas:
                    if normalizar_texto(a) == alvo: nome_aba = a; break
                if not nome_aba:
                    for a in abas:
                        if alvo in normalizar_texto(a): nome_aba = a; break
            aba_ler = nome_aba if nome_aba else 0
            
            df_preview = pd.read_excel(xls, sheet_name=aba_ler, header=None, nrows=30, dtype=str)
            header_row = 0
            for i, row in df_preview.iterrows():
                s = row.astype(str).str.lower().values
                has_sku = any("sku" in x for x in s)
                has_keywords = (
                    any("tarifa" in x for x in s) or 
                    any("tipo" in x for x in s) or 
                    any("preço" in x for x in s) or 
                    any("price" in x for x in s) or 
                    any("status" in x for x in s) or 
                    any("original_price" in x for x in s)
                )
                if has_sku and has_keywords:
                    header_row = i
                    break
            df = pd.read_excel(xls, sheet_name=aba_ler, header=header_row, dtype=str)

        if df.empty:
            try:
                df_prev = pd.read_csv(caminho, sep=',', header=None, nrows=30, dtype=str, on_bad_lines='skip')
                header_row = 0
                for i, row in df_prev.iterrows():
                    s = row.astype(str).str.lower().values
                    has_sku = any("sku" in x for x in s)
                    has_keywords = (
                        any("tarifa" in x for x in s) or 
                        any("tipo" in x for x in s) or 
                        any("preço" in x for x in s) or 
                        any("price" in x for x in s) or
                        any("status" in x for x in s) or
                        any("original_price" in x for x in s)
                    )
                    if has_sku and has_keywords: 
                        header_row = i; break
                df = pd.read_csv(caminho, sep=',', header=header_row, dtype=str, on_bad_lines='skip', low_memory=False)
            except:
                try: df = pd.read_csv(caminho, sep=';', header=0, dtype=str, encoding='latin1', on_bad_lines='skip')
                except: df = pd.read_csv(caminho, sep='\t', header=0, dtype=str, on_bad_lines='skip')

        if not df.empty:
            df.columns = df.columns.str.strip()
            
            if "anuncios" in nome_arquivo or "anúncios" in nome_arquivo:
                is_mercadoturbo = "mercadoturbo" in nome_arquivo
                if not is_mercadoturbo:
                    df = normalizar_df_anuncios(df)
                df = renomear_colunas_duplicadas(df)
                mapa = {'FEE_PER_SALE': 'Tarifa de venda','LISTING_TYPE': 'Tipo de anúncio','CATEGORY': 'Categoria', 'CATEGORY_ID': 'Categoria'}
                df.rename(columns=mapa, inplace=True)
                
                # Preenchimento e cálculo de tarifa
                if 'Tipo de anúncio' in df.columns:
                    df['Tipo de anúncio'] = df['Tipo de anúncio'].replace(r'^\s*$', np.nan, regex=True)
                    df['Tipo de anúncio'] = df['Tipo de anúncio'].ffill().fillna('')
                if 'Tarifa de venda' not in df.columns: df['Tarifa de venda'] = ""
                
                if 'Tipo de anúncio' in df.columns and 'Categoria' in df.columns:
                    def calcula_tarifa_real(row):
                        tipo = str(row.get('Tipo de anúncio', ''))
                        cat = str(row.get('Categoria', ''))
                        taxa = obter_taxa_por_categoria(tipo, cat)
                        if taxa == 0: return "-"
                        if taxa.is_integer(): return f"{int(taxa*100)}%"
                        else: return f"{taxa*100:.1f}%".replace('.', ',')
                    df['Tarifa de venda'] = df.apply(calcula_tarifa_real, axis=1)

            elif "promo" in nome_arquivo or "promoções" in nome_arquivo:
                 df = normalizar_df_promo(df)

    except Exception as e:
        st.warning(f"Erro ao ler {nome_arquivo}: {e}")
        return pd.DataFrame()
    return df

def preparar_para_sheets(df):
    if df.empty: return df
    df = df.dropna(how='all')
    df = df.replace([np.nan, np.inf, -np.inf, None], "")
    df = df.fillna("")
    
    def formatar_valor(val):
        s = str(val).strip()
        # Verifica se contém apenas números e pontos para converter para formato BR
        if re.match(r'^[\d\.]+$', s) and '.' in s:
            parts = s.rsplit('.', 1)
            if len(parts) == 2:
                return f"{parts[0]},{parts[1]}"
        return s

    for col in df.columns:
        df[col] = df[col].astype(str).apply(formatar_valor)
        
    return df

def atualizar_aba(sh, nome_aba, df):
    if df is None or df.empty: return
    try:
        try: ws = sh.worksheet(nome_aba)
        except: ws = sh.add_worksheet(title=nome_aba, rows="1000", cols="20")
        ws.batch_clear(['A:ZZ'])
        dados = [df.columns.values.tolist()] + df.astype(str).values.tolist()
        ws.update('A1', dados, value_input_option='USER_ENTERED')
    except Exception as e:
        st.error(f"❌ Erro ao atualizar aba {nome_aba}: {e}")

def carregar_dados_analise(sh):
    try:
        ws = sh.worksheet("Análise")
        dados_brutos = ws.get_all_values()
        if not dados_brutos: return None
        colunas_necessarias = 27
        dados_normalizados = []
        for row in dados_brutos:
            while len(row) < colunas_necessarias: row.append("")
            dados_normalizados.append(row)
        df = pd.DataFrame(dados_normalizados)
        col_indices = [12, 2, 3, 22, 19, 23, 24, 25] 
        df_analise = df.iloc[3:, col_indices].copy()
        df_analise.columns = ["MLB", "SKU", "Título", "Situação", "Desconto", "M 21 Fixa", "M ML", "Participar ou não"]
        df_analise = df_analise.astype(str)
        condicao_mlb = df_analise['MLB'].str.strip().str.upper().str.startswith('MLB')
        df_final = df_analise[condicao_mlb].copy()
        
        def padronizar_decisao(valor):
            if not valor: return "" 
            v_limpo = str(valor).strip().lower()
            if v_limpo == "participar": return "✅ Participar"
            elif v_limpo in ["não participar", "nao participar"]: return "🔻 NÃO PARTICIPAR"
            return str(valor).strip()
            
        df_final['Participar ou não'] = df_final['Participar ou não'].apply(padronizar_decisao)
        df_final.reset_index(drop=True, inplace=True)
        return df_final
    except Exception as e:
        st.error(f"Erro ao ler aba Análise: {e}")
        return None

def limpar_dados_para_exportacao(df_original):
    df_limpo = df_original.copy()
    def limpar_valor(val):
        if "✅ Participar" in str(val): return "Participar"
        if "🔻 NÃO PARTICIPAR" in str(val): return "Não participar"
        return val
    df_limpo["Participar ou não"] = df_limpo["Participar ou não"].apply(limpar_valor)
    return df_limpo

def gerar_excel_atualizado(caminho_arquivo, df_decisoes):
    try:
        df_clean = limpar_dados_para_exportacao(df_decisoes)
        wb = openpyxl.load_workbook(caminho_arquivo)
        sheet_name = None
        for name in wb.sheetnames:
            if "promoções" in name.lower() or "promo" in name.lower():
                sheet_name = name
                break
        if not sheet_name:
            st.error("Aba 'Promoções' não encontrada no arquivo original.")
            return None
        ws = wb[sheet_name]
        decisoes = df_clean["Participar ou não"].tolist()
        start_row = 6
        col_idx = 9 
        
        # Tenta achar a coluna ACTION
        try:
             header = [c.value for c in ws[1]]
             col_idx = header.index("ACTION") + 1
        except:
             col_idx = 13
             
        for i, decisao in enumerate(decisoes):
            current_row = start_row + i
            ws.cell(row=current_row, column=col_idx).value = decisao
        output = BytesIO()
        wb.save(output)
        output.seek(0)
        return output
    except Exception as e:
        st.error(f"Erro ao gerar Excel: {e}")
        return None

# ==============================================================================
# --- INTERFACE VISUAL (FRONTEND) ---
# ==============================================================================

def render_page(navegar_para_callback):
    """
    Função principal chamada pelo app.py para renderizar a página.
    """
    if st.button("⬅️ Voltar ao Submenu", key="btn_voltar_promo"):
        navegar_para_callback('submenu_promo')

    # --- Bloco para acionar o download ---
    if st.session_state.get('download_ready', False):
        download_label = "Clique aqui se o download não iniciar"
        st.download_button(
            label=download_label,
            data=st.session_state['download_buffer'],
            file_name=st.session_state['download_filename'],
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        # JS para auto-clique do botão de download
        components.html(f"""
            <script>
                var buttons = window.parent.document.querySelectorAll('button');
                for (var i = 0; i < buttons.length; i++) {{
                    if (buttons[i].innerText.includes("{download_label}")) {{
                        buttons[i].click();
                        break;
                    }}
                }}
            </script>
        """, height=0)
        # Limpa o estado após o download
        st.session_state['download_ready'] = False
        del st.session_state['download_buffer']
        del st.session_state['download_filename']

    st.markdown("## Análise Promoção Mercado Livre")
        
    if 'dados_analise' not in st.session_state: st.session_state['dados_analise'] = None
    if 'pasta_selecionada' not in st.session_state: st.session_state['pasta_selecionada'] = None
    if 'caminho_ml_arquivo' not in st.session_state: st.session_state['caminho_ml_arquivo'] = None
    
    st.markdown("---")
    
    # --- SELEÇÃO DE PASTA ---
    col1, col2, col3 = st.columns([2, 3, 1])
    with col1:
        if st.button("📂 Selecionar Pasta", use_container_width=True):
            try:
                root = tk.Tk(); root.withdraw(); root.wm_attributes('-topmost', 1) 
                pasta = filedialog.askdirectory(master=root); root.destroy()
                if pasta: st.session_state['pasta_selecionada'] = pasta; st.rerun() 
            except: st.warning("Use o campo manual ao lado.")
    with col2:
        val = st.session_state.get('pasta_selecionada', "")
        caminho_input = st.text_input("Caminho da Pasta:", value=val if val else "", label_visibility="collapsed", placeholder="Cole o caminho aqui...")
        if caminho_input: st.session_state['pasta_selecionada'] = caminho_input.strip('"')
    with col3:
        processar = st.button("▶️ PROCESSAR", type="primary", use_container_width=True, disabled=not st.session_state.get('pasta_selecionada'))

    if st.session_state.get('pasta_selecionada'):
        if os.path.exists(st.session_state['pasta_selecionada']): 
            st.success(f"Pasta: {st.session_state['pasta_selecionada']}")
        else: 
            st.error("Caminho não encontrado.")

    # --- PROCESSAMENTO ---
    if processar:
        pasta = st.session_state['pasta_selecionada']
        st.session_state['dados_analise'] = None
        st.session_state['caminho_ml_arquivo'] = None
        
        SHEET_ID = carregar_id_planilha_sistema()
        if not SHEET_ID:
            st.error("ID da Planilha não configurado.")
            st.stop()

        with st.status("Executando Análise...", expanded=True) as status:
            try:
                client = autenticar_google_sheets()
                if not client: st.error("Erro Auth Google."); st.stop()
                try: sh = client.open_by_key(SHEET_ID)
                except: st.error("Erro Conexão Google (ID Inválido)."); st.stop()
                
                status.write("🔍 Lendo arquivos...")
                arquivos = glob.glob(os.path.join(pasta, "*"))
                usados = set()
                df_imp = pd.DataFrame(); df_promo = pd.DataFrame(); df_custo = pd.DataFrame(); df_frete = pd.DataFrame(); df_ml = pd.DataFrame()
                
                # --- LEITURA E CATEGORIZAÇÃO (Lógica do App Final) ---
                for arq in arquivos:
                    nome = os.path.basename(arq).lower()
                    if nome.startswith('~$') or nome.endswith(('.py','.json','.bat','.png')): continue
                    
                    if nome.startswith("anuncios"): 
                        df_imp = ler_arquivo_bruto(arq, aba_alvo="Anúncios"); usados.add(arq)
                    elif nome.startswith("promo"): 
                        df_promo = ler_arquivo_bruto(arq, aba_alvo="Promoções"); usados.add(arq)
                    elif nome.startswith("atributos"):
                        temp = ler_arquivo_bruto(arq)
                        if temp.shape[1] == 1:
                            try: temp = pd.read_csv(arq, sep=';', dtype=str, on_bad_lines='skip')
                            except: pass
                        
                        # Normaliza colunas para maiúsculo
                        temp.columns = [str(c).upper().strip() for c in temp.columns]
                        
                        col_sku = None
                        for c in temp.columns: 
                            if 'SKU' in c: col_sku = c; break
                        
                        if not col_sku and not temp.empty: col_sku = temp.columns[0]
                        
                        if col_sku:
                            cols_order = [col_sku] + [c for c in temp.columns if c != col_sku]
                            df_custo = temp[cols_order]; df_custo.rename(columns={col_sku: 'SKU'}, inplace=True)
                        else: 
                            df_custo = temp
                        usados.add(arq)
                    elif nome.startswith("mercadoturbo"):
                        temp = ler_arquivo_bruto(arq); temp = renomear_colunas_duplicadas(temp)
                        if df_frete.empty: df_frete = temp
                        else: df_frete = pd.concat([df_frete, temp], ignore_index=True)
                        usados.add(arq)
                
                # Procura o arquivo ML (Sobrou)
                for arq in arquivos:
                    if arq not in usados:
                        nome = os.path.basename(arq).lower()
                        if nome.endswith(('.xls','.xlsx','.csv')):
                            df_ml = ler_arquivo_bruto(arq, aba_alvo="Promoções")
                            st.session_state['caminho_ml_arquivo'] = arq; break
                
                status.write("⚙️ Formatando dados...")
                
                # --- TRATAMENTOS ESPECÍFICOS (Lógica do App Final) ---
                if not df_frete.empty and df_frete.shape[1] >= 6:
                    df_frete.iloc[:, 1] = df_frete.iloc[:, 1].apply(lambda x: str(x).replace('.', ','))
                    df_frete.iloc[:, 5] = df_frete.iloc[:, 5].apply(formatar_moeda_br)
                
                if not df_custo.empty:
                    col_c = None
                    for c in df_custo.columns:
                        if "CUSTO" in str(c).upper(): col_c = c; break
                    if not col_c and df_custo.shape[1]>=2: col_c = df_custo.columns[1]
                    
                    if col_c:
                        # Remove custo vazio ou zero
                        df_custo = df_custo[df_custo[col_c].apply(lambda x: 0 if not str(x).strip() else 1) != 0]
                        df_custo[col_c] = df_custo[col_c].apply(formatar_moeda_br)

                if not df_imp.empty and df_imp.shape[1] >= 5:
                    df_imp.iloc[:, 4] = df_imp.iloc[:, 4].bfill()
                    # Correção de NCMs (Lógica Específica)
                    subs = {"1599": "230-1", "1935": "299-1", "1631": "239-1", "1585": "226-17"}
                    df_imp.iloc[:, 4] = df_imp.iloc[:, 4].apply(lambda x: subs.get(str(x).replace('.0',''), str(x).replace('.0','').zfill(3) if str(x).replace('.0','').isdigit() and int(str(x))<10 else str(x)))

                if not df_promo.empty: 
                     for i in [3,5]: 
                         if df_promo.shape[1]>i: df_promo.iloc[:, i] = df_promo.iloc[:, i].apply(formatar_moeda_br)
                
                if not df_ml.empty:
                    for i in [3,4,6]: 
                        if df_ml.shape[1]>i: df_ml.iloc[:, i] = df_ml.iloc[:, i].apply(formatar_moeda_br)

                status.write("📡 Upload Google...")
                if not df_imp.empty: atualizar_aba(sh, "Imports", preparar_para_sheets(df_imp))
                if not df_promo.empty: atualizar_aba(sh, "21% Promo", preparar_para_sheets(df_promo))
                if not df_custo.empty: atualizar_aba(sh, "Custo", preparar_para_sheets(df_custo))
                if not df_frete.empty: atualizar_aba(sh, "Frete", preparar_para_sheets(df_frete))
                if not df_ml.empty: atualizar_aba(sh, "ML Promo", preparar_para_sheets(df_ml))
                
                status.write("📥 Baixando Análise...")
                df_analise = carregar_dados_analise(sh)
                
                if df_analise is not None:
                    st.session_state['dados_analise'] = df_analise
                    status.update(label="Concluído!", state="complete", expanded=False)
                else:
                    status.update(label="Erro no retorno!", state="error")
            except Exception as e: 
                status.update(label="Erro Crítico!", state="error")
                st.error(f"Ocorreu um erro: {e}")

    st.markdown("---")
    
    # --- EXIBIÇÃO ---
    if st.session_state.get('dados_analise') is not None:
        df_exibicao = st.session_state['dados_analise']
        
        # As métricas e o botão de copiar são movidos para dentro do formulário
        # para que o usuário os veja junto com a tabela e o botão de download.
        
        with st.form(key='form_editor_analise'):
            c_metrics, c_copy = st.columns([3, 2])
            with c_metrics:
                col1, col2, col3 = st.columns(3)
                col1.metric("Anúncios", len(df_exibicao))
                col2.metric("Participar", len(df_exibicao[df_exibicao['Participar ou não'] == '✅ Participar']))
                col3.metric("Não Participar", len(df_exibicao[df_exibicao['Participar ou não'] == '🔻 NÃO PARTICIPAR']))
            with c_copy:
                st.markdown("**Ações**")
                df_clean = limpar_dados_para_exportacao(df_exibicao)
                json_data = json.dumps(df_clean["Participar ou não"].tolist())
                
                # Botões Copiar e Baixar (Topo)
                components.html(f"""
                    <div style="display: flex; flex-direction: column; gap: 5px;">
                        <button onclick="copy()" style="width:100%; padding:8px; background:#0d6efd; color:white; border:none; border-radius:5px; font-weight:bold; cursor:pointer;">📋 Copiar</button>
                        <button onclick="triggerDownload()" style="width:100%; padding:8px; background:#198754; color:white; border:none; border-radius:5px; font-weight:bold; cursor:pointer;">📥 Baixar Planilha com Alterações</button>
                    </div>
                    <script>
                        function copy() {{
                            const dados = {json_data};
                            navigator.clipboard.writeText(dados.join("\\n")).then(() => alert("Copiado!"));
                        }}
                        function triggerDownload() {{
                            var buttons = window.parent.document.querySelectorAll('button');
                            for (var i = 0; i < buttons.length; i++) {{
                                if (buttons[i].innerText.includes("Baixar Planilha com Alterações")) {{
                                    buttons[i].click();
                                    break;
                                }}
                            }}
                        }}
                    </script>
                """, height=100)

            st.markdown("<br>", unsafe_allow_html=True)
            dynamic_height = min((len(df_exibicao) + 1) * 35 + 10, 800)
            df_styled = df_exibicao.style.set_properties(subset=['M 21 Fixa'], **{'background-color': '#ADD8E6', 'color': 'black'}).set_properties(subset=['M ML'], **{'background-color': '#FFFACD', 'color': 'black'})
            
            df_editado = st.data_editor(
                df_styled, 
                key="editor_analise_tabela", 
                column_config={
                    "MLB": st.column_config.TextColumn("🆔 MLB", disabled=True), 
                    "SKU": st.column_config.TextColumn("📦 SKU", disabled=True), 
                    "Título": st.column_config.TextColumn("📝 Título", disabled=True, width="large"), 
                    "Situação": st.column_config.TextColumn("🚦 Situação", disabled=True), 
                    "Desconto": st.column_config.TextColumn("💸 Desconto", disabled=True), 
                    "M 21 Fixa": st.column_config.TextColumn("📉 M 21 Fixa", disabled=True), 
                    "M ML": st.column_config.TextColumn("📊 M ML", disabled=True), 
                    "Participar ou não": st.column_config.SelectboxColumn("🎯 Decisão", options=["✅ Participar", "🔻 NÃO PARTICIPAR"], required=False)
                }, 
                hide_index=True, 
                use_container_width=True, 
                height=dynamic_height
            )

            # Botão único para aplicar e baixar
            submitted = st.form_submit_button("📥 Baixar Planilha com Alterações", use_container_width=True)
            if submitted:
                st.session_state['dados_analise'] = df_editado
                
                if st.session_state.get('caminho_ml_arquivo'):
                    buffer_arquivo = gerar_excel_atualizado(st.session_state['caminho_ml_arquivo'], df_editado)
                    if buffer_arquivo:
                        st.session_state['download_buffer'] = buffer_arquivo
                        st.session_state['download_filename'] = os.path.basename(st.session_state['caminho_ml_arquivo'])
                        st.session_state['download_ready'] = True
                        st.rerun()

    else: 
        st.info("👈 Selecione a pasta acima e clique em PROCESSAR para iniciar.")