# Deploy na VPS (EasyPanel)

Tudo roda dentro do container: a varredura da fase, a conferência geográfica por
OCR e os painéis. Nada depende da máquina local.

## 1. Variáveis de ambiente

Em **Environment**, no serviço do EasyPanel:

```
SUPABASE_URL=https://<projeto>.supabase.co/rest/v1
SUPABASE_SERVICE_KEY=<a service_role>
PAINEL_TOKEN=<uma senha longa e aleatória>
```

**`PAINEL_TOKEN` não é opcional em produção.** Sem ele o painel fica aberto a
quem souber a URL: dá para ler os envios de auditoria, disparar uma varredura de
35 minutos, baixar as evidências e apagar registros. Com ele, tudo exige o token
— exceto `/api/health`, que a sonda do EasyPanel precisa alcançar e que não
devolve dado nenhum.

O token chega por `Authorization: Bearer`, pelo cabeçalho `X-Painel-Token`, ou
por `?token=` na URL (o caso do iframe); na primeira vez vira cookie e some da
barra de endereço. Quem abrir a página sem token vê um campo para digitá-lo.

Para embutir no Bubble, declare quem pode:

```
PAINEL_FRAME_ANCESTORS=https://seu-app.bubbleapps.io
```

Sem essa variável o padrão é `X-Frame-Options: SAMEORIGIN` — nenhum site externo
consegue embutir, o que é o certo enquanto o iframe não estiver definido.

Sem a segunda variável o painel ainda sobe e serve o histórico já em disco, mas
qualquer varredura nova responde `401 Unauthorized`. Para conferir depois do
deploy: `GET /api/health` devolve `{"supabase": true}` quando a chave chegou.

## 1b. Onde a auditoria é salva

Nada a fazer: **já funciona com a service_role**. O que vai para auditoria e o
documento de resolução são gravados no Supabase, não só no disco do container.

Há dois backends, escolhidos sozinhos na subida:

| Backend | Quando | O que dá |
|---|---|---|
| `tabelas` | depois de rodar o SQL | consultar por SQL, cruzar com `escolas_conectadas` |
| `storage` | padrão, sem DDL nenhum | guarda tudo, mas sem consulta SQL |

O `storage` grava os mesmos registros como objetos JSON no bucket
`auditoria-docs`, que ocupa alguns KB. Não perde nada: sem o disco local, os
registros voltam do Supabase.

**Os arquivos de resolução ficam no disco da VPS**, não no Supabase — em
`/app/auditoria/documentos/<envio>/<inep>/`. O banco guarda nome, tamanho e
`sha256` de cada um, então o registro de que o documento existiu sobrevive de
qualquer forma; mas **o arquivo em si depende do volume estar montado**.

**Para habilitar consulta por SQL** (opcional, recomendado): cole
[sql/auditoria.sql](sql/auditoria.sql) no SQL Editor do projeto e execute; cria
quatro tabelas e liga RLS. Depois rode a migração, que promove o que está no
Storage para as tabelas:

```bash
python migrar_auditoria.py --simular   # mostra o que subiria
python migrar_auditoria.py             # aplica
```

Rode na máquina que ainda tem a pasta `historico/` — os envios antigos guardavam
só ponteiros, e as evidências são remontadas dali na migração.

Se o Supabase ficar fora do ar, a operação **não** é dada como concluída em
silêncio: o registro entra em `auditoria/_pendentes/`, a tela mostra faixa
laranja com botão "Reenviar agora" e o envio responde avisando.

Conferência depois do deploy: `GET /api/health` deve trazer
`"auditoria_no_banco": true` e `"auditoria_pendentes": 0`.

## 2. Volumes

Os dados ficam fora da imagem. Sem volume, cada redeploy apaga tudo:

| Caminho no container | O que guarda |
|---|---|
| `/app/extracted_images` | miniaturas que o painel exibe — **é o que ocupa espaço** |
| `/app/historico` | histórico das execuções |
| `/app/cache_extracao` | cache de download e coordenadas já lidas pelo OCR |
| `/app/auditoria` | **documentos de resolução anexados** + cache dos envios |

**Dimensionamento:** uma varredura completa da fase 5 gerou aqui ~53 GB em
~518 mil miniaturas. Reserve pelo menos **100 GB** se a VPS for guardar mais de
uma execução. `historico` e `cache_extracao` somam menos de 1 GB.

**Memória:** a fase 5 carrega ~264 mil registros de imagem em memória, ~410 MB
só de dados, mais o processamento. Abaixo de **2 GB de RAM** a varredura falha.

## 3. Ordem de uso

1. **`/`** — escolher a fase e clicar em extrair. Leva ~35 min na fase 5 para
   baixar os PDFs, mais ~22 min de comparação visual. O progresso fica em
   `/api/fase-status`.

   A comparação visual acha a mesma foto reaproveitada mesmo quando ela foi
   recortada ou recomprimida, o que o SHA exato não pega: na fase 5 são 195
   achados a mais sobre os 302 exatos. Para desligar e ganhar os 22 min de
   volta, mande `somente_identicas: true` no corpo de `/api/scan-fase`.
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
