import streamlit as st
import fitz  # PyMuPDF
import barcode
from barcode.writer import ImageWriter
from io import BytesIO
import re
import os
import tempfile
import platform
import webbrowser
import time

# ==============================================================================
# --- LÓGICA DE PROCESSAMENTO (BACKEND - BASEADO NO APP V2.0) ---
# ==============================================================================

def extrair_chave_acesso(texto_pagina):
    """Extrai os 44 dígitos da chave de acesso da NF-e."""
    match = re.search(r'(?:\d[\s\.]?){44}', texto_pagina)
    if match:
        chave_raw = match.group(0)
        return re.sub(r'\D', '', chave_raw)
    return None

def gerar_imagem_barcode(numero_chave):
    """Gera um código de barras Code128 em memória."""
    if not numero_chave: return None
    try:
        code128 = barcode.get('code128', numero_chave, writer=ImageWriter())
        buffer = BytesIO()
        code128.write(buffer, options={"write_text": False, "quiet_zone": 1, "module_height": 4.0})
        return buffer.getvalue()
    except Exception: return None

def enviar_para_impressora_local(pdf_bytes, nome_arquivo="temp_print.pdf"):
    """
    Salva o arquivo temporariamente e abre com o visualizador padrão do SO.
    """
    try:
        temp_dir = tempfile.gettempdir()
        temp_path = os.path.join(temp_dir, nome_arquivo)
        
        with open(temp_path, "wb") as f:
            f.write(pdf_bytes)
            
        if platform.system() == "Windows":
            os.startfile(temp_path) 
        else:
            webbrowser.open(temp_path)
            
        st.toast(f"📄 Arquivo aberto! Pressione Ctrl+P na janela que abriu.", icon="✅")
        time.sleep(1)
        return True
    except Exception as e:
        st.error(f"Erro ao abrir arquivo para impressão: {e}")
        return False

def processar_arquivos_pdf(uploaded_file):
    """
    Processa o PDF extraindo etiquetas e listas de controle.
    Mantém dimensões (283x425) e fontes do App V2.0.
    """
    file_bytes = uploaded_file.read()
    
    doc_origem = fitz.open(stream=file_bytes, filetype="pdf")
    
    # Docs de saída
    doc_etiquetas = fitz.open()
    doc_lista = fitz.open()
    
    mapa_ids_dados = {}
    
    # --- A. Mapeamento Geral de SKUs ---
    texto_completo = ""
    for page in doc_origem:
        texto_completo += page.get_text() + "\n"
    
    texto_linear = texto_completo.replace('\n', ' ').replace('  ', ' ')
    last_pos = 0
    
    # Regex para capturar dados
    for match in re.finditer(r'Quantidade:\s*(\d+)', texto_linear):
        qtd = match.group(1)
        end_pos = match.end()
        segmento = texto_linear[last_pos:end_pos]
        
        skus_found = re.findall(r'SKU:\s*(.*?)(?=\s|Quant|Venda|Pack|$)', segmento)
        vendas_found = re.findall(r'Venda:\s*(\d+)', segmento)
        packs_found = re.findall(r'Pack ID:\s*(\d+)', segmento)
        
        sku = skus_found[-1].strip() if skus_found else None
        
        if sku:
            if vendas_found: mapa_ids_dados[vendas_found[-1]] = (sku, qtd)
            if packs_found: mapa_ids_dados[packs_found[-1]] = (sku, qtd)
        last_pos = end_pos

    # --- B. Separação e Geração ---
    count_etiquetas = 0
    total_paginas = len(doc_origem)
    
    # Dimensões exatas solicitadas
    w_page = 283
    h_page = 425
    
    index_inicio_lista = -1
    i = 0
    
    while i < total_paginas:
        page_etiqueta = doc_origem[i]
        
        # Tenta pegar a próxima página (Declaração), se existir
        page_declaracao = None
        if i + 1 < total_paginas:
            candidata_declaracao = doc_origem[i+1]
            texto_declaracao = candidata_declaracao.get_text()
            # Verifica se é lista de controle
            if len(re.findall(r'Pack ID', texto_declaracao)) > 1:
                index_inicio_lista = i + 1
                page_declaracao = None 
            else:
                page_declaracao = candidata_declaracao

        # Se a página atual já é a lista
        if len(re.findall(r'Pack ID', page_etiqueta.get_text())) > 2:
             index_inicio_lista = i
             break

        # CRIAÇÃO DA NOVA PÁGINA
        nova_pagina = doc_etiquetas.new_page(width=w_page, height=h_page)
        
        texto_limpo = page_etiqueta.get_text().replace(' ', '').replace('\n', '')
        dados_sku = None
        for k, v in mapa_ids_dados.items():
            if k in texto_limpo:
                dados_sku = v
                break
        
        # 1. Etiqueta (Base) - FULL WIDTH
        rect_destino = fitz.Rect(0, 0, 283, 390) 
        nova_pagina.show_pdf_page(rect_destino, doc_origem, i)
        
        # 2. SKU Overlay (Fonte tamanho 8, rotacionada)
        if dados_sku:
            sku_text = f"SKU {dados_sku[0]}  x  {dados_sku[1]}"
            color_sku = (0, 0, 0)
        else:
            sku_text = "SKU N/D"
            color_sku = (1, 0, 0)

        nova_pagina.insert_text(fitz.Point(281, 300), sku_text, fontsize=8, color=color_sku, rotate=90)
        
        # 3. Rodapé e Chave de Acesso
        nova_pagina.draw_line(fitz.Point(0, 390), fitz.Point(283, 390), color=(0,0,0), width=1)
        nova_pagina.insert_text(fitz.Point(110, 396), "CHAVE DE ACESSO", fontsize=5, color=(0, 0, 0))

        if page_declaracao:
            chave_numeros = extrair_chave_acesso(page_declaracao.get_text())
            if chave_numeros:
                nova_pagina.insert_text(fitz.Point(60, 402), chave_numeros, fontsize=5.5, color=(0, 0, 0))
                img_bytes = gerar_imagem_barcode(chave_numeros)
                if img_bytes:
                    rect_barcode = fitz.Rect(0, 403, 283, 425)
                    nova_pagina.insert_image(rect_barcode, stream=img_bytes)
        else:
            nova_pagina.insert_text(fitz.Point(90, 410), "(Sem Declaração/Chave)", fontsize=5, color=(0.5, 0.5, 0.5))

        # 4. Numeração (Fonte tamanho 6)
        count_etiquetas += 1
        num_pag_text = f"{count_etiquetas}"
        nova_pagina.draw_rect(fitz.Rect(260, 0, 283, 10), color=None, fill=(1,1,1)) 
        nova_pagina.insert_text(fitz.Point(265, 8), num_pag_text, fontsize=6, color=(0,0,0))
        
        # Incremento do loop
        if page_declaracao: i += 2 
        else: i += 1 
            
        if index_inicio_lista != -1 and i >= index_inicio_lista: break

    # --- C. Geração da Lista de Controle ---
    if index_inicio_lista != -1 and index_inicio_lista < total_paginas:
        doc_lista.insert_pdf(doc_origem, from_page=index_inicio_lista, to_page=total_paginas-1)

    # --- Retorno ---
    bytes_etiquetas = None
    if count_etiquetas > 0:
        buffer_et = BytesIO()
        doc_etiquetas.save(buffer_et)
        bytes_etiquetas = buffer_et.getvalue()
        
    bytes_lista = None
    if len(doc_lista) > 0:
        buffer_li = BytesIO()
        doc_lista.save(buffer_li)
        bytes_lista = buffer_li.getvalue()

    doc_etiquetas.close()
    doc_lista.close()
    doc_origem.close()
    
    return bytes_etiquetas, count_etiquetas, bytes_lista


# ==============================================================================
# --- INTERFACE VISUAL (FRONTEND) ---
# ==============================================================================

def render_page(navegar_para_callback):
    """
    Renderiza a página de etiquetas.
    """
    if st.button("⬅️ Voltar ao Menu", key="btn_voltar_etiquetas"):
        navegar_para_callback('menu')

    st.markdown("## 🏷️ Simplificador de Etiquetas (V2.0)")
    
    st.info("Gera etiquetas numeradas, com SKU, quantidades e lista de controle separada para impressão.")
    
    # Inicialização de Estado da Página
    if 'etiquetas_geradas' not in st.session_state: st.session_state['etiquetas_geradas'] = None
    if 'lista_gerada' not in st.session_state: st.session_state['lista_gerada'] = None
    if 'qtd_etiquetas' not in st.session_state: st.session_state['qtd_etiquetas'] = 0

    uploaded_file = st.file_uploader("Selecione o PDF completo", type="pdf")
    
    st.markdown("<br>", unsafe_allow_html=True)

    if uploaded_file is not None:
        if st.button("PROCESSAR ARQUIVO", type="primary"):
            with st.spinner('Lendo PDF e gerando arquivos...'):
                try:
                    etiquetas, qtd, lista = processar_arquivos_pdf(uploaded_file)
                    st.session_state['etiquetas_geradas'] = etiquetas
                    st.session_state['lista_gerada'] = lista
                    st.session_state['qtd_etiquetas'] = qtd
                    
                    if qtd > 0:
                        st.success(f"Sucesso! {qtd} etiquetas encontradas.")
                    else:
                        st.warning("Nenhuma etiqueta foi gerada.")
                except Exception as e:
                    st.error(f"Erro ao processar PDF: {e}")

    # Exibição dos resultados e botões de ação
    if st.session_state['etiquetas_geradas']:
        st.markdown("---")
        st.subheader("🖨️ Painel de Impressão")
        col1, col2 = st.columns(2)
        
        with col1:
            st.markdown(f"**Etiquetas ({st.session_state['qtd_etiquetas']} un)**")
            
            # Opção 1: Abrir localmente
            if st.button("ABRIR P/ IMPRESSÃO (Etiquetas)", type="primary"):
                enviar_para_impressora_local(st.session_state['etiquetas_geradas'], "etiquetas_trend.pdf")
            
            # Opção 2: Download direto
            st.download_button(
                label="⬇️ Baixar PDF Etiquetas",
                data=st.session_state['etiquetas_geradas'],
                file_name="etiquetas_prontas.pdf",
                mime="application/pdf"
            )
                
        with col2:
            if st.session_state['lista_gerada']:
                st.markdown("**Lista de Controle**")
                
                # Opção 1: Abrir localmente
                if st.button("ABRIR LISTA DE CONTROLE", type="secondary"):
                    enviar_para_impressora_local(st.session_state['lista_gerada'], "lista_controle.pdf")
                
                # Opção 2: Download direto
                st.download_button(
                    label="⬇️ Baixar Lista Controle",
                    data=st.session_state['lista_gerada'],
                    file_name="lista_controle.pdf",
                    mime="application/pdf"
                )
            else:
                st.info("Nenhuma lista de controle extraída.")