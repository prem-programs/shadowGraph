"""Synthetic company 'Acme Payments' (25 services). Every name, person and link here is invented.

Built for the demo story:
  * partner-gateway and legacy-mobile-bff reach the rest of the company ONLY through
    auth-service /v1/token-verify, so removing it leaves two islands (one holds a tier 0 service).
  * kiosk-bff reaches the rest ONLY through /v1/session-refresh (second demo PR).
  * nobody calls /v1/jwks (third demo PR passes).
  * ADR-08 (cited by the first PR) was replaced by ADR-19, which forbids the removal.
"""
from __future__ import annotations

TENANT = "acme"
COMPANY = "Acme Payments"

TEAMS = [
    # id, name, oncall_channel
    ("platform", "Platform", "#platform-oncall"),
    ("payments", "Payments", "#payments-oncall"),
    ("growth", "Growth", "#growth-oncall"),
    ("partners", "Partners", "#partners-oncall"),
    ("mobile", "Mobile", "#mobile-oncall"),
]

SERVICES = [
    # id, tier, team, description
    ("web-gateway", 0, "platform", "Edge for the web app"),
    ("checkout-api", 0, "payments", "Runs checkout end to end"),
    ("auth-service", 0, "platform", "Verifies tokens and refreshes sessions"),
    ("payment-worker", 0, "payments", "Charges cards and wallets"),
    ("ledger-service", 0, "payments", "Append-only money ledger"),
    ("session-service", 1, "platform", "User sessions"),
    ("user-profile", 1, "growth", "Profiles and preferences"),
    ("cart-service", 1, "payments", "Shopping carts"),
    ("fraud-scorer", 1, "payments", "Scores payments for fraud"),
    ("catalog-service", 1, "growth", "Products and categories"),
    ("inventory-service", 1, "payments", "Stock levels"),
    ("pricing-service", 1, "payments", "Prices and discounts"),
    ("notification-service", 2, "growth", "Fans out notifications"),
    ("email-sender", 3, "growth", "Sends email"),
    ("search-service", 2, "growth", "Product search"),
    ("feature-flags", 2, "platform", "Feature flag reads"),
    ("analytics-ingest", 3, "platform", "Collects events"),
    ("kiosk-bff", 1, "mobile", "Backend for in-store kiosks"),
    ("kiosk-sync", 3, "mobile", "Syncs kiosk catalogues"),
    ("partner-edge", 0, "partners", "Public edge for partner traffic"),
    ("partner-gateway", 1, "partners", "Partner API"),
    ("partner-webhooks", 2, "partners", "Delivers partner webhooks"),
    ("partner-billing", 2, "partners", "Invoices partners"),
    ("legacy-mobile-bff", 1, "mobile", "Backend for the old mobile app"),
    ("mobile-push", 2, "mobile", "Push notifications for the old app"),
]

ENDPOINTS = [
    # service, route, version, deprecated
    ("auth-service", "/v1/token-verify", 1, True),
    ("auth-service", "/v2/token-verify", 2, False),
    ("auth-service", "/v1/session-refresh", 1, True),
    ("auth-service", "/v2/session-refresh", 2, False),
    ("auth-service", "/v1/jwks", 1, True),
    ("session-service", "/v1/sessions", 1, False),
    ("user-profile", "/v1/users", 1, False),
    ("user-profile", "/v1/users/prefs", 1, False),
    ("cart-service", "/v1/cart", 1, False),
    ("checkout-api", "/v1/checkout", 1, False),
    ("payment-worker", "/v1/payments", 1, False),
    ("payment-worker", "/v1/refunds", 1, False),
    ("ledger-service", "/v1/ledger/entries", 1, False),
    ("ledger-service", "/v1/ledger/balance", 1, False),
    ("fraud-scorer", "/v1/score", 1, False),
    ("catalog-service", "/v1/products", 1, False),
    ("catalog-service", "/v1/categories", 1, False),
    ("inventory-service", "/v1/stock", 1, False),
    ("pricing-service", "/v1/prices", 1, False),
    ("notification-service", "/v1/notify", 1, False),
    ("email-sender", "/v1/send", 1, False),
    ("search-service", "/v1/search", 1, False),
    ("feature-flags", "/v1/flags", 1, False),
    ("analytics-ingest", "/v1/events", 1, False),
    ("kiosk-sync", "/v1/sync", 1, False),
    ("partner-gateway", "/v1/partner/orders", 1, False),
    ("partner-webhooks", "/v1/hooks", 1, False),
    ("partner-billing", "/v1/invoices", 1, False),
    ("mobile-push", "/v1/push", 1, False),
]


def ep(service: str, route: str) -> str:
    return f"{service}:{route}"


DEPENDS_ON = [
    # caller, provider service, route, p99_ms, rpm
    ("web-gateway", "auth-service", "/v2/token-verify", 12, 9000),
    ("web-gateway", "session-service", "/v1/sessions", 15, 7000),
    ("web-gateway", "catalog-service", "/v1/products", 40, 5000),
    ("web-gateway", "search-service", "/v1/search", 60, 3000),
    ("web-gateway", "cart-service", "/v1/cart", 25, 2500),
    ("web-gateway", "checkout-api", "/v1/checkout", 220, 600),
    ("web-gateway", "feature-flags", "/v1/flags", 5, 9000),
    ("web-gateway", "user-profile", "/v1/users", 20, 4000),
    ("checkout-api", "cart-service", "/v1/cart", 20, 600),
    ("checkout-api", "pricing-service", "/v1/prices", 18, 600),
    ("checkout-api", "payment-worker", "/v1/payments", 150, 550),
    ("checkout-api", "inventory-service", "/v1/stock", 22, 600),
    ("checkout-api", "auth-service", "/v2/token-verify", 11, 600),
    ("checkout-api", "notification-service", "/v1/notify", 30, 500),
    ("checkout-api", "fraud-scorer", "/v1/score", 80, 550),
    ("payment-worker", "ledger-service", "/v1/ledger/entries", 35, 550),
    ("payment-worker", "fraud-scorer", "/v1/score", 75, 550),
    ("payment-worker", "auth-service", "/v2/token-verify", 10, 550),
    ("ledger-service", "analytics-ingest", "/v1/events", 8, 1200),
    ("fraud-scorer", "user-profile", "/v1/users", 18, 1100),
    ("fraud-scorer", "analytics-ingest", "/v1/events", 8, 1100),
    ("session-service", "auth-service", "/v2/session-refresh", 14, 3000),
    ("session-service", "user-profile", "/v1/users", 16, 3000),
    ("cart-service", "pricing-service", "/v1/prices", 15, 3000),
    ("cart-service", "inventory-service", "/v1/stock", 20, 3000),
    ("cart-service", "catalog-service", "/v1/products", 30, 3000),
    ("catalog-service", "inventory-service", "/v1/stock", 20, 4000),
    ("catalog-service", "pricing-service", "/v1/prices", 15, 4000),
    ("search-service", "catalog-service", "/v1/products", 35, 3000),
    ("notification-service", "email-sender", "/v1/send", 120, 400),
    ("notification-service", "user-profile", "/v1/users/prefs", 15, 400),
    ("pricing-service", "feature-flags", "/v1/flags", 4, 7000),
    ("inventory-service", "analytics-ingest", "/v1/events", 7, 6000),
    ("user-profile", "analytics-ingest", "/v1/events", 7, 5000),
    # kiosk: reaches the company only through /v1/session-refresh
    ("kiosk-bff", "auth-service", "/v1/session-refresh", 30, 200),
    ("kiosk-bff", "kiosk-sync", "/v1/sync", 90, 50),
    # partners: reach the company only through /v1/token-verify
    ("partner-edge", "partner-gateway", "/v1/partner/orders", 70, 800),
    ("partner-gateway", "auth-service", "/v1/token-verify", 13, 800),
    ("partner-gateway", "partner-webhooks", "/v1/hooks", 45, 300),
    ("partner-gateway", "partner-billing", "/v1/invoices", 60, 100),
    # old mobile app: reaches the company only through /v1/token-verify
    ("legacy-mobile-bff", "auth-service", "/v1/token-verify", 14, 1500),
    ("legacy-mobile-bff", "mobile-push", "/v1/push", 50, 400),
]

ENGINEERS = [
    # handle, name, timezone, team
    ("priya", "Priya Raman", "Asia/Kolkata", "platform"),
    ("meera", "Meera Iyer", "Asia/Kolkata", "platform"),
    ("kenji", "Kenji Sato", "Asia/Tokyo", "platform"),
    ("tom", "Tom Becker", "Europe/Berlin", "platform"),
    ("vikram", "Vikram Shah", "Asia/Kolkata", "platform"),
    ("rahul", "Rahul Menon", "Asia/Kolkata", "payments"),
    ("aisha", "Aisha Bello", "Europe/London", "payments"),
    ("nina", "Nina Petrova", "Europe/Berlin", "payments"),
    ("sofia", "Sofia Alves", "America/Sao_Paulo", "growth"),
    ("elena", "Elena Rossi", "Europe/Rome", "growth"),
    ("zara", "Zara Khan", "Asia/Dubai", "growth"),
    ("arjun", "Arjun Nair", "Asia/Kolkata", "partners"),
    ("omar", "Omar Haddad", "Asia/Dubai", "partners"),
    ("li", "Li Wei", "Asia/Shanghai", "mobile"),
    ("dev", "Dev Patel", "Asia/Kolkata", "mobile"),
]

CONTRIBUTED_TO = [
    # handle, service, commits
    ("priya", "auth-service", 140), ("meera", "auth-service", 62), ("kenji", "auth-service", 45),
    ("tom", "auth-service", 21), ("arjun", "auth-service", 12), ("vikram", "auth-service", 8),
    ("priya", "session-service", 40), ("kenji", "session-service", 55), ("tom", "feature-flags", 70),
    ("vikram", "web-gateway", 30), ("meera", "web-gateway", 44), ("tom", "analytics-ingest", 25),
    ("rahul", "checkout-api", 120), ("aisha", "ledger-service", 90), ("nina", "fraud-scorer", 85),
    ("rahul", "payment-worker", 60), ("aisha", "payment-worker", 70), ("nina", "pricing-service", 30),
    ("rahul", "cart-service", 40), ("aisha", "inventory-service", 35),
    ("sofia", "search-service", 75), ("elena", "notification-service", 66), ("zara", "user-profile", 58),
    ("sofia", "catalog-service", 64), ("elena", "email-sender", 20),
    ("arjun", "partner-gateway", 88), ("omar", "partner-gateway", 52), ("omar", "partner-edge", 40),
    ("arjun", "partner-webhooks", 30), ("omar", "partner-billing", 26),
    ("li", "legacy-mobile-bff", 95), ("dev", "legacy-mobile-bff", 40), ("dev", "mobile-push", 33),
    ("li", "kiosk-bff", 50), ("dev", "kiosk-sync", 18),
]

# One edge per review approval or escalation: "from" deferred to "to".
DEFERRED_TO = (
    [("meera", "priya", "review")] * 4 + [("kenji", "priya", "review")] * 3 + [("tom", "priya", "review")] * 3
    + [("arjun", "priya", "review")] * 2 + [("omar", "priya", "escalation")] * 2 + [("li", "priya", "review")] * 2
    + [("dev", "priya", "review"), ("rahul", "priya", "review")] + [("vikram", "priya", "review")] * 2
    + [("priya", "vikram", "escalation"), ("meera", "kenji", "review"), ("tom", "kenji", "review")]
    + [("aisha", "rahul", "review")] * 3 + [("nina", "rahul", "review")] * 2
    + [("rahul", "aisha", "review"), ("priya", "rahul", "review")]
    + [("sofia", "elena", "review")] * 2 + [("zara", "elena", "review")] * 2 + [("elena", "sofia", "review")]
    + [("dev", "li", "review")] * 3 + [("arjun", "omar", "review")] * 2
)

THREADS = [
    # id, channel, summary, ts
    ("T-01", "eng-leads", "Agreed tier definitions and paging rules", "2025-06-02"),
    ("T-02", "platform", "All internal calls must verify tokens with auth-service", "2025-07-14"),
    ("T-04", "platform", "Proposal: exponential backoff with up to 5 retries", "2025-11-10"),
    ("T-05", "payments", "Ledger stays append-only; corrections are new entries", "2025-08-21"),
    ("T-06", "payments", "Fraud score must block checkout until it returns", "2025-09-03"),
    ("T-07", "payments", "Carts move to Redis with a 7-day expiry", "2025-09-18"),
    ("T-08", "platform", "v2 token-verify rollout: v1 can go once traffic is on v2", "2026-03-02"),
    ("T-09", "platform", "Flag reads cached for 30 seconds to protect feature-flags", "2025-10-05"),
    ("T-10", "growth", "Search index rebuilt nightly at 02:00 UTC", "2025-10-20"),
    ("T-11", "payments", "Every payment call carries an idempotency key", "2025-12-01"),
    ("T-12", "platform", "Session refresh moves to /v2/session-refresh", "2026-04-15"),
    ("T-13", "growth", "Notifications always go through the queue", "2026-01-12"),
    ("T-14", "partners", "Partner API rate limit set to 100 requests per second", "2026-02-08"),
    ("T-15", "platform", "/v1/jwks unused since keys moved to v2 metadata", "2026-07-01"),
    ("T-16", "mobile", "Kiosk app frozen: security fixes only", "2026-05-22"),
    ("T-17", "payments", "Retries capped at 3 with jitter after the March incident", "2026-08-20"),
    ("T-18", "growth", "Analytics events are fire-and-forget; no retries", "2026-06-10"),
    ("T-19", "platform", "partner-gateway and legacy-mobile-bff still call /v1/token-verify: keep it", "2026-09-12"),
    ("T-20", "mobile", "Old mobile app sunset planned for Q1 2027", "2026-08-30"),
    ("T-21", "platform", "kiosk-bff still calls /v1/session-refresh: keep it until it moves", "2026-09-30"),
]

DECISIONS = [
    # id, title, summary, valid_from, author, thread, governs (list of endpoint/service ids), blocks_removal
    ("ADR-01", "Service tiers", "Tier 0 services page on-call at any hour, tier 1 within 30 minutes, tiers 2 and 3 the next business day.",
     "2025-06-02", "vikram", "T-01", [], False),
    ("ADR-02", "Internal calls are authenticated", "Every internal call verifies its token with auth-service through /v2/token-verify.",
     "2025-07-14", "priya", "T-02", [ep("auth-service", "/v2/token-verify")], False),
    ("ADR-04", "Retry policy: exponential backoff", "Retry failed calls with exponential backoff, up to 5 retries.",
     "2025-11-10", "kenji", "T-04", [], False),
    ("ADR-05", "Ledger writes are append-only", "The ledger never updates or deletes entries; corrections are new entries.",
     "2025-08-21", "aisha", "T-05", [ep("ledger-service", "/v1/ledger/entries")], False),
    ("ADR-06", "Fraud scoring is synchronous in checkout", "Checkout waits for the fraud score before charging.",
     "2025-09-03", "nina", "T-06", [ep("fraud-scorer", "/v1/score")], False),
    ("ADR-07", "Carts live in Redis", "Carts are stored in Redis and expire after 7 days.",
     "2025-09-18", "rahul", "T-07", [ep("cart-service", "/v1/cart")], False),
    ("ADR-08", "Retire /v1/token-verify after the v2 rollout", "Once callers move to /v2/token-verify, the v1 endpoint can be removed.",
     "2026-03-02", "meera", "T-08", [ep("auth-service", "/v1/token-verify")], False),
    ("ADR-09", "Feature flags are cached", "Flag reads are cached for 30 seconds to protect feature-flags.",
     "2025-10-05", "tom", "T-09", [ep("feature-flags", "/v1/flags")], False),
    ("ADR-10", "Search index rebuilds nightly", "The search index is rebuilt every night at 02:00 UTC.",
     "2025-10-20", "sofia", "T-10", [ep("search-service", "/v1/search")], False),
    ("ADR-11", "Payments carry idempotency keys", "Every call to payment-worker carries an idempotency key.",
     "2025-12-01", "aisha", "T-11", [ep("payment-worker", "/v1/payments")], False),
    ("ADR-12", "Move session refresh to v2", "Session refresh moves to /v2/session-refresh; /v1/session-refresh can then be removed.",
     "2026-04-15", "kenji", "T-12", [ep("auth-service", "/v1/session-refresh")], False),
    ("ADR-13", "Notifications go through a queue", "Services never send notifications inline; they enqueue them.",
     "2026-01-12", "elena", "T-13", [ep("notification-service", "/v1/notify")], False),
    ("ADR-14", "Partner API rate limit", "The partner API allows 100 requests per second per partner.",
     "2026-02-08", "omar", "T-14", [ep("partner-gateway", "/v1/partner/orders")], False),
    ("ADR-15", "Retire /v1/jwks", "Signing keys are served through v2 token metadata, so /v1/jwks can be removed.",
     "2026-07-01", "priya", "T-15", [ep("auth-service", "/v1/jwks")], False),
    ("ADR-16", "Kiosk app is frozen", "The kiosk app gets security fixes only until it is replaced.",
     "2026-05-22", "li", "T-16", ["kiosk-bff"], False),
    ("ADR-17", "Retry policy: at most 3 retries with jitter", "Retry failed calls at most 3 times with jitter, and require idempotency keys on retried writes.",
     "2026-08-20", "rahul", "T-17", [], False),
    ("ADR-18", "Analytics events are fire-and-forget", "Analytics events are sent once with no retries.",
     "2026-06-10", "zara", "T-18", [ep("analytics-ingest", "/v1/events")], False),
    ("ADR-19", "Keep /v1/token-verify until partners and old mobile move",
     "Do not remove /v1/token-verify until partner-gateway and legacy-mobile-bff move to /v2/token-verify.",
     "2026-09-12", "priya", "T-19", [ep("auth-service", "/v1/token-verify")], True),
    ("ADR-20", "Old mobile app sunset", "legacy-mobile-bff is planned for shutdown in Q1 2027.",
     "2026-08-30", "li", "T-20", ["legacy-mobile-bff"], False),
    ("ADR-21", "Keep /v1/session-refresh until kiosks move",
     "Do not remove /v1/session-refresh until kiosk-bff moves to /v2/session-refresh.",
     "2026-09-30", "priya", "T-21", [ep("auth-service", "/v1/session-refresh")], True),
]

SUPERSEDED = [
    # old, new, superseded_at, reason
    ("ADR-04", "ADR-17", "2026-08-20", "5 retries amplified the March incident"),
    ("ADR-08", "ADR-19", "2026-09-12", "partner-gateway and legacy-mobile-bff still call /v1/token-verify"),
    ("ADR-12", "ADR-21", "2026-09-30", "kiosk-bff still calls /v1/session-refresh"),
]

HISTORICAL_PRS = [
    # number, title, author, target (endpoint or service id), change, cites, created_at
    (1001, "Add idempotency keys to payment calls", "aisha", ep("payment-worker", "/v1/payments"), "modify", ["ADR-11"], "2025-12-03"),
    (1002, "Cache flag reads for 30 seconds", "tom", ep("feature-flags", "/v1/flags"), "modify", ["ADR-09"], "2025-10-06"),
    (1003, "Nightly search index rebuild", "sofia", ep("search-service", "/v1/search"), "modify", ["ADR-10"], "2025-10-22"),
    (1004, "Move carts to Redis", "rahul", ep("cart-service", "/v1/cart"), "modify", ["ADR-07"], "2025-09-20"),
    (1005, "Append-only ledger writes", "aisha", ep("ledger-service", "/v1/ledger/entries"), "modify", ["ADR-05"], "2025-08-25"),
    (1006, "Make fraud scoring blocking in checkout", "nina", ep("fraud-scorer", "/v1/score"), "modify", ["ADR-06"], "2025-09-05"),
    (1007, "Add /v2/token-verify", "priya", ep("auth-service", "/v2/token-verify"), "modify", ["ADR-02"], "2026-02-20"),
    (1008, "Move web-gateway to /v2/token-verify", "meera", "web-gateway", "modify", ["ADR-08"], "2026-03-05"),
    (1009, "Move checkout-api to /v2/token-verify", "rahul", "checkout-api", "modify", ["ADR-08"], "2026-03-09"),
    (1010, "Move payment-worker to /v2/token-verify", "aisha", "payment-worker", "modify", ["ADR-08"], "2026-03-11"),
    (1011, "Add /v2/session-refresh", "kenji", ep("auth-service", "/v2/session-refresh"), "modify", ["ADR-12"], "2026-04-16"),
    (1012, "Move session-service to /v2/session-refresh", "kenji", "session-service", "modify", ["ADR-12"], "2026-04-20"),
    (1013, "Queue all notifications", "elena", ep("notification-service", "/v1/notify"), "modify", ["ADR-13"], "2026-01-15"),
    (1014, "Partner rate limit at 100 rps", "omar", ep("partner-gateway", "/v1/partner/orders"), "modify", ["ADR-14"], "2026-02-10"),
    (1015, "Exponential backoff helper", "kenji", "web-gateway", "modify", ["ADR-04"], "2025-11-12"),
    (1016, "Cap retries at 3 with jitter", "rahul", "checkout-api", "modify", ["ADR-17"], "2026-08-22"),
    (1017, "Fire-and-forget analytics client", "zara", ep("analytics-ingest", "/v1/events"), "modify", ["ADR-18"], "2026-06-12"),
    (1018, "Freeze kiosk features", "li", "kiosk-bff", "modify", ["ADR-16"], "2026-05-23"),
    (1019, "Serve signing keys in v2 token metadata", "priya", ep("auth-service", "/v2/token-verify"), "modify", ["ADR-15"], "2026-07-02"),
    (1020, "Ledger balance endpoint", "aisha", ep("ledger-service", "/v1/ledger/balance"), "modify", [], "2026-01-30"),
    (1021, "Refunds endpoint", "rahul", ep("payment-worker", "/v1/refunds"), "modify", ["ADR-11"], "2026-02-14"),
    (1022, "Category browsing", "sofia", ep("catalog-service", "/v1/categories"), "modify", [], "2026-03-18"),
    (1023, "Partner webhooks retry once", "arjun", ep("partner-webhooks", "/v1/hooks"), "modify", ["ADR-17"], "2026-08-28"),
    (1024, "Push notification batching", "dev", ep("mobile-push", "/v1/push"), "modify", [], "2026-06-01"),
    (1025, "Kiosk catalogue sync every hour", "dev", ep("kiosk-sync", "/v1/sync"), "modify", ["ADR-16"], "2026-05-28"),
    (1026, "User preference reads", "zara", ep("user-profile", "/v1/users/prefs"), "modify", [], "2026-04-02"),
    (1027, "Partner invoices in PDF", "omar", ep("partner-billing", "/v1/invoices"), "modify", [], "2026-07-19"),
]


def demo_prs(numbers: tuple[int, int, int]) -> list[dict]:
    """The three PRs the demo runs. Their numbers match the PRs opened in the demo GitHub repo."""
    tv, sr, jw = numbers
    return [
        {
            "number": tv, "title": "Remove /v1/token-verify", "author": "arjun", "created_at": "2026-10-16",
            "description": "The v2 rollout is complete, so per ADR-08 the v1 endpoint can go.\n\n"
                           "Removes: /v1/token-verify\nCites: ADR-08",
            "target": ep("auth-service", "/v1/token-verify"), "change": "remove", "cites": ["ADR-08"],
        },
        {
            "number": sr, "title": "Remove /v1/session-refresh", "author": "tom", "created_at": "2026-10-17",
            "description": "Session refresh moved to v2 (ADR-12), so the v1 route can go.\n\n"
                           "Removes: /v1/session-refresh\nCites: ADR-12",
            "target": ep("auth-service", "/v1/session-refresh"), "change": "remove", "cites": ["ADR-12"],
        },
        {
            "number": jw, "title": "Remove unused /v1/jwks", "author": "kenji", "created_at": "2026-10-17",
            "description": "Keys are served through v2 token metadata (ADR-15), so /v1/jwks is unused.\n\n"
                           "Removes: /v1/jwks\nCites: ADR-15",
            "target": ep("auth-service", "/v1/jwks"), "change": "remove", "cites": ["ADR-15"],
        },
    ]
