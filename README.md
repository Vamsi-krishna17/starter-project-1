# Starter Project 1 - Email-to-Order Workflow

`POST /orders` takes a raw customer email and returns a structured order, a status
(`Ready` / `Partial` / `Need info`), a list of issues and a drafted reply.

**Rule: the LLM reads and writes text; Python makes every decision.**

## Workflow (always the same order)

| # | Step | Done by |
|---|------|---------|
| - | Parse email (`email.message_from_string`, Date header) | Python |
| 1 | Extract customer, city, deadline, items | LLM (structured output) |
| 2 | Validate fields, quantities, deadline (+ `LEAD_TIME_DAYS`) | Python |
| 3 | Match products + read stock from Postgres, decide status | Python |
| 4 | Draft reply from the computed JSON (never the raw email) | LLM |

The LLM never sees the database, credentials, SQL, or the stock table. Only the
**customer email** goes to extraction, and only the **computed result** goes to the reply.

## Patterns
- **Facade** - `OrderFacade` orchestrates; routes hold no logic.
- **Adapter** - `AzureOpenAIAdapter` is the only code that imports the Azure SDK (timeouts, bounded retries with backoff on 429/timeouts/5xx, token + latency logs).
- **Repository** - `ProductRepository` holds the only SQL (fixed, parameterised, read-only session).

## Structure
```
app/main.py  config.py  models.py  prompts.py
app/api/orders.py
app/services/  order_facade, validation_service, stock_service, status_service
app/adapters/azure_openai_adapter.py
app/repositories/product_repository.py
app/utils/email_parser.py
app/logging_config.py   (central Loguru setup)
tests/  evaluate.py  data/ (seed.sql, emails/, answer_key.json)
```

## Setup
1. Put your files in `data/`: the email files in `data/emails/` and the answer key as `data/answer_key.json` (`data/seed.sql` is already there).
2. `cp .env.example .env` and fill in the four Azure values (never commit `.env`).
3. `data/seed.sql` is already included and `FIND_PRODUCT_SQL` matches it (`products.product_code/name`, `stock.quantity_available`). Just add `emails/` and `answer_key.json` to `data/`.

## Run with Docker
```
docker compose up --build
docker compose exec db psql -U postgres -d orders -c "\dt"
```
If you change `seed.sql`: `docker compose down -v` first (Postgres only seeds an empty volume).

## Run locally
```
pip install -r requirements.txt
docker compose up -d db
uvicorn app.main:app --reload
```

## Example
```
curl -X POST localhost:8000/orders -H "Content-Type: application/json" \
  -d '{"email": "Subject: Order\nDate: Wed, 18 Nov 2026 09:00:00 +0530\n\nGreenfield Public School, Nagpur. 40 Nimbus Tab 10 by 30 Nov."}'
```
Response shape: `customer, delivery_city, deadline, items[{product_name, product_code, requested, available}], status, issues, reply` - every field always present (`null` when unknown).

Errors: `422` invalid request body, `502` Azure OpenAI failed after retries, `503` database unavailable.

## Tests and evaluation
```
pytest                 # code steps + facade + API (no Azure, no DB needed)
python evaluate.py     # labelled emails through the real workflow vs answer_key.json
```
Practice emails have no key: send them through `/orders` and read the results by hand.

## Logging (Loguru)
Configured once in `app/logging_config.py` (level from `LOG_LEVEL`; uvicorn/stdlib logs are routed through Loguru).
Logged: request received, workflow start, parsing, extraction start/finish, validation, stock check, status, reply start/finish,
total duration, LLM latency + tokens, retries and errors. Never logged: API key, DB URL/credentials, full raw emails.

## Business rules (from the guide)
- Required: customer (organisation, not a person's name), delivery city, at least one item.
- Quantity: whole number > 0. Product match: ignore case and extra spaces only.
- Deadline optional (set `REQUIRE_DEADLINE=true` if you want a missing deadline to give Need info); if present, >= `LEAD_TIME_DAYS` (3) after the email's `Date:` header.
- "Same as last time" style emails always give `Need info`.
- Precedence: Need info > Partial > Ready. Stock is never reserved or reduced.

## Known limitations / notes
- Prompt instructions reduce but cannot fully prevent prompt injection; safety comes from the design: the model decides nothing, output is schema-validated, Python decides.
- If an email has no usable Date header, today's date is used as the reference (logged as a warning).
- Add one line per missed labelled email here, explaining why it missed.
