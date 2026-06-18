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

## Passo 2 — Gerar resumos IA

Leia o arquivo `_groups.json` e analise cada grupo de eventos.

Para cada grupo (tanto "today" quanto "yesterday"), gere um resumo curto em português — 1 a 2 frases — que interprete o que aconteceu naquela tarefa ou ação. Exemplos:

- Comentários seus + "liberado para teste" → "Tarefa concluída em desenvolvimento e liberada para testes."
- PR aberto → "PR aberto para revisão."
- Tarefa reprovada → "Tarefa reprovada. Aguardando correções conforme feedback."
- Apenas comentários de discussão → resumir o tema discutido
- Se houver observações importantes (OBS:, cuidado, pendência) → mencionar brevemente

Monte um JSON no formato `{ "group_key": "resumo", ... }` e salve em
`~/.claude/tmp/daily_YYYYMMDD_HHMM_summaries.json` (mesmo timestamp do Passo 1).

## Passo 3 — Injetar resumos e regenerar HTML

```bash
python "${CLAUDE_PLUGIN_ROOT}/tools/daily_report.py" \
  --from-data "$HOME/.claude/tmp/daily_YYYYMMDD_HHMM_data.json" \
  --summaries "$HOME/.claude/tmp/daily_YYYYMMDD_HHMM_summaries.json"
```

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

## Observações

- O HTML abre automaticamente no navegador no Passo 1 (sem resumos); após o Passo 3 ele é reaberto com os resumos IA.
- Para PDF: Ctrl+P → "Salvar como PDF" no navegador.
- Se ClickUp ou GitHub falharem, informe o motivo (token inválido, `gh` não logado, etc.).
- Para reconfigurar do zero, basta apagar `~/.claude/daily-report.config.json` e rodar `/daily` de novo.
