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
├── analisar_fase.py                # Motor da Análise por Fase + histórico
├── import_schools_to_supabase.py   # Script de importação CSV → Supabase
├── SISTEMA_RDO_ANALYZER.md         # Esta documentação
│
├── pares_duplicatas.py             # Visão por par de escolas + falso positivo
├── static/
│   ├── index.html                  # Interface web principal (tema escuro)
│   ├── duplicatas.html             # Painel de Duplicatas (tema claro)
│   ├── duplicatas.css              # Tokens e componentes do painel
│   ├── duplicatas.js               # Lógica do painel
│   ├── styles.css                  # Design glassmorphism + dark mode
│   ├── app.js                      # Lógica frontend
│   └── fase.js                     # Análise por Fase + Histórico
│
├── Banco de Escolas/
│   └── Correta.csv                 # CSV exportado do Bubble (26.963 escolas)
│
├── extracted_images/               # Cache de thumbnails das imagens extraídas
├── historico/                      # Execuções salvas (meta, grupos, analisados, ZIP)
├── cache_extracao/                 # Cache retomável dos downloads por fase
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

## 🎯 Análise por Fase (com Histórico) — aba principal

Fluxo de um clique para varrer **todos os PDFs de todas as escolas de uma fase**.

### Como usar

1. Abra a aba **Análise por Fase**.
2. Escolha a fase no primeiro seletor (ex.: `Fase 5 — 4.944 escolas`).
3. Deixe o segundo seletor em **-- Todos os Fornecedores --** (ou escolha um).
4. Clique em **Extrair Imagens Duplicadas**.
5. Acompanhe a barra de progresso com etapa, tempo decorrido e ETA.
6. Ao terminar, o resultado visual aparece na tela e fica salvo no **Histórico**.

### O que aparece na tela

O resultado tem **duas visões**, em sub-abas:

**1. Todos os PDFs Analisados** (padrão) — a mesma tabela da aba
`Todos os PDFs Analisados`, mas escopada à execução: uma linha por imagem
auditada com escola/fornecedor, miniatura, selo `⚠️ Duplicada` ou `✅ Única`,
a coluna **Duplicada com (outro INEP)** e os botões de abrir. Filtros por busca
livre e por status de duplicata. As duplicadas aparecem primeiro.

A coluna *Duplicada com* existe porque sem ela a tabela mostra várias linhas do
mesmo INEP marcadas “Duplicada” e parece estar apontando a própria escola — o par
está em outro INEP, e agora ele aparece na linha **com os próprios botões**:

```
Escola / Fornecedor   Imagem  Duplicata     Duplicada com      PDF do RDO
12009679  AC          [foto]  ⚠️ Duplicada  12020680 · AC      ...GESAC.pdf  Pág. 2
  STEIN TELECOM / ...                       [imagem][PDF       [Ver imagem]
                                             12020680]         [PDF 12009679]
```

Cada botão de PDF é **nomeado com o INEP** e abre o RDO daquela escola — são
arquivos distintos (`ESC MARIA DO CARMO RAMOS` e `ESC NOVA VIDA`), cada um na
página onde a imagem está.

**2. Grupos de Duplicatas** — um cartão por grupo, com **um painel por escola**
lado a lado (Escola A, Escola B, …). O cabeçalho traz um botão por escola,
`PDF 12009679` e `PDF 12020680`, e cada painel repete o seu, junto da foto:

```
G00112  [STEIN TELECOM / STEIN TELECOM FILIAL PR]  2 escolas · Exata (100%)
                                  AC   [PDF 12009679] [PDF 12020680]
┌──────────────────────────┐   ┌──────────────────────────┐
│ Escola A  12009679   AC  │   │ Escola B  12020680   AC  │
│ [        foto          ] │   │ [        foto          ] │
│ Pág. 2 · 2048×1153       │   │ Pág. 2 · 2048×1153       │
│ [Ver imagem][PDF 1200…]  │   │ [Ver imagem][PDF 1202…]  │
└──────────────────────────┘   └──────────────────────────┘
```

O PDF abre na página da imagem via `#page=N`. Filtros por busca e por
“N+ escolas envolvidas”.

| Componente | Descrição |
|---|---|
| **Métricas** | Escolas na fase, PDFs com imagens, imagens analisadas, grupos, escolas e fornecedores afetados |
| **Baixar Imagens (ZIP)** | ZIP com as imagens duplicadas, uma pasta por grupo |

### Histórico persistente

Cada execução é gravada em `historico/<run_id>/` **em disco** — não em memória.
Reiniciar o servidor, fechar o navegador ou perder a conexão não apaga nenhum
resultado. Na aba **Histórico** é possível reabrir (**Ver**), baixar o ZIP das imagens
ou remover uma execução.

```
historico/fase5_todos_20260818_143022/
├── meta.json          métricas, critério e status da execução
├── grupos.json        grupos de duplicatas (alimenta a visão de grupos)
├── analisados.jsonl   todos os PDFs auditados (alimenta a tabela)
└── imagens_duplicadas.zip   gerado sob demanda no primeiro download
```

### Critério de duplicata

A regra fixa é **INEPs diferentes com fornecedores iguais**. Três caixas na tela
ajustam o tratamento do ruído, todas ligadas por padrão:

| Caixa | Ligada (padrão) | Desligada |
|---|---|---|
| **Só INEPs diferentes do mesmo fornecedor** | O fornecedor reaproveitou a foto entre escolas dele | Qualquer par de INEPs diferentes |
| **Só imagens 100% idênticas (SHA-256)** | Byte a byte | Inclui as visualmente parecidas (pHash ≤ 8) |
| **Ignorar templates do sistema** | Descarta logo/páginas padrão | Mantém tudo |

Na fase 5 completa (4.944 escolas, 28.032 PDFs, 264.131 imagens):

| Critério | Grupos | Escolas afetadas | Maior grupo |
|---|---|---|---|
| **Padrão (as três ligadas)** | **239** | **107 (2,2%)** | 5 escolas |
| Sem o filtro de template | 332 | 1.612 (33%) | 534 escolas |
| Com pHash ligado | 3.015 | 4.589 (93%) | 754 escolas |

**Por que o pHash fica desligado por padrão.** O pHash une por transitividade:
1.206 heatmaps *distintos* do UniFi acabam num único "grupo" de 754 escolas só
porque a interface é idêntica. Ligue quando quiser pegar recortes e
recompressões, ciente desse efeito.

**Uma regra resolve os dois casos.** Não há lista manual de templates. Uma foto que
um fornecedor reusou aparece apenas em escolas dele — pode haver vários nomes no
campo, porque o RE varia de escola para escola, mas o nome do responsável está em
todas. Já o logo da EACE e a página "Aviso de desativação de escola" aparecem em
escolas de fornecedores sem nenhum nome em comum. A regra é a interseção: **se
existe um nome de fornecedor presente em todas as escolas que contêm a imagem, é
reaproveitamento; se nenhum nome cobre todas, as escolas são de fornecedores
distintos — o que também descreve exatamente os templates do sistema.**

Por isso *Ignorar templates* só tem efeito quando *mesmo fornecedor* está
desmarcado: exigir fornecedor em comum já aplica o mesmo teste. Testar a interseção
em vez de contar nomes evita descartar por engano uma reutilização real só porque
as escolas têm parceiros de rede externa diferentes.

### Nomes de fornecedor (RI / RE)

O campo `fornecedor` traz os dois juntos
(`STEIN TELECOM (RI) / STEIN TELECOM FILIAL PR (RE)`), então a comparação usa os
**nomes individuais**.

Sai **um grupo por imagem**, creditando todos os corresponsáveis no campo
`fornecedor` do grupo (`STEIN TELECOM / STEIN TELECOM FILIAL PR`). Emitir um grupo
por nome gerava dois achados idênticos para as mesmas escolas — na fase 5 eram 272
grupos onde há 239 achados distintos.

### Agrupamento em vez de pares

O motor da fase agrupa as ocorrências em **grupos** (união transitiva por
SHA-256 e por pHash), em vez de listar pares A×B. Um grupo só é considerado
duplicata quando aparece em **duas ou mais escolas (INEPs) diferentes** —
repetição dentro do mesmo RDO não é apontada.

Os pares gravados em `duplicatas_rdo` são **encadeados** (escola 1×2, 2×3, 3×4…),
não combinatórios: um grupo com k escolas gera k−1 linhas em vez de k(k−1)/2. Na
fase 5 completa isso é a diferença entre ~28 mil linhas e **6,1 milhões** — que
além de inviável de gravar, não acrescenta informação, já que basta agrupar por
`sha256` para recuperar todas as escolas envolvidas.

### Cache e retomada

- Os downloads ficam em `cache_extracao/<fase>/cache_imagens.jsonl`, marcados PDF a PDF.
- Os pHash calculados ficam em `phash_memo.jsonl`, evitando recalcular a mesma foto.
- Se a análise for **cancelada** ou cair no meio, a próxima execução **retoma de onde parou**.
- Repetir a mesma fase com o cache quente leva segundos.

### Desempenho

O custo dominante era `extract_image()` do PyMuPDF, chamado uma vez por
*aparição* de imagem. Como o mesmo objeto de imagem se repete em várias páginas
do RDO (um relatório com 570 aparições tem ~22 imagens distintas), a extração é
feita **uma vez por `xref`**, registrando junto as páginas onde aparece.

| | Antes | Depois |
|---|---|---|
| Ritmo | 0,8 PDF/s | 6,0 PDF/s |
| Fase 5 completa (28.032 PDFs) | ~10 h | **~35 min** (medido) |

Com o cache de download completo, reanalisar a fase 5 inteira com outro
critério leva **10 segundos** (sem pHash) ou **~10 min** (com pHash, por causa
do passo O(n²) sobre 181 mil hashes).

Outros ganhos: tarefa por **PDF** (não por escola — os RDOs vão de 30 KB a 25 MB),
memo de pHash por SHA-256 e decodificação em modo `draft` do JPEG.

### Endpoints

| Método | Endpoint | Descrição |
|---|---|---|
| `GET` | `/api/fases` | Fases disponíveis com contagem de escolas |
| `POST` | `/api/scan-fase` | Inicia a varredura `{fase, fornecedor, workers, amostra, gravar_supabase}` |
| `GET` | `/api/fase-status` | Progresso da varredura em andamento |
| `POST` | `/api/cancelar-fase` | Cancela a varredura (o cache é preservado) |
| `GET` | `/api/historico` | Lista as execuções salvas |
| `GET` | `/api/historico/<run_id>` | Resultado de uma execução (com paginação e filtros) |
| `DELETE` | `/api/historico/<run_id>` | Remove uma execução |
| `GET` | `/api/historico/<run_id>/analisados` | Lista os PDFs auditados na execução (paginada, com filtros) |
| `GET` | `/api/historico/<run_id>/zip` | Baixa o ZIP das imagens duplicadas |

### Linha de comando

```bash
python analisar_fase.py                        # fase 5, todos os fornecedores
python analisar_fase.py --fase 4.1
python analisar_fase.py --fornecedor BRISANET
python analisar_fase.py --amostra 30           # teste rápido
python analisar_fase.py --sem-supabase --zip
python analisar_fase.py --qualquer-fornecedor   # não exige mesmo fornecedor
python analisar_fase.py --so-analise            # reanalisa o cache sem baixar nada
```

---

## 🖥️ Painel de Duplicatas — `/duplicatas`

Página própria, tema claro, para **triagem** dos achados. A aba `Análise por Fase`
continua sendo onde a varredura é executada; o painel é onde o resultado é
trabalhado. São arquivos separados (`static/duplicatas.html|css|js`), então nada
do tema escuro antigo é herdado ou alterado.

### Uma linha por PAR de escolas

O motor produz um grupo por **imagem** compartilhada. Para auditar, o que importa é
a relação entre duas escolas: se INEP 1 e INEP 2 têm 33 fotos iguais, isso é **um
caso com 33 evidências**, não 33 casos. O painel reagrupa em pares
`INEP A × INEP B` — na fase 5, os 239 grupos viram **72 pares**.

A tabela é dividida em três blocos coloridos, como no app de pareamento:

```
ESCOLA A · INEP 1        │ IMAGENS DUPLICADAS      │ ESCOLA B · INEP 2       │ FALSO
Escola      PDF do RDO   │ Evidências  Miniaturas  │ Escola     PDF do RDO   │ POSITIVO
─────────────────────────┼─────────────────────────┼─────────────────────────┼─────────
21065810                 │ 33 imagens iguais       │ 21205809                │  ( ) Não
EM RAIMUNDO ABREU DA…    │ [100% exatas][29 fotos] │ EM RAIMUNDO ABREU DA…   │
MA · ZÉ DOCA             │ [mesmo PDF nos dois]    │ MA · ZÉ DOCA            │
[ST1 INTERNET]           │ ▣▣▣▣▣▣ +27              │ [ST1 INTERNET]          │
[PDF 21065810]           │                         │ [PDF 21205809]          │
```

Cada lado tem o seu botão `PDF <inep>`, que abre o RDO daquela escola na página da
imagem. As miniaturas abrem um visualizador com navegação entre as evidências e um
botão para alternar entre o lado A e o lado B da mesma imagem.

### Toggle de falso positivo

Toda linha nasce com o toggle **desativado**. Ao ligar, a marcação vai para
`historico/<run_id>/falsos_positivos.json` — sobrevive a recarregar a página e a
reiniciar o servidor. O cartão *Falsos positivos* mostra quantos foram marcados e
quantos pares seguem pendentes de triagem, e o filtro *Ocultar marcados* (padrão)
faz a lista encurtar conforme você vai triando.

### Filtros

| Filtro | Para que serve |
|---|---|
| **Nome do PDF: não contém / contém** | Texto livre no nome do arquivo (ex.: `não contém tiff`) |
| **Gravidade** | `Mesmo PDF nos dois INEPs` ou `PDF é de outra escola` |
| **Similaridade** | Somente 100% exatas, ou os que contêm visuais |
| **Tipo de imagem** | Somente fotos de câmera (proporção 4:3 / 3:4) |
| **Mínimo de imagens** | 2, 3, 5 ou 10+ evidências no par |
| **Município das escolas** | Mesmo município ou municípios diferentes |
| **Estado / Município / Fornecedor** | Preenchidos com os valores presentes no resultado |
| **Falso positivo** | Ocultar marcados (padrão), mostrar todos, somente marcados |
| **Busca** | INEP, nome da escola, município ou fornecedor |

Os filtros rodam **no servidor**, sobre o conjunto inteiro da execução — as métricas
e os gráficos refletem o recorte filtrado, e o cartão de pares mostra quanto sobrou
do total. O **Exportar CSV** respeita os filtros ativos.

### Dois sinais que o painel calcula

Além da imagem repetida, o painel deriva do **nome do arquivo** duas coisas que o
motor não olhava:

- **PDF é de outra escola** — o nome segue `INEP - UF - MUNICÍPIO - ESCOLA - TIPO.pdf`;
  quando o INEP do nome não bate com o INEP do registro, o RDO anexado pertence a
  outra escola. São 5 pares na fase 5.
- **Mesmo PDF nos dois INEPs** — os dois lados apontam para o mesmo arquivo. Não é
  foto repetida: é o relatório de uma escola servindo de RDO para a outra. São 3
  pares na fase 5.

Escola, município e UF exibidos vêm desse mesmo nome de arquivo — `escolas_conectadas`
não guarda esses campos. Nomes fora do padrão ficam em branco em vez de trazer dado
errado.

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
| 2026-08-18 | 1.1 | Aba Análise por Fase com um clique, histórico persistente em disco, resultado visual por grupos, download de ZIP e extração 6,7x mais rápida |
| 2026-08-18 | 1.2 | Duplicata restrita a INEPs diferentes do mesmo fornecedor, visão “Todos os PDFs Analisados” dentro do histórico, pares encadeados em vez de combinatórios e remoção do relatório Excel |
| 2026-08-18 | 1.3 | Modo “só idênticas” (SHA-256) e filtro automático de templates do sistema como padrão: fase 5 sai de 3.015 grupos / 93% das escolas para 239 grupos / 2,2% |
| 2026-08-18 | 1.4 | Coluna “Duplicada com (outro INEP)” na tabela, painéis lado a lado por escola com botões Ver imagem / Ver PDF, e um único grupo por imagem creditando RI e RE juntos |
| 2026-08-19 | 2.0 | Painel de Duplicatas em `/duplicatas`: tema claro, uma linha por par `INEP A × INEP B`, filtros de contém/não contém, gravidade, fotos de câmera e mais, toggle de falso positivo persistente e exportação CSV |
| 2026-08-18 | 1.5 | Botões de PDF nomeados com o INEP (um por escola, no cabeçalho do grupo, no painel e na contraparte da tabela) e desativação do cache de estáticos no Flask |
