# webhook-relay

A small, production-shaped webhook ingestion + relay service. It accepts inbound webhooks, verifies an HMAC-SHA256 signature over the raw body, persists every event, and relays it to a configured destination with retries and exponential backoff - with a full delivery log you can audit.

## Why it matters

Receiving webhooks reliably is deceptively hard: you have to reject forged payloads, survive a flaky downstream, avoid double-processing on redelivery, and be able to answer "did event X actually get through?". webhook-relay does all four:

- Authenticity - every payload is verified with a constant-time HMAC-SHA256 check. A tampered body or wrong secret is rejected with 401 before anything is stored.
- At-least-once delivery - failed deliveries are retried on a documented backoff schedule (1s, 2s, 4s, ...) until they succeed or hit the attempt cap.
- Idempotency - re-posting the same X-Event-Id never creates a duplicate delivery.
- Auditability - a queryable delivery log records status, attempt count, last error, and the next retry time.

The one thing that makes this testable offline is the "Sender" seam: all outbound delivery goes through an injectable interface. Production uses HttpxSender; tests and the demo use a FakeSender scripted to fail N times then succeed - so retry/backoff behaviour is verified deterministically without a real HTTP server and without ever sleeping.

## Architecture

```
HTTP (FastAPI)        api.py         thin adapter: reads the RAW body, maps errors -> 401/404
      |
RelayService          service.py     verify -> persist -> deliver_with_retries()  (framework-free)
   |        |
SqliteStore  Sender   store.py        (endpoint_id, event_id) is the PK -> idempotency in schema
             sender.py                HttpxSender (prod) | FakeSender (tests/demo)
   |
signing.py / backoff.py               HMAC compute+verify | pure backoff schedule (no sleeping)
```

The domain (models.py, service.py) imports neither FastAPI nor SQLite, which is what makes the core logic unit-testable in isolation. Time is injected too: RelayService takes a sleep callable and a clock callable, so tests record the exact backoff delays instead of waiting them out.

## Quickstart

Requires uv and Python 3.12.

```bash
uv sync                       # create venv + install pinned deps
uv run python -m webhook_relay # serve on http://127.0.0.1:8000  (docs at /docs)
```

Register an endpoint, then sign and post a payload:

```bash
# 1. register a destination + secret
curl -s -X POST localhost:8000/endpoints \
  -H 'content-type: application/json' \
  -d '{"destination_url":"https://httpbin.org/post","secret":"whsec_abc"}'
# -> {"id":"<endpoint_id>", ...}

# 2. compute the signature the way a real sender would
BODY='{"type":"order.paid","id":"ord_1"}'
SIG=$(python -c "import hmac,hashlib,sys; print(hmac.new(b'whsec_abc', sys.argv[1].encode(), hashlib.sha256).hexdigest())" "$BODY")

# 3. ingest it (bad/missing signature -> 401)
curl -s -X POST localhost:8000/ingest/<endpoint_id> \
  -H "X-Signature: $SIG" -H "X-Event-Id: evt_1" --data "$BODY"

# 4. read the delivery log
curl -s localhost:8000/deliveries
```

### API

| Method & path                | Purpose                                             |
| ---------------------------- | --------------------------------------------------- |
| POST /endpoints            | Register a destination URL + HMAC secret            |
| GET  /endpoints            | List endpoints (secret is never returned)           |
| POST /ingest/{endpoint_id} | Verify X-Signature, persist, and relay the event  |
| GET  /deliveries           | Delivery log; optional ?endpoint_id= filter       |

## Runnable demo

The demo drives the real service (real signing, real SQLite, real retry loop) through a FakeSender that fails the first attempt, then prints the actual delivery log:

```bash
uv run python -m webhook_relay.demo
```

Real output (the endpoint id is a freshly generated UUID, so it differs per run):

```text
=== webhook-relay demo ===
endpoint id        : 437ff54c5dde4c9cb47656385bf06fcf
destination        : https://example.test/webhooks/orders
payload bytes      : 56
x-signature        : b9a6e51492f449ca... (64 hex chars)

--- delivery log ---
event id           : evt_1001
final status       : delivered
attempt count      : 2
sender invocations : 2
backoff delays (s) : [1.0]
last error         : None

--- idempotent replay (same event id) ---
duplicate detected : True
sender invocations : 2 (unchanged)
total deliveries   : 1
```

Read that as: the payload failed once, was retried after a 1s backoff, and was delivered on attempt 2. Re-posting the same evt_1001 was detected as a duplicate and did not trigger another delivery - the sender was still only called twice.

## Delivery semantics

- Attempts. deliver_with_retries attempts delivery, and on failure waits base_delay * factor ** (n-1) seconds before retry n. With the defaults this is 1s, 2s, 4s, ... (see backoff.py).
- Terminal states. A 2xx from the destination marks the delivery delivered. Once attempt_count reaches max_attempts (default 3) without success, it becomes failed with the last error recorded. Until then it stays pending with next_retry_at set.
- Idempotency is enforced in the schema: (endpoint_id, event_id) is the primary key of both the events and deliveries tables, so a redelivery can never create a second row.

## Testing

```bash
uv run ruff check .          # lint (E,F,I,UP,B,SIM)
uv run ruff format --check . # formatting
uv run mypy .                # strict type checking
uv run pytest -q             # 34 tests
```

The suite (34 tests) covers real behaviour, not trivial asserts:

- Signing - correct signature accepted; tampered body, wrong secret, and missing signature all rejected.
- Retry/backoff - fail-twice-then-succeed ends delivered after 3 attempts with the recorded backoff sequence [1.0, 2.0]; a permanently-failing sender ends failed after the attempt cap; custom base/factor schedules are honoured.
- Idempotency - re-posting an event id (and the derived-id path) never duplicates a delivery and never re-invokes the sender.
- HTTP layer - 401 on bad/missing/tampered signatures, 404 on unknown endpoints, and the delivery log reports attempts and status through the real FastAPI app.

## Running as a container

```bash
docker build -t webhook-relay .
docker run -p 8000:8000 -v "$PWD/data:/data" webhook-relay
```

## Layout

```
src/webhook_relay/
  models.py    domain entities (framework-free dataclasses)
  signing.py   HMAC-SHA256 compute + constant-time verify
  backoff.py   pure exponential backoff schedule
  sender.py    Sender interface + HttpxSender + FakeSender
  store.py     SQLite repository (idempotency in the schema)
  service.py   RelayService: verify -> persist -> deliver with retries
  api.py       FastAPI app (thin adapter over the service)
  demo.py      end-to-end demo that prints the real delivery log
tests/         signing, backoff, store, service, api, and demo tests
```

## Maintainer

This project is maintained by Afsha Fathima, a Python Backend Developer with over 4 years of experience building robust backend applications, REST APIs, and service integrations. This implementation focuses on production-grade reliability, maintainable architecture, and high-performance delivery semantics.

Contact:
- LinkedIn: https://www.linkedin.com/in/afsha-fathima-lnu-a29996298/
- Email: fathimaafsha08@gmail.com