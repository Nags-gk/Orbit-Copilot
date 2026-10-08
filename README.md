# Orbit Copilot

The conversational AI layer of [Orbit](https://github.com/Nags-gk/orbit), split out into its own service.

Ask questions about your money in plain English ("how much did I spend on food last week?", "add my Schwab brokerage with $15,000", "cancel Netflix") and the copilot answers by calling typed tools against your Orbit data. It streams the answer over a WebSocket, and you can run it fully offline with a local model.

Orbit stays the finance app: accounts, transactions, budgets, bills, reports and the statement importer. This repo is the agent: the chat loop, LLM providers, tools, document search and the chat widget.

## What's in it

| Part | Where | What it does |
| --- | --- | --- |
| Chat service | `orbit_copilot/api/chat.py` | WebSocket endpoint that streams text, tool calls and tool results; REST chat history |
| Agent loop | `orbit_copilot/api/chat.py` | Claude path: stream → run requested tools → feed results back → repeat until no tools are called |
| Providers | `orbit_copilot/providers/` | Local (Ollama / LM Studio via OpenAI-compatible API), Gemini 2.5 Flash, Anthropic Claude, and a rule-based demo mode that needs no keys |
| Tools | `orbit_copilot/tools.py` | 12 typed tools: 6 read, 4 write, 2 helpers (below) |
| Document search | `orbit_copilot/rag.py` | TF-IDF + cosine similarity over the user's uploaded statements, top-2 snippets |
| Categorizer | `orbit_copilot/categorizer.py` | ~80 keyword rules, with optional local-LLM / Gemini fallback |
| Analytics | `orbit_copilot/analytics.py` | Forecast, anomalies, insights and subscription tips used by demo mode |
| Widget | `web/src/` | React chat bubble with streaming, tool-call cards and voice in/out |

**Tools**

- Read: `get_transactions`, `get_subscriptions`, `get_spending_summary`, `get_category_breakdown`, `get_accounts`, `get_net_worth`
- Write: `create_transaction`, `cancel_subscription`, `create_account`, `update_account`
- Helpers: `search_documents`, `auto_categorize_transaction`

**Provider priority** for each message: local LLM (if `USE_LOCAL_LLM=true`) → Gemini (if a key is set and no Anthropic key) → demo mode (no keys) → Claude.

## Run it

Orbit Copilot reads Orbit's database, so run Orbit first. Easiest is to check both repos out side by side:

```bash
git clone https://github.com/Nags-gk/orbit.git
git clone https://github.com/Nags-gk/orbit-copilot.git
cd orbit-copilot
cp .env.example .env      # pick a provider; defaults to demo mode
./launch.sh               # http://localhost:8001  (Orbit's API stays on :8000)
```

The widget in Orbit connects to `ws://localhost:8001/api/chat/ws` by default. Set `VITE_COPILOT_WS_URL` in Orbit's frontend to point elsewhere.

### Configuration

| Variable | Default | Notes |
| --- | --- | --- |
| `ORBIT_DATABASE_URL` | `../orbit/backend/orbit.db` | Orbit's database. The copilot maps 5 of its tables and never creates or migrates them. |
| `COPILOT_DATABASE_URL` | `./copilot.db` | The copilot's own conversations and messages |
| `ORBIT_JWT_SECRET` | Orbit's local key | Must match Orbit, so Orbit-issued tokens identify the user |
| `ALLOW_LOCAL_USER` | `true` | No valid token → single-user local mode. Turn off for shared deployments. |
| `LOCAL_USER_EMAIL` | empty | In local mode, act as this Orbit user; empty → the oldest user. Set it to the account your Orbit dashboard shows. |
| `USE_LOCAL_LLM`, `LOCAL_MODEL_URL`, `LOCAL_MODEL_NAME` | off, Ollama, `llama3.1` | Fully offline mode |
| `GEMINI_API_KEY`, `ANTHROPIC_API_KEY` | empty | Cloud providers |
| `CORS_ORIGINS` | `*` | Comma-separated list for anything beyond local use |

### Use the widget in another React app

```tsx
import { ChatAssistant } from "../orbit-copilot/web/src";

<ChatAssistant
  wsUrl="ws://localhost:8001/api/chat/ws"
  getToken={() => authStore.getState().token}   // optional: Orbit JWT
/>
```

Peer dependencies: React 18/19, `lucide-react`, `clsx`, `tailwind-merge`, and Tailwind with Orbit's theme tokens.

## WebSocket protocol

Client → server: `{"message": "...", "conversation_id": "..." | null}`

Server → client, in order per turn:

```text
{"type": "tool_call",   "name": "...", "input": {...}}      0..n
{"type": "tool_result", "name": "...", "result": {...}}     0..n
{"type": "text_delta",  "text": "..."}                      1..n
{"type": "done",        "conversation_id": "...", "message_id": "..."}
{"type": "error",       "error": "..."}                     instead of done
```

On the Claude path, text, tool calls and tool results interleave as they happen.

## Tests

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q          # 44 tests
ruff check orbit_copilot tests
cd web && npm install && npm run typecheck
```

The suite seeds a throwaway Orbit-shaped database with two users. It covers every tool (including that one user can't read or change another's data), document search ranking, the categorizer, demo-mode intents, the local provider's tool loop and the Claude agent loop (with the model mocked), JWT and local-mode identity, and full WebSocket turns with history. CI runs it on Python 3.10 and 3.12 and type-checks the widget.

## Docs

- [Architecture](docs/ARCHITECTURE.md): components, data contract with Orbit, request flow
- [Split record](docs/SPLIT.md): what moved out of Orbit, what changed, and why

## Status and known limits

- Opus escalation is designed, not shipped: `should_escalate_to_opus()` detects the signal, but the loop doesn't act on it yet.
- The copilot shares Orbit's database through a fixed table contract (`orbit_copilot/models/orbit.py`). Moving to Orbit's REST API would remove that coupling. That needs three Orbit endpoints that don't exist yet: category breakdown, cancel subscription and document search.
- Local mode trusts whoever can reach the port. Use `ALLOW_LOCAL_USER=false` and real Orbit JWTs if you expose it beyond your machine.
