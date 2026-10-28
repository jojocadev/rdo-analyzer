-- ---------------------------------------------------------------------------
-- Marcacoes da triagem do Painel de Duplicatas
--
-- Cole no SQL Editor do projeto RDO Escolas Sisop e execute. Pode rodar mais de
-- uma vez: tudo e `if not exists`.
--
-- Por que existe:
--     A marcacao de falso positivo vivia em historico/<run_id>/falsos_positivos.json,
--     em disco e fora do git. O julgamento de quem faz a triagem — e agora o
--     MOTIVO desse julgamento — e trabalho humano que nao pode se perder num
--     redeploy, e e justamente o que a proxima pessoa precisa ler para nao
--     refazer a analise do zero.
--
-- A trilha em auditoria_eventos continua registrando cada mudanca; esta tabela
-- guarda o estado atual.
-- ---------------------------------------------------------------------------

create table if not exists public.duplicatas_marcacoes (
    run_id          text not null,
    par_id          text not null,

    -- Falso positivo: a dupla nao e reaproveitamento de foto.
    falso_positivo  boolean not null default false,
    motivo_codigo   text,          -- categoria escolhida na tela
    motivo          text,          -- texto livre de quem marcou

    -- Mae/filho: as duas escolas dividem a mesma estrutura fisica, entao a foto
    -- repetida e legitima. Diferente de falso positivo: aqui a duplicata existe
    -- e esta correta, nao e engano do sistema.
    mae_filho       boolean not null default false,

    -- Copiados para dar para consultar sem cruzar com outra tabela.
    inep_a          bigint,
    inep_b          bigint,

    atualizado_em   timestamptz not null default now(),
    atualizado_por  text,

    primary key (run_id, par_id)
);

create index if not exists idx_marc_run        on public.duplicatas_marcacoes (run_id);
create index if not exists idx_marc_fp         on public.duplicatas_marcacoes (run_id, falso_positivo);
create index if not exists idx_marc_mae_filho  on public.duplicatas_marcacoes (run_id, mae_filho);
create index if not exists idx_marc_inep_a     on public.duplicatas_marcacoes (inep_a);
create index if not exists idx_marc_inep_b     on public.duplicatas_marcacoes (inep_b);

-- RLS ligada e sem policy: so a service_role enxerga, como nas demais tabelas
-- de auditoria. Sem isto a chave anon publica leria dado de fiscalizacao.
alter table public.duplicatas_marcacoes enable row level security;
