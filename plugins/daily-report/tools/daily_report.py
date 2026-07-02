#!/usr/bin/env python3
"""
daily_report.py — Relatório diário para standup

Coleta atividades do ClickUp e GitHub dos últimos 2 dias e gera
relatório HTML com timeline ordenada por horário.
Configuração por usuário em ~/.claude/daily-report.config.json.
"""

import argparse
import json
import os
import subprocess
import sys
import urllib.request
import urllib.parse
import webbrowser
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Windows: forçar UTF-8 no stdout/stderr para suportar caracteres especiais
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ─── Configuração ─────────────────────────────────────────────────────────────
# Nada de credenciais cravadas aqui. Os dados de cada usuário vêm de:
#   1) variáveis de ambiente (CLICKUP_TOKEN, CLICKUP_TEAM_ID, GITHUB_USER), ou
#   2) ~/.claude/daily-report.config.json (gravado pela skill no primeiro uso).
CONFIG_PATH = Path.home() / ".claude" / "daily-report.config.json"

# Preenchidos em tempo de execução por load_config(). Começam vazios de propósito.
CLICKUP_TOKEN = ""
CLICKUP_TEAM_ID = ""
GITHUB_USER = ""
USER_NAME = ""  # nome exibido no cabeçalho do relatório

BRT = timezone(timedelta(hours=-3))


def load_config():
    """Carrega a config do usuário. Ordem de precedência: env var > arquivo JSON.

    Popula os globais CLICKUP_TOKEN / CLICKUP_TEAM_ID / GITHUB_USER / USER_NAME.
    Não falha se faltar algo — quem valida e dispara o onboarding é a skill.
    """
    global CLICKUP_TOKEN, CLICKUP_TEAM_ID, GITHUB_USER, USER_NAME

    cfg = {}
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  ⚠️  Config inválida em {CONFIG_PATH}: {e}", file=sys.stderr)

    CLICKUP_TOKEN = os.environ.get("CLICKUP_TOKEN") or cfg.get("clickup_token", "")
    CLICKUP_TEAM_ID = os.environ.get("CLICKUP_TEAM_ID") or cfg.get("clickup_team_id", "")
    GITHUB_USER = os.environ.get("GITHUB_USER") or cfg.get("github_user", "")
    USER_NAME = cfg.get("user_name", "")
    return cfg

GH_PATHS = [
    "gh",
    r"C:\Program Files\GitHub CLI\gh.exe",
    "/c/Program Files/GitHub CLI/gh",
]

MAX_TASKS = 40  # Limite de tasks para evitar muitas chamadas à API

# Quantos dias corridos olhar para trás na COLETA. Serve só para enxergar onde
# está a última atividade quando o "ontem natural" vem vazio (férias, feriado,
# folga). O relatório continua mostrando apenas "Ontem" e "Hoje".
DEFAULT_LOOKBACK_DAYS = 21

# Snapshot do último status conhecido de cada task. Como o ClickUp não expõe
# histórico de status na API v2, detectamos transições (ex.: "revisão" → "teste")
# comparando o status atual com o salvo aqui. Fica ao lado da config (persistente),
# não no tmp, que pode ser limpo.
STATE_PATH = Path.home() / ".claude" / "daily-report.state.json"


# ─── Utilitários de data ───────────────────────────────────────────────────────
def ms_to_brt(ms_val):
    return datetime.fromtimestamp(int(ms_val) / 1000, tz=timezone.utc).astimezone(BRT)


def iso_to_brt(iso_str):
    if not iso_str:
        return None
    if iso_str.endswith("Z"):
        iso_str = iso_str[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(BRT)
    except Exception:
        return None


def fmt_time(dt):
    return dt.strftime("%H:%M")


def fmt_date(dt):
    return dt.strftime("%d/%m/%Y")


def prev_business_day(dt):
    """Retorna o último dia útil anterior a dt (pula sábado e domingo).

    Trabalhamos apenas de segunda a sexta, então o 'ontem' do relatório deve
    sempre cair no último dia útil real. Ex.: na segunda, 'ontem' = sexta.
    """
    d = dt - timedelta(days=1)
    while d.weekday() >= 5:  # 5 = sábado, 6 = domingo
        d -= timedelta(days=1)
    return d


def date_range(lookback_days=DEFAULT_LOOKBACK_DAYS):
    """Retorna (inicio_coleta_BRT, agora_BRT, start_ms, end_ms).

    A janela de COLETA recua `lookback_days` dias corridos — larga o suficiente
    para detectar gaps (férias/feriado/folga). Qual dia vira "ontem" no relatório
    é decidido depois, em determine_reference_day().
    """
    now_brt = datetime.now(BRT)
    today_start = now_brt.replace(hour=0, minute=0, second=0, microsecond=0)
    coll_start = today_start - timedelta(days=lookback_days)
    start_ms = int(coll_start.astimezone(timezone.utc).timestamp() * 1000)
    end_ms = int(now_brt.astimezone(timezone.utc).timestamp() * 1000)
    return coll_start, now_brt, start_ms, end_ms


def business_days_ago(day, today):
    """Quantos dias úteis `day` está atrás de `today` (datas). Ontem natural = 1."""
    n = 0
    d = today
    while d > day:
        d -= timedelta(days=1)
        if d.weekday() < 5:  # conta só segunda a sexta
            n += 1
    return n


def determine_reference_day(all_items, end_brt, override=None):
    """Decide qual dia é o "ontem" do relatório e detecta gap.

    Retorna dict com:
      - reference: date usada como "ontem" no relatório
      - natural:   date do ontem natural (último dia útil, pulando fim de semana)
      - last_active: date da última atividade antes de hoje (ou None)
      - gap: True quando a última atividade é mais antiga que o ontem natural
             E o usuário ainda não escolheu um dia manualmente (override)
      - days_ago: dias úteis entre last_active e hoje (quando há last_active)
    """
    today = end_brt.date()
    natural = prev_business_day(end_brt).date()

    dates_before = sorted({i["dt"].date() for i in all_items if i["dt"].date() < today})
    last_active = dates_before[-1] if dates_before else None
    days_ago = business_days_ago(last_active, today) if last_active else None

    if override:
        reference = override
        gap = False
    else:
        reference = natural
        gap = bool(last_active and last_active < natural)

    return {
        "reference": reference,
        "natural": natural,
        "last_active": last_active,
        "gap": gap,
        "days_ago": days_ago,
    }


# ─── ClickUp API ──────────────────────────────────────────────────────────────
def cu_get(path, params=None):
    url = f"https://api.clickup.com/api/v2{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params, doseq=True)
    req = urllib.request.Request(url, headers={"Authorization": CLICKUP_TOKEN})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"  [CU] HTTP {e.code} em {path}: {body[:200]}", file=sys.stderr)
        return {}
    except Exception as e:
        print(f"  [CU] Erro em {path}: {e}", file=sys.stderr)
        return {}


def get_cu_user():
    data = cu_get("/user")
    user = data.get("user", {})
    return user.get("id"), user.get("username", ""), user.get("email", "")


def get_cu_tasks(user_id, start_ms):
    """Busca tasks atribuídas ao usuário atualizadas desde start_ms"""
    tasks = []
    page = 0
    while len(tasks) < MAX_TASKS:
        data = cu_get(f"/team/{CLICKUP_TEAM_ID}/task", {
            "assignees[]": str(user_id),
            "date_updated_gt": str(start_ms),
            "include_closed": "true",
            "subtasks": "true",
            "page": str(page),
            "order_by": "updated",
            "reverse": "true",
        })
        batch = data.get("tasks", [])
        if not batch:
            break
        tasks.extend(batch)
        if data.get("last_page", True):
            break
        page += 1
    return tasks[:MAX_TASKS]


def get_cu_comments(task_id):
    data = cu_get(f"/task/{task_id}/comment", {
        "custom_task_ids": "true",
        "team_id": CLICKUP_TEAM_ID,
    })
    return data.get("comments", [])


def get_cu_replies(comment_id):
    """Busca as respostas (thread) de um comentário do ClickUp."""
    data = cu_get(f"/comment/{comment_id}/reply")
    return data.get("comments", [])


def load_state():
    """Carrega o snapshot de status da execução anterior."""
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(state):
    try:
        STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"  [estado] Falha ao salvar snapshot: {e}", file=sys.stderr)


def extract_comment_text(comment):
    """Extrai texto plano de um comentário ClickUp (plain ou rich text)"""
    # Campo prioritário: comment_text (plain text)
    ct = comment.get("comment_text", "")
    if ct and isinstance(ct, str) and ct.strip():
        return ct.strip()
    # Fallback: comment como lista de blocos rich text
    rich = comment.get("comment", [])
    if isinstance(rich, list):
        parts = []
        for block in rich:
            if isinstance(block, dict):
                text = block.get("text", "")
                if text:
                    parts.append(text)
        return " ".join(parts).strip()
    return str(rich).strip() if rich else ""


# ─── GitHub CLI ───────────────────────────────────────────────────────────────
def find_gh():
    for path in GH_PATHS:
        try:
            r = subprocess.run([path, "--version"], capture_output=True, timeout=5)
            if r.returncode == 0:
                return path
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            continue
    return None


def gh_api(endpoint, gh_path):
    try:
        result = subprocess.run(
            [gh_path, "api", endpoint],
            capture_output=True, text=True, encoding="utf-8", timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout)
        if result.stderr:
            print(f"  [GH] {result.stderr.strip()[:200]}", file=sys.stderr)
    except Exception as e:
        print(f"  [GH] Erro: {e}", file=sys.stderr)
    return []


def get_github_events(gh_path, start_brt):
    raw = gh_api(f"/users/{GITHUB_USER}/events?per_page=100", gh_path)
    if not isinstance(raw, list):
        return []
    events = []
    for ev in raw:
        try:
            created = iso_to_brt(ev.get("created_at", ""))
            if created and created >= start_brt:
                events.append({
                    "type": ev.get("type", ""),
                    "repo": ev.get("repo", {}).get("name", ""),
                    "payload": ev.get("payload", {}),
                    "created_at": created,
                    "event_id": ev.get("id", ""),
                })
        except Exception:
            continue
    return events


# ─── Processamento de eventos ─────────────────────────────────────────────────
def repo_short(repo_name):
    return repo_name.split("/")[-1] if "/" in repo_name else repo_name


def _pr_label(action, merged):
    """Rótulo amigável para uma ação de PR. `merged` pode ser None (payload
    reduzido) — nesse caso o enriquecimento corrige depois."""
    if action == "merged" or (action == "closed" and merged):
        return "PR mergeado"
    if action == "closed":
        return "PR fechado"
    return {
        "opened": "PR aberto",
        "reopened": "PR reaberto",
        "ready_for_review": "Pronto para review",
        "converted_to_draft": "Convertido para rascunho",
    }.get(action, f"PR {action}")


def process_github(events):
    items = []
    for ev in events:
        dt = ev["created_at"]
        repo = ev["repo"]
        rs = repo_short(repo)
        p = ev["payload"]
        etype = ev["type"]
        eid = ev.get("event_id", "")

        if etype == "PushEvent":
            branch = p.get("ref", "").replace("refs/heads/", "")
            commits = p.get("commits", [])
            gkey = f"gh-push-{repo}-{eid}"
            gtitle = f"[{rs}] Push → {branch}"
            for commit in commits:
                msg = commit.get("message", "").split("\n")[0]
                sha = commit.get("sha", "")[:7]
                items.append({
                    "dt": dt, "source": "github", "type": "commit",
                    "icon": "💻",
                    "title": gtitle,
                    "sub_title": msg,
                    "detail": f"{sha} · branch: {branch}",
                    "url": f"https://github.com/{repo}/commit/{commit.get('sha', '')}",
                    "group_key": gkey,
                    "group_title": gtitle,
                })

        elif etype == "PullRequestEvent":
            pr = p.get("pull_request", {})
            action = p.get("action", "")
            base = pr.get("base", {}).get("ref", "")
            head = pr.get("head", {}).get("ref", "")
            pr_num = pr.get("number", "")
            # Título vem vazio no payload de repos privados → preenchido em enrich_github
            gprefix = f"[{rs}] PR #{pr_num}"
            items.append({
                "dt": dt, "source": "github", "type": "pull_request",
                "icon": "🔀",
                "title": gprefix,
                "sub_title": _pr_label(action, pr.get("merged")),
                "detail": f"{head} → {base}" if head else "",
                "url": pr.get("html_url", ""),
                "group_key": f"gh-pr-{repo}-{pr_num}",
                "group_title": gprefix,
                "enrich_url": pr.get("url", ""),
                "pr_action": action,
            })

        elif etype == "PullRequestReviewEvent":
            pr = p.get("pull_request", {})
            review = p.get("review", {})
            state_map = {
                "approved": "✅ Aprovado",
                "changes_requested": "🔄 Mudanças solicitadas",
                "commented": "💬 Comentado",
                "dismissed": "❌ Dispensado",
            }
            state = state_map.get((review.get("state") or "").lower(), review.get("state", ""))
            pr_num = pr.get("number", "")
            gprefix = f"[{rs}] PR #{pr_num}"
            items.append({
                "dt": dt, "source": "github", "type": "review",
                "icon": "👁",
                "title": gprefix,
                "sub_title": f"Review: {state}",
                "detail": "",
                "url": review.get("html_url", pr.get("html_url", "")),
                "group_key": f"gh-pr-{repo}-{pr_num}",
                "group_title": gprefix,
                "enrich_url": pr.get("url", ""),
            })

        elif etype == "IssueCommentEvent":
            issue = p.get("issue", {})
            comment = p.get("comment", {})
            body = (comment.get("body") or "")[:120].replace("\n", " ")
            is_pr = "pull_request" in issue
            kind = "PR" if is_pr else "Issue"
            issue_num = issue.get("number", "")
            gprefix = f"[{rs}] {kind} #{issue_num}"
            group_key = f"gh-pr-{repo}-{issue_num}" if is_pr else f"gh-issue-{repo}-{issue_num}"
            items.append({
                "dt": dt, "source": "github", "type": "comment",
                "icon": "💬",
                "title": gprefix,
                "sub_title": "Você comentou",
                "detail": f'"{body}"',
                "url": comment.get("html_url", ""),
                "group_key": group_key,
                "group_title": gprefix,
                "enrich_url": issue.get("url", ""),
            })

        elif etype == "PullRequestReviewCommentEvent":
            pr = p.get("pull_request", {})
            comment = p.get("comment", {})
            body = (comment.get("body") or "")[:120].replace("\n", " ")
            pr_num = pr.get("number", "")
            gprefix = f"[{rs}] PR #{pr_num}"
            items.append({
                "dt": dt, "source": "github", "type": "review_comment",
                "icon": "📝",
                "title": gprefix,
                "sub_title": "Você comentou no review",
                "detail": f'"{body}"',
                "url": comment.get("html_url", ""),
                "group_key": f"gh-pr-{repo}-{pr_num}",
                "group_title": gprefix,
                "enrich_url": pr.get("url", ""),
            })

        elif etype == "CreateEvent":
            ref_type = p.get("ref_type", "")
            ref = p.get("ref", "")
            if ref_type == "branch":
                gtitle = f"[{rs}] Branch criada: {ref}"
                items.append({
                    "dt": dt, "source": "github", "type": "branch_created",
                    "icon": "🌿",
                    "title": gtitle,
                    "sub_title": gtitle,
                    "detail": "",
                    "url": f"https://github.com/{repo}/tree/{ref}",
                    "group_key": f"gh-branch-{repo}-{ref}",
                    "group_title": gtitle,
                })

    return items


def enrich_github(items, gh_path):
    """Preenche título/URL dos PRs e issues.

    A API de eventos do GitHub devolve payload reduzido para repos privados
    (sem `title`, `merged`, `html_url`). Aqui fazemos 1 chamada por PR/issue
    único — usando o campo `url` (endpoint da API REST) — para completar o
    título exibido, o link correto e o rótulo real de merge.
    """
    if not gh_path:
        return
    cache = {}
    for it in items:
        eu = it.get("enrich_url")
        if not eu:
            continue
        if eu not in cache:
            data = gh_api(eu, gh_path)
            cache[eu] = data if isinstance(data, dict) else {}
        d = cache[eu]
        if not d:
            continue
        title = d.get("title")
        if title:
            full = f'{it["group_title"]}: {title}'
            it["group_title"] = full
            it["title"] = full
        # Corrige o link quando o payload não trouxe html_url
        if not it.get("url") and d.get("html_url"):
            it["url"] = d["html_url"]
        # Corrige rótulo de merge agora que sabemos o estado real
        if it.get("type") == "pull_request" and it.get("pr_action") == "closed":
            it["sub_title"] = "PR mergeado" if d.get("merged") else "PR fechado"


def _emit_comment(items, comment, user_id, start_brt, end_brt, gkey, gtitle, task_url, is_reply=False):
    """Cria um item de timeline a partir de um comentário ou resposta, se
    estiver dentro da janela. Retorna True se emitiu."""
    cdt = ms_to_brt(comment.get("date", 0))
    if not (start_brt <= cdt <= end_brt):
        return False
    comment_uid = comment.get("user", {}).get("id")
    is_mine = str(comment_uid) == str(user_id)
    text = extract_comment_text(comment)[:150].replace("\n", " ")
    commenter = comment.get("user", {}).get("username", "alguém")
    verb = "respondeu" if is_reply else "comentou"
    if is_mine:
        sub_title = f"Você {verb}"
        detail = f'Você: "{text}"'
        icon = "↩️" if is_reply else "💬"
    else:
        sub_title = f"{commenter} {verb}"
        detail = f'{commenter}: "{text}"'
        icon = "↩️" if is_reply else "📨"
    items.append({
        "dt": cdt,
        "source": "clickup",
        "type": ("reply_sent" if is_reply else "comment_sent") if is_mine
                 else ("reply_received" if is_reply else "comment_received"),
        "icon": icon,
        "title": gtitle,
        "sub_title": sub_title,
        "detail": detail,
        "url": task_url,
        "group_key": gkey,
        "group_title": gtitle,
    })
    return True


def process_clickup(tasks, user_id, start_brt, end_brt, prev_state, new_state):
    items = []

    for task in tasks:
        task_id = task.get("id", "")
        task_name = task.get("name", "")
        task_url = task.get("url", "")
        custom_id = task.get("custom_id") or task_id

        cu_gkey = f"cu-{task_id}"
        cu_gtitle = f"[{custom_id}] {task_name}"

        # ── Detecção de transição de status (diff contra snapshot anterior) ──
        # O ClickUp não expõe histórico de status na API v2 (/activity dá 404),
        # então comparamos o status atual com o que vimos na última execução.
        cur_status = (task.get("status") or {}).get("status", "") or ""
        new_state[task_id] = {
            "status": cur_status,
            "name": task_name,
            "custom_id": custom_id,
        }
        prev = prev_state.get(task_id)
        prev_status = (prev or {}).get("status", "")
        if prev_status and cur_status and prev_status != cur_status:
            du = task.get("date_updated")
            udt = ms_to_brt(du) if du else None
            if udt and start_brt <= udt <= end_brt:
                items.append({
                    "dt": udt,
                    "source": "clickup",
                    "type": "status_change",
                    "icon": "🔄",
                    "title": cu_gtitle,
                    "sub_title": "Status alterado",
                    "detail": f'"{prev_status}" → "{cur_status}"',
                    "url": task_url,
                    "group_key": cu_gkey,
                    "group_title": cu_gtitle,
                })

        # ── Comentários (e respostas em thread) ──
        try:
            comments = get_cu_comments(task_id)
        except Exception:
            comments = []

        for comment in comments:
            try:
                _emit_comment(items, comment, user_id, start_brt, end_brt,
                              cu_gkey, cu_gtitle, task_url)
                # Respostas dentro da thread deste comentário
                if comment.get("reply_count", 0):
                    try:
                        replies = get_cu_replies(comment.get("id"))
                    except Exception:
                        replies = []
                    for rep in replies:
                        _emit_comment(items, rep, user_id, start_brt, end_brt,
                                      cu_gkey, cu_gtitle, task_url, is_reply=True)
            except Exception:
                continue

    return items


# ─── Serialização de dados (para cache entre passes) ──────────────────────────
def serialize_items(items):
    return [
        {**{k: v for k, v in item.items() if k != "dt"}, "dt": item["dt"].isoformat()}
        for item in items
    ]


def deserialize_items(data):
    return [
        {**{k: v for k, v in item.items() if k != "dt"},
         "dt": datetime.fromisoformat(item["dt"]).astimezone(BRT)}
        for item in data
    ]


def build_groups_export(all_items, end_brt, yesterday_date=None):
    today_date = end_brt.date()
    if yesterday_date is None:
        yesterday_date = prev_business_day(end_brt).date()

    def build_day(items):
        groups = {}
        order = []
        for item in sorted(items, key=lambda x: x["dt"]):
            key = item.get("group_key", f"_solo_{id(item)}")
            if key not in groups:
                groups[key] = {
                    "group_key": key,
                    "group_title": item.get("group_title", item["title"]),
                    "events": [],
                }
                order.append(key)
            groups[key]["events"].append({
                "time": fmt_time(item["dt"]),
                "source": item["source"],
                "type": item["type"],
                "icon": item.get("icon", ""),
                "sub_title": item.get("sub_title", ""),
                "detail": item.get("detail", ""),
            })
        return [groups[k] for k in order]

    today_items = [i for i in all_items if i["dt"].date() == today_date]
    yesterday_items = [i for i in all_items if i["dt"].date() == yesterday_date]

    return {
        "today_date": str(today_date),
        "yesterday_date": str(yesterday_date),
        "today": build_day(today_items),
        "yesterday": build_day(yesterday_items),
    }


# ─── Geração do HTML ──────────────────────────────────────────────────────────
def esc(text):
    return (str(text)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;"))


def _src_class(source):
    return "gh" if source == "github" else "cu"


def _src_label(source):
    return "GitHub" if source == "github" else "ClickUp"


def render_single(item, summary=None):
    sc = _src_class(item["source"])
    sl = _src_label(item["source"])
    url = item.get("url", "")
    title_html = (f'<a href="{esc(url)}" target="_blank">{esc(item["title"])}</a>'
                  if url else esc(item["title"]))
    detail_html = (f'<div class="detail">{esc(item["detail"])}</div>'
                   if item.get("detail") else "")
    summary_html = (f'<div class="ai-summary"><span>✨</span><span>{esc(summary)}</span></div>'
                    if summary else "")
    return f"""
    <div class="event {sc}">
      <div class="etime">{fmt_time(item["dt"])}</div>
      <div class="eicon">{item["icon"]}</div>
      <div class="econtent">
        <span class="badge {sc}-badge">{sl}</span>
        <div class="etitle">{title_html}</div>
        {detail_html}
        {summary_html}
      </div>
    </div>"""


def render_group(group_items, summary=None):
    group_items = sorted(group_items, key=lambda x: x["dt"])
    first = group_items[0]
    last = group_items[-1]
    sc = _src_class(first["source"])
    sl = _src_label(first["source"])
    count = len(group_items)
    url = first.get("url", "")

    t_start = fmt_time(first["dt"])
    t_end = fmt_time(last["dt"])
    time_range = f"{t_start}–{t_end}" if t_start != t_end else t_start

    gtitle = esc(first.get("group_title", first["title"]))
    title_html = (f'<a href="{esc(url)}" target="_blank">{gtitle}</a>'
                  if url else gtitle)

    # Identificadores únicos para JS: hash do group_key
    gid = f"g{abs(hash(first.get('group_key', str(first['dt']))))}"

    # Sub-eventos
    subs = []
    for item in group_items:
        sub_title = esc(item.get("sub_title") or item["title"])
        detail_html = (f'<div class="sub-detail">{esc(item["detail"])}</div>'
                       if item.get("detail") else "")
        sub_url = item.get("url", "")
        title_part = (f'<a href="{esc(sub_url)}" target="_blank">{sub_title}</a>'
                      if sub_url else sub_title)
        subs.append(f"""
        <div class="sub-event">
          <div class="sub-time">{fmt_time(item["dt"])}</div>
          <div class="sub-icon">{item["icon"]}</div>
          <div class="sub-content">
            <div class="sub-title">{title_part}</div>
            {detail_html}
          </div>
        </div>""")

    subs_html = "\n".join(subs)
    summary_html = (f'\n      <div class="ai-summary group-ai"><span>✨</span><span>{esc(summary)}</span></div>'
                    if summary else "")

    # Indicador no header quando há resumo IA
    ai_badge = '<span class="ai-pill">✨ IA</span>' if summary else ""

    return f"""
    <div class="event-group {sc}" id="{gid}">
      <div class="group-header" onclick="toggleGroup('{gid}')">
        <div class="etime">{time_range}</div>
        <div class="eicon">{first["icon"]}</div>
        <div class="econtent">
          <div class="group-meta">
            <span class="badge {sc}-badge">{sl}</span>
            <span class="count-pill">{count} interações</span>
            {ai_badge}
          </div>
          <div class="etitle">{title_html}</div>
        </div>
        <div class="chevron" id="{gid}-chv">›</div>
      </div>
      <div class="group-body" id="{gid}-body">
        {subs_html}
        {summary_html}
      </div>
    </div>"""


def render_events(day_items, summaries=None):
    if not day_items:
        return '<div class="empty">Nenhuma atividade registrada neste período.</div>'

    summaries = summaries or {}

    # Agrupar por group_key
    groups = {}
    order = []
    for item in day_items:
        key = item.get("group_key", f"_solo_{id(item)}")
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(item)

    # Ordenar grupos pelo horário do primeiro evento do grupo
    sorted_keys = sorted(order, key=lambda k: min(i["dt"] for i in groups[k]))

    html_parts = []
    for key in sorted_keys:
        g = groups[key]
        summary = summaries.get(key)
        if len(g) == 1:
            html_parts.append(render_single(g[0], summary=summary))
        else:
            html_parts.append(render_group(g, summary=summary))

    return "\n".join(html_parts)


def generate_html(all_items, start_brt, end_brt, summaries=None, yesterday_date=None):
    summaries = summaries or {}
    now_str = end_brt.strftime("%d/%m/%Y %H:%M")
    today_date = end_brt.date()
    if yesterday_date is None:
        yesterday_date = prev_business_day(end_brt).date()

    today_items = [i for i in all_items if i["dt"].date() == today_date]
    yesterday_items = [i for i in all_items if i["dt"].date() == yesterday_date]

    # Stats refletem apenas o que é exibido (ontem + hoje), não a janela larga de coleta.
    shown_items = today_items + yesterday_items
    total = len(shown_items)
    gh_count = sum(1 for i in shown_items if i["source"] == "github")
    cu_count = sum(1 for i in shown_items if i["source"] == "clickup")

    # Summaries pode ser flat {key: text} ou nested {"today": {...}, "yesterday": {...}}
    today_summaries = summaries.get("today", summaries) if isinstance(summaries.get("today"), dict) else summaries
    yesterday_summaries = summaries.get("yesterday", summaries) if isinstance(summaries.get("yesterday"), dict) else summaries

    # Resumo executivo do dia ("o que falar na daily") — string opcional
    executive = summaries.get("executive") if isinstance(summaries, dict) else None
    exec_html = (f"""
  <div class="exec">
    <div class="exec-hdr">✨ Resumo para a daily</div>
    <div class="exec-body">{esc(executive)}</div>
  </div>""" if executive else "")

    # Resumo copiável para o grupo do ClickUp — string pronta montada pela IA,
    # agrupada por estado (Concluídas / Em teste / ...), pra colar no grupo.
    group_post = summaries.get("group_post") if isinstance(summaries, dict) else None
    grouppost_html = (f"""
  <div class="grouppost">
    <div class="gp-hdr">
      <span>📋 Resumo para o grupo do ClickUp</span>
      <button class="gp-copy" onclick="copyGroupPost()">Copiar</button>
    </div>
    <pre class="gp-body" id="grouppost">{esc(group_post)}</pre>
  </div>""" if group_post else "")

    today_html = render_events(today_items, summaries=today_summaries)
    yesterday_html = render_events(yesterday_items, summaries=yesterday_summaries)

    # Contar tasks únicas (grupos) para os stats
    def count_groups(day_items):
        return len(set(i.get("group_key", f"_solo_{id(i)}") for i in day_items))

    today_groups = count_groups(today_items)
    yesterday_groups = count_groups(yesterday_items)

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Daily Report — {now_str}</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:'Segoe UI',system-ui,sans-serif;background:#f0f2f8;color:#2d3436;font-size:13px;line-height:1.5}}
  .wrap{{max-width:880px;margin:0 auto;padding:24px 16px}}

  /* Header */
  .header{{background:linear-gradient(135deg,#5f27cd,#8854d0);color:#fff;padding:24px 28px;border-radius:14px;margin-bottom:24px;box-shadow:0 4px 20px rgba(95,39,205,.3)}}
  .header h1{{font-size:20px;font-weight:700;letter-spacing:.3px}}
  .header .sub{{opacity:.85;font-size:12px;margin-top:4px}}
  .stats{{display:flex;gap:16px;margin-top:18px;flex-wrap:wrap}}
  .stat{{background:rgba(255,255,255,.18);padding:10px 20px;border-radius:10px;text-align:center;min-width:80px}}
  .stat .n{{font-size:26px;font-weight:800;line-height:1}}
  .stat .l{{font-size:10px;text-transform:uppercase;letter-spacing:.8px;opacity:.85;margin-top:3px}}

  /* Resumo executivo */
  .exec{{background:linear-gradient(135deg,#f3eeff,#fbf9ff);border:1px solid #ddd0ff;border-left:4px solid #5f27cd;border-radius:12px;padding:16px 20px;margin-bottom:24px;box-shadow:0 1px 6px rgba(95,39,205,.08)}}
  .exec-hdr{{font-size:11px;font-weight:800;text-transform:uppercase;letter-spacing:1px;color:#5f27cd;margin-bottom:8px}}
  .exec-body{{font-size:13px;color:#3a3550;line-height:1.6;white-space:pre-line}}

  /* Seção de dia */
  .section{{margin-bottom:28px}}
  .day-hdr{{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:1.2px;color:#636e72;margin-bottom:12px;padding-bottom:8px;border-bottom:2px solid #dfe6e9;display:flex;align-items:center;gap:8px}}
  .day-hdr span{{background:#dfe6e9;padding:2px 10px;border-radius:20px;font-size:11px}}

  /* Evento solo */
  .event{{display:flex;align-items:flex-start;gap:10px;padding:11px 14px;background:#fff;border-radius:9px;margin-bottom:7px;border-left:4px solid transparent;box-shadow:0 1px 4px rgba(0,0,0,.07);transition:box-shadow .15s}}
  .event:hover{{box-shadow:0 2px 10px rgba(0,0,0,.12)}}
  .event.gh{{border-left-color:#6c5ce7}}
  .event.cu{{border-left-color:#e17055}}

  /* Campos comuns */
  .etime{{font-size:12px;color:#636e72;font-weight:600;min-width:52px;padding-top:2px;font-variant-numeric:tabular-nums;white-space:nowrap}}
  .eicon{{font-size:15px;min-width:20px;padding-top:2px}}
  .econtent{{flex:1;min-width:0}}
  .badge{{display:inline-block;font-size:9px;font-weight:800;text-transform:uppercase;letter-spacing:.6px;padding:2px 7px;border-radius:4px;margin-bottom:4px}}
  .gh-badge{{background:#ede9ff;color:#5f27cd}}
  .cu-badge{{background:#fff0ed;color:#c0392b}}
  .etitle{{font-size:13px;font-weight:600;color:#2d3436;word-break:break-word}}
  .etitle a{{color:inherit;text-decoration:none}}
  .etitle a:hover{{color:#5f27cd;text-decoration:underline}}
  .detail{{font-size:12px;color:#636e72;margin-top:3px;word-break:break-word}}
  .empty{{color:#b2bec3;font-style:italic;padding:16px;text-align:center;background:#fff;border-radius:9px}}

  /* Grupo accordion */
  .event-group{{background:#fff;border-radius:9px;margin-bottom:7px;border-left:4px solid transparent;box-shadow:0 1px 4px rgba(0,0,0,.07);overflow:hidden;transition:box-shadow .15s}}
  .event-group:hover{{box-shadow:0 2px 10px rgba(0,0,0,.12)}}
  .event-group.gh{{border-left-color:#6c5ce7}}
  .event-group.cu{{border-left-color:#e17055}}
  .event-group.open{{box-shadow:0 3px 14px rgba(0,0,0,.13)}}

  .group-header{{display:flex;align-items:center;gap:10px;padding:11px 14px;cursor:pointer;user-select:none;transition:background .15s}}
  .group-header:hover{{background:#fafafa}}
  .group-meta{{display:flex;align-items:center;gap:6px;margin-bottom:4px;flex-wrap:wrap}}
  .count-pill{{background:#eef2ff;color:#5f27cd;font-size:10px;font-weight:700;padding:2px 9px;border-radius:20px;white-space:nowrap}}
  .event-group.cu .count-pill{{background:#fff0ec;color:#c0392b}}
  .chevron{{font-size:20px;color:#b2bec3;margin-left:4px;transition:transform .22s ease;line-height:1;flex-shrink:0}}
  .event-group.open .chevron{{transform:rotate(90deg)}}

  /* Sub-eventos */
  .group-body{{display:none;border-top:1px solid #f0f0f5;padding:6px 0 4px}}
  .event-group.open .group-body{{display:block}}
  .sub-event{{display:flex;align-items:flex-start;gap:10px;padding:8px 14px 8px 46px;border-bottom:1px solid #f8f8fb}}
  .sub-event:last-child{{border-bottom:none}}
  .sub-event:hover{{background:#fafafa}}
  .sub-time{{font-size:11px;color:#aaa;font-weight:600;min-width:42px;font-variant-numeric:tabular-nums;padding-top:1px}}
  .sub-icon{{font-size:13px;min-width:18px;padding-top:1px}}
  .sub-content{{flex:1;min-width:0}}
  .sub-title{{font-size:12px;font-weight:500;color:#2d3436;word-break:break-word}}
  .sub-title a{{color:inherit;text-decoration:none}}
  .sub-title a:hover{{color:#5f27cd;text-decoration:underline}}
  .sub-detail{{font-size:11px;color:#636e72;margin-top:2px;word-break:break-word}}

  /* AI Summary */
  .ai-summary{{display:flex;gap:8px;align-items:flex-start;padding:9px 14px 9px 46px;background:linear-gradient(to right,#f0ebff,#f8f6ff);border-top:1px solid #e5deff;font-size:12px;color:#5f27cd;font-style:italic}}
  .ai-summary span:first-child{{font-size:13px;flex-shrink:0;margin-top:1px}}
  .event .ai-summary{{padding-left:14px;margin-top:8px;border-radius:6px;border:1px solid #e5deff}}
  .ai-pill{{background:#f0ebff;color:#5f27cd;font-size:9px;font-weight:800;padding:2px 7px;border-radius:4px;white-space:nowrap}}

  /* Resumo para o grupo (copiável) */
  .grouppost{{background:#fff;border:1px solid #dfe6e9;border-left:4px solid #00b894;border-radius:12px;padding:16px 20px;margin-bottom:24px;box-shadow:0 1px 6px rgba(0,184,148,.1)}}
  .gp-hdr{{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:12px}}
  .gp-hdr span{{font-size:11px;font-weight:800;text-transform:uppercase;letter-spacing:1px;color:#00997c}}
  .gp-copy{{background:#00b894;color:#fff;border:none;padding:6px 16px;border-radius:8px;font-size:12px;font-weight:700;cursor:pointer;transition:background .15s;white-space:nowrap}}
  .gp-copy:hover{{background:#00a383}}
  .gp-body{{font-family:'Segoe UI',system-ui,sans-serif;font-size:13px;color:#2d3436;line-height:1.7;white-space:pre-wrap;word-break:break-word;background:#f7faf9;border:1px solid #e5efec;border-radius:8px;padding:14px 16px;margin:0}}

  /* Footer */
  .footer{{text-align:center;color:#b2bec3;font-size:11px;margin-top:28px;padding-top:16px;border-top:1px solid #dfe6e9}}
  .footer a{{color:#b2bec3;text-decoration:none;cursor:pointer}}

  /* Print */
  @media print{{
    body{{background:#fff}}
    .wrap{{padding:8px;max-width:100%}}
    .header{{print-color-adjust:exact;-webkit-print-color-adjust:exact;border-radius:8px}}
    .event,.event-group{{box-shadow:none;border:1px solid #eee;break-inside:avoid;border-left:4px solid transparent!important}}
    .event.gh,.event-group.gh{{border-left-color:#6c5ce7!important}}
    .event.cu,.event-group.cu{{border-left-color:#e17055!important}}
    .event-group.open .group-body,.group-body{{display:block!important}}
    .chevron{{display:none}}
  }}
</style>
</head>
<body>
<div class="wrap">

  <div class="header">
    <h1>📋 Daily Report{f" — {esc(USER_NAME)}" if USER_NAME else ""}</h1>
    <div class="sub">Período: {fmt_date(yesterday_date)} 00:00 até {now_str} (BRT)</div>
    <div class="stats">
      <div class="stat"><div class="n">{total}</div><div class="l">Interações</div></div>
      <div class="stat"><div class="n">{gh_count}</div><div class="l">GitHub</div></div>
      <div class="stat"><div class="n">{cu_count}</div><div class="l">ClickUp</div></div>
      <div class="stat"><div class="n">{today_groups}</div><div class="l">Hoje tasks</div></div>
      <div class="stat"><div class="n">{yesterday_groups}</div><div class="l">Ontem tasks</div></div>
    </div>
  </div>
{exec_html}
  <div class="section">
    <div class="day-hdr">Ontem{" (ajustado)" if yesterday_date != prev_business_day(end_brt).date() else ""} <span>{fmt_date(yesterday_date)}</span></div>
    {yesterday_html}
  </div>

  <div class="section">
    <div class="day-hdr">Hoje <span>{fmt_date(end_brt)}</span></div>
    {today_html}
  </div>
{grouppost_html}
  <div class="footer">
    Gerado em {now_str} · <a onclick="window.print()">Imprimir / Salvar PDF</a>
  </div>
</div>
<script>
function toggleGroup(id) {{
  const group = document.getElementById(id);
  group.classList.toggle('open');
}}
function copyGroupPost() {{
  const el = document.getElementById('grouppost');
  if (!el) return;
  const text = el.innerText;
  const btn = document.querySelector('.gp-copy');
  const done = () => {{
    if (!btn) return;
    const old = btn.innerText;
    btn.innerText = '✅ Copiado!';
    setTimeout(() => {{ btn.innerText = old; }}, 1800);
  }};
  if (navigator.clipboard && navigator.clipboard.writeText) {{
    navigator.clipboard.writeText(text).then(done).catch(() => fallbackCopy(text, done));
  }} else {{
    fallbackCopy(text, done);
  }}
}}
function fallbackCopy(text, done) {{
  const ta = document.createElement('textarea');
  ta.value = text;
  ta.style.position = 'fixed';
  ta.style.opacity = '0';
  document.body.appendChild(ta);
  ta.select();
  try {{ document.execCommand('copy'); done(); }} catch (e) {{}}
  document.body.removeChild(ta);
}}
</script>
</body>
</html>"""


# ─── Main ──────────────────────────────────────────────────────────────────────
def main():
    global CLICKUP_TEAM_ID, GITHUB_USER, USER_NAME
    parser = argparse.ArgumentParser(description="Daily Report")
    parser.add_argument("--from-data", metavar="PATH",
                        help="Reusar dados coletados (JSON) — pula chamadas às APIs")
    parser.add_argument("--summaries", metavar="PATH",
                        help="JSON com resumos IA por group_key para injetar no HTML")
    parser.add_argument("--no-browser", action="store_true",
                        help="Não abrir o navegador ao salvar o HTML")
    parser.add_argument("--reference-day", metavar="YYYY-MM-DD",
                        help="Força qual dia é o 'ontem' do relatório "
                             "(uso interno, após o usuário confirmar um gap)")
    args = parser.parse_args()

    reference_override = None
    if args.reference_day:
        try:
            reference_override = datetime.strptime(args.reference_day, "%Y-%m-%d").date()
        except ValueError:
            print(f"  ⚠️  --reference-day inválido: {args.reference_day} (use YYYY-MM-DD)",
                  file=sys.stderr)

    report_dir = Path.home() / ".claude" / "tmp"
    report_dir.mkdir(parents=True, exist_ok=True)

    # ── Carregar config do usuário (token / team / github user / nome) ──
    cfg = load_config()
    try:
        lookback = int(cfg.get("lookback_days", DEFAULT_LOOKBACK_DAYS))
    except (TypeError, ValueError):
        lookback = DEFAULT_LOOKBACK_DAYS

    if not CLICKUP_TOKEN:
        print("❌ ClickUp não configurado.")
        print(f"   Defina CLICKUP_TOKEN no ambiente ou crie {CONFIG_PATH}")
        print("   Dica: rode /daily de novo — a skill faz o onboarding e grava a config.")
        sys.exit(2)

    print("=" * 58)
    print("  Daily Report — Coletando atividades...")
    print("=" * 58)

    all_items = []
    cu_ok = False
    gh_ok = False

    # ── Modo rápido: reusar dados cacheados ──
    if args.from_data:
        cached = json.loads(Path(args.from_data).read_text(encoding="utf-8"))
        all_items = deserialize_items(cached["items"])
        start_brt = datetime.fromisoformat(cached["start_brt"]).astimezone(BRT)
        end_brt = datetime.fromisoformat(cached["end_brt"]).astimezone(BRT)
        print(f"  Período: {fmt_date(start_brt)} 00:00 → {fmt_date(end_brt)} {fmt_time(end_brt)}")
        print(f"  📂 Dados carregados do cache: {len(all_items)} eventos")
        cu_ok = any(i["source"] == "clickup" for i in all_items)
        gh_ok = any(i["source"] == "github" for i in all_items)
        # Recupera o nome do cache se a config não tiver user_name
        if not USER_NAME:
            USER_NAME = cached.get("user_name", "")

    # ── Modo normal: coletar das APIs ──
    else:
        start_brt, end_brt, start_ms, end_ms = date_range(lookback)
        print(f"  Janela de coleta: {fmt_date(start_brt)} → {fmt_date(end_brt)} "
              f"({lookback}d, p/ detectar gaps)")
        print()

        # ClickUp
        print("📋 ClickUp...")
        prev_state = load_state()
        new_state = dict(prev_state)  # preserva tasks não tocadas neste run
        user_id, username, email = get_cu_user()
        if user_id:
            print(f"   Autenticado como: {username} (ID: {user_id})")
            # Nome do cabeçalho: usa o da config, senão o nome do ClickUp
            if not USER_NAME:
                USER_NAME = username
            # team_id: usa o da config, senão descobre o primeiro time do usuário
            if not CLICKUP_TEAM_ID:
                teams = cu_get("/team").get("teams", [])
                if teams:
                    CLICKUP_TEAM_ID = str(teams[0].get("id", ""))
                    print(f"   Time detectado: {teams[0].get('name','')} (ID: {CLICKUP_TEAM_ID})")
            tasks = get_cu_tasks(user_id, start_ms)
            print(f"   {len(tasks)} tasks com atividade recente")
            cu_items = process_clickup(tasks, user_id, start_brt, end_brt,
                                       prev_state, new_state)
            all_items.extend(cu_items)
            n_status = sum(1 for i in cu_items if i["type"] == "status_change")
            print(f"   → {len(cu_items)} eventos extraídos ({n_status} mudanças de status)")
            save_state(new_state)
            cu_ok = True
        else:
            print("   ⚠️  Falha na autenticação ClickUp")

        print()

        # GitHub
        print("💻 GitHub...")
        gh = find_gh()
        if gh:
            # github_user: usa o da config, senão descobre via gh (já autenticado)
            if not GITHUB_USER:
                me = gh_api("/user", gh)
                if isinstance(me, dict) and me.get("login"):
                    GITHUB_USER = me["login"]
                    print(f"   Usuário detectado: {GITHUB_USER}")
            if not GITHUB_USER:
                print("   ⚠️  Não foi possível detectar o usuário do GitHub")
            gh_raw = get_github_events(gh, start_brt)
            gh_items = process_github(gh_raw)
            print(f"   {len(gh_items)} eventos — buscando títulos dos PRs...")
            enrich_github(gh_items, gh)
            all_items.extend(gh_items)
            print(f"   → {len(gh_items)} eventos extraídos")
            gh_ok = True
        else:
            print("   ⚠️  gh CLI não encontrado")
            print("   Dica: export PATH=\"$PATH:/c/Program Files/GitHub CLI\"")

        # Salvar cache de dados para segundo passo
        ts = end_brt.strftime("%Y%m%d_%H%M")
        data_path = report_dir / f"daily_{ts}_data.json"
        data_path.write_text(json.dumps({
            "start_brt": start_brt.isoformat(),
            "end_brt": end_brt.isoformat(),
            "user_name": USER_NAME,
            "items": serialize_items(all_items),
        }, ensure_ascii=False), encoding="utf-8")

        print()
        print(f"📊 Total de eventos (janela larga): {len(all_items)}")
        print(f"📂 Cache salvo: {data_path.name}")

    # ── Determinar qual dia é "ontem" (detecta gap de férias/feriado/folga) ──
    ref = determine_reference_day(all_items, end_brt, override=reference_override)
    yesterday_date = ref["reference"]

    # Exportar grupos do dia de referência (Claude usa para gerar os resumos)
    ts = end_brt.strftime("%Y%m%d_%H%M")
    groups_path = report_dir / f"daily_{ts}_groups.json"
    groups_path.write_text(
        json.dumps(build_groups_export(all_items, end_brt, yesterday_date),
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"📂 Grupos exportados ('ontem' = {yesterday_date}): {groups_path.name}")

    # Sinal de gap: a skill lê esta linha e pergunta ao usuário se confirma o dia
    if ref["gap"]:
        print("GAP_DETECTED: " + json.dumps({
            "suggested_day": str(ref["last_active"]),
            "business_days_ago": ref["days_ago"],
            "natural_yesterday": str(ref["natural"]),
        }))
        print(f"  ⚠️  Possível gap: última atividade em {ref['last_active']} "
              f"({ref['days_ago']} dias úteis atrás), não no ontem natural ({ref['natural']}).")

    # ── Carregar resumos IA se fornecidos ──
    summaries = {}
    if args.summaries:
        summaries = json.loads(Path(args.summaries).read_text(encoding="utf-8"))
        total_s = sum(len(v) if isinstance(v, dict) else 1 for v in summaries.values())
        print(f"✨ {total_s} resumos IA carregados")

    # ── Gerar HTML ──
    html = generate_html(all_items, start_brt, end_brt, summaries=summaries,
                         yesterday_date=yesterday_date)

    ts_out = end_brt.strftime("%Y%m%d_%H%M")
    filename = f"daily_{ts_out}.html"
    report_path = report_dir / filename
    report_path.write_text(html, encoding="utf-8")

    print(f"✅ Relatório salvo em: {report_path}")
    if not args.no_browser:
        webbrowser.open(report_path.as_uri())
        print("🌐 Abrindo no navegador...")

    # ── Resumo para a daily ──
    print()
    print("=" * 58)
    print("  RESUMO RÁPIDO PARA DAILY")
    print("=" * 58)

    today_items = sorted(
        [i for i in all_items if i["dt"].date() == end_brt.date()],
        key=lambda x: x["dt"]
    )
    yesterday_items = sorted(
        [i for i in all_items if i["dt"].date() == yesterday_date],
        key=lambda x: x["dt"]
    )

    ontem_label = "ONTEM" if yesterday_date == prev_business_day(end_brt).date() else f"ONTEM (ajustado: {yesterday_date})"
    for label, day_items in [(ontem_label, yesterday_items), ("HOJE", today_items)]:
        print(f"\n  {label} ({len(day_items)} eventos):")
        if not day_items:
            print("    — Sem atividades registradas")
        for item in day_items:
            src = "GH" if item["source"] == "github" else "CU"
            print(f"  {fmt_time(item['dt'])} [{src}] {item['icon']}  {item['title']}")
            if item.get("detail"):
                print(f"         {item['detail'][:100]}")

    if not cu_ok:
        print("\n  ⚠️  ClickUp não coletado — verificar token")
    if not gh_ok:
        print("  ⚠️  GitHub não coletado — verificar gh CLI no PATH")

    print()
    print(f"  💡 Abra {report_path} no browser para ver o relatório completo")
    print("     Use Ctrl+P → Salvar como PDF para exportar")
    print("=" * 58)


if __name__ == "__main__":
    main()
