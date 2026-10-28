-- ---------------------------------------------------------------------------
-- Historico das execucoes do Painel de Duplicatas
--
-- Cole no SQL Editor do projeto RDO Escolas Sisop e execute. Pode rodar mais de
-- uma vez: tudo e `if not exists`.
--
-- Por que so uma parte do historico vem para ca:
--     A pasta historico/<run_id> da fase 5 tem 308 MB, mas quase tudo e
--     analisados.jsonl (233 MB), que ja esta espelhado em rdo_analisados, e o
--     ZIP das imagens (74 MB), que e gerado sob demanda. O que o painel
--     realmente precisa para abrir uma execucao — meta.json, grupos.json e o
--     cache de classes — nao chega a 1 MB.
--
--     Entao e isso que vem para o banco. Os 300 MB continuam no disco da VPS,
--     e perde-los deixa de apagar a execucao: o painel volta a monta-la daqui.
-- ---------------------------------------------------------------------------

-- Uma varredura: parametros, metricas e status.
create table if not exists public.execucoes (
    run_id          text primary key,
    fase            text,
    fornecedor      text,
    status          text,
    iniciado_em     text,
    concluido_em    text,
    metricas        jsonb not null default '{}'::jsonb,
    meta            jsonb not null default '{}'::jsonb,
    -- Classe de cada imagem (foto de vistoria, print, assinatura...), para o
    -- painel nao reclassificar do zero quando as miniaturas nao estiverem no
    -- disco deste container.
    classes         jsonb not null default '{}'::jsonb,
    registrado_em   timestamptz not null default now()
);

-- Um grupo de imagens repetidas dentro de uma execucao. `membros` traz as
-- ocorrencias (INEP, PDF, pagina, miniatura), que e o que o painel usa para
-- montar os pares INEP A x INEP B.
create table if not exists public.execucao_grupos (
    id              bigserial primary key,
    run_id          text not null
                    references public.execucoes(run_id) on delete cascade,
    grupo_id        text not null,
    tipo            text,
    fornecedor      text,
    qtd_ineps       integer,
    qtd_ocorrencias integer,
    qtd_shas        integer,
    ineps           bigint[],
    ufs             text[],
    membros         jsonb not null,
    unique (run_id, grupo_id)
);

create index if not exists idx_exec_fase     on public.execucoes (fase, iniciado_em desc);
create index if not exists idx_exec_grp_run  on public.execucao_grupos (run_id);
create index if not exists idx_exec_grp_inep on public.execucao_grupos using gin (ineps);

-- RLS ligada e sem policy: so a service_role enxerga, como nas demais tabelas.
alter table public.execucoes       enable row level security;
alter table public.execucao_grupos enable row level security;
