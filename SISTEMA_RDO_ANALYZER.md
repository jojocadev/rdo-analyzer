# 📄 Documentação do Sistema – RDO Image Analyzer

> Versão 1.0 | Última Atualização: Agosto 2026

---

## 🧭 Visão Geral

O **RDO Image Analyzer** é um sistema web local desenvolvido em Python + Flask + HTML/CSS/JS que detecta **imagens duplicadas em relatórios RDO (Relatório de Desempenho Operacional)** gerados em PDF pelas escolas do programa de conectividade escolar.

O sistema integra-se diretamente com o banco de dados **Supabase** (PostgreSQL gerenciado em nuvem) para consultar o cadastro de escolas conectadas e então baixar, extrair, comparar e reportar imagens duplicadas nos PDFs daquelas escolas — organizando os resultados **por fornecedor**.

---

## 🏛️ Arquitetura do Sistema

```
┌─────────────────────────────────────────────────────────────────┐
│                     NAVEGADOR DO USUÁRIO                        │
│           http://localhost:5000 (Interface Web)                 │
│                                                                 │
│   ┌──────────┐   ┌────────────────────────────┐                │
│   │ Tab 1    │   │ Tab 2 (Upload PDFs)         │                │
│   │ Supabase │   │ Tab 3 (Varredura Pasta)     │                │
│   │ Dropdown │   │                             │                │
│   └──────────┘   └────────────────────────────┘                │
└─────────────┬───────────────────────────────────────────────────┘
              │ HTTP REST
              ▼
┌─────────────────────────────────────────────────────────────────┐
│               SERVIDOR FLASK LOCAL (app.py)                     │
│                        Porta 5000                               │
│                                                                 │
│  Endpoints:                                                     │
│  • POST /api/scan-supabase-supplier  → Inicia análise Supabase │
│  • GET  /api/suppliers               → Lista fornecedores       │
│  • POST /api/upload                  → Upload de PDFs           │
│  • GET  /api/status                  → Polling de progresso     │
│  • GET  /api/results                 → Retorna resultados       │
│  • GET  /api/download-excel          → Baixa relatório .xlsx    │
│  • GET  /extracted_images/<arquivo>  → Serve thumbnails         │
└─────────────┬───────────────────────────────────────────────────┘
              │
   ┌──────────┴───────────┐
   │                      │
   ▼                      ▼
┌────────────┐     ┌───────────────────────────────┐
│  Supabase  │     │    Motor de Análise (Python)   │
│ PostgreSQL │     │                               │
│ (Nuvem)    │     │  1. Fetch escolas (Supabase)  │
│            │     │  2. Download PDFs (HTTP)       │
│ Tabela:    │     │  3. Extrai imagens (PyMuPDF)   │
│ escolas_   │     │  4. Calcula SHA-256 + pHash    │
│ conectadas │     │  5. Compara e agrupa           │
│            │     │  6. Gera relatório Excel       │
└────────────┘     └───────────────────────────────┘
```

---

## 🗄️ Banco de Dados – Supabase

### Projeto

| Campo | Valor |
|---|---|
| **Plataforma** | Supabase (PostgreSQL gerenciado) |
| **Project URL** | `https://jclwfskzstjwmfskbanz.supabase.co` |
| **Tabela Principal** | `escolas_conectadas` |
| **Total de Registros** | 26.963 escolas conectadas |

### Schema da Tabela `escolas_conectadas`

| Coluna | Tipo | Descrição |
|---|---|---|
| `id` | `UUID` | Chave primária gerada automaticamente |
| `escola_id_bubble` | `TEXT UNIQUE` | ID de referência do Bubble (`bubble_row_N`) |
| `inep` | `BIGINT` | Código INEP da escola |
| `uf` | `TEXT` | Unidade Federativa (ex: `AM`, `BA`, `PA`) |
| `fornecedor` | `TEXT` | Nome do(s) fornecedor(es) RI e RE |
| `tipo_fornecedor` | `TEXT` | Classificação: `RI`, `RE` ou `RI / RE` |
| `status_geral` | `TEXT` | Status da escola (ex: `Conectada`) |
| `fase` | `TEXT` | Fase do programa (ex: `3`, `4.1`, `4.2`, `Piloto`) |
| `books` | `JSONB` | Array de URLs dos PDFs do RDO |
| `atualizacao` | `TIMESTAMPTZ` | Data de última atualização |
| `ativacao_rede_externa` | `TIMESTAMPTZ` | Data de ativação da rede externa (RE) |
| `ativacao_rede_interna` | `TIMESTAMPTZ` | Data de ativação da rede interna (RI) |
| `criado_em` | `TIMESTAMPTZ` | Data de inserção no banco |

### Fornecedores Cadastrados (24 únicos)

| # | Fornecedor | # | Fornecedor |
|---|---|---|---|
| 1 | ACESSO.NET | 13 | INFORCENTER INFORMATICA |
| 2 | ALT | 14 | INFOTEC |
| 3 | ARAUJO E ALMEIDA SERVICOS LTDA | 15 | LINEJET |
| 4 | ARCAN INTERNET PREMIUM | 16 | MAM INFORMATICA |
| 5 | ATEL TELECOM | 17 | MAMTECH TECNOLOGIA E SERVICOS |
| 6 | AW FIBRA | 18 | MEGATELECOM |
| 7 | BEDUTECH | 19 | MTNSAT |
| 8 | BRASIL TEC NET | 20 | NETCON |
| 9 | BRISANET | 21 | ROTASUL TELECOM |
| 10 | CAVALCANTE NET | 22 | TELEBRAS |
| 11 | COMANDOS INFORMATICA | 23 | TERESINET TELECOM |
| 12 | EVOLVE INTERNET | 24 | WM TELECOM |

---

## 📦 Estrutura de Arquivos do Projeto

```
C:\Projetos AI\Sisop\RDO images\
│
├── app.py                          # Servidor Flask – todos os endpoints
├── analyzer.py                     # Motor local (upload/pasta)
├── supabase_analyzer.py            # Motor integrado ao Supabase
├── import_schools_to_supabase.py   # Script de importação CSV → Supabase
├── SISTEMA_RDO_ANALYZER.md         # Esta documentação
│
├── static/
│   ├── index.html                  # Interface web principal
│   ├── styles.css                  # Design glassmorphism + dark mode
│   └── app.js                      # Lógica frontend
│
├── Banco de Escolas/
│   └── Correta.csv                 # CSV exportado do Bubble (26.963 escolas)
│
├── extracted_images/               # Cache de thumbnails das imagens extraídas
├── reports/                        # Relatórios Excel gerados
│   └── Relatorio_*.xlsx
│
└── start.bat                       # Script de inicialização Windows
```

---

## ⚙️ Dependências Python

| Biblioteca | Finalidade |
|---|---|
| `flask` | Servidor web HTTP local |
| `PyMuPDF` (fitz) | Extração de imagens de arquivos PDF |
| `Pillow` | Manipulação e conversão de imagens |
| `imagehash` | Cálculo de pHash (Perceptual Hash) |
| `pandas` | Leitura de CSV e geração de Excel |
| `openpyxl` | Escrita de arquivos `.xlsx` |
| `python-dateutil` | Parsing de datas do CSV |
| `hashlib` | Cálculo de SHA-256 (nativa Python) |
| `urllib` | Download de PDFs via HTTP (nativa Python) |

### Instalação

```bash
pip install flask PyMuPDF Pillow imagehash pandas openpyxl python-dateutil
```

---

## 🔍 Como Funciona a Detecção de Duplicatas

O sistema usa **duas técnicas combinadas**:

### 1. Duplicatas Exatas — SHA-256

- Calcula o hash SHA-256 dos bytes brutos da imagem.
- Hash idêntico entre dois arquivos = **100% duplicata**.
- Detecta fotos copiadas e coladas diretamente.

### 2. Duplicatas Visuais — pHash (Perceptual Hash)

- Reduz cada imagem a uma impressão digital de 64 bits.
- Calcula a **distância de Hamming** entre dois hashes.
- Distância ≤ 8 bits (padrão) → imagens são **visualmente similares**.
- Detecta fotos recomprimidas, redimensionadas ou com pequenas alterações.

```
Fórmula de Similaridade:
Similaridade (%) = (1 - distância / 64) × 100
```

### Filtros de Qualidade

- Imagens com menos de **120×120 pixels** são descartadas (logos, ícones).
- Apenas imagens embutidas nos PDFs são extraídas.

---

## 🖥️ Interface Web – Funcionalidades

### Aba 1 — Análise por Fornecedor (Supabase)

- **Dropdown de Fornecedores:** Lista carregada dinamicamente do Supabase.
- **Botão "Analisar Fornecedor":** Analisa apenas as escolas daquele fornecedor.
- **Botão "Analisar BASE COMPLETA":** Analisa todas as 26.963 escolas de uma vez.

### Aba 2 — Upload de PDFs

- Drag & drop ou seleção de múltiplos arquivos `.pdf`.
- Análise local sem conexão com o Supabase.

### Aba 3 — Varredura de Pasta Local

- Informe o caminho de uma pasta local.
- O sistema encontra e analisa todos os PDFs automaticamente.

### Seção de Resultados

| Componente | Descrição |
|---|---|
| **Métricas** | Escolas/PDFs, imagens, pares exatos e visuais |
| **Cartões por Fornecedor** | Um cartão por fornecedor com totais e INEPs afetados |
| **Botão "Ver Duplicatas"** | Filtra a tabela para aquele fornecedor |
| **Lista de INEPs Afetados** | INEPs onde foram encontradas duplicatas |
| **Tabela de Duplicatas** | INEP A, Fornecedor A, %, INEP B, Fornecedor B |
| **Modal de Comparação** | Visualização lado a lado ampliada |
| **Filtros** | Busca por INEP/fornecedor/UF, tipo, % mínima |
| **Download Excel** | Relatório `.xlsx` com 3 abas detalhadas |

---

## 📡 Endpoints da API

| Método | Endpoint | Descrição |
|---|---|---|
| `GET` | `/` | Painel web principal |
| `GET` | `/api/suppliers` | Lista fornecedores únicos do Supabase |
| `POST` | `/api/scan-supabase-supplier` | Inicia análise por fornecedor |
| `POST` | `/api/upload` | Upload de PDFs |
| `POST` | `/api/scan-folder` | Varredura de pasta local |
| `GET` | `/api/status` | Estado do processamento |
| `GET` | `/api/results` | Resultados completos em JSON |
| `GET` | `/api/download-excel` | Download do relatório `.xlsx` |
| `GET` | `/extracted_images/<file>` | Thumbnails das imagens |

### Payload: `POST /api/scan-supabase-supplier`

```json
{
  "fornecedor": "MAM INFORMATICA",  // vazio = analisa todos
  "limit": 27000
}
```

---

## 📊 Relatório Excel – Estrutura

### Aba 1 — Resumo Geral

| Métrica | Valor Exemplo |
|---|---|
| Total de Escolas Analisadas | 20 |
| Total de Imagens Extraídas | 1.640 |
| Pares de Duplicatas Encontrados | 4.323 |
| Duplicatas Exatas (100%) | 4.296 |
| Duplicatas Visuais (<100%) | 27 |

### Aba 2 — INEPs Afetados

Colunas: `INEP`, `UF`, `Fornecedor`, `Contagem de Duplicatas`

### Aba 3 — Duplicatas Detectadas

Colunas: `Tipo`, `Similaridade (%)`, `Distância Hamming`, `INEP A`, `UF A`, `Fornecedor A`, `PDF A`, `Página A`, `URL A`, `INEP B`, `UF B`, `Fornecedor B`, `PDF B`, `Página B`, `URL B`, `SHA256`

---

## 🚀 Como Iniciar o Sistema

```bash
# No terminal Windows (PowerShell ou CMD):
cd "C:\Projetos AI\Sisop\RDO images"
python app.py

# Ou via script:
start.bat
```

Acesse em: **http://localhost:5000**

---

## 🔐 Credenciais e Configuração

As credenciais do Supabase ficam em `supabase_analyzer.py` e `import_schools_to_supabase.py`:

```python
SUPABASE_URL = "https://jclwfskzstjwmfskbanz.supabase.co/rest/v1"
SERVICE_KEY  = "<service_role_key>"
```

> ⚠️ **Nunca exponha a `SERVICE_KEY` publicamente.** Ela possui permissão total sobre o banco.

---

## 📅 Histórico de Versões

| Data | Versão | Mudança |
|---|---|---|
| 2026-08-03 | 0.1 | Motor de análise local (PyMuPDF + pHash + SHA-256) |
| 2026-08-03 | 0.2 | Interface web glassmorphism com drag & drop |
| 2026-08-04 | 0.5 | Importação de 26.963 escolas para o Supabase |
| 2026-08-04 | 0.6 | Extração de INEP, UF e tipo_fornecedor das URLs |
| 2026-08-04 | 0.7 | CSV completo com fornecedores, fases e datas |
| 2026-08-04 | 0.8 | Análise de duplicatas por fornecedor via Supabase |
| 2026-08-04 | 0.9 | Visão agrupada por fornecedor com cartões interativos |
| 2026-08-04 | 1.0 | Dropdown de fornecedores + botão Analisar BASE COMPLETA |
