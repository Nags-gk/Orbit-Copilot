# Split record: Orbit → Orbit + Orbit Copilot

October 2026. Orbit was one repo holding two products: a personal finance app and a conversational agent on top of it. This record says where each piece went, what changed on the way, and why.

## Who owns what

| Area | Orbit | Orbit Copilot |
| --- | --- | --- |
| Accounts, transactions, budgets, goals, bills, reports | ✓ | |
| Auth (register, login, JWT issue) | ✓ | verifies Orbit's JWTs |
| Statement import (multimodal document analyzer, comparator) | ✓ | |
| Analytics API and Intelligence page | ✓ | vendored copy for demo-mode answers |
| Keyword categorizer | ✓ (used by transactions API) | vendored copy (used by tools) |
| Chat WebSocket, history, agent loop | | ✓ |
| LLM providers: Claude, Gemini, local, demo | | ✓ |
| 12 copilot tools | | ✓ |
| Document search (TF-IDF) | | ✓ |
| Chat widget, chat hook, voice hook | renders it | ✓ source |
| `conversations` and `messages` tables | | ✓ (moved to `copilot.db`) |

The categorizer and analytics engine exist in both repos for now, because both sides call them. A shared package is the clean follow-up. Until then, change them in both places.

## Files moved (history preserved)

`git filter-repo` carried over the 10 Orbit commits that touched these files, with original authors and dates.

| Orbit path | Copilot path |
| --- | --- |
| `backend/app/routers/chat.py` | `orbit_copilot/api/chat.py` |
| `backend/app/services/llm.py` | `orbit_copilot/providers/anthropic_provider.py` |
| `backend/app/services/gemini_llm.py` | `orbit_copilot/providers/gemini_provider.py` |
| `backend/app/services/local_llm.py` | `orbit_copilot/providers/local_provider.py` |
| `backend/app/services/demo.py` | `orbit_copilot/providers/demo_provider.py` |
| `backend/app/services/tools.py` | `orbit_copilot/tools.py` |
| `backend/app/services/rag_engine.py` | `orbit_copilot/rag.py` |
| `backend/app/services/categorizer.py` | `orbit_copilot/categorizer.py` (copy) |
| `backend/app/services/analytics_engine.py` | `orbit_copilot/analytics.py` (copy) |
| `backend/app/prompts/system.py` | `orbit_copilot/prompts.py` |
| `src/components/ai/ChatAssistant.tsx` | `web/src/ChatAssistant.tsx` |
| `src/hooks/useChat.ts` | `web/src/useChat.ts` |
| `src/hooks/useVoice.ts` | `web/src/useVoice.ts` |

## New in this repo

`config.py`, `db.py`, `auth.py`, `main.py`, `models/orbit.py`, `models/chat.py`, `llm_utils.py`, the test suite, CI, and the widget's `package.json` / `index.ts` / `cn.ts`.

## Behavior changes and fixes made during the split

| Change | Why |
| --- | --- |
| Removed the hardcoded personal email from the chat router. Identity is now a verified Orbit JWT, or the oldest user in local mode. | Personal data in public code; the email lookup only worked on one machine. |
| Chat history moved to the copilot's own DB. | The service owns its data; Orbit's schema no longer carries chat tables. |
| The widget takes `wsUrl` and `getToken` instead of importing Orbit's auth store and a hardcoded `localhost:8000`. | Lets it run against a separate service and in other apps. |
| The widget no longer reconnects after it unmounts. | `onclose` scheduled a reconnect even when the component closed the socket itself, leaking a socket per mount. |
| `openai` 1.54.3 → 1.82.0. | 1.54 passes `proxies=` to httpx, which httpx 0.28 (required by `google-genai`) removed, so local mode crashed on any fresh install. Orbit has the same bug. |
| Document search uses smoothed IDF, `log((1+N)/(1+df)) + 1`. | The old `log(N/(df+1))` gave 0 or negative weights with one or two documents, so search results were arbitrary for most users. |
| `create_account` normalizes the type before inferring the subtype. | A model sending `"Depository"` produced subtype "Other" instead of "Checking". |
| Pinned the missing dependencies `python-jose`, `google-genai`. | Orbit imports them but doesn't list them. |
| The Anthropic provider docstring now says Opus escalation is designed, not wired. | It claimed Sonnet → Opus routing that never runs. |
| Server port 8001. | Orbit's API keeps 8000. |

No other behavior changed. The wire protocol, tool schemas, prompts, provider order and demo intents are the same as in Orbit.
