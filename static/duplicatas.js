/* ==========================================================================
 * Painel de Duplicatas — visão por par de escolas (INEP A × INEP B).
 *
 * Toda a filtragem é feita no servidor (/api/painel/<run_id>), então a tela
 * sempre reflete o conjunto completo da execução e não só a página carregada.
 * ========================================================================== */

const $ = (id) => document.getElementById(id);
const num = (v) => (Number(v) || 0).toLocaleString('pt-BR');

let runAtual = null;
let paginaAtual = 1;
let imagensPorPar = {};   // par_id -> imagens, para o lightbox navegar

/* --------------------------- inicialização ----------------------------- */


document.addEventListener('DOMContentLoaded', async () => {
    ligarEventos();
    await carregarExecucoes();
    if (runAtual) carregar(1);
});

function ligarEventos() {
    $('btnFiltrar').addEventListener('click', () => carregar(1));
    $('btnLimpar').addEventListener('click', limparFiltros);
    $('btnCsv').addEventListener('click', baixarCsv);
    $('btnZip').addEventListener('click', () => {
        if (runAtual) window.location.href = `/api/historico/${encodeURIComponent(runAtual)}/zip`;
    });
    $('runSelect').addEventListener('change', (e) => {
        runAtual = e.target.value;
        carregar(1);
    });
    $('fLimit').addEventListener('change', () => carregar(1));

    // Enter nos campos de texto aplica o filtro
    ['fTexto', 'fBusca'].forEach((id) =>
        $(id).addEventListener('keydown', (e) => { if (e.key === 'Enter') carregar(1); }));

    // Os seletores aplicam na hora — são escolhas discretas, não digitação
    ['fUf', 'fMunicipio', 'fFornecedor', 'fTipo', 'fFp',
     'fMinImagens', 'fFotos', 'fMesmoMunicipio', 'fGravidade'].forEach((id) =>
        $(id).addEventListener('change', () => carregar(1)));

    $('lbFechar').addEventListener('click', fecharLightbox);
    $('lb').addEventListener('click', (e) => { if (e.target.id === 'lb') fecharLightbox(); });
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape') fecharLightbox(); });
}

async function carregarExecucoes() {
    try {
        const r = await fetch('/api/painel/execucoes');
        const d = await r.json();
        const runs = d.runs || [];
        const sel = $('runSelect');

        if (!runs.length) {
            sel.innerHTML = '<option value="">Nenhuma execução concluída</option>';
            $('runInfo').textContent = 'Rode uma análise na aba Análise por Fase primeiro.';
            return;
        }

        sel.innerHTML = runs.map((r) => {
            const m = r.metricas || {};
            return `<option value="${r.run_id}">Fase ${r.fase} · ${r.iniciado_em} · ` +
                   `${num(m.grupos_duplicatas)} grupos</option>`;
        }).join('');
        runAtual = runs[0].run_id;
    } catch (e) {
        $('runInfo').textContent = 'Erro ao carregar execuções: ' + e.message;
    }
}

/* ------------------------------ filtros -------------------------------- */

function filtrosAtuais() {
    const modo = $('fModoTexto').value;
    const texto = $('fTexto').value.trim();
    return {
        busca: $('fBusca').value.trim(),
        contem: modo === 'contem' ? texto : '',
        nao_contem: modo === 'nao_contem' ? texto : '',
        uf: $('fUf').value,
        municipio: $('fMunicipio').value,
        fornecedor: $('fFornecedor').value,
        tipo: $('fTipo').value,
        falso_positivo: $('fFp').value,
        min_imagens: $('fMinImagens').value,
        somente_fotos: $('fFotos').value,
        mesmo_municipio: $('fMesmoMunicipio').value,
        gravidade: $('fGravidade').value,
    };
}

function queryString(extra) {
    const p = new URLSearchParams({ ...filtrosAtuais(), ...(extra || {}) });
    return p.toString();
}

function limparFiltros() {
    ['fBusca', 'fTexto'].forEach((id) => ($(id).value = ''));
    ['fUf', 'fMunicipio', 'fFornecedor', 'fTipo', 'fMesmoMunicipio',
     'fGravidade'].forEach((id) => ($(id).value = ''));
    $('fModoTexto').value = 'nao_contem';
    $('fFp').value = 'ocultar';
    $('fMinImagens').value = '0';
    $('fFotos').value = '';
    carregar(1);
}

/* ------------------------------ carregar ------------------------------- */

async function carregar(pagina) {
    if (!runAtual) return;
    paginaAtual = pagina || 1;

    $('tbody').innerHTML = `<tr><td colspan="7" class="empty">
        <i class="fa-solid fa-spinner spinner"></i> Carregando…</td></tr>`;

    try {
        const qs = queryString({ page: paginaAtual, limit: $('fLimit').value });
        const r = await fetch(`/api/painel/${encodeURIComponent(runAtual)}?${qs}`);
        const d = await r.json();

        if (!r.ok) {
            $('tbody').innerHTML = `<tr><td colspan="7" class="empty">${d.error || 'Erro'}</td></tr>`;
            return;
        }

        renderInfo(d);
        renderCards(d);
        renderBarras(d);
        preencherOpcoes(d.opcoes);
        renderTabela(d);
        renderPager(d);
    } catch (e) {
        $('tbody').innerHTML = `<tr><td colspan="7" class="empty">Erro: ${e.message}</td></tr>`;
    }
}

function renderInfo(d) {
    const run = d.run || {};
    $('runInfo').textContent =
        `Fase ${run.fase} · ${run.fornecedor || 'todos os fornecedores'} · ` +
        `executado em ${run.iniciado_em} · ${run.criterio || ''}`;
}

function renderCards(d) {
    const r = d.resumo, g = d.resumo_geral;
    const filtrado = r.pares !== g.pares;

    const cards = [
        ['is-green', 'Pares de escolas', r.pares,
         filtrado ? `de ${num(g.pares)} no total` : 'INEP A × INEP B'],
        ['is-blue', 'Escolas envolvidas', r.escolas, 'INEPs distintos'],
        ['is-amber', 'Imagens duplicadas', r.imagens,
         `${num(r.fotos_camera)} são fotos de câmera`],
        ['is-purple', 'Mesmo PDF nos dois', r.mesmo_pdf,
         `${num(r.pdf_divergente)} com PDF de outra escola`],
        ['is-red', 'Falsos positivos', g.falsos_positivos,
         `${num(g.pendentes)} pares pendentes de triagem`],
    ];

    $('cards').innerHTML = cards.map(([cls, label, valor, hint]) => `
        <div class="card stat ${cls}">
            <div class="label">${label}</div>
            <div class="value">${num(valor)}</div>
            <div class="hint">${hint}</div>
        </div>`).join('');
}

function renderBarras(d) {
    const desenhar = (destino, dados, cor) => {
        if (!dados.length) {
            $(destino).innerHTML = '<div class="empty" style="padding:1.5rem;">Sem dados</div>';
            return;
        }
        const max = dados[0].valor || 1;
        $(destino).innerHTML = dados.map((x) => `
            <div class="bar-row">
                <span class="nome" title="${x.nome}">${x.nome}</span>
                <span class="bar-track">
                    <span class="bar-fill" style="width:${(x.valor / max) * 100}%;
                          background:${cor};"></span>
                </span>
                <span class="num">${num(x.valor)}</span>
            </div>`).join('');
    };

    desenhar('barsForn', d.resumo.por_fornecedor, 'linear-gradient(90deg,#e8a33d,#c07c15)');
    desenhar('barsUf', d.resumo.por_uf, 'linear-gradient(90deg,#4d93f0,#1857c0)');
    $('metaForn').textContent = `${d.resumo.por_fornecedor.length} fornecedores`;
    $('metaUf').textContent = `${d.resumo.por_uf.length} estados`;
}

function preencherOpcoes(o) {
    if (!o) return;
    const encher = (id, itens, rotulo) => {
        const sel = $(id), atual = sel.value;
        sel.innerHTML = `<option value="">${rotulo}</option>` +
            itens.map((x) => `<option value="${x}">${x}</option>`).join('');
        sel.value = atual;    // não perde a escolha ao recarregar
    };
    encher('fUf', o.ufs, 'Todos os estados');
    encher('fMunicipio', o.municipios, 'Todos os municípios');
    encher('fFornecedor', o.fornecedores, 'Todos os fornecedores');
}

/* ------------------------------- tabela -------------------------------- */

const MAX_THUMBS = 6;

function renderTabela(d) {
    const pares = d.pares || [];
    $('tableCount').innerHTML = pares.length
        ? `Exibindo <strong>${num(pares.length)}</strong> de <strong>${num(d.total)}</strong> pares`
        : 'Nenhum par com os filtros atuais';

    if (!pares.length) {
        $('tbody').innerHTML = `<tr><td colspan="7" class="empty">
            <div class="big">🔍</div>Nenhum par encontrado com os filtros atuais.
            <div style="margin-top:.5rem; font-size:.85rem;">
                Tente limpar os filtros ou mostrar os falsos positivos.</div></td></tr>`;
        return;
    }

    imagensPorPar = {};
    pares.forEach((p) => (imagensPorPar[p.par_id] = p.imagens));

    $('tbody').innerHTML = pares.map((p) => `
        <tr class="${p.falso_positivo ? 'is-fp' : ''}" data-par="${p.par_id}">
            <td class="cel-de">${celEscola(p.escola_a, p)}</td>
            <td class="cel-de">${celPdfs(p.pdfs_a, p.escola_a.inep)}</td>
            <td class="cel-meio">${celEvidencias(p)}</td>
            <td class="cel-meio">${celThumbs(p)}</td>
            <td class="cel-para">${celEscola(p.escola_b, p)}</td>
            <td class="cel-para">${celPdfs(p.pdfs_b, p.escola_b.inep)}</td>
            <td>${celToggle(p)}</td>
        </tr>`).join('');

    $('tbody').querySelectorAll('.tg input').forEach((el) =>
        el.addEventListener('change', (e) => marcarFalsoPositivo(
            e.target.dataset.par, e.target.checked, e.target)));
}

function celEscola(e, p) {
    return `
        <div class="inep">${e.inep}</div>
        <div class="escola-nome">${e.escola || '<span style="color:var(--text-3)">nome não identificado</span>'}</div>
        <div class="escola-meta">
            ${e.uf ? `<span class="tag tag-uf">${e.uf}</span>` : ''}
            ${e.municipio || ''}
            ${p.mesmo_municipio ? '<span class="tag tag-mesmo">mesmo município</span>' : ''}
        </div>
        <div class="escola-meta">
            ${e.fornecedor ? `<span class="tag tag-forn">${
                e.fornecedor.replace(/ \(RI\)| \(RE\)/g, '')}</span>` : ''}
        </div>`;
}

function celPdfs(pdfs, inep) {
    if (!pdfs || !pdfs.length) return '<span style="color:var(--text-3)">—</span>';
    return `<div class="pdf-list">` + pdfs.map((f) => {
        const pag = (f.paginas && f.paginas.length) ? f.paginas[0] : 1;
        const lista = (f.paginas || []).join(', ');
        const aviso = f.inep_divergente
            ? ` — ATENÇÃO: o arquivo é da escola ${f.inep_no_nome}` : '';
        return `<button class="btn btn-sm pdf-btn ${f.inep_divergente ? 'is-divergente' : ''}"
                    title="${(f.pdf_filename || '').replace(/"/g, '')}${aviso}"
                    onclick="abrirPdf('${f.pdf_url}', ${pag})">
                    <i class="fa-solid fa-file-pdf" style="color:var(--red)"></i>
                    PDF ${inep}${lista ? ` <small>· pág. ${lista}</small>` : ''}
                </button>
                ${f.inep_divergente
                    ? `<span class="tag tag-alerta" title="O nome do arquivo aponta outra escola">
                         arquivo de ${f.inep_no_nome}</span>` : ''}`;
    }).join('') + `</div>`;
}

function celEvidencias(p) {
    return `
        <div class="img-count">${num(p.qtd_imagens)} imagem${p.qtd_imagens > 1 ? 'ns' : ''} igual${p.qtd_imagens > 1 ? 'is' : ''}</div>
        <div class="img-tags">
            ${p.todas_exatas
                ? '<span class="tag tag-exata">100% exatas</span>'
                : `<span class="tag tag-exata">${num(p.qtd_exatas)} exatas</span>
                   <span class="tag tag-visual">${num(p.qtd_imagens - p.qtd_exatas)} visuais</span>`}
            ${p.qtd_fotos_camera
                ? `<span class="tag tag-foto"><i class="fa-solid fa-camera"></i> ${num(p.qtd_fotos_camera)} foto${p.qtd_fotos_camera > 1 ? 's' : ''}</span>`
                : ''}
        </div>
        <div class="img-tags">
            ${p.mesmo_pdf
                ? '<span class="tag tag-grave"><i class="fa-solid fa-triangle-exclamation"></i> mesmo PDF nos dois</span>'
                : (p.pdf_divergente
                    ? '<span class="tag tag-alerta"><i class="fa-solid fa-file-circle-exclamation"></i> PDF de outra escola</span>'
                    : '')}
        </div>
        <div class="img-tags">
            ${p.grupos.slice(0, 3).map((g) => `<span class="tag tag-grupo">${g}</span>`).join('')}
            ${p.grupos.length > 3 ? `<span class="tag tag-grupo">+${p.grupos.length - 3}</span>` : ''}
        </div>`;
}

function celThumbs(p) {
    const imgs = p.imagens || [];
    const mostra = imgs.slice(0, MAX_THUMBS);
    const resto = imgs.length - mostra.length;

    return `<div class="thumbs">` + mostra.map((im, i) => `
        <img src="/extracted_images/${encodeURIComponent(im.thumb_a)}" loading="lazy"
             alt="Imagem duplicada ${i + 1}"
             title="${im.tipo} · ${im.largura}×${im.altura} · pág. ${im.pagina_a} / ${im.pagina_b}"
             onclick="abrirLightbox('${p.par_id}', ${i})">`).join('') +
        (resto > 0
            ? `<div class="mais" onclick="abrirLightbox('${p.par_id}', ${MAX_THUMBS})">+${resto}</div>`
            : '') + `</div>`;
}

function celToggle(p) {
    return `
        <label class="tg" title="Marcar este par como falso positivo">
            <input type="checkbox" data-par="${p.par_id}" ${p.falso_positivo ? 'checked' : ''}>
            <span class="slider"></span>
            <span class="txt">${p.falso_positivo ? 'Sim' : 'Não'}</span>
        </label>`;
}

function renderPager(d) {
    const paginas = Math.ceil((d.total || 0) / (d.limit || 25));
    if (paginas <= 1) { $('pager').innerHTML = ''; return; }

    $('pager').innerHTML = `
        <button class="btn" ${d.page <= 1 ? 'disabled' : ''} onclick="carregar(${d.page - 1})">
            <i class="fa-solid fa-chevron-left"></i> Anterior</button>
        <span class="info">Página ${d.page} de ${paginas}</span>
        <button class="btn" ${d.page >= paginas ? 'disabled' : ''} onclick="carregar(${d.page + 1})">
            Próxima <i class="fa-solid fa-chevron-right"></i></button>`;
}

/* ------------------------- falso positivo ------------------------------ */

async function marcarFalsoPositivo(parId, valor, input) {
    const linha = $('tbody').querySelector(`tr[data-par="${parId}"]`);
    const txt = input.parentElement.querySelector('.txt');
    input.disabled = true;

    try {
        const r = await fetch(`/api/painel/${encodeURIComponent(runAtual)}/falso-positivo`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ par_id: parId, falso_positivo: valor }),
        });
        if (!r.ok) throw new Error((await r.json()).error || 'falha ao salvar');

        txt.textContent = valor ? 'Sim' : 'Não';
        if (linha) linha.classList.toggle('is-fp', valor);

        // Com "Ocultar marcados" ativo, a linha sai da lista — recarrega para
        // as métricas e a paginação acompanharem.
        if (valor && $('fFp').value === 'ocultar') {
            setTimeout(() => carregar(paginaAtual), 350);
        } else {
            atualizarContadorFp(valor ? 1 : -1);
        }
    } catch (e) {
        input.checked = !valor;          // desfaz o toggle se não salvou
        alert('Não foi possível salvar a marcação: ' + e.message);
    } finally {
        input.disabled = false;
    }
}

function atualizarContadorFp(delta) {
    const card = $('cards').querySelector('.stat.is-red .value');
    if (card) {
        const atual = Number(card.textContent.replace(/\./g, '')) || 0;
        card.textContent = num(Math.max(0, atual + delta));
    }
}

/* ---------------------------- utilitários ------------------------------ */

function abrirPdf(url, pagina) {
    window.open(pagina ? `${url}#page=${pagina}` : url, '_blank');
}

let lbPar = null, lbIdx = 0, lbLado = 'a';

function abrirLightbox(parId, indice) {
    lbPar = parId; lbIdx = indice; lbLado = 'a';
    mostrarLightbox();
}

function mostrarLightbox() {
    const imgs = imagensPorPar[lbPar] || [];
    const im = imgs[lbIdx];
    if (!im) return;

    const [a, b] = lbPar.split('x');
    const inep = lbLado === 'a' ? a : b;
    const pagina = lbLado === 'a' ? im.pagina_a : im.pagina_b;
    const thumb = lbLado === 'a' ? im.thumb_a : im.thumb_b;

    $('lbImg').src = `/extracted_images/${encodeURIComponent(thumb)}`;
    $('lbCap').innerHTML =
        `<strong>INEP ${inep}</strong> · pág. ${pagina} · ${im.tipo} · ` +
        `${im.largura}×${im.altura} &nbsp;|&nbsp; imagem ${lbIdx + 1} de ${imgs.length}` +
        `<div style="margin-top:.7rem; display:flex; gap:.5rem; justify-content:center; flex-wrap:wrap;">
            <button class="btn btn-sm" onclick="lbNavegar(-1)">‹ anterior</button>
            <button class="btn btn-sm" onclick="lbTrocarLado()">
                ver do INEP ${lbLado === 'a' ? b : a}</button>
            <button class="btn btn-sm" onclick="lbNavegar(1)">próxima ›</button>
         </div>`;
    $('lb').classList.add('open');
}

function lbNavegar(passo) {
    const imgs = imagensPorPar[lbPar] || [];
    lbIdx = (lbIdx + passo + imgs.length) % imgs.length;
    mostrarLightbox();
}

function lbTrocarLado() {
    lbLado = lbLado === 'a' ? 'b' : 'a';
    mostrarLightbox();
}

function fecharLightbox() {
    $('lb').classList.remove('open');
    $('lbImg').src = '';
}

function baixarCsv() {
    if (runAtual) {
        window.location.href = `/api/painel/${encodeURIComponent(runAtual)}/csv?${queryString()}`;
    }
}
