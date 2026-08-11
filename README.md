# LLM Gateway

A FastAPI proxy that sits in front of the Claude API — per-user rate limiting,
automatic quality checks on every response, and a fallback to a second
provider when the primary fails or answers badly. Callers see one endpoint
and one response shape, regardless of which provider actually served the
request.

## Why it's built this way

**Two different reasons to fall back, not one.** Most gateway examples only
handle the "provider is down" case. This one also fires when Claude
*succeeds* but the answer is bad — empty, truncated, too short, or a refusal
(`quality_check.py`). A hard failure and a low-quality 200 are different
problems, but from the caller's side they should look the same: get a good
answer or a clear error, not a bad answer with a 200 status.

**Response reshaping, not a shared schema.** The fallback (Groq, running
`openai/gpt-oss-120b`) has its own request/response shape. `groq_fallback.py`
translates it back into the Anthropic Messages format — same `content`,
`stop_reason`, `usage` fields — so nothing downstream needs to know a
fallback happened.

**Token bucket rate limiting, with refunds.** Each user reserves
`max_tokens` up front against their bucket (`rate_limiter.py`), before the
call is made — not after, once the real usage is known. Whatever wasn't
actually used gets refunded once the response comes back, so a user isn't
penalized for requesting a bigger ceiling than they needed.

**Every request logged, pass or fail.** `db.py` writes latency, token
counts, `stop_reason`, which provider actually served the response, and any
quality flag to SQLite — including on errors. That's the data an eval or a
cost dashboard would read from later.

## Architecture

```
POST /v1/messages
      │
      ▼
check_and_reserve(user_id, max_tokens)   (rate_limiter.py)
      │  429 if the bucket doesn't have enough tokens
      ▼
call Claude (primary)                     (main.py)
      │
      ├── provider error (down/timeout/rate-limited)
      │         └──▶ call_groq_fallback()          (groq_fallback.py)
      │
      └── success ──▶ is_low_quality(text, stop_reason)   (quality_check.py)
                              │
                              ├── passes ──▶ return response, refund unused tokens
                              │
                              └── flagged ──▶ retry once on Groq (if budget allows)
                                                    │
                                                    ▼
                                          return best available answer
      │
      ▼
log_request(...)                          (db.py — logged on every path, including errors)
```

## Running it locally

```bash
pip install fastapi uvicorn anthropic groq python-dotenv
```

Create a `.env` with:

```
ANTHROPIC_API_KEY=sk-ant-...
GROQ_API_KEY=gsk_...
```

Start it:

```bash
uvicorn main:app --reload
```

Send a request (v0 recognizes two hardcoded users, `alice` and `bob`, each
with a 200-token bucket refilling hourly — see `rate_limiter.py`):

```bash
curl -X POST http://127.0.0.1:8000/v1/messages \
  -H "X-User-Id: alice" \
  -H "Content-Type: application/json" \
  -d '{"model": "claude-sonnet-4-6", "max_tokens": 200, "messages": [{"role": "user", "content": "hello"}]}'
```

`GET /health` reports whether the Anthropic key loaded. `GET
/debug/bucket/{user_id}` shows a user's current token balance.

## What this doesn't do (on purpose, for v0)

- **Real auth.** `USERS` in `rate_limiter.py` is a hardcoded dict — no API
  keys, no signup flow. The rate-limiting *logic* is real; the identity
  layer in front of it isn't, yet.
- **Persistent rate-limit state.** Token buckets live in an in-memory dict,
  so they reset on restart. Fine for a single process; a real deployment
  needs Redis or similar.
- **Cost-based budgets.** Limits are on token count, not on the actual
  dollar cost of Claude vs. Groq per request — a natural next step.
- **Streaming.** Every response is buffered and returned whole.

## Roadmap

- Swap the in-memory token bucket for Redis so limits survive a restart and
  work across multiple instances.
- Real per-user API keys instead of the `X-User-Id` header.
- A small dashboard over the SQLite log — requests, latency, and fallback
  rate per user.
