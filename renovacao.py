import streamlit as st
import openpyxl
from openpyxl.cell.cell import MergedCell
from io import BytesIO

# ==============================================================================
# --- LÓGICA DE PROCESSAMENTO (BACKEND) ---
# ==============================================================================

def processar_renovacao_campanha(file_antiga, file_nova):
    """
    Lê a planilha antiga para verificar o status dos itens.
    Se o status for diferente de 'ativo', marca como 'Não participar' na planilha nova.
    """
    try:
        # 1. Carregar planilha antiga
        wb_antiga = openpyxl.load_workbook(file_antiga, data_only=True)
        sheet_antiga_name = None
        
        # Tenta encontrar a aba correta
        for name in wb_antiga.sheetnames:
            if "promoções" in name.lower() or "promo" in name.lower():
                sheet_antiga_name = name
                break
        if not sheet_antiga_name: 
            sheet_antiga_name = wb_antiga.sheetnames[0]
            
        ws_antiga = wb_antiga[sheet_antiga_name]
        
        # 2. Identificar colunas pelo cabeçalho (Linha 1)
        header_row = [str(c.value).strip().upper() if c.value else "" for c in ws_antiga[1]]
        
        try:
            idx_status = header_row.index("STATUS")
            idx_action = header_row.index("ACTION")
        except ValueError:
            return None, "Colunas 'STATUS' e 'ACTION' não encontradas na linha 1 do arquivo antigo."

        linhas_processadas = []
        
        # 3. Iterar dados a partir da linha 6 (min_row=6 é o padrão dessas planilhas de campanha)
        for row in ws_antiga.iter_rows(min_row=6, values_only=True):
            if not any(row): continue
            
            row_list = list(row)
            
            # Garante que a lista tenha tamanho suficiente
            max_idx = max(idx_status, idx_action)
            if len(row_list) <= max_idx:
                row_list.extend([None] * (max_idx - len(row_list) + 1))

            val_status = row_list[idx_status]
            status_str = str(val_status).strip().lower() if val_status else ""
            
            # LÓGICA PRINCIPAL:
            # Se status não for vazio E diferente de "ativo" -> Action = "Não participar"
            if status_str and status_str != "ativo":
                row_list[idx_action] = "Não participar"
            
            linhas_processadas.append(row_list)

        if not linhas_processadas: 
            return None, "Nenhuma linha válida encontrada a partir da linha 6."
        
        # 4. Escrever na planilha nova
        wb_nova = openpyxl.load_workbook(file_nova)
        sheet_nova_name = None
        for name in wb_nova.sheetnames:
            if "promoções" in name.lower() or "promo" in name.lower():
                sheet_nova_name = name
                break
        if not sheet_nova_name: 
            sheet_nova_name = wb_nova.sheetnames[0]
            
        ws_nova = wb_nova[sheet_nova_name]
        
        start_row = 6
        
        for i, row_data in enumerate(linhas_processadas):
            current_row = start_row + i
            for j, value in enumerate(row_data):
                # Verifica células mescladas antes de escrever
                cell = ws_nova.cell(row=current_row, column=j+1)
                if isinstance(cell, MergedCell):
                    # Se for mesclada, precisamos desmesclar para escrever (ou pular)
                    # Aqui optamos por desmesclar para garantir a escrita
                    for merged_range in list(ws_nova.merged_cells.ranges):
                        if cell.coordinate in merged_range:
                            ws_nova.unmerge_cells(str(merged_range))
                            break
                    cell = ws_nova.cell(row=current_row, column=j+1)
                
                cell.value = value
                
        output = BytesIO()
        wb_nova.save(output)
        output.seek(0)
        return output, f"Sucesso! {len(linhas_processadas)} linhas processadas."

    except Exception as e:
        return None, str(e)


# ==============================================================================
# --- INTERFACE VISUAL (FRONTEND) ---
# ==============================================================================

def render_page(navegar_para_callback):
    """
    Renderiza a página de Renovação de Campanha.
    :param navegar_para_callback: Função do app principal para mudar de tela.
    """
    if st.button("⬅️ Voltar ao Submenu", key="btn_voltar_renovacao"):
        navegar_para_callback('submenu_promo')

    st.markdown("## Renovação de Campanha Fixa")
    
    # Gerenciamento de Estado Local
    if 'renovacao_buffer' not in st.session_state: st.session_state['renovacao_buffer'] = None
    if 'renovacao_nome' not in st.session_state: st.session_state['renovacao_nome'] = None
    
    st.markdown("---")
    
    # Uploaders lado a lado
    col1, col2 = st.columns(2)
    with col1:
        st.info("📄 **Passo 1:** Anexar campanha Antiga")
        arquivo_antiga = st.file_uploader("Selecione arquivo XLSX (Antigo)", type=["xlsx"], key="up_antiga")
    with col2:
        st.info("🆕 **Passo 2:** Anexar campanha Nova")
        arquivo_nova = st.file_uploader("Selecione arquivo XLSX (Novo)", type=["xlsx"], key="up_nova")
    
    st.markdown("<br>", unsafe_allow_html=True)
    
    # Botão de Processamento
    if st.button("🚀 Processar Renovação", type="primary", use_container_width=True, disabled=not (arquivo_antiga and arquivo_nova)):
        with st.status("Processando...", expanded=True) as status:
            buffer_result, msg = processar_renovacao_campanha(arquivo_antiga, arquivo_nova)
            
            if buffer_result:
                st.session_state['renovacao_buffer'] = buffer_result
                st.session_state['renovacao_nome'] = arquivo_nova.name
                status.update(label="Concluído!", state="complete", expanded=False)
                st.success(msg)
            else:
                status.update(label="Erro!", state="error", expanded=True)
                st.error(f"Erro: {msg}")

    # Botão de Download (aparece apenas se processado com sucesso)
    if st.session_state['renovacao_buffer']:
        st.markdown("---")
        col_down, _ = st.columns([2, 1])
        with col_down:
            st.download_button(
                label="📥 BAIXAR PLANILHA AGORA", 
                data=st.session_state['renovacao_buffer'], 
                file_name=st.session_state['renovacao_nome'], 
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", 
                use_container_width=True, 
                type="primary"
            )