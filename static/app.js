let allDuplicatePairs = [];
let statusInterval = null;

document.addEventListener('DOMContentLoaded', () => {
    setupDropzone();
    setupFileInput();
    loadSuppliers();
    checkInitialStatus();
});

async function checkInitialStatus() {
    try {
        const res = await fetch('/api/status');
        const data = await res.json();
        if (data.status === 'processing') {
            showProgressCard();
            startPollingStatus();
        } else if (data.status === 'completed' && data.results) {
            displayResults(data.results);
        }
    } catch (e) {
        console.error("Erro ao verificar status inicial:", e);
    }
}

async function loadSuppliers() {
    try {
        const resp = await fetch('/api/suppliers');
        const suppliers = await resp.json();
        const select = document.getElementById('supplierSelect');
        if (!select) return;

        select.innerHTML = '<option value="">-- Selecione um Fornecedor --</option>';
        suppliers.forEach(name => {
            const opt = document.createElement('option');
            opt.value = name;
            opt.textContent = name;
            select.appendChild(opt);
        });
    } catch(e) {
        console.error("Erro ao carregar fornecedores:", e);
        const select = document.getElementById('supplierSelect');
        if (select) select.innerHTML = '<option value="">Erro ao carregar fornecedores</option>';
    }
}

// Switch Tab
function switchTab(tabName) {
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));

    if (tabName === 'supabase') {
        document.getElementById('tabSupabaseBtn').classList.add('active');
        document.getElementById('tabSupabase').classList.add('active');
    } else if (tabName === 'upload') {
        document.getElementById('tabUploadBtn').classList.add('active');
        document.getElementById('tabUpload').classList.add('active');
    } else {
        document.getElementById('tabScanBtn').classList.add('active');
        document.getElementById('tabScan').classList.add('active');
    }
}

async function startSupabaseSupplierScan() {
    const select = document.getElementById('supplierSelect');
    const supplierName = select ? select.value.trim() : '';

    if (!supplierName) {
        alert("Por favor, selecione um fornecedor no dropdown.");
        return;
    }

    showProgressCard();

    try {
        const response = await fetch('/api/scan-supabase-supplier', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ fornecedor: supplierName, limit: 200 })
        });

        const data = await response.json();
        if (response.ok) {
            startPollingStatus();
        } else {
            alert(data.error || "Erro ao iniciar análise no Supabase.");
            hideProgressCard();
        }
    } catch (err) {
        console.error(err);
        alert("Erro de conexão ao iniciar análise.");
        hideProgressCard();
    }
}

// Drag & Dropzone Setup
function setupDropzone() {
    const dropzone = document.getElementById('dropzone');
    if (!dropzone) return;

    ['dragenter', 'dragover', 'dragleave', 'drop'].forEach(eventName => {
        dropzone.addEventListener(eventName, preventDefaults, false);
    });

    function preventDefaults(e) {
        e.preventDefault();
        e.stopPropagation();
    }

    ['dragenter', 'dragover'].forEach(eventName => {
        dropzone.addEventListener(eventName, () => dropzone.classList.add('dragover'), false);
    });

    ['dragleave', 'drop'].forEach(eventName => {
        dropzone.addEventListener(eventName, () => dropzone.classList.remove('dragover'), false);
    });

    dropzone.addEventListener('drop', handleDrop, false);
}

function handleDrop(e) {
    const dt = e.dataTransfer;
    const files = dt.files;
    if (files && files.length > 0) {
        uploadFiles(files);
    }
}

function setupFileInput() {
    const input = document.getElementById('fileInput');
    if (input) {
        input.addEventListener('change', (e) => {
            if (e.target.files && e.target.files.length > 0) {
                uploadFiles(e.target.files);
            }
        });
    }
}

// API Calls
async function uploadFiles(files) {
    const formData = new FormData();
    let count = 0;
    for (let i = 0; i < files.length; i++) {
        if (files[i].name.toLowerCase().endsWith('.pdf')) {
            formData.append('files', files[i]);
            count++;
        }
    }

    if (count === 0) {
        alert("Por favor, selecione arquivos com extensão .pdf");
        return;
    }

    showProgressCard();

    try {
        const response = await fetch('/api/upload', {
            method: 'POST',
            body: formData
        });

        const data = await response.json();
        if (response.ok) {
            startPollingStatus();
        } else {
            alert(data.error || "Erro ao fazer upload dos arquivos.");
            hideProgressCard();
        }
    } catch (err) {
        console.error(err);
        alert("Erro de conexão ao enviar arquivos.");
        hideProgressCard();
    }
}

async function startFolderScan() {
    const pathInput = document.getElementById('folderPathInput');
    const folderPath = pathInput ? pathInput.value.trim() : '';

    if (!folderPath) {
        alert("Por favor, informe o caminho de uma pasta local.");
        return;
    }

    showProgressCard();

    try {
        const response = await fetch('/api/scan-folder', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ folder_path: folderPath, recursive: true })
        });

        const data = await response.json();
        if (response.ok) {
            startPollingStatus();
        } else {
            alert(data.error || "Erro ao iniciar varredura da pasta.");
            hideProgressCard();
        }
    } catch (err) {
        console.error(err);
        alert("Erro de conexão ao iniciar varredura.");
        hideProgressCard();
    }
}

function startPollingStatus() {
    if (statusInterval) clearInterval(statusInterval);
    
    statusInterval = setInterval(async () => {
        try {
            const res = await fetch('/api/status');
            const data = await res.json();

            updateProgressUI(data);

            if (data.status === 'completed') {
                clearInterval(statusInterval);
                displayResults(data.results);
            } else if (data.status === 'error') {
                clearInterval(statusInterval);
                alert("Erro durante a análise: " + data.error_message);
            }
        } catch (err) {
            console.error("Erro ao verificar status:", err);
        }
    }, 1000);
}

// UI Updates
function showProgressCard() {
    document.getElementById('progressCard').style.display = 'block';
    document.getElementById('resultsSection').style.display = 'none';
    document.getElementById('systemStatus').innerHTML = '<span class="dot yellow"></span> Analisando PDFs...';
}

function hideProgressCard() {
    document.getElementById('progressCard').style.display = 'none';
    document.getElementById('systemStatus').innerHTML = '<span class="dot green"></span> Sistema Pronto';
}

function updateProgressUI(data) {
    document.getElementById('progressPct').innerText = `${data.progress_pct}%`;
    document.getElementById('progressBarFill').style.width = `${data.progress_pct}%`;
    document.getElementById('currentFileText').innerText = data.current_file ? `Processando: ${data.current_file}` : 'Preparando...';
    document.getElementById('statFiles').innerText = `${data.processed_files} / ${data.total_files}`;
    document.getElementById('statImages').innerText = data.total_images;
    document.getElementById('statTime').innerText = `${data.elapsed_time || 0.0}s`;
}

async function startAllSuppliersSequential() {
    const confirmed = confirm(
        "Isso vai analisar TODOS os fornecedores da base em segundo plano direto no servidor.\n\n" +
        "Você poderá fechar o navegador ou recarregar a página a qualquer momento que o processo continuará rodando.\n\n" +
        "Deseja iniciar?"
    );
    if (!confirmed) return;

    showProgressCard();

    try {
        const response = await fetch('/api/scan-all-suppliers', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });

        const data = await response.json();
        if (response.ok) {
            startPollingStatus();
        } else {
            alert(data.error || "Erro ao iniciar análise de todos os fornecedores.");
            hideProgressCard();
        }
    } catch (err) {
        console.error(err);
        alert("Erro de conexão ao iniciar análise.");
        hideProgressCard();
    }
}


function displayResults(results) {
    hideProgressCard();
    document.getElementById('resultsSection').style.display = 'block';
    document.getElementById('systemStatus').innerHTML = '<span class="dot green"></span> Análise Concluída';

    // Métricas
    document.getElementById('metricTotalPDFs').innerText = results.total_schools_analyzed || results.total_pdfs_analyzed || 0;
    document.getElementById('metricTotalImages').innerText = results.total_images || 0;
    document.getElementById('metricDuplicatePairs').innerText = results.total_duplicate_pairs || 0;
    document.getElementById('metricExactPairs').innerText = results.exact_duplicate_pairs || 0;
    document.getElementById('metricVisualPairs').innerText = results.visual_duplicate_pairs || 0;
    document.getElementById('metricAffectedFiles').innerText = results.affected_ineps_count || results.affected_files_count || 0;

    // Banner simples com nome do fornecedor analisado
    const supplierSection = document.getElementById('supplierGroupSection');
    const supplierName = document.getElementById('supplierSelect')?.value || 'Todos os Fornecedores';
    const totalPairs = results.total_duplicate_pairs || 0;
    const affectedCount = results.affected_ineps_count || 0;

    document.getElementById('supplierAnalyzedName').textContent = supplierName;
    document.getElementById('supplierSummaryText').textContent =
        totalPairs > 0
            ? `${totalPairs} pares de imagens duplicadas encontrados em ${affectedCount} escolas (INEPs). Veja a tabela abaixo.`
            : 'Nenhuma imagem duplicada encontrada para este fornecedor.';
    supplierSection.style.display = 'block';

    // INEPs Afetados
    renderAffectedFiles(results.affected_ineps || results.affected_files || []);

    // Tabela de duplicatas
    allDuplicatePairs = results.duplicate_pairs || [];
    renderTable(allDuplicatePairs);

    // Scroll para resultados
    setTimeout(() => supplierSection.scrollIntoView({ behavior: 'smooth', block: 'start' }), 200);
}


function renderSupplierGroups(suppliers) {
    const card = document.getElementById('supplierGroupSection');
    const container = document.getElementById('supplierCardsContainer');
    const badge = document.getElementById('supplierCountBadge');

    if (!suppliers || suppliers.length === 0) {
        card.style.display = 'none';
        return;
    }

    card.style.display = 'block';
    badge.innerText = suppliers.length;
    container.innerHTML = '';

    suppliers.forEach(s => {
        const item = document.createElement('div');
        item.className = 'supplier-item-card';

        const title = document.createElement('div');
        title.className = 'supplier-title';
        title.innerHTML = `<i class="fa-solid fa-building text-warning"></i> ${s.fornecedor}`;

        const stats = document.createElement('div');
        stats.className = 'supplier-stats';
        stats.innerHTML = `
            <span>Pares Duplicados: <strong>${s.total_pairs}</strong></span>
            <span>INEPs: <strong>${s.affected_ineps_count}</strong></span>
        `;

        const btn = document.createElement('button');
        btn.className = 'btn btn-primary';
        btn.style.cssText = 'width: 100%; justify-content: center; margin-top: 0.4rem;';
        btn.innerHTML = '<i class="fa-solid fa-eye"></i> Ver Duplicatas deste Fornecedor';
        btn.addEventListener('click', () => filterBySupplier(s.fornecedor));

        item.appendChild(title);
        item.appendChild(stats);
        item.appendChild(btn);
        container.appendChild(item);
    });
}

function filterBySupplier(supplierName) {
    // Verificar se existe análise rodada
    if (!allDuplicatePairs || allDuplicatePairs.length === 0) {
        alert(`Nenhuma análise foi rodada ainda. Primeiro selecione "${supplierName}" no dropdown acima e clique em "Analisar Fornecedor" (ou "Analisar BASE COMPLETA"). Depois os resultados aparecerão aqui.`);
        return;
    }

    // Exibe a seção de resultados
    const resultsSection = document.getElementById('resultsSection');
    if (resultsSection) resultsSection.style.display = 'block';

    // Resetar slider de similaridade para 0 para não filtrar nada fora
    const slider = document.getElementById('simSlider');
    if (slider) { slider.value = 0; updateSimLabel(0); }

    // Define o filtro no campo de busca com o nome do fornecedor
    const searchInput = document.getElementById('searchInput');
    if (searchInput) searchInput.value = supplierName;

    // Aplica o filtro na tabela
    filterResults();

    // Scroll suave para a tabela após breve delay
    setTimeout(() => {
        const table = document.getElementById('resultsTable');
        if (table) table.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }, 150);
}

function renderAffectedFiles(affectedFiles) {
    const card = document.getElementById('affectedFilesCard');
    const container = document.getElementById('affectedFilesList');
    const badge = document.getElementById('affectedCountBadge');

    if (!affectedFiles || affectedFiles.length === 0) {
        card.style.display = 'none';
        return;
    }

    card.style.display = 'block';
    badge.innerText = affectedFiles.length;
    container.innerHTML = '';

    affectedFiles.forEach(file => {
        const pagesText = file.impacted_pages && file.impacted_pages.length > 0 
            ? `Pág. ${file.impacted_pages.join(', ')}` 
            : '';

        const item = document.createElement('div');
        item.className = 'affected-file-item';
        item.title = `Caminho completo: ${file.pdf_path}`;
        item.innerHTML = `
            <div class="affected-file-name">
                <i class="fa-solid fa-file-pdf text-warning" style="margin-right: 0.4rem;"></i>
                ${file.filename}
                ${pagesText ? `<small style="display: block; color: var(--text-muted); font-size: 0.75rem; font-weight: 400;">${pagesText}</small>` : ''}
            </div>
            <span class="affected-file-badge">
                <i class="fa-solid fa-clone"></i> ${file.duplicate_count} duplicatas
            </span>
        `;
        container.appendChild(item);
    });
}

function renderTable(pairs) {
    const tbody = document.getElementById('tableBody');
    tbody.innerHTML = '';

    if (!pairs || pairs.length === 0) {
        tbody.innerHTML = `<tr><td colspan="6" style="text-align: center; color: var(--text-muted); padding: 2rem;">
            <i class="fa-solid fa-check-circle" style="color: #22c55e; font-size: 1.5rem;"></i><br><br>
            Nenhuma imagem duplicada encontrada entre INEPs diferentes.
        </td></tr>`;
        return;
    }

    pairs.forEach((pair, idx) => {
        const imgA = pair.imgA;
        const imgB = pair.imgB;

        const badgeClass = pair.similarity === 100 ? 'badge-green' : 'badge-orange';

        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td>
                <div style="font-size: 1rem; font-weight: 700; color: var(--accent);">
                    ${imgA.inep || '—'}
                </div>
                <div style="font-size: 0.78rem; color: var(--text-muted); margin-top: 0.2rem;">
                    ${imgA.uf ? `<span class="badge" style="background:rgba(59,130,246,0.2);">${imgA.uf}</span> ` : ''}
                    ${imgA.fornecedor ? `<span style="font-size:0.73rem;">${imgA.fornecedor.replace(/ \(RI\)| \(RE\)/g, '')}</span>` : ''}
                </div>
                <div style="font-size:0.72rem; color: var(--text-muted); margin-top:0.1rem;">Pág. ${imgA.page}</div>
            </td>
            <td>
                <img src="/extracted_images/${imgA.thumb_filename}" class="thumb-preview" alt="Imagem A" style="max-width:80px; max-height:80px; border-radius:6px; cursor:pointer;" onclick="openCompareModal(${idx})">
            </td>
            <td style="text-align:center;">
                <span class="badge ${badgeClass}" style="font-size:1rem; padding: 0.4rem 0.7rem;">${pair.similarity}%</span><br>
                <small style="color: var(--text-muted); font-size:0.72rem;">${pair.type === 'Exata (100%)' ? '🔴 Exata' : '🟡 Visual'}</small>
            </td>
            <td>
                <div style="font-size: 1rem; font-weight: 700; color: #f87171;">
                    ${imgB.inep || '—'}
                </div>
                <div style="font-size: 0.78rem; color: var(--text-muted); margin-top: 0.2rem;">
                    ${imgB.uf ? `<span class="badge" style="background:rgba(59,130,246,0.2);">${imgB.uf}</span> ` : ''}
                    ${imgB.fornecedor ? `<span style="font-size:0.73rem;">${imgB.fornecedor.replace(/ \(RI\)| \(RE\)/g, '')}</span>` : ''}
                </div>
                <div style="font-size:0.72rem; color: var(--text-muted); margin-top:0.1rem;">Pág. ${imgB.page}</div>
            </td>
            <td>
                <img src="/extracted_images/${imgB.thumb_filename}" class="thumb-preview" alt="Imagem B" style="max-width:80px; max-height:80px; border-radius:6px; cursor:pointer;" onclick="openCompareModal(${idx})">
            </td>
            <td>
                <button class="btn btn-primary" style="padding: 0.4rem 0.8rem; font-size: 0.85rem;" onclick="openCompareModal(${idx})">
                    <i class="fa-solid fa-eye"></i> Ver
                </button>
            </td>
        `;
        tbody.appendChild(tr);
    });
}

function updateSimLabel(val) {
    document.getElementById('simLabel').innerText = `${val}%`;
}

function filterResults() {
    const search = document.getElementById('searchInput').value.toLowerCase();
    const typeFilter = document.getElementById('typeFilter').value;
    const minSim = parseFloat(document.getElementById('simSlider').value);

    const filtered = allDuplicatePairs.filter(p => {
        const strA = `${p.imgA.fornecedor || ''} ${p.imgA.inep || ''} ${p.imgA.uf || ''} ${p.imgA.pdf_filename || ''} ${p.imgA.filename || ''}`.toLowerCase();
        const strB = `${p.imgB.fornecedor || ''} ${p.imgB.inep || ''} ${p.imgB.uf || ''} ${p.imgB.pdf_filename || ''} ${p.imgB.filename || ''}`.toLowerCase();

        const matchSearch = strA.includes(search) || strB.includes(search);
        
        let matchType = true;
        if (typeFilter === 'exact') matchType = (p.similarity === 100);
        if (typeFilter === 'visual') matchType = (p.similarity < 100);

        const matchSim = p.similarity >= minSim;

        return matchSearch && matchType && matchSim;
    });

    renderTable(filtered);
}

function openCompareModal(pairIndex) {
    const pair = allDuplicatePairs[pairIndex];
    if (!pair) return;

    const imgA = pair.imgA;
    const imgB = pair.imgB;

    document.getElementById('modalImgA').src = `/extracted_images/${imgA.thumb_filename}`;
    document.getElementById('modalFileA').innerText = imgA.filename;
    document.getElementById('modalPageA').innerText = imgA.page;
    document.getElementById('modalDimA').innerText = `${imgA.width}x${imgA.height}px`;

    document.getElementById('modalImgB').src = `/extracted_images/${imgB.thumb_filename}`;
    document.getElementById('modalFileB').innerText = imgB.filename;
    document.getElementById('modalPageB').innerText = imgB.page;
    document.getElementById('modalDimB').innerText = `${imgB.width}x${imgB.height}px`;

    document.getElementById('modalSimBadge').innerText = `${pair.similarity}%`;
    document.getElementById('modalSimText').innerText = pair.type;

    document.getElementById('compareModal').style.display = 'flex';
}

function closeModal() {
    document.getElementById('compareModal').style.display = 'none';
}

function downloadExcelReport() {
    window.location.href = '/api/download-report';
}
