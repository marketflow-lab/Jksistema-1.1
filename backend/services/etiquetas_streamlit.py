"""Legacy Streamlit UI helpers for Etiquetas."""

from __future__ import annotations

import os
import platform
import sys
import tempfile
import time
import webbrowser

import streamlit as st

from backend.services.etiquetas_marketplaces import (
    processar_mercado_livre,
    processar_shopee,
    processar_tiktok,
    set_streamlit_runtime,
)

set_streamlit_runtime(st)


def enviar_para_impressora_local(pdf_bytes, nome_arquivo="temp_print.pdf"):
    """
    Salva o arquivo temporariamente e abre com o visualizador padrão do SO para impressão.
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
            
        st.toast(f"🖨️ Arquivo enviado: {nome_arquivo}", icon="✅")
        time.sleep(1)
        return True
    except Exception as e:
        st.error(f"Erro ao abrir arquivo para impressão: {e}")
        return False


def render_page(navegar_para_callback):
    """
    Renderiza a página consolidada de etiquetas.
    """
    if st.button("⬅️ Voltar ao Menu", key="btn_voltar_etiquetas"):
        navegar_para_callback('menu')

    st.markdown("## Gerador de Etiquetas Inteligente (V2.0)")
    st.markdown("### Automação Logística Trend Minas")
    
    # Inicialização de Estado
    if 'etiquetas_geradas' not in st.session_state:
        st.session_state.update({
            'etiquetas_geradas': None, 
            'lista_gerada': None, 
            'qtd': 0, 
            'processed': False,
            'loja': None
        })

    st.subheader("1. Selecione a Loja")
    loja_selecionada = st.radio(
        "Escolha a plataforma:",
        ("Mercado Livre", "Shopee", "TikTok"),
        key='loja_radio',
        horizontal=True,
        index=None
    )

    if loja_selecionada:
        st.markdown("---")
        st.subheader("2. Faça o Upload dos Arquivos")
        
        uploaded_file = None
        uploaded_list = None

        if loja_selecionada == "Mercado Livre" or loja_selecionada == "Shopee":
            uploaded_file = st.file_uploader(
                "Importar arquivo PDF", 
                type="pdf", 
                help=f"Arraste o PDF da {loja_selecionada} aqui."
            )
        else: # TikTok
            col1, col2 = st.columns(2)
            with col1:
                uploaded_file = st.file_uploader("Arquivo de Etiquetas (PDF)", type="pdf")
            with col2:
                uploaded_list = st.file_uploader("Lista de Picking (PDF)", type="pdf", help="Obrigatório para TikTok")

        if uploaded_file:
            if st.button("🚀 INICIAR PROCESSAMENTO", type="primary", use_container_width=True):
                try:
                    etiquetas, qtd, lista = None, 0, None
                    
                    if loja_selecionada == "Mercado Livre":
                        etiquetas, qtd, lista = processar_mercado_livre(uploaded_file)
                    elif loja_selecionada == "Shopee":
                        etiquetas, lista, qtd = processar_shopee(uploaded_file)
                    else:
                        etiquetas, qtd, lista = processar_tiktok(uploaded_file, uploaded_list)
                    
                    st.session_state['etiquetas_geradas'] = etiquetas
                    st.session_state['lista_gerada'] = lista
                    st.session_state['qtd'] = qtd
                    st.session_state['processed'] = True
                    st.session_state['loja'] = loja_selecionada
                    
                    if qtd > 0:
                        st.toast(f"Sucesso! {qtd} etiquetas geradas.", icon="🎉")
                    else:
                        st.warning("Nenhuma etiqueta detectada.")
                except Exception as e:
                    st.error(f"Erro crítico: {e}")

    # Exibição dos Resultados
    if st.session_state.get('processed'):
        st.markdown("---")
        st.subheader("📊 Resultados")
        
        col_kpi1, col_kpi2, col_kpi3 = st.columns(3)
        tem_etiquetas = st.session_state['etiquetas_geradas'] is not None
        tem_lista = st.session_state['lista_gerada'] is not None
        
        with col_kpi1: st.metric("Etiquetas", st.session_state['qtd'])
        with col_kpi2: st.metric("Lista", "Pronta" if tem_lista else "-")
        with col_kpi3: st.metric("Loja", st.session_state.get('loja', '-'))

        st.markdown("<br>", unsafe_allow_html=True)
        col_act1, col_act2 = st.columns(2)

        with col_act1:
            if tem_etiquetas:
                # Botão de Impressão Direta (Local)
                if st.button("📦 ABRIR P/ IMPRESSÃO", type="primary", use_container_width=True):
                    enviar_para_impressora_local(st.session_state['etiquetas_geradas'], "etiquetas_prontas.pdf")
                
                # Download
                st.download_button(
                    label="⬇️ Baixar PDF Etiquetas",
                    data=st.session_state['etiquetas_geradas'],
                    file_name="etiquetas_processadas.pdf",
                    mime="application/pdf",
                    use_container_width=True
                )
                
        with col_act2:
            if tem_lista:
                # Botão de Impressão Direta (Local)
                if st.button("📋 ABRIR LISTA", type="secondary", use_container_width=True):
                    enviar_para_impressora_local(st.session_state['lista_gerada'], "lista_controle.pdf")
                
                # Download
                st.download_button(
                    label="⬇️ Baixar Lista Controle",
                    data=st.session_state['lista_gerada'],
                    file_name="lista_controle.pdf",
                    mime="application/pdf",
                    use_container_width=True
                )
            elif st.session_state.get('loja') == "Shopee":
                 st.info("Shopee: Manifesto não encontrado no arquivo.")
