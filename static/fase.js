/* ==========================================================================
 * Análise por Fase + Histórico persistente de execuções.
 *
 * A varredura de uma fase inteira pode levar horas, então nada aqui depende de
 * a aba ficar aberta: o backend grava cada execução em disco (pasta historico/)
 * e esta tela apenas consulta o progresso e recarrega resultados salvos.
 * ========================================================================== */

let faseStatusInterval = null;
let faseRunIdAtual = null;

async function loadFases() {
    const sel = document.getElementById('faseSelect');
    if (!sel) return;
    try {
        const resp = await fetch('/api/fases');
        const fases = await resp.json();
        sel.innerHTML = '';
        fases.forEach(f => {
            const opt = document.createElement('option');
            opt.value = f.fase;
            opt.textContent = 'Fase ' + f.fase + ' — ' + f.escolas.toLocaleString('pt-BR') + ' escolas';
            if (f.fase === '5') opt.selected = true;
            sel.appendChild(opt);
        });
    } catch (e) {
        sel.innerHTML = '<option value="5">Fase 5</option>';
    }
}

async function startFaseScan() {
    const fase = document.getElementById('faseSelect').value || '5';
    const fornecedor = document.getElementById('faseSupplierSelect').value || '';
    const workers = parseInt(document.getElementById('faseWorkers').value || '32', 10);
    const amostra = parseInt(document.getElementById('faseAmostra').value || '0', 10);
    const gravar = document.getElementById('faseGravarSupabase').checked;
    const mesmoForn = document.getElementById('faseMesmoFornecedor').checked;
    const soIdenticas = document.getElementById('faseSomenteIdenticas').checked;
    const ignoraTpl = document.getElementById('faseIgnorarTemplates').checked;

    const alvo = fornecedor || 'todos os fornecedores';
    if (!amostra && !confirm(
            'Iniciar a varredura completa da fase ' + fase + ' (' + alvo + ')?\n\n' +
            'Todos os PDFs das escolas dessa fase serão baixados e analisados. ' +
            'Isso pode levar bastante tempo — o progresso aparece na tela e o ' +
            'resultado fica salvo no Histórico automaticamente.')) {
        return;
    }

    const btn = document.getElementById('btnAnalyzeFase');
    btn.disabled = true;

    try {
        const resp = await fetch('/api/scan-fase', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ fase: fase, fornecedor: fornecedor, workers: workers,
                                   amostra: amostra, gravar_supabase: gravar,
                                   mesmo_fornecedor: mesmoForn,
                                   somente_identicas: soIdenticas,
                                   ignorar_templates: ignoraTpl })
        });
        const data = await resp.json();
        if (!resp.ok) {
            alert(data.error || 'Não foi possível iniciar a análise.');
            btn.disabled = false;
            return;
        }
        faseRunIdAtual = data.run_id;
        document.getElementById('faseResultsSection').style.display = 'none';
        showFaseProgress('Fase ' + fase + ' — ' + alvo);
        startFasePolling();
    } catch (e) {
        alert('Erro ao iniciar a análise: ' + e.message);
        btn.disabled = false;
    }
}

async function cancelFaseScan() {
    if (!confirm('Cancelar a análise em andamento?\n\nOs PDFs já baixados ficam em cache ' +
                 'e a próxima execução retoma de onde parou.')) return;
    try {
        const resp = await fetch('/api/cancelar-fase', { method: 'POST' });
        const data = await resp.json();
        document.getElementById('faseProgressMsg').textContent = data.message;
    } catch (e) {
        alert('Erro ao cancelar: ' + e.message);
    }
}

function showFaseProgress(titulo) {
    document.getElementById('faseProgressCard').style.display = 'block';
    document.getElementById('faseProgressTitle').textContent = titulo;
    document.getElementById('btnCancelFase').style.display = 'inline-flex';
    document.getElementById('btnAnalyzeFase').disabled = true;
}

function hideFaseProgress() {
    document.getElementById('faseProgressCard').style.display = 'none';
    document.getElementById('btnCancelFase').style.display = 'none';
    document.getElementById('btnAnalyzeFase').disabled = false;
}

function startFasePolling() {
    if (faseStatusInterval) clearInterval(faseStatusInterval);
    faseStatusInterval = setInterval(pollFaseStatus, 2000);
    pollFaseStatus();
}

async function pollFaseStatus() {
    try {
        const resp = await fetch('/api/fase-status');
        const s = await resp.json();

        document.getElementById('faseProgressPct').textContent = s.progress_pct + '%';
        document.getElementById('faseProgressBar').style.width = s.progress_pct + '%';
        document.getElementById('faseProgressMsg').textContent = s.mensagem || '—';
        document.getElementById('faseStatEtapa').textContent = s.etapa || '—';
        document.getElementById('faseStatTime').textContent = formatarDuracao(s.elapsed_time);
        document.getElementById('faseStatRunId').textContent = s.run_id || '—';

        if (s.status === 'processing') {
            showFaseProgress('Fase ' + s.fase + ' — ' + (s.fornecedor || 'todos os fornecedores'));
            return;
        }

        if (faseStatusInterval) {
            clearInterval(faseStatusInterval);
            faseStatusInterval = null;
        }
        hideFaseProgress();

        if (s.status === 'completed' && s.run_id) {
            faseRunIdAtual = s.run_id;
            await abrirRun(s.run_id);
            loadHistorico();
        } else if (s.status === 'error') {
            alert('A análise terminou com erro:\n\n' + (s.erro || 'erro desconhecido'));
            loadHistorico();
        } else if (s.status === 'cancelled') {
            document.getElementById('faseProgressCard').style.display = 'block';
            document.getElementById('faseProgressMsg').textContent =
                'Análise cancelada. Os PDFs já baixados ficaram em cache.';
            loadHistorico();
        }
    } catch (e) {
        console.error('Erro no polling da fase:', e);
    }
}

function formatarDuracao(segundos) {
    const s = Number(segundos || 0);
    if (s < 60) return s.toFixed(1) + 's';
    if (s < 3600) return Math.floor(s / 60) + 'min ' + Math.round(s % 60) + 's';
    return Math.floor(s / 3600) + 'h ' + Math.round((s % 3600) / 60) + 'min';
}

/* ------------------------------ Histórico -------------------------------- */

async function loadHistorico() {
    const cont = document.getElementById('historicoList');
    if (!cont) return;
    try {
        const resp = await fetch('/api/historico');
        const data = await resp.json();
        const runs = data.runs || [];

        if (!runs.length) {
            cont.innerHTML = '<p class="help-text"><i class="fa-solid fa-inbox"></i> ' +
                'Nenhuma análise realizada ainda. Rode uma varredura na aba ' +
                '<strong>Análise por Fase</strong>.</p>';
            return;
        }

        cont.innerHTML = runs.map(function (r) {
            const m = r.metricas || {};
            const cor = r.status === 'concluido' ? 'var(--success)'
                      : r.status === 'erro' ? 'var(--danger)'
                      : r.status === 'processando' ? 'var(--warning)'
                      : r.status === 'interrompido' ? 'var(--warning)' : 'var(--text-muted)';
            const rotulos = { concluido: 'Concluído', erro: 'Erro',
                              processando: 'Em andamento', interrompido: 'Interrompido' };
            const rotulo = rotulos[r.status] || r.status;
            const n = function (v) { return (v || 0).toLocaleString('pt-BR'); };
            const resumo = r.status === 'concluido'
                ? n(m.grupos_duplicatas) + ' grupos · ' + n(m.imagens_duplicadas) +
                  ' imagens duplicadas · ' + n(m.escolas_afetadas) + ' escolas afetadas · ' +
                  n(m.imagens_extraidas) + ' imagens analisadas'
                : (r.erro || '—');

            const acoes = r.status === 'concluido'
                ? '<button class="btn btn-primary" onclick="abrirRun(\'' + r.run_id + '\')">' +
                  '<i class="fa-solid fa-eye"></i> Ver</button>' +
                  '<button class="btn btn-success" onclick="baixarExcelRun(\'' + r.run_id + '\')">' +
                  '<i class="fa-solid fa-file-excel"></i> Excel</button>' +
                  '<button class="btn btn-primary" onclick="baixarZipRun(\'' + r.run_id + '\')">' +
                  '<i class="fa-solid fa-file-zipper"></i> ZIP</button>'
                : '';

            return '' +
            '<div class="card" style="margin-bottom:0.8rem; padding:1rem 1.2rem; display:flex;' +
            ' justify-content:space-between; align-items:center; flex-wrap:wrap; gap:1rem;' +
            ' border-left:3px solid ' + cor + ';">' +
              '<div style="min-width:280px;">' +
                '<div style="font-weight:600; font-size:1rem;">Fase ' + r.fase + ' — ' +
                  (r.fornecedor || 'todos os fornecedores') +
                  '<span style="color:' + cor + '; font-size:0.75rem; margin-left:0.5rem;">● ' +
                  rotulo + '</span></div>' +
                '<div class="subtitle" style="margin:0.3rem 0 0; font-size:0.82rem;">' +
                  r.iniciado_em + ' · ' + (r.duracao_min || 0) + ' min · <code style="font-size:0.75rem;">' +
                  r.run_id + '</code></div>' +
                '<div style="margin-top:0.4rem; font-size:0.85rem; color:var(--text-soft);">' + resumo + '</div>' +
              '</div>' +
              '<div style="display:flex; gap:0.5rem; flex-wrap:wrap;">' + acoes +
                '<button class="btn" onclick="excluirRun(\'' + r.run_id + '\')"' +
                ' style="background:var(--danger-soft); border:1px solid var(--danger); color:var(--danger);">' +
                '<i class="fa-solid fa-trash"></i></button>' +
              '</div>' +
            '</div>';
        }).join('');
    } catch (e) {
        cont.innerHTML = '<p class="help-text" style="color:var(--danger);">Erro ao carregar histórico: ' +
                         e.message + '</p>';
    }
}

async function excluirRun(runId) {
    if (!confirm('Remover a execução ' + runId + ' do histórico?\n\nEsta ação não pode ser desfeita.')) return;
    try {
        await fetch('/api/historico/' + encodeURIComponent(runId), { method: 'DELETE' });
        if (faseRunIdAtual === runId) {
            document.getElementById('faseResultsSection').style.display = 'none';
            faseRunIdAtual = null;
        }
        loadHistorico();
    } catch (e) {
        alert('Erro ao remover: ' + e.message);
    }
}

/* --------------------------- Resultado visual ---------------------------- */

async function abrirRun(runId) {
    faseRunIdAtual = runId;
    switchTab('fase');
    switchFaseView('analisados');
    await loadFaseGroups(1);        // preenche o cabecalho e as metricas
    await loadFaseAnalisados(1);    // visao padrao: todos os PDFs analisados
    const sec = document.getElementById('faseResultsSection');
    if (sec) sec.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

/* Alterna entre a tabela de PDFs analisados e os grupos de duplicatas. */
function switchFaseView(view) {
    const ehAnalisados = view === 'analisados';
    document.getElementById('faseViewAnalisados').style.display = ehAnalisados ? 'block' : 'none';
    document.getElementById('faseViewGrupos').style.display = ehAnalisados ? 'none' : 'block';
    document.getElementById('faseViewAnalisadosBtn').classList.toggle('active', ehAnalisados);
    document.getElementById('faseViewGruposBtn').classList.toggle('active', !ehAnalisados);
}

/* ------------------- Visao "Todos os PDFs Analisados" -------------------- */

async function loadFaseAnalisados(page) {
    page = page || 1;
    if (!faseRunIdAtual) return;

    const buscaEl = document.getElementById('faseAnalisadosBusca');
    const dupEl = document.getElementById('faseAnalisadosDup');
    const busca = buscaEl ? buscaEl.value.trim() : '';
    const dup = dupEl ? dupEl.value : '';
    const tbody = document.getElementById('faseAnalisadosBody');

    tbody.innerHTML = '<tr><td colspan="6" style="text-align:center; padding:2rem; color:var(--text-muted);">' +
                      '<i class="fa-solid fa-spinner fa-spin"></i> Carregando...</td></tr>';

    try {
        const url = '/api/historico/' + encodeURIComponent(faseRunIdAtual) + '/analisados' +
                    '?page=' + page + '&limit=50&busca=' + encodeURIComponent(busca) +
                    '&tem_duplicata=' + dup;
        const resp = await fetch(url);
        const data = await resp.json();
        if (!resp.ok) {
            tbody.innerHTML = '<tr><td colspan="6" style="text-align:center; padding:2rem; color:var(--danger);">' +
                              (data.error || 'Erro ao carregar.') + '</td></tr>';
            return;
        }
        renderFaseAnalisados(data, page);
    } catch (e) {
        tbody.innerHTML = '<tr><td colspan="6" style="text-align:center; padding:2rem; color:var(--danger);">' +
                          'Erro: ' + e.message + '</td></tr>';
    }
}

function renderFaseAnalisados(data, page) {
    const itens = data.analisados || [];
    const tbody = document.getElementById('faseAnalisadosBody');
    document.getElementById('faseAnalisadosCount').textContent =
        (data.total || 0).toLocaleString('pt-BR') + ' · ' +
        (data.com_duplicata || 0).toLocaleString('pt-BR') + ' com duplicata';

    if (!itens.length) {
        tbody.innerHTML = '<tr><td colspan="6" style="text-align:center; padding:2rem; color:var(--text-muted);">' +
            '<i class="fa-solid fa-circle-check" style="color:var(--success); font-size:1.4rem;"></i><br><br>' +
            'Nenhum PDF encontrado com os filtros atuais.</td></tr>';
        document.getElementById('faseAnalisadosPagination').innerHTML = '';
        return;
    }

    tbody.innerHTML = itens.map(function (it) {
        const forn = (it.fornecedor || '—').replace(/ \(RI\)| \(RE\)/g, '');
        const legenda = 'INEP ' + it.inep + ' \u00b7 ' + (it.uf || '') + ' \u00b7 p\u00e1g. ' + it.pagina;
        const dim = it.width && it.height ? it.width + 'x' + it.height : '';
        const badge = it.tem_duplicata
            ? '<span class="badge badge-orange" style="font-size:0.85rem; padding:0.35rem 0.6rem;">' +
              '\u26a0\ufe0f Duplicada</span>'
            : '<span class="badge badge-green" style="font-size:0.85rem; padding:0.35rem 0.6rem;">' +
              '\u2705 \u00danica</span>';
        const outros = it.duplicada_com || [];
        const parceiros = outros.length
            ? outros.map(function (o) {
                  // Aceita o formato antigo (só o número) e o novo (objeto), para que
                  // execuções gravadas antes desta versão continuem legíveis.
                  if (typeof o !== 'object') {
                      return '<div style="font-size:0.9rem; font-weight:700; color:var(--danger);">' +
                             o + '</div>';
                  }
                  const leg = 'INEP ' + o.inep + ' · ' + (o.uf || '') + ' · pág. ' + o.pagina;
                  return '<div style="margin-bottom:0.5rem;">' +
                    '<div style="font-size:0.95rem; font-weight:700; color:var(--danger);">' +
                      o.inep + (o.uf ? ' · ' + o.uf : '') + '</div>' +
                    '<div style="display:flex; gap:0.35rem; margin-top:0.3rem; flex-wrap:wrap;">' +
                      '<button class="btn btn-primary" style="font-size:0.72rem;' +
                        ' padding:0.3rem 0.55rem;" onclick="ampliarImagem(\'' +
                        o.thumb_url + '\', \'' + leg + '\')">' +
                        '<i class="fa-solid fa-image"></i> imagem</button>' +
                      (o.pdf_url
                        ? '<button class="btn btn-success" style="font-size:0.72rem;' +
                          ' padding:0.3rem 0.55rem;" onclick="abrirPdf(\'' + o.pdf_url +
                          '\', ' + o.pagina + ')">' +
                          '<i class="fa-solid fa-file-pdf"></i> PDF ' + o.inep + '</button>'
                        : '') +
                    '</div></div>';
              }).join('')
            : '<span style="color:var(--text-muted); font-size:0.8rem;">—</span>';
        const grupos = (it.grupos || []).length
            ? (it.grupos || []).map(function (g) {
                  return '<span class="badge" style="background:var(--danger-soft); color:var(--danger); ' +
                         'border:1px solid var(--danger); font-size:0.72rem;">' + g + '</span>';
              }).join(' ')
            : '<span style="color:var(--text-muted); font-size:0.8rem;">—</span>';

        return '<tr>' +
            '<td>' +
              '<div style="font-size:1rem; font-weight:700; color:var(--accent);">' + (it.inep || '—') + '</div>' +
              '<div style="font-size:0.78rem; color:var(--text-muted); margin-top:0.2rem;">' +
                (it.uf ? '<span class="badge" style="background:var(--primary-soft);">' + it.uf + '</span> ' : '') +
                '<span style="font-size:0.73rem;">' + forn + '</span>' +
              '</div>' +
              (it.fase ? '<div style="font-size:0.72rem; color:var(--text-muted); margin-top:0.1rem;">Fase ' +
                         it.fase + '</div>' : '') +
            '</td>' +
            '<td>' +
              '<img src="/extracted_images/' + encodeURIComponent(it.thumb_url) + '" loading="lazy"' +
              ' alt="' + legenda + '" title="' + legenda + '"' +
              ' onclick="ampliarImagem(\'' + it.thumb_url + '\', \'' + legenda + '\')"' +
              ' style="max-width:80px; max-height:80px; border-radius:6px; cursor:zoom-in;' +
              ' border:1px solid var(--border-strong);">' +
              (dim ? '<div style="font-size:0.68rem; color:var(--text-muted); margin-top:0.2rem;">' +
                     dim + '</div>' : '') +
            '</td>' +
            '<td style="text-align:center;">' + badge + '</td>' +
            '<td>' + parceiros + '</td>' +
            '<td>' +
              '<div style="font-size:0.8rem; max-width:340px; word-break:break-word;">' +
                (it.pdf_filename || '—') + '</div>' +
              '<div style="font-size:0.72rem; color:var(--text-muted); margin-top:0.2rem;">P\u00e1g. ' +
                it.pagina + '</div>' +
              '<div style="display:flex; gap:0.4rem; margin-top:0.45rem; flex-wrap:wrap;">' +
                '<button class="btn btn-primary" style="font-size:0.75rem; padding:0.35rem 0.6rem;"' +
                ' onclick="ampliarImagem(\'' + it.thumb_url + '\', \'' + legenda + '\')">' +
                '<i class="fa-solid fa-image"></i> Ver imagem</button>' +
                (it.pdf_url
                  ? '<button class="btn btn-success" style="font-size:0.75rem;' +
                    ' padding:0.35rem 0.6rem;" onclick="abrirPdf(\'' + it.pdf_url + '\', ' +
                    it.pagina + ')"><i class="fa-solid fa-file-pdf"></i> PDF ' +
                    it.inep + '</button>'
                  : '') +
              '</div>' +
            '</td>' +
            '<td style="text-align:center;">' + grupos + '</td>' +
        '</tr>';
    }).join('');

    const total = data.total || 0;
    const limit = data.limit || 50;
    const paginas = Math.ceil(total / limit);
    const pag = document.getElementById('faseAnalisadosPagination');
    if (paginas <= 1) { pag.innerHTML = ''; return; }

    pag.innerHTML =
        '<button class="btn btn-primary" ' + (page <= 1 ? 'disabled' : '') +
        ' onclick="loadFaseAnalisados(' + (page - 1) + ')">' +
        '<i class="fa-solid fa-chevron-left"></i> Anterior</button>' +
        '<span style="align-self:center; color:var(--text-muted); font-size:0.9rem;">P\u00e1gina ' + page +
        ' de ' + paginas + ' (' + total.toLocaleString('pt-BR') + ' PDFs)</span>' +
        '<button class="btn btn-primary" ' + (page >= paginas ? 'disabled' : '') +
        ' onclick="loadFaseAnalisados(' + (page + 1) + ')">' +
        'Pr\u00f3xima <i class="fa-solid fa-chevron-right"></i></button>';
}

async function loadFaseGroups(page) {
    page = page || 1;
    if (!faseRunIdAtual) return;

    const buscaEl = document.getElementById('faseBusca');
    const minEl = document.getElementById('faseMinEscolas');
    const busca = buscaEl ? buscaEl.value.trim() : '';
    const minEscolas = minEl ? minEl.value : '0';
    const cont = document.getElementById('faseGroupsContainer');
    cont.innerHTML = '<p class="help-text" style="padding:1.5rem;">' +
                     '<i class="fa-solid fa-spinner fa-spin"></i> Carregando grupos...</p>';

    try {
        const url = '/api/historico/' + encodeURIComponent(faseRunIdAtual) +
                    '?page=' + page + '&limit=25&busca=' + encodeURIComponent(busca) +
                    '&min_escolas=' + minEscolas;
        const resp = await fetch(url);
        const data = await resp.json();
        if (!resp.ok) {
            cont.innerHTML = '<p class="help-text" style="color:var(--danger); padding:1.5rem;">' +
                             (data.error || 'Erro ao carregar.') + '</p>';
            return;
        }

        document.getElementById('faseResultsSection').style.display = 'block';
        renderFaseHeader(data);
        renderFaseGroups(data, page);
    } catch (e) {
        cont.innerHTML = '<p class="help-text" style="color:var(--danger); padding:1.5rem;">Erro: ' +
                         e.message + '</p>';
    }
}

function renderFaseHeader(data) {
    const m = data.metricas || {};
    const n = function (v) { return (v || 0).toLocaleString('pt-BR'); };

    document.getElementById('faseResultTitle').textContent =
        'Fase ' + data.fase + (data.fornecedor ? ' · ' + data.fornecedor
                                               : ' · todos os fornecedores');
    document.getElementById('faseResultSubtitle').textContent =
        'Executado em ' + data.iniciado_em + ' · ' + data.duracao_min + ' min · ' + data.run_id;
    document.getElementById('faseGroupCount').textContent = n(data.grupos_total);

    const cards = [
        ['blue', 'fa-school', m.escolas_selecionadas, 'Escolas na Fase'],
        ['blue', 'fa-file-pdf', m.pdfs_com_imagem, 'PDFs com Imagens'],
        ['blue', 'fa-photo-film', m.imagens_extraidas, 'Imagens Analisadas'],
        ['red', 'fa-object-group', m.grupos_duplicatas, 'Grupos Duplicados'],
        ['orange', 'fa-copy', m.imagens_duplicadas, 'Imagens Duplicadas'],
        ['red', 'fa-triangle-exclamation', m.escolas_afetadas, 'Escolas Afetadas'],
        ['purple', 'fa-building', m.fornecedores_afetados, 'Fornecedores Afetados'],
        ['green', 'fa-equals', m.pares_exatos, 'Pares Exatos (100%)']
    ];

    document.getElementById('faseMetricsGrid').innerHTML = cards.map(function (c) {
        return '<div class="metric-card">' +
               '<div class="metric-icon ' + c[0] + '"><i class="fa-solid ' + c[1] + '"></i></div>' +
               '<div><span class="metric-value">' + n(c[2]) + '</span>' +
               '<span class="metric-label">' + c[3] + '</span></div></div>';
    }).join('');
}

function renderFaseGroups(data, page) {
    const grupos = data.grupos || [];
    const cont = document.getElementById('faseGroupsContainer');

    if (!grupos.length) {
        cont.innerHTML = '<p class="help-text" style="padding:2rem; text-align:center;">' +
            '<i class="fa-solid fa-circle-check" style="color:var(--success);"></i> ' +
            'Nenhum grupo de duplicatas encontrado com os filtros atuais.</p>';
        document.getElementById('fasePagination').innerHTML = '';
        return;
    }

    cont.innerHTML = grupos.map(function (g) {
        // Um painel por ESCOLA (não por ocorrência): o grupo aponta escolas
        // diferentes, então a comparação útil é escola contra escola.
        const porEscola = {};
        (g.membros || []).forEach(function (m) {
            if (!porEscola[m.inep]) porEscola[m.inep] = m;
        });

        const paineis = Object.keys(porEscola).map(function (inep, i) {
            const m = porEscola[inep];
            const legenda = 'INEP ' + m.inep + ' \u00b7 ' + (m.uf || '') + ' \u00b7 p\u00e1g. ' + m.pagina;
            const pdfUrl = m.pdf_url || '';
            const rotulo = String.fromCharCode(65 + i);   // A, B, C...

            return '' +
            '<div style="flex:1 1 320px; min-width:300px; background:var(--bg-soft);' +
            ' border:1px solid var(--border-color); border-radius:10px; padding:0.9rem;">' +
              '<div style="display:flex; align-items:baseline; gap:0.5rem; margin-bottom:0.6rem;">' +
                '<span class="badge" style="background:var(--primary-soft); color:var(--primary);">' +
                  'Escola ' + rotulo + '</span>' +
                '<span style="font-size:1.15rem; font-weight:700; color:var(--accent);">' +
                  m.inep + '</span>' +
                '<span class="badge" style="background:var(--primary-soft);">' + (m.uf || '\u2014') + '</span>' +
              '</div>' +
              '<img src="/extracted_images/' + encodeURIComponent(m.thumb_url) + '"' +
                ' alt="' + legenda + '" loading="lazy"' +
                ' onclick="ampliarImagem(\'' + m.thumb_url + '\', \'' + legenda + '\')"' +
                ' style="width:100%; height:200px; object-fit:cover; border-radius:8px;' +
                ' cursor:zoom-in; border:1px solid var(--border-strong);">' +
              '<div style="font-size:0.78rem; color:var(--text-muted); margin:0.6rem 0 0.2rem;' +
                ' word-break:break-word;">' + (m.pdf_filename || '\u2014') + '</div>' +
              '<div style="font-size:0.75rem; color:var(--text-muted); margin-bottom:0.7rem;">' +
                'P\u00e1g. ' + m.pagina + ' \u00b7 ' + m.width + '\u00d7' + m.height +
                ' \u00b7 ' + (m.fornecedor || '').replace(/ \(RI\)| \(RE\)/g, '') + '</div>' +
              '<div style="display:flex; gap:0.5rem; flex-wrap:wrap;">' +
                '<button class="btn btn-primary" style="flex:1; justify-content:center;' +
                  ' font-size:0.82rem; padding:0.5rem 0.7rem;"' +
                  ' onclick="ampliarImagem(\'' + m.thumb_url + '\', \'' + legenda + '\')">' +
                  '<i class="fa-solid fa-image"></i> Ver imagem</button>' +
                (pdfUrl
                  ? '<button class="btn btn-success" style="flex:1; justify-content:center;' +
                    ' font-size:0.82rem; padding:0.5rem 0.7rem;"' +
                    ' onclick="abrirPdf(\'' + pdfUrl + '\', ' + m.pagina + ')">' +
                    '<i class="fa-solid fa-file-pdf"></i> PDF ' + m.inep + '</button>'
                  : '') +
              '</div>' +
            '</div>';
        });

        const escolas = Object.keys(porEscola).length;

        // Um botão por escola, nomeado com o INEP: abre o RDO daquela escola direto,
        // sem precisar localizar o painel certo.
        const botoesPdf = Object.keys(porEscola).map(function (inep) {
            const m = porEscola[inep];
            if (!m.pdf_url) return '';
            return '<button class="btn btn-success" style="font-size:0.8rem;' +
                   ' padding:0.45rem 0.8rem;" title="Abrir o RDO do INEP ' + m.inep +
                   ' na página ' + m.pagina + '"' +
                   ' onclick="abrirPdf(\'' + m.pdf_url + '\', ' + m.pagina + ')">' +
                   '<i class="fa-solid fa-file-pdf"></i> PDF ' + m.inep + '</button>';
        }).join('');

        return '' +
        '<div class="card" style="margin:0 0 1.2rem; padding:1.1rem 1.2rem;">' +
          '<div style="display:flex; justify-content:space-between; align-items:flex-start;' +
          ' flex-wrap:wrap; gap:0.8rem; margin-bottom:0.9rem;">' +
            '<div><span class="badge" style="background:var(--danger-soft); color:var(--danger);' +
            ' border:1px solid var(--danger);">' + g.grupo_id + '</span>' +
            (g.fornecedor ? '<span class="badge" style="margin-left:0.4rem;' +
              ' background:var(--purple-soft); color:var(--purple);' +
              ' border:1px solid var(--purple);">' + g.fornecedor + '</span>' : '') +
            '<strong style="margin-left:0.6rem;">' + escolas + ' escolas diferentes</strong>' +
            '<span style="color:var(--text-muted);"> \u00b7 ' + g.tipo + '</span></div>' +
            '<div style="display:flex; align-items:center; gap:0.6rem; flex-wrap:wrap;">' +
              '<span style="font-size:0.8rem; color:var(--text-muted);">' +
                '<i class="fa-solid fa-location-dot"></i> ' +
                ((g.ufs || []).join(', ') || '\u2014') + '</span>' +
              botoesPdf +
            '</div>' +
          '</div>' +
          '<div style="display:flex; gap:1rem; flex-wrap:wrap; align-items:stretch;">' +
            paineis.join('') +
          '</div>' +
        '</div>';
    }).join('');

    const total = data.grupos_total || 0;
    const limit = data.limit || 25;
    const paginas = Math.ceil(total / limit);
    const pag = document.getElementById('fasePagination');
    if (paginas <= 1) { pag.innerHTML = ''; return; }

    pag.innerHTML =
        '<button class="btn btn-primary" ' + (page <= 1 ? 'disabled' : '') +
        ' onclick="loadFaseGroups(' + (page - 1) + ')">' +
        '<i class="fa-solid fa-chevron-left"></i> Anterior</button>' +
        '<span style="align-self:center; color:var(--text-muted); font-size:0.9rem;">P\u00e1gina ' + page +
        ' de ' + paginas + ' (' + total.toLocaleString('pt-BR') + ' grupos)</span>' +
        '<button class="btn btn-primary" ' + (page >= paginas ? 'disabled' : '') +
        ' onclick="loadFaseGroups(' + (page + 1) + ')">' +
        'Pr\u00f3xima <i class="fa-solid fa-chevron-right"></i></button>';
}

/* Abre o PDF do RDO já na página onde a imagem duplicada está. */
function abrirPdf(url, pagina) {
    const destino = pagina ? url + '#page=' + pagina : url;
    window.open(destino, '_blank');
}


function ampliarImagem(thumb, legenda) {
    const win = window.open('', '_blank');
    if (!win) return;
    win.document.write(
        '<title>' + legenda + '</title>' +
        '<body style="margin:0;background:rgba(16,24,36,0.92);display:flex;flex-direction:column;' +
        'align-items:center;justify-content:center;height:100vh;font-family:sans-serif;">' +
        '<img src="/extracted_images/' + encodeURIComponent(thumb) + '"' +
        ' style="max-width:95vw;max-height:88vh;object-fit:contain;">' +
        '<p style="color:var(--text-main);font-size:0.95rem;">' + legenda + '</p></body>');
    win.document.close();
}

function baixarZipRun(runId) {
    window.location.href = '/api/historico/' + encodeURIComponent(runId) + '/zip';
}

function downloadFaseZip() {
    if (faseRunIdAtual) baixarZipRun(faseRunIdAtual);
}

/* Inicialização: carrega fases, histórico e reengancha em análise em andamento. */
document.addEventListener('DOMContentLoaded', function () {
    loadFases();
    loadHistorico();
    fetch('/api/fase-status').then(function (r) { return r.json(); }).then(function (s) {
        if (s.status === 'processing') {
            faseRunIdAtual = s.run_id;
            startFasePolling();
        }
    }).catch(function () {});
});
