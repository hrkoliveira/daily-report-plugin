# daily-report — plugin para Claude Code

Gera um **relatório diário de standup** direto no Claude Code: coleta suas atividades do **ClickUp** e do **GitHub** das últimas 48h (pulando fim de semana), agrupa por tarefa, escreve **resumos por IA** de cada item e monta um **HTML** bonito que abre no navegador.

Você roda `/daily` e recebe, no chat e em HTML, o que fez ontem e hoje — pronto pra apresentar na daily.

---

## O que ele faz

- 📋 **ClickUp** — comentários, mudanças de status, atribuições e prazos das suas tasks.
- 💻 **GitHub** — commits, PRs (aberto/mergeado/review), branches e comentários.
- ✨ **Resumos por IA** — para cada tarefa, uma ou duas frases interpretando o que aconteceu.
- 🖨️ **Exportação** — o HTML imprime/salva em PDF (Ctrl+P).

O plugin **se configura sozinho no primeiro uso**: ele procura (com sua permissão) o token do ClickUp já salvo na máquina e descobre o resto (seu time no ClickUp e seu usuário do GitHub) automaticamente.

---

## Pré-requisitos

- **Claude Code**
- **Python 3** disponível no PATH (`python` ou `py`)
- **GitHub CLI (`gh`)** instalado e autenticado (`gh auth login`) — usado para ler suas atividades do GitHub
- **Token de API do ClickUp** — você gera o seu em **ClickUp → Settings → Apps → API Token** (começa com `pk_`)

---

## Instalação

No Claude Code, registre o marketplace e instale o plugin:

```
/plugin marketplace add hrkoliveira/daily-report-plugin
/plugin install daily-report@claude-plugins
/reload-plugins
```

Escolha **"Install for you (user scope)"** para ter o `/daily` disponível em qualquer pasta.

---

## Primeiro uso (configuração automática)

Rode:

```
/daily
```

Na primeira vez, o plugin faz o onboarding:

1. **Pede permissão** para procurar um token do ClickUp já salvo na sua máquina.
   - Se você **autorizar**, ele procura (variáveis de ambiente, configs de MCP, arquivos `CLAUDE.md`) e, se achar, **usa direto** — só avisa de onde veio, mascarado.
   - Se você **recusar**, ou se nada for encontrado, ele **pede o token** para você colar.
2. Grava a configuração em `~/.claude/daily-report.config.json`.
3. Gera o relatório.

A partir daí, todo `/daily` vai direto ao relatório.

---

## Uso no dia a dia

```
/daily
```

O HTML abre no navegador e o resumo aparece no chat. Para PDF: Ctrl+P → "Salvar como PDF".

---

## Configuração

O arquivo `~/.claude/daily-report.config.json` guarda seus dados **só na sua máquina** (nunca no plugin):

```json
{
  "clickup_token": "pk_...",
  "clickup_team_id": "",
  "github_user": "",
  "user_name": ""
}
```

- **`clickup_token`** — único campo obrigatório.
- **`clickup_team_id`** — opcional; se vazio, o script usa o primeiro time do seu ClickUp.
- **`github_user`** — opcional; se vazio, é detectado pelo `gh` já autenticado.
- **`user_name`** — opcional; se vazio, usa o nome do seu perfil no ClickUp (aparece no cabeçalho).

Como alternativa ao arquivo, você pode definir as variáveis de ambiente `CLICKUP_TOKEN`, `CLICKUP_TEAM_ID` e `GITHUB_USER` (elas têm prioridade sobre o arquivo).

**Reconfigurar do zero:** apague `~/.claude/daily-report.config.json` e rode `/daily` de novo.

---

## Atualizar o plugin

Quando sair uma versão nova:

```
/plugin update daily-report
/reload-plugins
```

---

## Solução de problemas

| Sintoma | Causa provável | O que fazer |
|---|---|---|
| "ClickUp não configurado" | sem token | rode `/daily` e faça o onboarding, ou crie o config |
| GitHub sem eventos | `gh` não logado | rode `gh auth login` |
| "python não encontrado" | Python fora do PATH | instale o Python 3 ou use `py` |
| Token inválido (HTTP 401) | token errado/expirado | gere outro no ClickUp e reconfigure |

---

## Privacidade

- Seu token fica **apenas** em `~/.claude/daily-report.config.json`, na sua máquina.
- O plugin **não** envia seus dados para lugar nenhum além das APIs oficiais do ClickUp e do GitHub, com a sua própria credencial.
- O relatório HTML é gerado localmente em `~/.claude/tmp/`.
