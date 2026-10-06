/* ==========================================================================
 * Painel de Duplicatas — visão por par de escolas (INEP A × INEP B).
 *
 * Toda a filtragem é feita no servidor (/api/painel/<run_id>), então a tela
 * sempre reflete o conjunto completo da execução e não só a página carregada.
 * ========================================================================== */

const $ = (id) => document.getElementById(id);
const num = (v) => (Number(v) || 0).toLocaleString('pt-BR');

// A mesma tela serve o painel de duplicatas e a auditoria; muda a fonte dos
// dados e o seletor do topo. auditoria.html define window.PAINEL_MODO.
const MODO = window.PAINEL_MODO || 'painel';
const EH_AUDITORIA = MODO === 'auditoria';

/** "2026-09-02 09:03:30" -> "02/09/2026 09:03" */
function dataBR(valor) {
    if (!valor) return '';
    const m = String(valor).match(/^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})/);
    return m ? `${m[3]}/${m[2]}/${m[1]} ${m[4]}:${m[5]}` : valor;
}

let runAtual = null;
let paginaAtual = 1;
let imagensPorPar = {};   // par_id -> imagens, para o lightbox navegar

// Pares marcados para auditoria. Guardado por par_id num Set, e nao lido das
// checkboxes da tela, para a selecao sobreviver a troca de pagina e de filtro.
// Documentos ja anexados no envio aberto, indexados por INEP.
let documentosPorInep = {};

const selecionados = new Set();
const dadosSelecionados = new Map();   // par_id -> dados, para quem for enviar depois

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
        if (!runAtual) return;
        // Na auditoria o id e de um envio, nao de uma execucao: a rota e outra.
        window.location.href = EH_AUDITORIA
            ? `/api/auditoria/${encodeURIComponent(runAtual)}/zip`
            : `/api/historico/${encodeURIComponent(runAtual)}/zip`;
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

    // A pagina de auditoria nao tem coluna de selecao nem botao de envio.
    const liga = (id, evt, fn) => { const el = $(id); if (el) el.addEventListener(evt, fn); };
    liga('chkTodos', 'change', (e) => selecionarPagina(e.target.checked));
    liga('btnSelTodos', 'click', selecionarTodosFiltrados);
    liga('btnSelLimpar', 'click', limparSelecao);
    liga('btnAuditoria', 'click', abrirModalEnvio);
    liga('envCancelar', 'click', fecharModalEnvio);
    liga('envConfirmar', 'click', confirmarEnvio);
    liga('modalEnvio', 'click', (e) => { if (e.target.id === 'modalEnvio') fecharModalEnvio(); });

    $('lbFechar').addEventListener('click', fecharLightbox);
    $('lb').addEventListener('click', (e) => { if (e.target.id === 'lb') fecharLightbox(); });
    document.addEventListener('keydown', (e) => {
        if (e.key !== 'Escape') return;
        fecharLightbox();
        const m = $('modalEnvio');
        if (m) m.hidden = true;
    });
}

async function carregarExecucoes() {
    const sel = $('runSelect');
    try {
        if (EH_AUDITORIA) {
            const r = await fetch('/api/auditoria/envios');
            const d = await r.json();
            const envios = d.envios || [];
            if (!envios.length) {
                sel.innerHTML = '<option value="">Nenhum envio ainda</option>';
                $('runInfo').textContent =
                    'Selecione pares no Painel de Duplicatas e clique em Enviar para auditoria.';
                return;
            }
            sel.innerHTML = envios.map((e) => {
                const rs = e.resumo || {};
                return `<option value="${e.envio_id}">${dataBR(e.criado_em)} · ` +
                       `${num(rs.pares)} pares · ${num(rs.escolas)} escolas</option>`;
            }).join('');
            runAtual = envios[0].envio_id;
            return;
        }

        const r = await fetch('/api/painel/execucoes');
        const d = await r.json();
        const runs = d.runs || [];

        if (!runs.length) {
            sel.innerHTML = '<option value="">Nenhuma execução concluída</option>';
            $('runInfo').textContent = 'Rode uma análise na aba Análise por Fase primeiro.';
            return;
        }

        sel.innerHTML = runs.map((r) => {
            const m = r.metricas || {};
            return `<option value="${r.run_id}">Fase ${r.fase} · ${dataBR(r.iniciado_em)} · ` +
                   `${num(m.grupos_duplicatas)} grupos</option>`;
        }).join('');
        runAtual = runs[0].run_id;
    } catch (e) {
        $('runInfo').textContent = 'Erro ao carregar: ' + e.message;
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

    $('tbody').innerHTML = `<tr><td colspan="${EH_AUDITORIA ? 6 : 8}" class="empty">
        <i class="fa-solid fa-spinner spinner"></i> Carregando…</td></tr>`;

    try {
        const qs = queryString({ page: paginaAtual, limit: $('fLimit').value });
        const base = EH_AUDITORIA ? '/api/auditoria/' : '/api/painel/';
        const r = await fetch(`${base}${encodeURIComponent(runAtual)}?${qs}`);
        const d = await r.json();

        if (!r.ok) {
            $('tbody').innerHTML = `<tr><td colspan="${EH_AUDITORIA ? 6 : 8}" class="empty">${d.error || 'Erro'}</td></tr>`;
            return;
        }

        if (EH_AUDITORIA) await carregarDocumentos();
        renderInfo(d);
        renderCards(d);
        renderBarras(d);
        preencherOpcoes(d.opcoes);
        renderTabela(d);
        renderPager(d);
    } catch (e) {
        $('tbody').innerHTML = `<tr><td colspan="${EH_AUDITORIA ? 6 : 8}" class="empty">Erro: ${e.message}</td></tr>`;
    }
}

function renderInfo(d) {
    const run = d.run || {};
    if (EH_AUDITORIA) {
        const e = d.envio || {};
        const rs = e.resumo || {};
        $('runInfo').textContent =
            `Enviado em ${dataBR(e.criado_em)} · ${num(rs.pares)} pares · ` +
            `${num(rs.escolas)} escolas · origem: execução da fase ${e.fase}` +
            (e.observacao ? ` · ${e.observacao}` : '');
        return;
    }
    $('runInfo').textContent =
        `Fase ${run.fase} · ${run.fornecedor || 'todos os fornecedores'} · ` +
        `executado em ${dataBR(run.iniciado_em)} · ${run.criterio || ''}`;
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
        ['is-amber', 'Fora do raio de 50 m', r.fora_do_raio,
         'pares com foto longe da escola'],
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

const MAX_THUMBS = 2;

function renderTabela(d) {
    const pares = d.pares || [];
    $('tableCount').innerHTML = pares.length
        ? `Exibindo <strong>${num(pares.length)}</strong> de <strong>${num(d.total)}</strong> pares`
        : 'Nenhum par com os filtros atuais';

    if (!pares.length) {
        $('tbody').innerHTML = `<tr><td colspan="${EH_AUDITORIA ? 6 : 8}" class="empty">
            <div class="big">🔍</div>Nenhum par encontrado com os filtros atuais.
            <div style="margin-top:.5rem; font-size:.85rem;">
                Tente limpar os filtros ou mostrar os falsos positivos.</div></td></tr>`;
        return;
    }

    imagensPorPar = {};
    pares.forEach((p) => {
        imagensPorPar[p.par_id] = p.imagens;
        dadosSelecionados.set(p.par_id, p);
    });

    $('tbody').innerHTML = pares.map((p) => `
        <tr class="${p.falso_positivo ? 'is-fp' : ''}${selecionados.has(p.par_id) ? ' is-sel' : ''}"
            data-par="${p.par_id}">
            ${EH_AUDITORIA ? '' : `<td class="cel-sel">${celSelecao(p)}</td>`}
            <td class="cel-de">${celEscola(p.escola_a, p)}</td>
            <td class="cel-de">${celPdfs(p.pdfs_a, p.escola_a.inep)}</td>
            <td class="cel-meio">${celEvidencias(p)}</td>
            <td class="cel-meio">${celThumbs(p)}</td>
            <td class="cel-para">${celEscola(p.escola_b, p)}</td>
            <td class="cel-para">${celPdfs(p.pdfs_b, p.escola_b.inep)}</td>
            ${EH_AUDITORIA ? `<td class="cel-doc">${celDocumentos(p)}</td>`
                            : `<td>${celToggle(p)}</td>`}
        </tr>`).join('');

    $('tbody').querySelectorAll('.tg input').forEach((el) =>
        el.addEventListener('change', (e) => marcarFalsoPositivo(
            e.target.dataset.par, e.target.checked, e.target)));

    $('tbody').querySelectorAll('.chk-envio').forEach((el) =>
        el.addEventListener('change', (e) => alternarSelecao(
            e.target.dataset.par, e.target.checked)));

    atualizarSelecao();
}

/* ------------------------ seleção para auditoria ----------------------- */

function alternarSelecao(parId, marcado) {
    if (marcado) selecionados.add(parId);
    else selecionados.delete(parId);

    const linha = $('tbody').querySelector(`tr[data-par="${parId}"]`);
    if (linha) linha.classList.toggle('is-sel', marcado);
    atualizarSelecao();
}

/** Marca ou desmarca todas as linhas da página atual. */
function selecionarPagina(marcar) {
    $('tbody').querySelectorAll('.chk-envio').forEach((el) => {
        el.checked = marcar;
        if (marcar) selecionados.add(el.dataset.par);
        else selecionados.delete(el.dataset.par);
        const linha = el.closest('tr');
        if (linha) linha.classList.toggle('is-sel', marcar);
    });
    atualizarSelecao();
}

/**
 * Seleciona todos os pares que passam nos filtros, não só os da página.
 *
 * O cabeçalho age sobre a página visível; aqui busca-se o conjunto inteiro no
 * servidor, para o usuário não precisar paginar marcando de 25 em 25.
 */
async function selecionarTodosFiltrados() {
    const btn = $('btnSelTodos');
    const rotulo = btn.textContent;
    btn.disabled = true;
    btn.textContent = 'Selecionando…';

    try {
        let pagina = 1;
        for (;;) {
            const qs = queryString({ page: pagina, limit: 200 });
            const base = EH_AUDITORIA ? '/api/auditoria/' : '/api/painel/';
            const r = await fetch(`${base}${encodeURIComponent(runAtual)}?${qs}`);
            const d = await r.json();
            if (!r.ok) throw new Error(d.error || 'falha ao buscar');

            (d.pares || []).forEach((p) => {
                selecionados.add(p.par_id);
                dadosSelecionados.set(p.par_id, p);
            });

            if (pagina * (d.limit || 200) >= (d.total || 0)) break;
            pagina += 1;
        }

        $('tbody').querySelectorAll('.chk-envio').forEach((el) => {
            el.checked = selecionados.has(el.dataset.par);
            const linha = el.closest('tr');
            if (linha) linha.classList.toggle('is-sel', el.checked);
        });
        atualizarSelecao();
    } catch (e) {
        alert('Não foi possível selecionar todos: ' + e.message);
    } finally {
        btn.disabled = false;
        btn.textContent = rotulo;
    }
}

function limparSelecao() {
    selecionados.clear();
    $('tbody').querySelectorAll('.chk-envio').forEach((el) => (el.checked = false));
    $('tbody').querySelectorAll('tr.is-sel').forEach((tr) => tr.classList.remove('is-sel'));
    atualizarSelecao();
}

/** Mantém o botão, o contador e a barra coerentes com o Set de seleção. */
function atualizarSelecao() {
    const n = selecionados.size;

    const btn = $('btnAuditoria');
    if (!btn) return;   // pagina de auditoria: nao ha o que atualizar
    btn.disabled = n === 0;
    $('btnAuditoriaCont').textContent = n ? `(${num(n)})` : '';
    btn.title = n ? `${n} par(es) selecionado(s)` : 'Selecione pares na coluna Enviar';

    // INEPs distintos: um par tem duas escolas, e a mesma escola pode estar em vários
    const ineps = new Set();
    selecionados.forEach((id) => {
        const p = dadosSelecionados.get(id);
        if (p) { ineps.add(p.escola_a.inep); ineps.add(p.escola_b.inep); }
    });

    // Cabeçalho reflete a página: marcado se todas, traço se algumas
    const naPagina = Array.from($('tbody').querySelectorAll('.chk-envio'));
    const marcadas = naPagina.filter((el) => el.checked).length;
    const mestre = $('chkTodos');
    mestre.checked = naPagina.length > 0 && marcadas === naPagina.length;
    mestre.indeterminate = marcadas > 0 && marcadas < naPagina.length;

    const barra = $('selBar');
    barra.hidden = n === 0;
    $('selResumo').innerHTML = n
        ? `<strong>${num(n)}</strong> par(es) · <strong>${num(ineps.size)}</strong> INEPs selecionados`
        : '';
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
        const pags = f.paginas || [];
        const pag = pags.length ? pags[0] : 1;
        // A lista inteira ("pag. 2, 3, 4 ... 14") esticava a coluna e empurrava
        // o Falso positivo para fora da tela. Mostra as 3 primeiras; o resto
        // vira um "+N", e o titulo do botao traz a lista completa.
        const lista = pags.length > 3
            ? `${pags.slice(0, 3).join(', ')} +${pags.length - 3}`
            : pags.join(', ');
        const aviso = f.inep_divergente
            ? ` — ATENÇÃO: o arquivo é da escola ${f.inep_no_nome}` : '';
        return `<button class="btn btn-sm pdf-btn ${f.inep_divergente ? 'is-divergente' : ''}"
                    title="${(f.pdf_filename || '').replace(/"/g, '')}${aviso}${
                        pags.length > 3 ? ' — paginas: ' + pags.join(', ') : ''}"
                    onclick="abrirPdf('${f.pdf_url}', ${pag})">
                    <i class="fa-solid fa-file-pdf" style="color:var(--red)"></i>
                    <span class="pdf-txt">
                        <span class="pdf-nome">PDF ${inep}</span>
                        ${lista ? `<small class="pdf-pags">pág. ${lista}</small>` : ''}
                    </span>
                </button>
                ${f.inep_divergente
                    ? `<span class="tag tag-alerta" title="O nome do arquivo aponta outra escola">
                         arquivo de ${f.inep_no_nome}</span>` : ''}`;
    }).join('') + `</div>`;
}

function celEvidencias(p) {
    return `
        <div class="img-count">${num(p.qtd_imagens)} image${p.qtd_imagens > 1 ? 'ns' : 'm'} igua${p.qtd_imagens > 1 ? 'is' : 'l'}</div>
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
            ${p.fora_do_raio
                ? `<span class="tag tag-geo" title="${num(p.fora_do_raio)} de ${num(p.com_coordenada)} fotos com coordenada foram tiradas a mais de 50 m do centro da escola (máx. ${p.distancia_max_m} m)"><i class="fa-solid fa-location-crosshairs"></i> ${num(p.fora_do_raio)} foto(s) fora do raio</span>`
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

function celSelecao(p) {
    const marcado = selecionados.has(p.par_id) ? 'checked' : '';
    return `<label class="sel-box" title="Selecionar ${p.escola_a.inep} x ${p.escola_b.inep}">
                <input type="checkbox" class="chk-envio" data-par="${p.par_id}" ${marcado}>
            </label>`;
}

/** Coluna Documentação: um anexo por escola do par. */
function celDocumentos(p) {
    return '<div class="doc-col">' +
        [p.escola_a, p.escola_b].map((e) => {
            const docs = documentosPorInep[String(e.inep)] || [];
            const lista = docs.map((d) => `
                <div class="doc-item" title="${d.arquivo_nome}">
                    <a href="/api/auditoria/documento?path=${encodeURIComponent(d.arquivo_path)}">
                        <i class="fa-solid fa-paperclip"></i> ${d.arquivo_nome}
                    </a>
                    <button class="doc-x" title="Remover"
                            onclick="removerDocumento('${d.doc_id}')">&times;</button>
                </div>`).join('');

            return `
            <div class="doc-escola">
                <div class="doc-inep">${e.inep}</div>
                ${lista}
                <label class="btn btn-sm doc-add">
                    <i class="fa-solid fa-upload"></i> Anexar
                    <input type="file" hidden
                           onchange="enviarDocumento(this, ${e.inep}, '${p.par_id}')"
                           accept=".pdf,.doc,.docx,.xls,.xlsx,.png,.jpg,.jpeg,.zip">
                </label>
            </div>`;
        }).join('') + '</div>';
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

/* --------------------- envio para auditoria ---------------------------- */

/** Abre o modal com o resumo do que sera enviado. */
function abrirModalEnvio() {
    if (!selecionados.size) return;

    const ineps = new Set();
    selecionados.forEach((id) => {
        const p = dadosSelecionados.get(id);
        if (p) { ineps.add(p.escola_a.inep); ineps.add(p.escola_b.inep); }
    });

    $('envResumo').innerHTML =
        `<strong>${num(selecionados.size)}</strong> par(es) e ` +
        `<strong>${num(ineps.size)}</strong> INEPs serão encaminhados.`;
    $('envObs').value = '';
    $('envErro').hidden = true;
    $('modalEnvio').hidden = false;
    $('envObs').focus();
}

function fecharModalEnvio() {
    $('modalEnvio').hidden = true;
}

/** Grava o lote e leva para a página de auditoria. */
async function confirmarEnvio() {
    const ids = Array.from(selecionados);
    if (!ids.length) return fecharModalEnvio();

    const btn = $('envConfirmar');
    const erro = $('envErro');
    btn.disabled = true;
    btn.textContent = 'Enviando…';
    erro.hidden = true;

    try {
        const r = await fetch('/api/auditoria/envios', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                run_id: runAtual, par_ids: ids,
                observacao: $('envObs').value.trim(),
            }),
        });
        const d = await r.json();
        if (!r.ok) throw new Error(d.error || 'falha ao enviar');

        limparSelecao();
        fecharModalEnvio();
        // Sem redirecionar: quem esta triando continua de onde parou.
        avisar(`Enviado para auditoria: ${num(d.resumo.pares)} pares, ` +
               `${num(d.resumo.escolas)} escolas.`);
    } catch (e) {
        erro.textContent = 'Não foi possível enviar: ' + e.message;
        erro.hidden = false;
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="fa-solid fa-paper-plane"></i> Enviar';
    }
}

/* ------------------------- documentos da auditoria --------------------- */

async function carregarDocumentos() {
    try {
        const r = await fetch(`/api/auditoria/${encodeURIComponent(runAtual)}/documentos`);
        const d = await r.json();
        documentosPorInep = d.por_inep || {};
    } catch (e) {
        documentosPorInep = {};
    }
}

/** Envia o arquivo escolhido para o INEP daquela linha. */
async function enviarDocumento(input, inep, parId) {
    const arquivo = input.files && input.files[0];
    if (!arquivo) return;

    const rotulo = input.closest('.doc-add');
    const original = rotulo.innerHTML;
    rotulo.innerHTML = '<i class="fa-solid fa-spinner spinner"></i> Enviando…';

    const dados = new FormData();
    dados.append('arquivo', arquivo);
    dados.append('inep', inep);
    dados.append('par_id', parId || '');

    try {
        const r = await fetch(`/api/auditoria/${encodeURIComponent(runAtual)}/documentos`, {
            method: 'POST', body: dados });
        const d = await r.json();
        if (!r.ok) throw new Error(d.error || 'falha no envio');

        await carregarDocumentos();
        await carregar(paginaAtual);
        avisar(`Documento anexado ao INEP ${inep}.`);
    } catch (e) {
        rotulo.innerHTML = original;
        input.value = '';
        avisarErro('Não foi possível anexar: ' + e.message);
    }
}

async function removerDocumento(docId) {
    try {
        const r = await fetch(
            `/api/auditoria/${encodeURIComponent(runAtual)}/documentos/${encodeURIComponent(docId)}`,
            { method: 'DELETE' });
        const d = await r.json();
        if (!r.ok) throw new Error(d.error || 'falha ao remover');
        await carregarDocumentos();
        await carregar(paginaAtual);
        avisar('Documento removido.');
    } catch (e) {
        avisarErro('Não foi possível remover: ' + e.message);
    }
}

/* ---------------------------- utilitários ------------------------------ */

function avisarErro(texto) { avisar(texto, true); }

/** Aviso curto no canto, sem interromper o que o usuário está fazendo. */
function avisar(texto, erro) {
    let el = $('aviso');
    if (!el) {
        el = document.createElement('div');
        el.id = 'aviso';
        el.className = 'toast';
        document.body.appendChild(el);
    }
    el.innerHTML = `<i class="fa-solid fa-${erro ? 'triangle-exclamation' : 'circle-check'}"></i> ${texto}`;
    el.classList.toggle('erro', !!erro);
    el.classList.add('aberto');
    clearTimeout(el._t);
    el._t = setTimeout(() => el.classList.remove('aberto'), 4500);
}

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
        const base = EH_AUDITORIA ? '/api/auditoria/' : '/api/painel/';
        window.location.href = `${base}${encodeURIComponent(runAtual)}/csv?${queryString()}`;
    }
}
