-- ---------------------------------------------------------------------------
-- Auditoria de duplicatas de RDO — estrutura no Supabase
--
-- Cole este arquivo inteiro no SQL Editor do projeto RDO Escolas Sisop e
-- execute. Pode rodar mais de uma vez: tudo e `if not exists`.
--
-- Por que o par e guardado inteiro em `snapshot`:
--     Antes, o envio guardava so os `par_ids` e relia as evidencias de
--     historico/<run_id>/grupos.json em disco. Se aquela pasta sumisse — e ela
--     nao vai para o git, entao a VPS subiu sem ela — a auditoria abria vazia.
--     Aqui o par e copiado por inteiro no momento do envio: o registro para de
--     depender de arquivo local e deixa de mudar se a execucao for refeita.
-- ---------------------------------------------------------------------------

-- Lote encaminhado do Painel de Duplicatas para a auditoria.
create table if not exists public.auditoria_envios (
    envio_id        text primary key,
    run_id          text not null,
    fase            text,
    criado_em       timestamptz not null default now(),
    autor           text,
    observacao      text,
    status          text not null default 'enviado',
    resumo          jsonb not null default '{}'::jsonb,
    registrado_em   timestamptz not null default now()
);

-- Um par INEP A x INEP B dentro de um envio, com copia integral das evidencias.
create table if not exists public.auditoria_envio_pares (
    id              bigserial primary key,
    envio_id        text not null
                    references public.auditoria_envios(envio_id) on delete cascade,
    par_id          text not null,
    inep_a          bigint,
    inep_b          bigint,
    uf_a            text,
    uf_b            text,
    municipio_a     text,
    municipio_b     text,
    escola_a        text,
    escola_b        text,
    fornecedores    text[],
    qtd_imagens     integer,
    qtd_fotos       integer,
    mesmo_pdf       boolean,
    pdf_divergente  boolean,
    fora_do_raio    integer,
    distancia_max_m double precision,
    snapshot        jsonb not null,
    criado_em       timestamptz not null default now(),
    unique (envio_id, par_id)
);

-- Documento de resolucao anexado por INEP depois da auditoria.
--
-- O ARQUIVO fica no disco da VPS (auditoria/documentos/<envio>/<inep>/), para
-- nao encher o Supabase com PDF de parecer. Aqui fica so o metadado — mas com
-- tamanho e sha256, entao da para provar o que foi anexado mesmo que o arquivo
-- se perca. Isso torna o volume de /app/auditoria obrigatorio na VPS.
create table if not exists public.auditoria_documentos (
    doc_id          text primary key,
    envio_id        text not null
                    references public.auditoria_envios(envio_id) on delete cascade,
    inep            bigint not null,
    par_id          text,
    arquivo_nome    text not null,
    arquivo_path    text not null,
    bucket          text not null default 'disco-vps',
    sha256          text,
    tamanho_bytes   bigint,
    tipo_mime       text,
    observacao      text,
    enviado_por     text,
    criado_em       timestamptz not null default now()
);

-- Trilha append-only de tudo que acontece. Nada aqui e atualizado nem apagado
-- pela aplicacao: e o registro de que a movimentacao existiu, mesmo que o
-- envio ou o documento sejam removidos depois.
create table if not exists public.auditoria_eventos (
    id              bigserial primary key,
    tipo            text not null,
    envio_id        text,
    par_id          text,
    doc_id          text,
    inep            bigint,
    detalhe         jsonb not null default '{}'::jsonb,
    criado_em       timestamptz not null default now()
);

create index if not exists idx_aud_pares_envio    on public.auditoria_envio_pares (envio_id);
create index if not exists idx_aud_pares_inep_a   on public.auditoria_envio_pares (inep_a);
create index if not exists idx_aud_pares_inep_b   on public.auditoria_envio_pares (inep_b);
create index if not exists idx_aud_docs_envio     on public.auditoria_documentos (envio_id);
create index if not exists idx_aud_docs_inep      on public.auditoria_documentos (inep);
create index if not exists idx_aud_eventos_envio  on public.auditoria_eventos (envio_id);
create index if not exists idx_aud_eventos_tipo   on public.auditoria_eventos (tipo, criado_em desc);

-- RLS ligada e sem policy: so a service_role (que ignora RLS) enxerga. Sem isto
-- a chave anon publica leria dado de fiscalizacao.
alter table public.auditoria_envios      enable row level security;
alter table public.auditoria_envio_pares enable row level security;
alter table public.auditoria_documentos  enable row level security;
alter table public.auditoria_eventos     enable row level security;
