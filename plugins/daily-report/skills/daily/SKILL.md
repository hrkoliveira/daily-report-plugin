---
name: daily
description: Gera o relatório diário para standup, coletando atividades do ClickUp e GitHub das últimas 48h, com resumos IA por tarefa. Configura-se sozinho no primeiro uso. Use quando o usuário digitar /daily.
---

Quando este skill for invocado, **primeiro garanta que o usuário está configurado** (Passo 0) e só então gere o relatório (Passos 1 a 4).

O script Python vive dentro do plugin e é sempre chamado por:

```
${CLAUDE_PLUGIN_ROOT}/tools/daily_report.py
```

A configuração de cada usuário fica em `~/.claude/daily-report.config.json` (na home dele) — nunca dentro do plugin.

---

## Passo 0 — Onboarding (só na primeira vez)

### 0.1 — Verificar se já está configurado

Cheque se o arquivo `~/.claude/daily-report.config.json` existe **e** contém um `clickup_token` não vazio.

- **Se existe e tem token** → o usuário já está configurado. Pule direto para o **Passo 1**.
- **Se não existe ou está sem token** → siga para 0.2 (onboarding).

### 0.2 — Pedir permissão ANTES de procurar (portão obrigatório)

Não saia procurando token nas pastas do usuário sem avisar. Primeiro, mostre exatamente esta mensagem e **aguarde a resposta**:

> 👋 É a primeira vez que você roda o **/daily** por aqui. Pra funcionar, eu preciso do seu **token de API do ClickUp**.
>
> Posso **procurar automaticamente** um token já salvo nas suas configurações (variáveis de ambiente, configs de MCP, arquivos `CLAUDE.md` e afins) pra te poupar o trabalho. **Você me autoriza a fazer essa busca?**
>
> - Responda **sim** → eu procuro e, se achar, **já uso direto** (só te aviso de onde veio).
> - Responda **não** → sem problema, eu só vou te pedir o token diretamente.

- Se o usuário **autorizar** → siga para 0.3 (busca agressiva).
- Se o usuário **recusar** → **não procure nada**. Vá direto para 0.4 (pedir manualmente).

### 0.3 — Busca agressiva pelo token (só com autorização)

Procure por um token do ClickUp, cujo formato é `pk_` seguido de dígitos, underscore e caracteres alfanuméricos (regex: `pk_[0-9]+_[A-Z0-9]+`). Varra, na ordem:

1. **Variáveis de ambiente**: `CLICKUP_TOKEN`, `CLICKUP_API_KEY`, `CLICKUP_API_TOKEN`.
2. **Configs de MCP do Claude Code**: `~/.claude.json`, `~/.claude/settings.json`, `~/.claude/settings.local.json`, e `.mcp.json` / `.claude/settings.json` do projeto atual.
3. **Config do Claude Desktop**:
   - Windows: `%APPDATA%\Claude\claude_desktop_config.json`
   - macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
   - Linux: `~/.config/Claude/claude_desktop_config.json`
4. **Arquivos `CLAUDE.md`**: o global (`~/.claude/CLAUDE.md`) e o do projeto atual.
5. **Varredura final**: um grep pelo padrão `pk_[0-9]+_[A-Z0-9]+` dentro de `~/.claude/`.

A ordem importa: o primeiro token válido encontrado é o que vale. Por isso comece
pela fonte canônica (config de MCP ativa, `~/.claude.json`) antes de backups,
históricos ou caches — assim você pega o token em uso, não um antigo. Pare assim
que encontrar o primeiro token válido nessa ordem.

- **Achou** → **use o token direto, sem pedir confirmação** (a permissão da etapa 0.2 já autorizou o uso). Apenas avise em uma linha, por transparência, mostrando mascarado de onde veio — ex.: *"Usei o token encontrado em `<arquivo>`: `pk_7559…P155`."* — e siga para 0.5.
- **Não achou nada** → vá para 0.4 (pedir manualmente).

### 0.4 — Pedir o token manualmente

Mostre esta mensagem:

> Pra continuar a configuração eu **preciso do seu token de API do ClickUp**. Você gera o seu em: **ClickUp → Settings → Apps → API Token** (começa com `pk_`). Cole o token aqui que eu finalizo a configuração.

Aguarde o usuário colar o token. Sem o token, **não dá para prosseguir** — não tente gerar o relatório sem ele.

### 0.5 — Gravar a configuração

Crie o arquivo `~/.claude/daily-report.config.json` com o conteúdo abaixo. **Só o `clickup_token` é obrigatório** — o script descobre o resto sozinho (team_id pela API do ClickUp, usuário do GitHub pelo `gh`, nome pelo próprio ClickUp). Deixe os demais campos como string vazia, a menos que o usuário tenha informado:

```json
{
  "clickup_token": "pk_...",
  "clickup_team_id": "",
  "github_user": "",
  "user_name": ""
}
```

Confirme para o usuário que a configuração foi salva e siga para o Passo 1.

---

## Passo 1 — Coletar dados e gerar HTML base

```bash
python "${CLAUDE_PLUGIN_ROOT}/tools/daily_report.py"
```

Se `python` não funcionar, tente `py "${CLAUDE_PLUGIN_ROOT}/tools/daily_report.py"`.

O script vai:
- Coletar eventos do ClickUp e GitHub
- Salvar, em `~/.claude/tmp/`, os arquivos `daily_YYYYMMDD_HHMM_data.json` (cache) e `daily_YYYYMMDD_HHMM_groups.json` (grupos)
- Gerar e abrir no navegador `daily_YYYYMMDD_HHMM.html`

Anote os caminhos exatos dos arquivos `_data.json`, `_groups.json` e `.html` impressos na saída.

**Importante:** verifique se a saída contém uma linha começando com `GAP_DETECTED:`. Se contiver, **não pule para o Passo 2** — vá primeiro para o Passo 1.5.

## Passo 1.5 — Tratar gap de dias (férias, feriado, folga)

O script coleta uma janela larga (semanas) só para descobrir onde está sua última atividade. Normalmente o "ontem" é o último dia útil (pulando fim de semana). Mas se o ontem natural estiver **vazio** e a última atividade for de vários dias úteis atrás, o script emite uma linha assim:

```
GAP_DETECTED: {"suggested_day": "2026-06-11", "business_days_ago": 5, "natural_yesterday": "2026-06-17"}
```

Quando isso aparecer:

1. **Pergunte ao usuário**, usando os valores da linha:

   > 📅 Notei que seu **ontem natural** (`{natural_yesterday}`) não teve atividades. Seu último dia com ações foi **`{suggested_day}`** — cerca de **{business_days_ago} dias úteis atrás**. Isso costuma acontecer após **férias, feriado ou folga**.
   >
   > Quer que eu traga **`{suggested_day}`** como "ontem" no relatório? (sim/não)

2. **Se o usuário confirmar (sim)** → regenere a base apontando esse dia como "ontem", reaproveitando o cache (sem coletar de novo):

   ```bash
   python "${CLAUDE_PLUGIN_ROOT}/tools/daily_report.py" \
     --from-data "$HOME/.claude/tmp/daily_YYYYMMDD_HHMM_data.json" \
     --reference-day SUGGESTED_DAY \
     --no-browser
   ```

   Troque `SUGGESTED_DAY` pelo valor de `suggested_day` (formato `YYYY-MM-DD`). Isso reescreve o `_groups.json` para esse dia. **Guarde o `suggested_day`** — você vai reusá-lo no Passo 3.

3. **Se o usuário recusar (não)** → siga normalmente com o ontem natural (a base já foi gerada assim). Não use `--reference-day`.

Depois disso, vá para o Passo 2.

## Passo 2 — Gerar resumos IA

Leia o arquivo `_groups.json` e analise cada grupo de eventos.

Os eventos incluem (além de comentários):
- **Mudanças de status** (`type: status_change`) — ex.: `"revisão" → "teste"`. Use para narrar o avanço da tarefa no fluxo.
- **Respostas em thread** (`type: reply_sent` / `reply_received`) — quando você respondeu ou foi respondido dentro de um comentário.
- **PRs com título completo** — abertura, merge, review e comentários de review.

Para cada grupo (tanto "today" quanto "yesterday"), gere um resumo curto em português — 1 a 2 frases — que interprete o que aconteceu naquela tarefa ou ação. Exemplos:

- Comentários seus + "liberado para teste" + status `→ "teste"` → "Implementação concluída e tarefa movida para testes."
- PR aberto → "PR aberto para revisão."
- PR mergeado + comentário de deploy → "PR mergeado e deploy concluído."
- Status `"teste" → "concluído"` → "Tarefa validada e concluída."
- Tarefa reprovada → "Tarefa reprovada. Aguardando correções conforme feedback."
- Apenas comentários de discussão → resumir o tema discutido
- Se houver observações importantes (OBS:, cuidado, pendência) → mencionar brevemente

Monte um JSON **aninhado por dia** (evita colisão quando a mesma task aparece nos dois dias) e inclua um **resumo executivo do dia** na chave `executive` e o **resumo para o grupo** na chave `group_post`:

```json
{
  "executive": "1-3 frases sobre o que falar na daily: o que avançou, o que fechou e o que está pendente/bloqueado. Fala direta, primeira pessoa.",
  "group_post": "📋 Resumo do dia — DD/MM\n\n✅ Concluídas\n• [TECH-XXXX](https://app.clickup.com/t/xxx) Distribuidor | Título da tarefa\n\n🧪 Em teste\n• [TECH-YYYY](https://app.clickup.com/t/yyy) Distribuidor | Título da tarefa\n...",
  "today": {
    "group_key_1": "Resumo da tarefa 1.",
    "group_key_2": "Resumo da tarefa 2."
  },
  "yesterday": {
    "group_key_3": "Resumo da tarefa 3."
  }
}
```

O `executive` é renderizado num box destacado no topo do relatório — é o "o que falar na daily". Sintetize o conjunto, não repita tarefa por tarefa.

### Como montar o `group_post` (resumo pra colar no grupo do ClickUp)

É um **texto pronto pra copiar e colar** no grupo do ClickUp — o registro documentado do que foi feito, pro time. Renderiza num bloco copiável no fim do relatório. Regras:

- **Uma string única** com quebras de linha reais (`\n`). Não é objeto.
- **Agrupe por estado** da tarefa (o estado final no dia, inferido pelas mudanças de
  status e comentários). Baldes, nesta ordem, **omitindo os vazios**:
  - `✅ Concluídas`
  - `🧪 Em teste`
  - `🔍 Em revisão`
  - `⛔ Bloqueadas` (ou reprovadas — mencionar o motivo curto entre parênteses)
  - `🚀 Iniciando` (em andamento)
  - `📋 A fazer` (fila — ver abaixo)
- O balde **`📋 A fazer`** vem do array **`todo`** do `_groups.json` (tasks atribuídas a
  você no primeiro status, independente de atividade). Para cada item do `todo`, monte a
  linha `• [custom_id](url) name` usando os campos `custom_id`, `url` e `name` do próprio
  item. Inclua este balde só se o `todo` não estiver vazio. Não invente estado — essas
  tasks não tiveram atividade, são só a fila.
- **Cada linha:** `• [TECH-XXXX](URL) Distribuidor | Título da tarefa`. O `[TECH-XXXX]`
  é o `custom_id` que já vem no `group_title` (formato `[custom_id] nome`), e a `URL` é o
  campo `url` **daquele mesmo grupo** no `_groups.json` (o link da tarefa no ClickUp).
  Montado como link markdown, o TECH-XXXX fica **clicável** quando colado no grupo do
  ClickUp e no HTML. Se um grupo não tiver `url` (ex.: item de GitHub), deixe o
  `[TECH-XXXX]` sem link. O distribuidor costuma estar no próprio título — mantenha.
- **Mostre o "antes estava em X":** quando a tarefa teve um evento `status_change`
  (o `detail` traz `"anterior" → "atual"`), **acrescente ao final da linha**
  `(antes estava em <status anterior>)`. Ex.:
  `• [TECH-2705](url) Plena | Ajustar orderMinValue... (antes estava em revisão)`.
  Se não houve mudança de status (só comentário/PR), não coloque o "(antes estava…)".
- **Se houve comentário**, use a descrição/IA do comentário como a observação curta da
  linha (o que aconteceu na tarefa). Junte tudo da MESMA tarefa numa linha só —
  status + comentário + PR são o mesmo assunto.
- Comece com um cabeçalho `📋 Resumo do dia — DD/MM`.
- **NÃO deixe nada de fora:** toda tarefa que aparece em `today`/`yesterday` no
  `_groups.json` (mudança de status, comentário, menção, resposta, ou PR do GitHub
  correlacionado pelo `TECH-XXXX`) tem que ter uma linha. Se a mesma task aparece nos
  dois dias, use o estado mais recente. Uma tarefa por linha (janela do mesmo assunto).
- Emojis/símbolos são bem-vindos — o objetivo é ficar legível no chat do grupo.

Salve em `~/.claude/tmp/daily_YYYYMMDD_HHMM_summaries.json` (mesmo timestamp do Passo 1).

## Passo 3 — Injetar resumos e regenerar HTML

```bash
python "${CLAUDE_PLUGIN_ROOT}/tools/daily_report.py" \
  --from-data "$HOME/.claude/tmp/daily_YYYYMMDD_HHMM_data.json" \
  --summaries "$HOME/.claude/tmp/daily_YYYYMMDD_HHMM_summaries.json"
```

**Se o usuário confirmou um gap no Passo 1.5**, adicione `--reference-day SUGGESTED_DAY` (o mesmo dia confirmado) a este comando, para o HTML final usar o dia ajustado como "ontem".

Isso sobrescreve o mesmo HTML com os resumos IA embutidos e reabre o navegador.

## Passo 4 — Apresentar o resumo no chat

Após os 3 passos, apresente ao usuário de forma organizada:

```
📋 Daily Report — DD/MM/YYYY HH:MM

ONTEM (N eventos)
  HH:MM [CU] 💬  [TECH-XXXX] descrição
  ...

HOJE (N eventos)
  HH:MM [GH] 🔀  [repo] PR: título
  ...
```

Em seguida, **exiba o `group_post` num bloco de código** (cercado por ```) para o
usuário copiar direto do chat e colar no grupo do ClickUp — é o mesmo texto que
aparece no fim do HTML (com botão "Copiar"). Ex.:

````
Aqui está o resumo pra colar no grupo:

```
📋 Resumo do dia — DD/MM

✅ Concluídas
• [TECH-XXXX] Distribuidor | Título da tarefa
...
```
````

## Observações

- O HTML abre automaticamente no navegador no Passo 1 (sem resumos); após o Passo 3 ele é reaberto com os resumos IA.
- Para PDF: Ctrl+P → "Salvar como PDF" no navegador.
- Se ClickUp ou GitHub falharem, informe o motivo (token inválido, `gh` não logado, etc.).
- Para reconfigurar do zero, basta apagar `~/.claude/daily-report.config.json` e rodar `/daily` de novo.
