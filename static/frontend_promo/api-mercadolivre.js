function alternarModoAnalise() {
    modoAnaliseAtual = 'api';
    const painelArquivos = document.getElementById('painelArquivos');
    const painelApi = document.getElementById('painelApi');
    const painelMlb = document.getElementById('painelMlb');
    if (painelArquivos) painelArquivos.style.display = 'none';
    if (painelApi) painelApi.style.display = 'block';
    if (painelMlb) painelMlb.style.display = 'none';
    document.getElementById('tabArquivos')?.classList.remove('active');
    document.getElementById('tabApi')?.classList.add('active');
    document.getElementById('tabMlb')?.classList.remove('active');
}

function getAuthHeadersWithClient(extra = {}) {
    const userData = JSON.parse(localStorage.getItem('user_data') || '{}');
    const clientId = userData.client_id;
    if (typeof obterAuthHeaders === 'function') {
        return obterAuthHeaders({ ...(clientId ? { 'X-Client-ID': clientId } : {}), ...extra });
    }
    const token = localStorage.getItem('access_token');
    return {
        ...(token ? { 'Authorization': `Bearer ${token}` } : {}),
        ...(clientId ? { 'X-Client-ID': clientId } : {}),
        ...extra,
    };
}

function getPromoClientId() {
    try {
        const userData = JSON.parse(localStorage.getItem('user_data') || '{}');
        return String(userData.client_id || '').trim();
    } catch (_e) {
        return '';
    }
}

async function fetchJsonOrThrow(url, options, fallbackMessage) {
    const resp = await fetch(url, options || {});
    const payload = await resp.json().catch(() => ({}));
    if (!resp.ok || !payload.success) {
        throw new Error(buildApiErrorMessage(resp, payload, fallbackMessage || 'Falha na consulta.'));
    }
    return payload;
}

async function consultarProgressoAnaliseApi(jobId) {
    const progressUrl = `/api/promo/analise-via-api-arquivos/progresso/${encodeURIComponent(jobId)}`;
    try {
        return await fetchJsonOrThrow(
            progressUrl,
            { headers: getAuthHeadersWithClient() },
            'Erro ao consultar o andamento da analise.'
        );
    } catch (backendError) {
        const message = String(backendError && backendError.message ? backendError.message : backendError || '');
        const pareceFalhaRede = !message || /failed to fetch|networkerror|load failed|falha.*fetch/i.test(message);
        if (!pareceFalhaRede) {
            throw backendError;
        }
        const clientId = getPromoClientId();
        if (!clientId) {
            throw backendError;
        }
        try {
            return await fetchJsonOrThrow(
                `http://127.0.0.1:8011/api/promo/jobs/${encodeURIComponent(jobId)}?client_id=${encodeURIComponent(clientId)}`,
                { headers: { 'X-Client-ID': clientId } },
                'Erro ao consultar o worker de promocoes.'
            );
        } catch (_workerError) {
            throw backendError;
        }
    }
}

async function carregarLojasApiPromo() {
    const selectApi = document.getElementById('apiLojaSelect');
    const selectMlb = document.getElementById('mlbLojaSelect');
    if (!selectApi || !selectMlb) return;
    selectApi.innerHTML = '<option value="">Carregando...</option>';
    selectMlb.innerHTML = '<option value="">Carregando...</option>';
    try {
        const resp = await fetch('/api/lojas', { headers: getAuthHeadersWithClient() });
        if (!resp.ok) throw new Error('Erro ao carregar lojas');
        const lojas = await resp.json();
        selectApi.innerHTML = '';
        selectMlb.innerHTML = '';
        const lojasLista = Array.isArray(lojas) ? lojas : [];
        lojasLista.forEach((loja) => {
            const nome = loja && loja.nome ? loja.nome : '';
            if (!nome) return;
            const opt = document.createElement('option');
            opt.value = nome;
            opt.textContent = nome;
            selectApi.appendChild(opt);
            selectMlb.appendChild(opt.cloneNode(true));
        });
        if (!selectApi.options.length) {
            selectApi.innerHTML = '<option value="">Nenhuma loja cadastrada</option>';
            selectMlb.innerHTML = '<option value="">Nenhuma loja cadastrada</option>';
            return;
        }
        await carregarPromocoesApi();
    } catch (e) {
        selectApi.innerHTML = '<option value="">Erro ao carregar lojas</option>';
        selectMlb.innerHTML = '<option value="">Erro ao carregar lojas</option>';
    }
}

function normalizarMlbInput(raw) {
    const txt = String(raw || '').trim().toUpperCase();
    if (!txt) return '';
    if (/^MLB\d+$/.test(txt)) return txt;
    if (/^\d+$/.test(txt)) return `MLB${txt}`;
    return txt;
}

function formatMoneyBr(raw) {
    const n = Number(raw);
    if (!Number.isFinite(n)) return '-';
    return n.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' });
}

function formatNumber(raw) {
    const n = Number(raw);
    if (!Number.isFinite(n)) return '-';
    return n.toLocaleString('pt-BR');
}

function formatPercent(raw) {
    const n = Number(raw);
    if (!Number.isFinite(n)) return '-';
    return `${n.toFixed(1).replace('.', ',')}%`;
}

function parseMoneyTextBr(raw) {
    const txt = String(raw || '').trim();
    if (!txt) return null;
    const norm = txt.replace(/[^\d,.-]/g, '').replace(/\./g, '').replace(',', '.');
    const n = Number(norm);
    return Number.isFinite(n) ? n : null;
}

function inferFixedFee(fee) {
    if (!fee || typeof fee !== 'object') return null;
    const direct = Number(fee.fixed_fee_amount);
    if (Number.isFinite(direct) && direct > 0) return direct;
    const txt = String(fee.fee_breakdown || '');
    const m = txt.match(/taxa\s+fixa:\s*r\$\s*([0-9.,]+)/i);
    if (!m) return null;
    return parseMoneyTextBr(m[1]);
}

function formatDateIso(raw) {
    if (!raw) return '-';
    const d = new Date(raw);
    if (Number.isNaN(d.getTime())) return String(raw);
    return d.toLocaleString('pt-BR');
}

function valorAttr(item, attrId) {
    const attrs = Array.isArray(item && item.attributes) ? item.attributes : [];
    const found = attrs.find((a) => String(a && a.id || '').toUpperCase() === String(attrId || '').toUpperCase());
    if (!found) return '';
    return found.value_name || (Array.isArray(found.values) && found.values[0] && found.values[0].name) || '';
}

function buildKvGroup(title, rows) {
    const cleanRows = rows.filter((r) => r && r[0]);
    if (!cleanRows.length) return '';
    const body = cleanRows.map(([k, v]) => (
        `<div><span>${k}</span><span>${(v === null || v === undefined || v === '') ? '-' : String(v)}</span></div>`
    )).join('');
    return `<section class="mlb-group"><h4>${title}</h4><div class="mlb-kv">${body}</div></section>`;
}

function renderMlbSummary(item) {
    const priceDetails = item && item.price_details ? item.price_details : {};
    const shippingDetails = item && item.shipping_details ? item.shipping_details : {};
    const campos = [
        ['MLB', item && item.id ? item.id : '-'],
        ['Titulo', item && item.title ? item.title : '-'],
        ['Status', item && item.status ? item.status : '-'],
        ['SKU', (item && (item.seller_sku || item.seller_custom_field)) ? (item.seller_sku || item.seller_custom_field) : '-'],
        ['Pre?o de venda', formatMoneyBr(priceDetails.amount ?? priceDetails.price ?? item?.price)],
        ['Pre?o original', formatMoneyBr(priceDetails.regular_amount ?? priceDetails.original_price ?? item?.original_price)],
        ['Tem promo??o', (priceDetails.promotion_id || (item && item.original_price && item.price && Number(item.original_price) > Number(item.price))) ? 'Sim' : 'Nao'],
        ['Promotion ID', priceDetails.promotion_id || '-'],
        ['Promotion Type', priceDetails.promotion_type || '-'],
        ['Frete ML estimado', formatMoneyBr(shippingDetails.shipping_cost)],
        ['Frete comprador', formatMoneyBr(shippingDetails.shipping_buyer_cost)],
        ['Modalidade log­stica', shippingDetails.logistic_type || '-'],
    ];
    const box = document.getElementById('mlbSummary');
    if (!box) return;
    box.innerHTML = campos.map(([k, v]) =>
        `<div class="mlb-chip"><span class="k">${k}</span><span class="v">${String(v ?? '-')}</span></div>`
    ).join('');
}

function renderMlbSpotlight(item) {
    const box = document.getElementById('mlbSpotlight');
    if (!box) return;
    const fee = item && item.fee_details ? item.fee_details : {};
    const listingType = fee.listing_type_name || item?.listing_type_id || '-';
    const taxaMl = fee.ad_cost_text || formatMoneyBr(fee.ad_cost);
    const taxaPct = formatPercent(fee.meli_fee_pct ?? fee.sale_fee_pct);
    const taxaPctLabel = fee.meli_fee_pct != null ? 'Taxa de venda %' : 'Percentual total da API';
    const fixedFee = inferFixedFee(fee);
    const taxaFixa = (fee.fixed_fee_text && fee.fixed_fee_text !== '-') ? fee.fixed_fee_text : formatMoneyBr(fixedFee);

    box.innerHTML = [
        `<article class="mlb-spot-card"><span class="k">Tipo de anuncio</span><span class="v">${listingType || '-'}</span></article>`,
        `<article class="mlb-spot-card"><span class="k">Taxa cobrada pelo ML</span><span class="v">${taxaMl || '-'}</span></article>`,
        `<article class="mlb-spot-card"><span class="k">${taxaPctLabel}</span><span class="v">${taxaPct || '-'}</span></article>`,
        `<article class="mlb-spot-card"><span class="k">Taxa fixa</span><span class="v">${taxaFixa || '-'}</span></article>`,
    ].join('');
}

function renderMlbOrganized(item) {
    const box = document.getElementById('mlbOrganized');
    if (!box) return;
    const p = item && item.price_details ? item.price_details : {};
    const s = item && item.shipping_details ? item.shipping_details : {};
    const fee = item && item.fee_details ? item.fee_details : {};
    const shipping = item && item.shipping ? item.shipping : {};
    const sellerAddress = item && item.seller_address ? item.seller_address : {};
    const attrs = Array.isArray(item && item.attributes) ? item.attributes : [];
    const fixedFee = inferFixedFee(fee);

    const groups = [
        buildKvGroup('Identifica??o', [
            ['MLB', item && item.id],
            ['Titulo', item && item.title],
            ['Categoria', item && item.category_id],
            ['Tipo anuncio', item && item.listing_type_id],
            ['Status', item && item.status],
            ['Condicao', item && item.condition],
            ['Domain', item && item.domain_id],
            ['SKU', valorAttr(item, 'SELLER_SKU') || item?.seller_custom_field || ''],
        ]),
        buildKvGroup('Pre?o e promo??o', [
            ['Pre§o atual do item', formatMoneyBr(item && item.price)],
            ['Pre§o base do item', formatMoneyBr(item && item.base_price)],
            ['Pre?o final da promo??o', formatMoneyBr(p.price)],
            ['Pre?o original da promo??o', formatMoneyBr(p.original_price)],
            ['Pre§o padr£o', formatMoneyBr(p.standard_price)],
            ['Taxa ML', fee.ad_cost_text || formatMoneyBr(fee.ad_cost)],
            ['Taxa venda %', formatPercent(fee.meli_fee_pct)],
            ['Taxa total %', formatPercent(fee.sale_fee_pct)],
            ['Parcelamento %', formatPercent(fee.financing_fee_pct)],
            ['Taxa fixa', (fee.fixed_fee_text && fee.fixed_fee_text !== '-') ? fee.fixed_fee_text : formatMoneyBr(fixedFee)],
            ['Escopo % API', fee.pct_scope === 'selling_fee' ? 'Tarifa de venda' : (fee.pct_scope === 'total_commission' ? 'Comiss?o total' : '-')],
            ['Obs taxa API', fee.pct_note || '-'],
            ['Tem promo??o', p.has_promotion ? 'Sim' : 'Nao'],
            ['Desconto', formatPercent(p.discount_pct)],
            ['Promotion ID', p.promotion_id || '-'],
            ['Promotion type', p.promotion_type || '-'],
            ['Price source', p.price_source || '-'],
        ]),
        buildKvGroup('Frete', [
            ['Frete custo ML', formatMoneyBr(s.shipping_cost)],
            ['Frete comprador', formatMoneyBr(s.shipping_buyer_cost)],
            ['Frete base', formatMoneyBr(s.shipping_base_cost)],
            ['Frete texto', s.shipping_text || '-'],
            ['Frete breakdown', s.shipping_breakdown || '-'],
            ['Frete gr?tis', s.free_shipping ? 'Sim' : 'Nao'],
            ['Modo de envio', s.shipping_mode || shipping.mode || '-'],
            ['Tipo log­stico', s.logistic_type || shipping.logistic_type || '-'],
            ['CEP simulacao', s.shipping_zip || '-'],
        ]),
        buildKvGroup('Estoque e vendas', [
            ['Quantidade inicial', formatNumber(item && item.initial_quantity)],
            ['Quantidade disponivel', formatNumber(item && item.available_quantity)],
            ['Quantidade vendida', formatNumber(item && item.sold_quantity)],
            ['Data de cria??o', formatDateIso(item && item.date_created)],
            ['?ltima atualiza??o', formatDateIso(item && item.last_updated)],
            ['Expira em', formatDateIso(item && item.expiration_time)],
            ['Health', formatPercent((item && item.health) ? Number(item.health) * 100 : null)],
        ]),
        buildKvGroup('Endere§o do vendedor', [
            ['Cidade', sellerAddress?.city?.name || '-'],
            ['Estado', sellerAddress?.state?.name || '-'],
            ['CEP', sellerAddress?.zip_code || '-'],
            ['Endere?o', sellerAddress?.address_line || '-'],
            ['Logradouro ref', sellerAddress?.comment || '-'],
        ]),
        buildKvGroup('Atributos chave', [
            ['GTIN', valorAttr(item, 'GTIN')],
            ['Marca', valorAttr(item, 'BRAND')],
            ['Cor', valorAttr(item, 'COLOR')],
            ['Material', valorAttr(item, 'MATERIAL')],
            ['Part number', valorAttr(item, 'PART_NUMBER')],
            ['Origem', valorAttr(item, 'ORIGIN')],
            ['Tipo veiculo', valorAttr(item, 'VEHICLE_TYPE')],
            ['Total atributos', formatNumber(attrs.length)],
        ]),
    ].filter(Boolean);

    box.innerHTML = groups.join('');
}

async function consultarMlbApi() {
    const loja = document.getElementById('mlbLojaSelect')?.value || '';
    const mlbRaw = document.getElementById('mlbInput')?.value || '';
    const mlb = normalizarMlbInput(mlbRaw);
    const loading = document.getElementById('mlbLoading');
    const errorMsg = document.getElementById('mlbErrorMsg');
    const wrap = document.getElementById('mlbResultWrap');
    const out = document.getElementById('mlbJsonOutput');
    const spot = document.getElementById('mlbSpotlight');

    if (!loja || !mlb) {
        alert('Informe loja e MLB.');
        return;
    }
    loading.style.display = 'block';
    errorMsg.style.display = 'none';
    wrap.style.display = 'none';
    out.textContent = '';
    if (spot) spot.innerHTML = '';

    try {
        const query = new URLSearchParams({ loja });
        const resp = await fetch(`/api/mercadolivre/anuncios/${encodeURIComponent(mlb)}?${query.toString()}`, {
            headers: getAuthHeadersWithClient(),
        });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok) {
            throw new Error(data.detail || 'Erro ao consultar MLB.');
        }
        renderMlbSpotlight(data);
        renderMlbSummary(data);
        renderMlbOrganized(data);
        out.textContent = JSON.stringify(data, null, 2);
        wrap.style.display = 'block';
    } catch (e) {
        errorMsg.textContent = e.message;
        errorMsg.style.display = 'block';
    } finally {
        loading.style.display = 'none';
    }
}

function formatPromoDateShort(value) {
    const raw = String(value || '').trim();
    if (!raw) return '';
    const iso = raw.match(/^(\d{4})-(\d{2})-(\d{2})/);
    if (iso) {
        const mesIso = Number(iso[2]) - 1;
        const mesesIso = ['jan', 'fev', 'mar', 'abr', 'mai', 'jun', 'jul', 'ago', 'set', 'out', 'nov', 'dez'];
        return `${Number(iso[3])}/${mesesIso[mesIso] || ''}`;
    }
    const date = new Date(raw);
    if (Number.isNaN(date.getTime())) return '';
    const meses = ['jan', 'fev', 'mar', 'abr', 'mai', 'jun', 'jul', 'ago', 'set', 'out', 'nov', 'dez'];
    return `${date.getDate()}/${meses[date.getMonth()]}`;
}

function formatPromoDateRange(campanha) {
    const inicio = formatPromoDateShort(campanha?.start_date || campanha?.date_start || campanha?.begin_date);
    const fim = formatPromoDateShort(campanha?.finish_date || campanha?.end_date || campanha?.date_end);
    if (inicio && fim) return ` · ${inicio} a ${fim}`;
    if (inicio) return ` · desde ${inicio}`;
    if (fim) return ` · até ${fim}`;
    return '';
}

function formatPromoOption(campanha) {
    const id = campanha.id || '';
    const nome = campanha.name || campanha.title || id;
    const status = campanha.status ? ` - ${campanha.status}` : '';
    const periodo = formatPromoDateRange(campanha);
    return `${nome}${status}${periodo} (${id})`;
}

function promoSelectionGroup(campanha) {
    const explicit = String(campanha?.selection_group || '').trim().toLowerCase();
    if (explicit) return explicit;
    const type = String(campanha?.type || campanha?.promotion_type || '').trim().toUpperCase();
    const nameNorm = String(campanha?.name || campanha?.title || '').toLowerCase();
    if (['SELLER_CAMPAIGN', 'SELLER_COUPON_CAMPAIGN'].includes(type)) return 'usuario';
    if (['SMART', 'PRICE_MATCHING', 'PRICE_MATCHING_MELI_ALL', 'MARKETPLACE_CAMPAIGN', 'PRE_NEGOTIATED'].includes(type)) return 'menos_tarifas';
    if (['aceler', 'tarifa', 'menos tarifa', 'reduzimos', 'aumente suas vendas'].some(k => nameNorm.includes(k))) return 'menos_tarifas';
    return 'outra';
}

function isCampanhaMercadoLivre(campanha) {
    return promoSelectionGroup(campanha) !== 'usuario';
}

function parsePromoCount(value) {
    if (value === null || value === undefined || value === '') return null;
    const num = Number(value);
    if (!Number.isFinite(num)) return null;
    return Math.max(0, Math.trunc(num));
}

function normalizePromoCounts(raw) {
    if (raw === null || raw === undefined || raw === '') return { active: null, eligible: null };
    if (typeof raw !== 'object') {
        return { active: null, eligible: parsePromoCount(raw) };
    }
    return {
        active: parsePromoCount(
            raw.active_count ?? raw.activeCount ?? raw.active ?? raw.ativos ??
            raw.active_items_count ?? raw.active_item_count ?? raw.started_items_count ??
            raw.participating_items_count ?? raw.items_active_count
        ),
        eligible: parsePromoCount(
            raw.eligible_count ?? raw.eligibleCount ?? raw.eligible ?? raw.elegiveis ??
            raw.eligible_items_count ?? raw.eligible_item_count ?? raw.candidate_items_count ??
            raw.candidate_count ?? raw.items_count ?? raw.item_count ?? raw.total_items ??
            raw.products_count ?? raw.offers_count
        ),
    };
}

function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, (char) => ({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;',
    }[char]));
}

function resolvePromoActiveCount(campanha, countsByCampaignId = null) {
    const candidateFields = [
        campanha?.active_count,
        campanha?.activeCount,
        campanha?.active,
        campanha?.ativos,
        campanha?.active_items_count,
        campanha?.active_item_count,
        campanha?.started_items_count,
        campanha?.participating_items_count,
        campanha?.items_active_count,
        campanha?.summary?.active_count,
        campanha?.summary?.active_items_count,
        campanha?.totals?.active_count,
        campanha?.totals?.active_items_count,
    ];
    for (const raw of candidateFields) {
        const parsed = parsePromoCount(raw);
        if (parsed !== null) return parsed;
    }
    const id = String(campanha?.id || campanha?.promo_b_id || '').trim();
    if (id && countsByCampaignId && Object.prototype.hasOwnProperty.call(countsByCampaignId, id)) {
        const normalized = normalizePromoCounts(countsByCampaignId[id]);
        if (normalized.active !== null) return normalized.active;
    }
    return null;
}

function resolvePromoEligibleCount(campanha, countsByCampaignId = null) {
    const candidateFields = [
        campanha?.eligible_count,
        campanha?.eligibleCount,
        campanha?.eligible,
        campanha?.elegiveis,
        campanha?.eligible_items_count,
        campanha?.eligible_item_count,
        campanha?.candidate_items_count,
        campanha?.items_count,
        campanha?.item_count,
        campanha?.total_items,
        campanha?.products_count,
        campanha?.offers_count,
        campanha?.summary?.eligible_count,
        campanha?.summary?.eligible_items_count,
        campanha?.totals?.eligible_count,
        campanha?.totals?.items_count,
    ];
    for (const raw of candidateFields) {
        const parsed = parsePromoCount(raw);
        if (parsed !== null) return parsed;
    }
    const id = String(campanha?.id || campanha?.promo_b_id || '').trim();
    if (id && countsByCampaignId && Object.prototype.hasOwnProperty.call(countsByCampaignId, id)) {
        const normalized = normalizePromoCounts(countsByCampaignId[id]);
        if (normalized.eligible !== null) return normalized.eligible;
    }
    return null;
}

function applyPromoCounts(campanha, countsByCampaignId = null) {
    if (!campanha) return campanha;
    const activeCount = resolvePromoActiveCount(campanha, countsByCampaignId);
    const eligibleCount = resolvePromoEligibleCount(campanha, countsByCampaignId);
    return {
        ...campanha,
        ...(activeCount !== null ? { active_count: activeCount } : {}),
        ...(eligibleCount !== null ? { eligible_count: eligibleCount } : {}),
    };
}

function formatPromoCountsLabel(campanha) {
    const activeCount = resolvePromoActiveCount(campanha);
    const eligibleCount = resolvePromoEligibleCount(campanha);
    return `Ativos: ${activeCount !== null ? activeCount : '-'} | Elegíveis: ${eligibleCount !== null ? eligibleCount : '-'}`;
}

function hasPromoCounts(campanha) {
    return resolvePromoActiveCount(campanha) !== null || resolvePromoEligibleCount(campanha) !== null;
}

function formatPromoOptionWithCounts(campanha) {
    return hasPromoCounts(campanha) ? `${formatPromoOption(campanha)} · ${formatPromoCountsLabel(campanha)}` : formatPromoOption(campanha);
}

function findApiPromoBCampaignById(id) {
    const target = String(id || '').trim();
    if (!target || !Array.isArray(apiPromoBCampaigns)) return null;
    return apiPromoBCampaigns.find((campanha) => String(campanha?.id || '').trim() === target) || null;
}

function buildApiAnalysisTabLabel(analise, idx) {
    const base = analise?.tab_label || analise?.promo_b_nome || analise?.arquivo_nome || `Campanha ${idx + 1}`;
    const campanhaBase = findApiPromoBCampaignById(analise?.promo_b_id) || {};
    const campanha = {
        ...(analise || {}),
        ...campanhaBase,
        id: analise?.promo_b_id || analise?.id,
    };
    const analiseActive = parsePromoCount(analise?.active_count ?? analise?.activeCount ?? analise?.active);
    const analiseEligible = parsePromoCount(analise?.eligible_count ?? analise?.eligibleCount ?? analise?.eligible);
    if (analiseActive !== null) campanha.active_count = analiseActive;
    if (analiseEligible !== null) campanha.eligible_count = analiseEligible;
    return hasPromoCounts(campanha) ? `${base} · ${formatPromoCountsLabel(campanha)}` : base;
}

async function carregarContagensCampanhasApi(loja) {
    try {
        const query = new URLSearchParams({ loja: String(loja || '').trim() });
        const resp = await fetch(`/api/mercadolivre/promocoes/contagens?${query.toString()}`, {
            headers: getAuthHeadersWithClient(),
        });
        if (!resp.ok) return {};
        const payload = await resp.json().catch(() => ({}));
        const counts = (payload && typeof payload.counts === 'object' && payload.counts) ? payload.counts : {};
        const out = {};
        Object.entries(counts).forEach(([key, value]) => {
            const normalized = normalizePromoCounts(value);
            if (normalized.active !== null || normalized.eligible !== null) {
                out[String(key)] = normalized;
            }
        });
        return out;
    } catch {
        return {};
    }
}

function buildApiPromoBSelectionInfo(campanha) {
    return {
        value: String(campanha?.id || '').trim(),
        promoType: String(campanha?.type || campanha?.promotion_type || '').trim(),
        text: formatPromoOption(campanha),
        displayText: formatPromoOptionWithCounts(campanha),
        matchName: String(campanha?.name || campanha?.title || campanha?.id || '').trim(),
        activeCount: resolvePromoActiveCount(campanha),
        eligibleCount: resolvePromoEligibleCount(campanha),
    };
}

function getApiPromoBSelectedIdSet() {
    if (apiPromoBSelectionReady && apiPromoBSelectedIds instanceof Set) {
        return apiPromoBSelectedIds;
    }
    if (apiPromoBPendingSelectedIds instanceof Set && apiPromoBPendingSelectedIds.size) {
        return apiPromoBPendingSelectedIds;
    }
    return new Set((Array.isArray(apiPromoBCampaigns) ? apiPromoBCampaigns : [])
        .map((campanha) => String(campanha?.id || '').trim())
        .filter(Boolean));
}

function isApiPurchaseCoupon(promoType) {
    return String(promoType || '').trim().toUpperCase() === 'SELLER_COUPON_CAMPAIGN';
}

function getApiPromoACompatibilityError() {
    const select = document.getElementById('apiPromoASelect');
    const tipo = select?.selectedOptions?.[0]?.dataset?.promoType;
    if (select?.value && !isApiPurchaseCoupon(tipo)) select.dataset.couponBlocked = '0';
    if (!isApiPurchaseCoupon(tipo) && !(select?.dataset?.couponBlocked === '1' && !select.value)) return '';
    return 'A Promoção 1 selecionada é um cupom por compra. Esta análise compara preços por anúncio. Selecione uma campanha com preço promocional por anúncio.';
}

function mostrarApiPromoCompatibilityError(message) {
    const errorMsg = document.getElementById('apiErrorMsg');
    if (errorMsg) {
        errorMsg.textContent = message;
        errorMsg.style.display = 'block';
    }
}

function getApiPromoBSelections() {
    const selectedIds = getApiPromoBSelectedIdSet();
    return Array.isArray(apiPromoBCampaigns) ? apiPromoBCampaigns
        .filter((campanha) => !isApiPurchaseCoupon(campanha?.type || campanha?.promotion_type))
        .map((campanha) => buildApiPromoBSelectionInfo(campanha))
        .filter((item) => item.value && selectedIds.has(item.value)) : [];
}

function normalizePromoMatchText(raw) {
    return String(raw || '')
        .normalize('NFD')
        .replace(/[\u0300-\u036f]/g, '')
        .toLowerCase()
        .replace(/\([^)]*\)/g, ' ')
        .replace(/\.(xlsx|xls|csv)$/i, ' ')
        .replace(/[^a-z0-9]+/g, ' ')
        .replace(/\b(started|pending|active|ended|paused|programada|programado|promocao|promocoes|promo|campanha|mercado|livre)\b/g, ' ')
        .replace(/\s+/g, ' ')
        .trim();
}

function buildPromoOfflineDownloadUrl(promoId) {
    const id = String(promoId || '').trim();
    if (!id) return '#';
    return `https://www.mercadolivre.com.br/anuncios/lista/promos/edicao-offline/${encodeURIComponent(id)}/download`;
}

async function openPromoDownloadInChrome(url, event) {
    if (event && typeof event.preventDefault === 'function') event.preventDefault();
    if (event && typeof event.stopPropagation === 'function') event.stopPropagation();
    const target = String(url || '').trim();
    if (!target || target === '#') return false;
    if (window.electronAPI && typeof window.electronAPI.openExternalChrome === 'function') {
        try {
            await window.electronAPI.openExternalChrome(target);
            return false;
        } catch (err) {
            console.warn('Falha ao abrir download no Google Chrome:', err);
        }
    }
    window.open(target, '_blank', 'noopener');
    return false;
}

function extractPromoMatchIds(raw) {
    const txt = String(raw || '').toUpperCase();
    const ids = new Set();
    const regexes = [
        /\bP-MLB\d+\b/g,
        /\bMLB\d+\b/g,
        /\bOFFER-MLB\d+-\d+\b/g,
    ];
    regexes.forEach((regex) => {
        const found = txt.match(regex) || [];
        found.forEach((id) => ids.add(id.trim()));
    });
    return Array.from(ids);
}

function scorePromoFileMatch(fileName, promoName, promoId) {
    const fileIds = extractPromoMatchIds(fileName);
    const promoIds = extractPromoMatchIds(`${promoId || ''} ${promoName || ''}`);
    if (promoIds.length && fileIds.some((id) => promoIds.includes(id))) {
        return 5000;
    }

    const fileNorm = normalizePromoMatchText(fileName);
    const promoNorm = normalizePromoMatchText(promoName);
    if (!fileNorm || !promoNorm) return 0;
    if (fileNorm === promoNorm) return 1000;
    if (fileNorm.includes(promoNorm)) return 900 + promoNorm.length;
    if (promoNorm.includes(fileNorm)) return 800 + fileNorm.length;

    const fileTokens = new Set(fileNorm.split(' ').filter((token) => token.length >= 3));
    const promoTokens = promoNorm.split(' ').filter((token) => token.length >= 3);
    let overlap = 0;
    let chars = 0;
    promoTokens.forEach((token) => {
        if (fileTokens.has(token)) {
            overlap += 1;
            chars += token.length;
        }
    });
    if (!overlap) return 0;

    const coverage = promoTokens.length ? overlap / promoTokens.length : 0;
    if (overlap >= 3 && coverage >= 0.6) return 700 + chars;
    if (overlap >= 2 && coverage >= 0.45) return 500 + chars;
    if (overlap >= 1 && chars >= 8) return 200 + chars;
    return 0;
}

function matchApiPromoFilesToSelections(selecionadas, files) {
    const arquivos = Array.from(files || []);
    const usados = new Set();
    return selecionadas.map((promo) => {
        let best = null;
        let bestScore = 0;
        let empate = false;
        arquivos.forEach((file, idx) => {
            if (!file || usados.has(idx)) return;
            const score = scorePromoFileMatch(file.name, promo.matchName || promo.text || '', promo.value || '');
            if (score > bestScore) {
                best = { file, idx };
                bestScore = score;
                empate = false;
            } else if (score > 0 && score === bestScore) {
                empate = true;
            }
        });
        if (best && !empate && bestScore >= 200) {
            usados.add(best.idx);
            return { promo, file: best.file, matched: true, score: bestScore };
        }
        return { promo, file: null, matched: false, score: bestScore };
    });
}

function hydrateApiPromoBSelections(lista) {
    const validIds = new Set(lista.map((campanha) => String(campanha?.id || '').trim()).filter(Boolean));
    if (apiPromoBPendingSelectedIds instanceof Set && apiPromoBPendingSelectedIds.size) {
        apiPromoBSelectedIds = new Set(Array.from(apiPromoBPendingSelectedIds).filter((id) => validIds.has(id)));
        apiPromoBPendingSelectedIds = null;
        apiPromoBSelectionReady = true;
        return;
    }
    if (!apiPromoBSelectionReady) {
        apiPromoBSelectedIds = new Set(validIds);
        apiPromoBSelectionReady = true;
        return;
    }
    apiPromoBSelectedIds = new Set(Array.from(apiPromoBSelectedIds || []).filter((id) => validIds.has(id)));
}

function formatApiPromoBCardMeta(campanha) {
    const partes = [];
    const status = String(campanha?.status || '').trim();
    const tipo = String(campanha?.type || campanha?.promotion_type || '').trim();
    const id = String(campanha?.id || '').trim();
    const inicio = formatPromoDateShort(campanha?.start_date || campanha?.date_start || campanha?.begin_date);
    const fim = formatPromoDateShort(campanha?.finish_date || campanha?.end_date || campanha?.date_end);
    if (status) partes.push(status);
    if (tipo && tipo !== '-') partes.push(tipo);
    if (inicio && fim) partes.push(`${inicio} a ${fim}`);
    else if (inicio) partes.push(`desde ${inicio}`);
    else if (fim) partes.push(`ate ${fim}`);
    if (id) partes.push(id);
    return partes.join(' · ');
}

function atualizarApiPromoBSelectionSummary() {
    const summary = document.getElementById('apiPromoBSelectionSummary');
    if (!summary) return;
    const selecionadas = getApiPromoBSelections();
    const total = Array.isArray(apiPromoBCampaigns) ? apiPromoBCampaigns.length : 0;
    const totalAtivos = selecionadas.reduce((sum, promo) => promo.activeCount === null ? sum : sum + promo.activeCount, 0);
    const totalElegiveis = selecionadas.reduce((sum, promo) => promo.eligibleCount === null ? sum : sum + promo.eligibleCount, 0);
    const temAtivos = selecionadas.some((promo) => promo.activeCount !== null);
    const temElegiveis = selecionadas.some((promo) => promo.eligibleCount !== null);
    summary.textContent = `${selecionadas.length} de ${total} campanha(s) selecionada(s) para análise. Ativos: ${temAtivos ? totalAtivos : '-'} | Elegíveis: ${temElegiveis ? totalElegiveis : '-'}.`;
}

function salvarApiPromoBSelectionPrefs() {
    saveApiAutoPrefs();
    if (apiAutoInicializada && getApiAutoPrefs().enabled) {
        salvarApiAutoPrefsServidor().catch(() => {});
    }
}

function toggleApiPromoBSelection(input) {
    const id = String(input?.value || '').trim();
    if (!id) return;
    apiPromoBSelectionReady = true;
    if (!(apiPromoBSelectedIds instanceof Set)) apiPromoBSelectedIds = new Set();
    if (input.checked) {
        apiPromoBSelectedIds.add(id);
    } else {
        apiPromoBSelectedIds.delete(id);
    }
    const card = input.closest('.api-promo-card');
    if (card) card.classList.toggle('is-selected', input.checked);
    atualizarApiPromoBSelectionSummary();
    renderApiPromoBFiles();
    salvarApiPromoBSelectionPrefs();
}

function selecionarTodasApiPromoB() {
    apiPromoBSelectedIds = new Set((Array.isArray(apiPromoBCampaigns) ? apiPromoBCampaigns : [])
        .map((campanha) => String(campanha?.id || '').trim())
        .filter(Boolean));
    apiPromoBSelectionReady = true;
    renderApiPromoBButtons(apiPromoBCampaigns);
    salvarApiPromoBSelectionPrefs();
}

function limparSelecaoApiPromoB() {
    apiPromoBSelectedIds = new Set();
    apiPromoBSelectionReady = true;
    renderApiPromoBButtons(apiPromoBCampaigns);
    salvarApiPromoBSelectionPrefs();
}

function renderApiPromoBButtons(campanhas) {
    const wrap = document.getElementById('apiPromoBSelect');
    if (!wrap) return;
    const lista = Array.isArray(campanhas) ? campanhas.filter((campanha) => campanha && campanha.id
        && !isApiPurchaseCoupon(campanha.type || campanha.promotion_type)) : [];
    apiPromoBCampaigns = lista;
    if (!lista.length) {
        apiPromoBCampaigns = [];
        apiPromoBSelectedIds = new Set();
        apiPromoBSelectionReady = false;
        wrap.innerHTML = '<div class="api-promo-empty">Nenhuma campanha criada pelo Mercado Livre.</div>';
        renderApiPromoBFiles();
        return;
    }

    hydrateApiPromoBSelections(lista);
    const selectedIds = getApiPromoBSelectedIdSet();
    wrap.innerHTML = `
        <div class="api-promo-toolbar">
            <div id="apiPromoBSelectionSummary" class="api-promo-selection-summary"></div>
            <div class="api-promo-actions">
                <button class="api-promo-mini-btn" type="button" onclick="selecionarTodasApiPromoB()">Selecionar todas</button>
                <button class="api-promo-mini-btn" type="button" onclick="limparSelecaoApiPromoB()">Limpar</button>
            </div>
        </div>
        <div class="api-promo-card-grid">
            ${lista.map((campanha) => {
                const id = String(campanha?.id || '').trim();
                const nome = String(campanha?.name || campanha?.title || id || 'Campanha sem nome').trim();
                const selecionada = selectedIds.has(id);
                return `
                    <label class="api-promo-card ${selecionada ? 'is-selected' : ''}">
                        <input
                            type="checkbox"
                            value="${escapeHtml(id)}"
                            ${selecionada ? 'checked' : ''}
                            onchange="toggleApiPromoBSelection(this)"
                        >
                        <span class="api-promo-card-body">
                            <span class="api-promo-card-title">${escapeHtml(nome)}</span>
                            <span class="api-promo-card-meta">${escapeHtml(formatApiPromoBCardMeta(campanha))}</span>
                            <span class="api-promo-card-counts">${escapeHtml(formatPromoCountsLabel(campanha))}</span>
                        </span>
                    </label>
                `;
            }).join('')}
        </div>
    `;
    atualizarApiPromoBSelectionSummary();
    renderApiPromoBFiles();
}

function renderApiPromoBFiles() {
    const wrap = document.getElementById('apiPromoBFilesWrap');
    const fileInput = document.getElementById('apiPromoFilesInput');
    if (!wrap) return;

    const selecionadas = getApiPromoBSelections();
    if (!selecionadas.length) {
        wrap.innerHTML = '';
        return;
    }

    const vinculacoes = matchApiPromoFilesToSelections(selecionadas, fileInput?.files || []);
    wrap.innerHTML = vinculacoes.map(({ promo, file, matched }) => `
        <div class="api-file-row ${matched ? 'api-file-row--matched' : 'api-file-row--unmatched'}">
            <label>${escapeHtml(promo.displayText || promo.text)}</label>
            <div class="api-file-badge">Ativos: ${promo.activeCount != null && promo.activeCount !== '' ? promo.activeCount : '-'} | Elegíveis: ${promo.eligibleCount != null && promo.eligibleCount !== '' ? promo.eligibleCount : '-'}</div>
            <div class="api-file-badge ${matched ? 'api-file-badge-ok' : ''}">${matched ? `<span class="api-status-dot"></span> Arquivo anexado: ${escapeHtml(file.name)}` : 'Arquivo não localizado pelo nome'}</div>
            <div style="margin-top:10px;">
                <a
                    class="api-promo-link"
                    href="${buildPromoOfflineDownloadUrl(promo.value)}"
                    onclick="return openPromoDownloadInChrome(this.href, event)"
                    target="_blank"
                    rel="noopener noreferrer"
                >Abrir download</a>
            </div>
        </div>
    `).join('');
}

function renderApiAnalysisTabs() {
    const wrap = document.getElementById('apiAnalysisTabs');
    if (!wrap) return;
    const analises = Array.isArray(apiAnalisesPorCampanha) ? apiAnalisesPorCampanha : [];
    if (!analises.length) {
        wrap.innerHTML = '';
        wrap.style.display = 'none';
        return;
    }
    wrap.style.display = 'flex';
    wrap.style.flexWrap = 'wrap';
    wrap.style.gap = '8px';
    wrap.style.marginBottom = '14px';
    wrap.innerHTML = analises.map((analise, idx) => {
        const confirmada = !!analise?.participacao_confirmada;
        return `
        <div class="api-analysis-tab-group">
            <button
                type="button"
                onclick="ativarAnaliseApiPorCampanha(${idx})"
                style="padding:8px 12px;border-radius:6px 0 0 6px;border:1px solid rgba(126, 181, 231, 0.35);border-right:none;background:${idx === apiAnaliseAtiva ? '#2e9fff' : '#17324d'};color:#ffffff;cursor:pointer;font-weight:700;"
            >${escapeHtml(buildApiAnalysisTabLabel(analise, idx))}</button>
            <button
                type="button"
                title="Baixar planilha: ${(analise.arquivo_nome || ('analise_' + (idx + 1) + '.xlsx')).replace(/"/g, '')}"
                onclick="exportarAnaliseApi(${idx})"
                style="padding:8px 10px;border-radius:0;border:1px solid rgba(126, 181, 231, 0.35);background:${idx === apiAnaliseAtiva ? '#1a7acc' : '#0f2236'};color:#7ee8a2;cursor:pointer;font-weight:700;font-size:1rem;"
            >⬇</button>
            <button
                type="button"
                class="api-tab-confirm ${confirmada ? 'is-done' : ''}"
                title="Confirmar participação desta campanha no Mercado Livre"
                onclick="confirmarParticipacaoCampanhaApi(${idx})"
            >${confirmada ? 'Confirmada' : 'Confirmar'}</button>
        </div>
    `;
    }).join('');
}

function ativarAnaliseApiPorCampanha(index) {
    const analise = Array.isArray(apiAnalisesPorCampanha) ? apiAnalisesPorCampanha[index] : null;
    if (!analise) return;
    apiAnaliseAtiva = index;
    currentData = normalizeApiDatasetRules(Array.isArray(analise.data) ? analise.data : []);
    planilhaGeradaAtual = analise.planilha_gerada || null;
    renderApiAnalysisTabs();
    renderTable(currentData);
}

function buildApiErrorMessage(resp, payload, fallback) {
    const status = resp && resp.status ? `HTTP ${resp.status}` : '';
    const detail = (payload && (payload.detail || payload.error || payload.message)) || '';
    if (status && detail) return `${fallback} (${status}). ${detail}`;
    if (status) return `${fallback} (${status}).`;
    if (detail) return `${fallback}. ${detail}`;
    return fallback;
}

function clampApiProgress(value) {
    const number = Number(value);
    if (!Number.isFinite(number)) return 0;
    return Math.max(0, Math.min(100, Math.round(number)));
}

function getLatestApiLogMessage(logs) {
    const list = Array.isArray(logs) ? logs : [];
    for (let i = list.length - 1; i >= 0; i -= 1) {
        const message = String(list[i]?.message || '').trim();
        if (message) return message;
    }
    return '';
}

function aplicarOcultacaoApiStatusBar(bar) {
    if (!bar) return;
    bar.classList.remove('is-active', 'is-complete', 'is-error');
    bar.classList.add('is-hidden');
    bar.setAttribute('aria-hidden', 'true');
    bar.style.display = 'none';
    document.body.classList.remove('api-status-active');
}

function deveMostrarApiStatusBar(status) {
    return ['queued', 'running', 'processing', 'pending', 'completed', 'canceled', 'cancelled', 'error'].includes(String(status || '').toLowerCase());
}

function prepararBotaoApiStatusBar() {
    const btn = document.getElementById('apiStatusCloseBtn');
    if (!btn || btn.dataset.bound === '1') return;
    btn.dataset.bound = '1';
    btn.addEventListener('click', (event) => ocultarApiStatusBar(true, event));
}

function atualizarApiStatusBar(payload = {}) {
    const bar = document.getElementById('apiStatusBar');
    const titleEl = document.getElementById('apiStatusTitle');
    const messageEl = document.getElementById('apiStatusMessage');
    const percentEl = document.getElementById('apiStatusPercent');
    const progressEl = document.getElementById('apiStatusProgress');
    const detailEl = document.getElementById('apiStatusDetail');
    if (!bar || !titleEl || !messageEl || !percentEl || !progressEl || !detailEl) return;

    if (apiStatusFechadoManualmente && !payload.force) {
        aplicarOcultacaoApiStatusBar(bar);
        return;
    }

    if (apiStatusHideTimer) {
        clearTimeout(apiStatusHideTimer);
        apiStatusHideTimer = null;
    }

    const status = String(payload.status || '').toLowerCase();
    const progress = clampApiProgress(payload.progress);
    const message = String(payload.message || '').trim() || 'Processando analise via API...';
    const latestLog = getLatestApiLogMessage(payload.logs);
    const updatedAt = Number(payload.updated_at || 0);
    const ageText = updatedAt > 0 ? `Atualizado ha ${Math.max(0, Math.floor(Date.now() / 1000 - updatedAt))}s` : '';

    if (!deveMostrarApiStatusBar(status)) {
        aplicarOcultacaoApiStatusBar(bar);
        return;
    }

    let title = 'Analise via API';
    if (status === 'queued') title = 'Na fila';
    if (status === 'running') title = 'Analisando via API';
    if (status === 'completed') title = 'Analise concluida';
    if (status === 'canceled' || status === 'cancelled') title = 'Analise cancelada';
    if (status === 'error') title = 'Erro na analise';
    if (payload.title) title = String(payload.title);

    bar.style.display = '';
    bar.classList.remove('is-hidden');
    bar.classList.add('is-active');
    bar.setAttribute('aria-hidden', 'false');
    bar.classList.toggle('is-complete', status === 'completed');
    bar.classList.toggle('is-error', status === 'error');
    document.body.classList.add('api-status-active');
    titleEl.textContent = title;
    messageEl.textContent = message;
    percentEl.textContent = `${progress}%`;
    progressEl.style.width = `${progress}%`;
    const detailOverride = String(payload.detail || '').trim();
    detailEl.textContent = detailOverride || [latestLog ? `Ultimo passo: ${latestLog}` : '', ageText].filter(Boolean).join(' | ');

    if (status === 'completed' || status === 'canceled' || status === 'cancelled') {
        ocultarApiStatusBarDepois(2500);
    }
}

function ocultarApiStatusBar(manual = false, event = null) {
    if (event) {
        event.preventDefault();
        event.stopPropagation();
    }
    const bar = document.getElementById('apiStatusBar');
    if (!bar) return;
    if (manual) apiStatusFechadoManualmente = true;
    if (apiStatusHideTimer) {
        clearTimeout(apiStatusHideTimer);
        apiStatusHideTimer = null;
    }
    aplicarOcultacaoApiStatusBar(bar);
}

function ocultarApiStatusBarDepois(delayMs = 3800) {
    if (apiStatusHideTimer) clearTimeout(apiStatusHideTimer);
    apiStatusHideTimer = setTimeout(() => {
        apiStatusHideTimer = null;
        ocultarApiStatusBar(false);
    }, Math.max(0, Number(delayMs) || 0));
}

function pararStatusParticipacaoPromocoes() {
    if (promoParticipacaoStatusTimer) {
        clearInterval(promoParticipacaoStatusTimer);
        promoParticipacaoStatusTimer = null;
    }
}

function iniciarStatusParticipacaoPromocoes({ nome, total, loja }) {
    pararStatusParticipacaoPromocoes();
    if (apiStatusHideTimer) {
        clearTimeout(apiStatusHideTimer);
        apiStatusHideTimer = null;
    }
    apiStatusFechadoManualmente = false;

    const totalItens = Math.max(0, Number(total) || 0);
    const nomeCampanha = String(nome || 'campanha selecionada').trim();
    const lojaTexto = String(loja || '').trim();
    const detalhe = [lojaTexto ? `Loja: ${lojaTexto}` : '', `${totalItens} anúncio(s) na fila`].filter(Boolean).join(' | ');
    const mensagens = [
        `Preparando entrada na campanha ${nomeCampanha}...`,
        'Validando anúncios marcados como Participar...',
        'Enviando solicitação para o Mercado Livre...',
        'Aguardando processamento da API...',
        'Conferindo retorno da confirmação...',
    ];
    let tick = 0;
    let progress = 6;

    const atualizar = () => {
        const elapsed = tick * 850;
        const limite = elapsed < 3500 ? 62 : elapsed < 9000 ? 84 : 94;
        const incremento = elapsed < 3500 ? 9 : elapsed < 9000 ? 4 : 1;
        progress = Math.min(limite, progress + incremento);
        const mensagem = mensagens[Math.min(mensagens.length - 1, Math.floor(tick / 2))];
        atualizarApiStatusBar({
            status: 'running',
            progress,
            title: 'Entrada na campanha',
            message: mensagem,
            detail: detalhe,
        });
        tick += 1;
    };

    atualizar();
    promoParticipacaoStatusTimer = setInterval(atualizar, 850);
}

function concluirStatusParticipacaoPromocoes({ nome, result }) {
    pararStatusParticipacaoPromocoes();
    const sucesso = Number(result?.total_sucesso || 0);
    const falha = Number(result?.total_falha || 0);
    const ignorados = Number(result?.total_ignorados || 0);
    const partes = [`${sucesso} sucesso(s)`, `${falha} falha(s)`];
    if (ignorados) partes.push(`${ignorados} ignorado(s)`);
    atualizarApiStatusBar({
        status: 'completed',
        progress: 100,
        title: 'Entrada concluida',
        message: partes.join(', ') + '.',
        detail: `Campanha: ${String(nome || 'campanha selecionada')}`,
        force: true,
    });
    ocultarApiStatusBarDepois(3500);
}

function falharStatusParticipacaoPromocoes({ nome, erro }) {
    pararStatusParticipacaoPromocoes();
    atualizarApiStatusBar({
        status: 'error',
        progress: 100,
        title: 'Erro na entrada',
        message: String(erro || 'Erro ao confirmar participação.'),
        detail: `Campanha: ${String(nome || 'campanha selecionada')}`,
        force: true,
    });
}

function renderApiJobDetails(payload) {
    const detailsEl = document.getElementById('apiLoadingDetails');
    if (!detailsEl) return;
    const logs = Array.isArray(payload?.logs) ? payload.logs : [];
    const atualizadoEm = Number(payload?.updated_at || 0);
    const linhas = [];
    if (atualizadoEm > 0) {
        const segundos = Math.max(0, Math.floor(Date.now() / 1000 - atualizadoEm));
        linhas.push(`Última atualização: há ${segundos}s`);
    }
    if (logs.length) {
        const ultimas = logs.slice(-6);
        ultimas.forEach((log) => {
            const msg = String(log?.message || '').trim();
            if (!msg) return;
            const pct = Number(log?.progress || 0);
            linhas.push(`• ${msg}${pct ? ` (${pct}%)` : ''}`);
        });
    }
    if (!linhas.length) {
        detailsEl.style.display = 'none';
        detailsEl.innerHTML = '';
        return;
    }
    detailsEl.style.display = 'block';
    detailsEl.innerHTML = linhas.map((l) => `<div>${l}</div>`).join('');
}

async function acompanharJobAnaliseApi(jobId) {
    const loading = document.getElementById('apiLoading');
    const loadingDetails = document.getElementById('apiLoadingDetails');
    const errorMsg = document.getElementById('apiErrorMsg');
    const resultsArea = document.getElementById('resultsArea');
    if (!jobId) return null;
    apiAnaliseJobId = String(jobId || '').trim();
    limparPollingAnaliseApi();
    apiAnaliseResolveAtual = null;

    const consultar = async () => {
        try {
            if (apiAnaliseCancelada) {
                return { status: 'canceled', payload: { message: 'Verificacao cancelada pelo usuario.' } };
            }
            const payload = await consultarProgressoAnaliseApi(jobId);
            const status = String(payload.status || '').toLowerCase();
            const progress = Number(payload.progress || 0);
            const message = payload.message || 'Processando análise em segundo plano...';
            loading.textContent = `${message} ${progress ? `(${progress}%)` : ''}`.trim();
            loading.style.display = 'block';
            renderApiJobDetails(payload);
            atualizarApiStatusBar({
                status,
                progress,
                message,
                logs: payload.logs,
                updated_at: payload.updated_at,
            });

            if (status === 'completed') {
                limparPollingAnaliseApi();
                setApiCancelButtonVisible(false);
                const result = payload.result || {};
                const jobIdAtual = String(payload.job_id || jobId || apiAnaliseJobId || '').trim();
                apiAnaliseJobId = jobIdAtual;
                apiAnalisesPorCampanha = Array.isArray(result.analises) ? result.analises : [];
                apiAnalisesPorCampanha.forEach((analise) => {
                    if (analise && jobIdAtual) {
                        analise.job_id = jobIdAtual;
                    }
                    if (analise && Array.isArray(analise.data)) {
                        analise.data = normalizeApiDatasetRules(analise.data);
                    }
                });
                const primeiraComDados = apiAnalisesPorCampanha.findIndex((analise) => Array.isArray(analise?.data) && analise.data.length > 0);
                apiAnaliseAtiva = primeiraComDados >= 0 ? primeiraComDados : 0;
                const analiseInicial = apiAnalisesPorCampanha[apiAnaliseAtiva] || null;
                currentData = normalizeApiDatasetRules(analiseInicial?.data || result.data || []);
                const concluidaSemLinhas = currentData.length === 0;
                mlFileName = null;
                planilhaGeradaAtual = analiseInicial?.planilha_gerada || result.planilha_gerada || null;
                garantirColunasVisiveis(['Preço Final', 'Preço Final ML']);
                pendingWidthPrefs = {
                    ...loadColumnWidthPrefs(),
                    ...(pendingWidthPrefs || {}),
                };
                renderTable(currentData);
                renderApiAnalysisTabs();
                loading.style.display = 'none';
                if (loadingDetails) {
                    loadingDetails.style.display = 'none';
                    loadingDetails.innerHTML = '';
                }
                resultsArea.style.display = 'block';
                atualizarApiStatusBar({
                    status: 'completed',
                    progress: 100,
                    message: concluidaSemLinhas
                        ? 'Analise concluida sem anuncios para exibir.'
                        : (payload.message || 'Analise concluida.'),
                    logs: payload.logs,
                    updated_at: payload.updated_at,
                });
                ocultarApiStatusBarDepois(2500);
                return { status: 'completed', payload, result };
            } else if (status === 'canceled' || status === 'cancelled') {
                limparPollingAnaliseApi();
                setApiCancelButtonVisible(false);
                loading.style.display = 'none';
                if (loadingDetails) {
                    loadingDetails.style.display = 'none';
                    loadingDetails.innerHTML = '';
                }
                errorMsg.style.display = 'none';
                atualizarApiStatusBar({
                    status: 'canceled',
                    progress: 100,
                    message: payload.message || 'Verificacao cancelada pelo usuario.',
                    logs: payload.logs,
                    updated_at: payload.updated_at,
                });
                ocultarApiStatusBarDepois(2500);
                atualizarStatusAutomacaoPromo('Verificacao cancelada.');
                return { status: 'canceled', payload };
            } else if (status === 'error') {
                limparPollingAnaliseApi();
                setApiCancelButtonVisible(false);
                loading.style.display = 'none';
                if (loadingDetails) {
                    loadingDetails.style.display = 'none';
                    loadingDetails.innerHTML = '';
                }
                errorMsg.textContent = payload.error || payload.message || 'Erro ao processar a análise em segundo plano.';
                errorMsg.style.display = 'block';
                atualizarApiStatusBar({
                    status: 'error',
                    progress: 100,
                    message: errorMsg.textContent,
                    logs: payload.logs,
                    updated_at: payload.updated_at,
                });
                return { status: 'error', payload, error: errorMsg.textContent };
            }
        } catch (e) {
            limparPollingAnaliseApi();
            setApiCancelButtonVisible(false);
            loading.style.display = 'none';
            if (loadingDetails) {
                loadingDetails.style.display = 'none';
                loadingDetails.innerHTML = '';
            }
            errorMsg.textContent = e.message || 'Erro ao consultar o andamento da análise.';
            errorMsg.style.display = 'block';
            atualizarApiStatusBar({
                status: 'error',
                progress: 100,
                message: errorMsg.textContent,
            });
            return { status: 'error', error: errorMsg.textContent };
        }
        return null;
    };

    const finalizado = await consultar();
    if (finalizado) return finalizado;
    return new Promise((resolve) => {
        apiAnaliseResolveAtual = resolve;
        apiAnaliseJobPolling = setInterval(async () => {
            const final = await consultar();
            if (final) {
                apiAnaliseResolveAtual = null;
                resolve(final);
            }
        }, 3000);
    });
}

async function carregarPromocoesApi() {
    const loja = document.getElementById('apiLojaSelect')?.value || '';
    const selectA = document.getElementById('apiPromoASelect');
    const selectB = document.getElementById('apiPromoBSelect');
    if (!selectA || !selectB) return;
    const selecaoAnteriorEraCupom = isApiPurchaseCoupon(selectA.selectedOptions?.[0]?.dataset?.promoType)
        || (selectA.dataset.couponBlocked === '1' && !selectA.value);
    selectA.innerHTML = '<option value="">Carregando...</option>';
    selectB.innerHTML = '<div class="api-promo-empty">Carregando...</div>';
    apiPromoBCampaigns = [];
    apiPromoBSelectedIds = new Set();
    apiPromoBSelectionReady = false;
    renderApiPromoBFiles();
    if (!loja) return;
    try {
        const errorMsg = document.getElementById('apiErrorMsg');
        if (errorMsg) {
            errorMsg.textContent = '';
            errorMsg.style.display = 'none';
        }
        const query = new URLSearchParams({ loja });
        const resp = await fetch(`/api/mercadolivre/promocoes?${query.toString()}`, { headers: getAuthHeadersWithClient() });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok || data.success === false) {
            throw new Error(buildApiErrorMessage(resp, data, 'Erro ao carregar promoções'));
        }
        const campanhas = Array.isArray(data.campaigns) ? data.campaigns : [];
        const campanhasEnriquecidas = campanhas.map((campanha) => applyPromoCounts(campanha));
        selectA.innerHTML = '';
        const campanhasCompativeis = campanhasEnriquecidas.filter((campanha) => !isApiPurchaseCoupon(campanha.type || campanha.promotion_type));
        const campanhasA = campanhasCompativeis.filter((campanha) => promoSelectionGroup(campanha) === 'usuario');
        const campanhasB = campanhasCompativeis.filter((campanha) => isCampanhaMercadoLivre(campanha));
        campanhasA.forEach((campanha) => {
            if (!campanha || !campanha.id) return;
            const optA = document.createElement('option');
            optA.value = campanha.id;
            optA.textContent = formatPromoOption(campanha);
            const promoType = campanha.type || campanha.promotion_type || '';
            optA.dataset.promoType = promoType === '-' ? '' : promoType;
            selectA.appendChild(optA);
        });
        if (!campanhasA.length) {
            selectA.innerHTML = '<option value="">Nenhuma promoção do usuário</option>';
        }
        if (selecaoAnteriorEraCupom) {
            selectA.value = '';
            selectA.dataset.couponBlocked = '1';
            mostrarApiPromoCompatibilityError(getApiPromoACompatibilityError());
        }
        renderApiPromoBButtons(campanhasB);
        if (apiAutoInicializada && getApiAutoPrefs().enabled) {
            salvarApiAutoPrefsServidor().catch(() => {});
        }
        carregarContagensCampanhasApi(loja).then((countsByCampaignId) => {
            if (!countsByCampaignId || !Object.keys(countsByCampaignId).length) return;
            if ((document.getElementById('apiLojaSelect')?.value || '') !== loja) return;
            const campanhasComContagem = campanhasB.map((campanha) => applyPromoCounts(campanha, countsByCampaignId));
            renderApiPromoBButtons(campanhasComContagem);
            if (apiAutoInicializada && getApiAutoPrefs().enabled) {
                salvarApiAutoPrefsServidor().catch(() => {});
            }
        }).catch(() => {});
    } catch (e) {
        const mensagem = e?.message || 'Erro ao carregar promoções';
        selectA.innerHTML = `<option value="">${escapeHtml(mensagem)}</option>`;
        selectB.innerHTML = `<div class="api-promo-empty">${escapeHtml(mensagem)}</div>`;
        const errorMsg = document.getElementById('apiErrorMsg');
        if (errorMsg) {
            errorMsg.textContent = mensagem;
            errorMsg.style.display = 'block';
        }
        renderApiPromoBFiles();
    }
}

async function processarAnaliseApi(opcoes = {}) {
    const automatico = !!opcoes.automatico;
    const loja = document.getElementById('apiLojaSelect')?.value || '';
    const selectPromoA = document.getElementById('apiPromoASelect');
    const promocaoA = selectPromoA?.value || '';
    const promocaoAType = selectPromoA?.selectedOptions?.[0]?.dataset?.promoType || '';
    let promoBOptions = getApiPromoBSelections();
    const margemMinima = Number(document.getElementById('apiMargemMinima')?.value || 15);
    const margemTolerancia = getActionTolerancePct();
    const loading = document.getElementById('apiLoading');
    const loadingDetails = document.getElementById('apiLoadingDetails');
    const errorMsg = document.getElementById('apiErrorMsg');
    const resultsArea = document.getElementById('resultsArea');
    const tabsWrap = document.getElementById('apiAnalysisTabs');

    const incompatibilidade = getApiPromoACompatibilityError();
    if (incompatibilidade) {
        mostrarApiPromoCompatibilityError(incompatibilidade);
        if (automatico) atualizarStatusAutomacaoPromo(incompatibilidade);
        return { status: 'error', error: incompatibilidade };
    }

    if (!loja || !promocaoA || !promoBOptions.length) {
        if (automatico) {
            atualizarStatusAutomacaoPromo('Automatico aguardando loja e promocoes carregadas.');
            return null;
        }
        alert('Selecione a loja, a Promoção 1 e pelo menos uma Promoção 2.');
        return null;
    }

    saveColumnPrefs();
    savePagePrefs();
    pendingWidthPrefs = {
        ...loadColumnWidthPrefs(),
        ...getColumnWidthPrefsFromDom(),
    };
    mergeAndSaveColumnWidthPrefs(pendingWidthPrefs);
    saveColumnWidthPrefsFromDomRobust(true);

    loading.style.display = 'block';
    loading.textContent = 'Enviando análise para processamento em segundo plano...';
    apiAnaliseCancelada = false;
    if (!automatico) apiStatusFechadoManualmente = false;
    setApiCancelButtonVisible(true);
    atualizarApiStatusBar({
        status: 'queued',
        progress: 3,
        message: 'Enviando analise para processamento em segundo plano...',
    });
    if (loadingDetails) {
        loadingDetails.style.display = 'none';
        loadingDetails.innerHTML = '';
    }
    errorMsg.style.display = 'none';
    resultsArea.style.display = 'none';
    if (tabsWrap) {
        tabsWrap.innerHTML = '';
        tabsWrap.style.display = 'none';
    }
    apiAnalisesPorCampanha = [];
    apiAnaliseAtiva = 0;
    limparPollingAnaliseApi();
    uploadedFilesMap = {};

    try {
        await carregarColumnWidthPrefsServidor();
        if (promoBOptions.some((promo) => promo.activeCount === null || promo.eligibleCount === null)) {
            const countsByCampaignId = await carregarContagensCampanhasApi(loja);
            if (countsByCampaignId && Object.keys(countsByCampaignId).length) {
                apiPromoBCampaigns = apiPromoBCampaigns.map((campanha) => applyPromoCounts(campanha, countsByCampaignId));
                renderApiPromoBButtons(apiPromoBCampaigns);
                promoBOptions = getApiPromoBSelections();
            }
        }
        const formData = new FormData();
        formData.append('loja', loja);
        formData.append('promocao_a_id', promocaoA);
        formData.append('promocao_a_type', promocaoAType || '');
        formData.append('margem_minima', String(Number.isFinite(margemMinima) ? margemMinima : 15));
        formData.append('margem_tolerancia', String(margemTolerancia));
        formData.append('promocoes_b_meta', JSON.stringify(promoBOptions.map((promo) => ({
            promo_b_id: promo.value,
            promo_b_type: promo.promoType || '',
            promo_texto: promo.text || promo.matchName || promo.value,
            active_count: promo.activeCount,
            eligible_count: promo.eligibleCount,
        }))));

        const resp = await fetch('/api/promo/analise-via-api/start', {
            method: 'POST',
            headers: getAuthHeadersWithClient(),
            body: formData,
        });
        const result = await resp.json().catch(() => ({}));
        if (!resp.ok || !result.success) {
            throw new Error(buildApiErrorMessage(resp, result, 'Erro ao analisar promoções via API'));
        }
        apiAnaliseJobId = String(result.job_id || '').trim();
        if (apiAnaliseCancelada) {
            try {
                await cancelarJobAnaliseApi(apiAnaliseJobId);
            } catch (_e) {}
            setApiCancelButtonVisible(false);
            return { status: 'canceled' };
        }
        loading.textContent = result.message || 'Análise iniciada em segundo plano.';
        atualizarApiStatusBar({
            status: 'queued',
            progress: 5,
            message: result.message || 'Analise iniciada em segundo plano.',
        });
        const acompanhamento = await acompanharJobAnaliseApi(result.job_id);
        setApiCancelButtonVisible(false);
        if (acompanhamento?.status === 'completed' && !apiAnaliseCancelada) {
            await executarParticipacaoAutomaticaPromocoes();
        }
        return acompanhamento;
    } catch (e) {
        setApiCancelButtonVisible(false);
        loading.style.display = 'none';
        errorMsg.textContent = e.message || 'Erro ao iniciar a análise em segundo plano.';
        errorMsg.style.display = 'block';
        atualizarApiStatusBar({
            status: 'error',
            progress: 100,
            message: errorMsg.textContent,
        });
        if (automatico) atualizarStatusAutomacaoPromo(`Erro no automatico: ${errorMsg.textContent}`);
        return null;
    }
}

function setProcessingOverlay(active, message) {
    const overlay = document.getElementById('processingOverlay');
    const subtitle = document.getElementById('processingSubtitle');
    if (!overlay) return;
    if (subtitle && message) subtitle.textContent = message;
    overlay.classList.toggle('is-active', !!active);
}
