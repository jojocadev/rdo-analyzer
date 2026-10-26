# Deploy na VPS (EasyPanel)

Tudo roda dentro do container: a varredura da fase, a conferência geográfica por
OCR e os painéis. Nada depende da máquina local.

## 1. Variáveis de ambiente

Em **Environment**, no serviço do EasyPanel:

```
SUPABASE_URL=https://<projeto>.supabase.co/rest/v1
SUPABASE_SERVICE_KEY=<a service_role>
```

Sem a segunda variável o painel ainda sobe e serve o histórico já em disco, mas
qualquer varredura nova responde `401 Unauthorized`. Para conferir depois do
deploy: `GET /api/health` devolve `{"supabase": true}` quando a chave chegou.

## 1b. Tabelas da auditoria (obrigatório)

Abra o **SQL Editor** do projeto RDO Escolas Sisop, cole o conteúdo de
[sql/auditoria.sql](sql/auditoria.sql) e execute. Cria quatro tabelas e liga RLS.
Pode rodar mais de uma vez.

Sem isso o envio para auditoria fica **só no disco do container** — e some no
próximo redeploy. O sistema não falha calado nesse caso: a tela mostra uma faixa
laranja no topo, o envio acusa "não chegou ao banco" e o registro entra numa
fila de reenvio. Mas o certo é rodar o SQL antes de usar.

Depois do SQL, uma vez, para levar o que já existe em disco:

```bash
python migrar_auditoria.py --simular   # mostra o que subiria
python migrar_auditoria.py             # aplica
```

Rode na máquina que ainda tem a pasta `historico/` — os envios antigos guardavam
só ponteiros, e as evidências são remontadas dali na migração.

Conferência rápida depois do deploy: `GET /api/health` deve trazer
`"auditoria_no_banco": true` e `"auditoria_pendentes": 0`.

## 2. Volumes

Os dados ficam fora da imagem. Sem volume, cada redeploy apaga tudo:

| Caminho no container | O que guarda |
|---|---|
| `/app/extracted_images` | miniaturas que o painel exibe — **é o que ocupa espaço** |
| `/app/historico` | histórico das execuções |
| `/app/cache_extracao` | cache de download e coordenadas já lidas pelo OCR |
| `/app/auditoria` | cache local dos envios e fila de reenvio |

**Dimensionamento:** uma varredura completa da fase 5 gerou aqui ~53 GB em
~518 mil miniaturas. Reserve pelo menos **100 GB** se a VPS for guardar mais de
uma execução. `historico` e `cache_extracao` somam menos de 1 GB.

**Memória:** a fase 5 carrega ~264 mil registros de imagem em memória, ~410 MB
só de dados, mais o processamento. Abaixo de **2 GB de RAM** a varredura falha.

## 3. Ordem de uso

1. **`/`** — escolher a fase e clicar em extrair. Leva ~35 min na fase 5
   (download dos PDFs); a análise em si é de segundos. O progresso fica em
   `/api/fase-status`.
2. **`/duplicatas`** — abrir a execução e, se quiser a conferência geográfica,
   clicar em **Conferir coordenadas**. O OCR roda no servidor e marca as fotos
   tiradas fora do raio de 50 m da escola. É demorado na primeira vez; as
   coordenadas já lidas ficam em cache e as próximas execuções reaproveitam.

   O filtro **Tipo de evidência** já abre em *"Só com foto de vistoria"*. Par
   que só repete print de tela, assinatura ou logo fica de fora: é material que
   o fornecedor anexa igual em toda escola, não reaproveitamento de foto. Para
   ver o que foi excluído, troque para *"Só material comum"*.
3. **`/auditoria`** — os pares encaminhados do painel, com upload de documento
   por INEP.

## 4. Atualizar a base de escolas

Pelo terminal do container, quando sair uma exportação nova do Bubble:

```bash
python atualizar_base.py --csv /caminho/da/base.csv --simular   # vê o diff
python atualizar_base.py --csv /caminho/da/base.csv             # aplica
```

A chave de conflito é ancorada no INEP, nunca na posição da linha. Não há
`DELETE`: escolas que estão no banco e não vieram no CSV ficam intactas.

## 5. O que ainda falta antes do iframe no Bubble

As rotas **não têm autenticação**. Assim que o container ganhar URL pública,
qualquer pessoa com o endereço pode disparar `/api/scan-fase` ou baixar os
dados. Antes de embutir no Bubble é preciso:

- um token nas rotas `/api/*`;
- `Content-Security-Policy: frame-ancestors` limitando quem pode embutir a
  página — hoje qualquer site consegue.

A chave `service_role` também continua exposta no histórico público deste
repositório. Rotacionar no painel do Supabase e trocar a variável aqui.
