---
title: AI CRM
description: The django_ai_crm app of the package — a salesperson's day plan and an agent that updates the CRM, served to the CMS, a REST API v2 and an MCP server.
---

`django_ai_crm` ships in `entirius-django-leads` next to `django_leads` and is enabled only when the host lists it in
`INSTALLED_APPS`. App label and table prefix `django_ai_crm`, URL prefix `api/ai-crm/v2/`, access area `ai_crm.agent`.
How to connect a terminal, Claude Code or ChatGPT: `README.md` § Connect from the terminal, Claude Code and ChatGPT.

## What it does

- **Day plan** — the salesperson's open tasks overdue and due today (django-todo), the drafts waiting for review and
  the replies waiting for a person (django-communicator); the same work as the Today screen, numbered. A company
  coming back is a task ("Come back", created by leads), so it is in the first list.
- **Tools** — `get_day_plan`, `close_task`, `create_task`, `add_note`, `move_deal`, `mark_do_not_contact`,
  `approve_draft`, `list_proposals`, `confirm_proposal`, `reject_proposal`.
- **Confirmation first** for what cannot be undone: a lost deal, Do not contact, sending a draft. Those tools store a
  `Proposal`; it runs only when the same salesperson confirms it, and expires after `AI_CRM_PROPOSAL_TTL_HOURS`.
- **Only their own data** — their tasks, the companies and deals they own; anything else is "not found". Every tool
  also needs the salesperson's permission on the area it changes (`todo.tasks`, `leads.companies`,
  `communicator.review`), so a Viewer reads their plan and never writes through the agent.
- **Every change is signed** `"<username> via ai-crm (<cms | api | mcp>)"` — in the company timeline (History) and on
  the task (`done_by`, `created_by`). The History person filter of leads takes both (`activities/?actor=<username>`).
- **Chat** — short commands in English and Polish run without a model (`plan`, `done 1: called, interested`,
  `zrobione 1: …`, `note 2: …`, `task 1 tomorrow: …`, `deal #12 -> offer`, `yes` / `tak`); anything else goes to the
  AI toolbox model (`AI_CRM_MODEL`, tag `ai_crm.chat`), whose tool calls go through the same checks. The model never
  confirms a proposal.

## Install

```python
INSTALLED_APPS = [
    # ...
    "django_leads",
    "django_ai_crm",  # after django_leads; django_todo and django_access for the full agent
]

# main/urls.py
urlpatterns.append(path("", include("django_ai_crm.urls")))
```

`manage.py migrate django_ai_crm` creates one table (`django_ai_crm_proposal`). No Celery task, no beat entry.
Every source of the plan and every tool gates itself on `apps.is_installed()`: without `django_todo` there are no
tasks, without `django_communicator` no drafts or replies, without `django_access` any active staff user may call
every tool.

| Setting | Default | Meaning |
|---|---|---|
| `AI_CRM_MODEL` | `""` | toolbox model of the chat; empty = commands only, no model call (`"fake-chat"` in zeno) |
| `AI_CRM_MAX_TOKENS` | `1024` | `max_tokens` of one chat completion |
| `AI_CRM_MAX_ACTIONS` | `5` | actions of one model answer that are run |
| `AI_CRM_PROPOSAL_TTL_HOURS` | `24` | a proposal not confirmed within this many hours expires |

Read lazily on every access (`override_settings` works) — unlike `django_leads.settings`. The toolbox settings
(`AI_TOOLBOX_*`) are those of `django_utils.toolbox`.

## API

Admin API v2 under `api/ai-crm/v2/admin/<channel_idx>/`, JWT of the salesperson (`Authorization: Bearer <token>`),
`IsAdminUser`, access area `ai_crm.agent` (a POST needs only read on it: each tool asks for the write of the area it
changes).

| Route | Meaning |
|---|---|
| `GET plan/?tz=Europe/Warsaw&language=pl` | the day plan: `items` (numbered `n`), `upcoming_7d`, `text` (numbered lines) |
| `GET tools/` | every tool with the JSON Schema of its arguments |
| `POST tools/<name>/` | `{"arguments": {...}, "source": "cms" \| "api", "language": "en" \| "pl"}` → `status` `done` / `proposed` / `rejected` / `failed`; 400 / 403 / 404 / 409 / 503 when refused |
| `GET proposals/` | the caller's proposals waiting for a yes or no |
| `POST chat/` | `{"message": "...", "language": "pl", "source": "cms", "history": [...]}` → `reply`, `results`, `proposals`, `plan` |
| `POST mcp/` | the MCP server: streamable HTTP transport, JSON-RPC 2.0 (`initialize`, `ping`, `tools/list`, `tools/call`), JSON answers, no sessions, no event stream; a refused tool is a tool result with `isError: true` |

## Architecture

```
src/django_ai_crm/
├── apps.py (area ai_crm.agent)  enums.py (Source, ProposalStatus)  settings.py (AI_CRM_*, read lazily)  admin.py
├── models/        proposal
├── schemas/       requests.py  tools.py (arguments of every tool = MCP inputSchema)
├── api/admin/     views/agent_views.py  views/mcp_views.py  views/_base.py  urls.py
├── urls.py        api/ai-crm/v2/admin/<channel_idx>/ → api.admin.urls
└── services/      caller (actor, permissions)  crm (todo / leads / communicator)  plan_service  tool_service
                   proposal_service  chat_service (commands + toolbox)  mcp_service (JSON-RPC)  texts (en, pl)
```

## Rules

- `services/crm.py` is the only file of the app that imports another app (todo, leads, communicator) — lazily, behind
  `apps.is_installed()`.
- Every change goes through `tool_service.call`: the chat, the model's actions, REST and MCP. An irreversible tool has
  a `confirm` function and becomes a `Proposal`; never add a path that runs it unconfirmed.
- Never rename the package, the app label or the table prefix; never edit a released migration.
- A plain number in a chat command is an item of the plan; `#12` is the object's own id.
- The fake toolbox answers every tag with a fixture: in zeno free text falls back to the command help — test with
  commands, or mock the toolbox (`django_utils.toolbox.testing.mock_toolbox`).

## Tests

`tests/ai_crm/` (`django_todo` and `django_access` come with the `test` extra; PostgreSQL in CI, sqlite works too). Its conftest switches on the access gate (`enforce`), measures the day in `Europe/Warsaw` and runs
`transaction.on_commit` at once (leads writes the task timeline on commit).

| File | Covers |
|---|---|
| `test_tools.py` | every tool: ownership, permissions, actor, proposals and their expiry, the plan |
| `test_api.py` | REST routes, auth, a role-less staff user stopped by the gate, the MCP handshake, `tools/list`, `tools/call`, errors, 401 / 405, OpenAPI paths, an `access_area` on every admin view |
| `test_chat.py` | commands in en / pl, the toolbox model's actions (mocked), never a confirm by the model |

BDD: `@ai-crm` in `entirius-test-package-emporium` (`features/leads/ai_crm.feature`), run by the entirius-zeno harness.
