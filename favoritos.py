import logging
import time
import requests
import streamlit as st
from bs4 import BeautifulSoup

logger = logging.getLogger("jk_sistema")

def estimar_meses_anuncio(url_anuncio, max_retries=3, delay=2):
    """
    Tenta estimar há quantos meses o anúncio está ativo usando avaliações ou perguntas.
    Retorna número de meses (float) ou None.
    """
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    for tentativa in range(max_retries):
        try:
            resp = requests.get(url_anuncio, headers=headers, timeout=10)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, 'lxml')
                # Tenta encontrar datas de avaliações
                datas = []
                for tag in soup.find_all('span'):
                    if tag.text.strip().lower().startswith('há '):
                        datas.append(tag.text.strip())
                # Exemplo: 'há 3 meses', 'há 1 ano', 'há 15 dias'
                if datas:
                    # Pega a avaliação mais antiga
                    texto = datas[-1]
                    logger.info(f"Texto de data de avaliação encontrado: {texto}")
                    if 'ano' in texto:
                        anos = int(texto.split('há')[1].split('ano')[0].strip())
                        meses = anos * 12
                        if 'mes' in texto:
                            meses += int(texto.split('ano')[1].split('mes')[0].strip())
                        return meses
                    elif 'mes' in texto:
                        return int(texto.split('há')[1].split('mes')[0].strip())
                    elif 'dia' in texto:
                        dias = int(texto.split('há')[1].split('dia')[0].strip())
                        return max(1, dias // 30)
                # Se não achar avaliações, tenta perguntas (datas)
                perguntas = soup.find_all('div', {'class': 'questions__item'})
                datas_perg = []
                for p in perguntas:
                    data_tag = p.find('span', {'class': 'questions__item-date'})
                    if data_tag:
                        datas_perg.append(data_tag.text.strip())
                if datas_perg:
                    texto = datas_perg[-1]
                    logger.info(f"Texto de data de pergunta encontrado: {texto}")
                    if 'ano' in texto:
                        anos = int(texto.split('há')[1].split('ano')[0].strip())
                        meses = anos * 12
                        if 'mes' in texto:
                            meses += int(texto.split('ano')[1].split('mes')[0].strip())
                        return meses
                    elif 'mes' in texto:
                        return int(texto.split('há')[1].split('mes')[0].strip())
                    elif 'dia' in texto:
                        dias = int(texto.split('há')[1].split('dia')[0].strip())
                        return max(1, dias // 30)
                logger.warning(f"Nenhuma data de avaliação ou pergunta encontrada para {url_anuncio}")
                return None
            else:
                logger.warning(f"Erro ao buscar {url_anuncio}: status {resp.status_code}. Tentativa {tentativa + 1}/{max_retries}")
                time.sleep(delay)
        except Exception as e:
            logger.exception(f"Erro ao processar {url_anuncio}. Tentativa {tentativa + 1}/{max_retries}")
            time.sleep(delay)
    return None


def buscar_anuncios_mercadolivre(termo, max_retries=3, delay=2):
    """
    Pesquisa a primeira página de anúncios do Mercado Livre para o termo informado.
    Retorna lista de dicionários com dados básicos dos anúncios.
    """
    if termo.startswith('http'):
        url = termo
    else:
        termo_url = termo.replace(' ', '-')
        url = f'https://lista.mercadolivre.com.br/{termo_url}'
    
    logger.info(f"Buscando anúncios para '{termo}' na URL: {url}")
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    for tentativa in range(max_retries):
        try:
            resp = requests.get(url, headers=headers, timeout=10)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, 'lxml')
                anuncios = []
                
                # Tenta o seletor original primeiro
                items = soup.select('li.ui-search-layout__item')
                logger.info(f"Tentativa com 'li.ui-search-layout__item': {len(items)} itens encontrados.")

                # Se falhar, tenta o alternativo
                if not items:
                    items = soup.select('div.ui-search-result__content-wrapper')
                    logger.info(f"Tentativa com 'div.ui-search-result__content-wrapper': {len(items)} itens encontrados.")

                if not items:
                    logger.warning("Nenhum item de anúncio encontrado com os seletores conhecidos.")
                    logger.debug(f"Início do HTML recebido (status {resp.status_code}):\n{resp.text[:2000]}")
                    return []

                for item in items:
                    # Link do anúncio
                    link_tag = item.select_one('a.ui-search-link')
                    if not link_tag:
                        link_tag = item.select_one('a[href*="/MLB-"]')
                    # Título do anúncio
                    titulo_tag = item.select_one('h2.ui-search-item__title')
                    if not titulo_tag:
                        titulo_tag = item.select_one('span.ui-search-item__group__element.ui-search-item__title')
                    # Preço do anúncio
                    preco_tag = item.select_one('span.ui-search-price__part')
                    if not preco_tag:
                        preco_tag = item.select_one('span.andes-money-amount__fraction')
                    # Vendas (texto de variação ou badge)
                    vendas_tag = item.select_one('span.ui-search-item__group__element.ui-search-item__variations-text')
                    if not vendas_tag:
                        vendas_tag = item.find('span', string=lambda t: t and ("vendido" in t or "vendidos" in t))
                    if link_tag and titulo_tag and preco_tag:
                        anuncios.append({
                            'url': link_tag['href'],
                            'titulo': titulo_tag.text.strip(),
                            'preco': preco_tag.text.strip(),
                            'vendas': vendas_tag.text.strip() if vendas_tag else None
                        })
                    else:
                        logger.warning("Não foi possível extrair dados de um item. HTML do item:")
                        logger.debug(item.prettify())
                logger.info(f"Extraídos {len(anuncios)} anúncios com sucesso.")
                return anuncios
            else:
                logger.warning(f"Erro ao buscar {url}: status {resp.status_code}. Tentativa {tentativa + 1}/{max_retries}")
                logger.debug(f"Resposta recebida (status {resp.status_code}):\n{resp.text[:1000]}")
                time.sleep(delay)
        except Exception as e:
            logger.exception(f"Erro ao processar busca por '{termo}'. Tentativa {tentativa + 1}/{max_retries}")
            time.sleep(delay)
    return []


def render_page(navegar_para):
    if st.button("⬅️ Voltar ao Menu"):
        navegar_para('menu')

    st.header('Pesquisa Mercado Livre - Favoritos')
    st.write('Informe o nome, código ou link do produto para pesquisar no Mercado Livre:')
    termo = st.text_input('Nome, código ou link do produto')
    if termo:
        st.info(f'Pesquisa para: {termo}')
        with st.spinner('Buscando anúncios no Mercado Livre...'):
            anuncios = buscar_anuncios_mercadolivre(termo)
        if not anuncios:
            st.warning('Nenhum anúncio encontrado ou limite de acesso atingido. Verifique o log para mais detalhes.')
            return
        st.success(f'{len(anuncios)} anúncios encontrados na primeira página.')

        resultados = []
        progress_bar = st.progress(0, text="Analisando anúncios...")
        for i, anuncio in enumerate(anuncios):
            url = anuncio['url']
            vendas = anuncio['vendas']
            try:
                vendas_num = int(vendas.split()[0].replace('.', '')) if vendas else None
            except Exception:
                vendas_num = None
            
            meses = estimar_meses_anuncio(url)
            media = vendas_num / meses if vendas_num and meses and meses > 0 else None
            
            resultados.append({
                'Título': anuncio['titulo'],
                'Preço': anuncio['preco'],
                'Vendas': vendas_num,
                'Meses': meses,
                'Média vendas/mês': round(media, 2) if media else None,
                'Link': url
            })
            progress_bar.progress((i + 1) / len(anuncios), text=f"Analisando anúncio {i+1}/{len(anuncios)}")

        progress_bar.empty()
        
        resultados_ordenados = sorted([r for r in resultados if r['Média vendas/mês'] is not None], key=lambda x: x['Média vendas/mês'], reverse=True)
        top7 = resultados_ordenados[:7]

        if top7:
            st.subheader('Top 7 anúncios por média de vendas/mês')
            st.dataframe(top7)
        else:
            st.info("Não foi possível calcular a média de vendas para os anúncios encontrados.")