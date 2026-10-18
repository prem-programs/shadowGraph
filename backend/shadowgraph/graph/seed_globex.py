"""Second synthetic company 'Globex' (8 services). Exists to show that memory is separate per tenant."""
from __future__ import annotations

TENANT = "globex"
COMPANY = "Globex"

TEAMS = [("core", "Core", "#core-oncall"), ("data", "Data", "#data-oncall")]

SERVICES = [
    ("globex-web", 0, "core", "Web edge"),
    ("globex-api", 0, "core", "Public API"),
    ("globex-auth", 0, "core", "Authentication"),
    ("globex-orders", 1, "core", "Orders"),
    ("globex-billing", 1, "core", "Billing"),
    ("globex-export", 2, "data", "Data exports"),
    ("globex-reports", 2, "data", "Reports"),
    ("globex-mailer", 3, "data", "Email"),
]

ENDPOINTS = [
    ("globex-auth", "/v1/verify", 1, False),
    ("globex-orders", "/v1/orders", 1, False),
    ("globex-billing", "/v1/bills", 1, False),
    ("globex-export", "/v1/export", 1, False),
    ("globex-export", "/v1/legacy-export", 1, True),
    ("globex-reports", "/v1/reports", 1, False),
    ("globex-mailer", "/v1/mail", 1, False),
    ("globex-api", "/v1/public", 1, False),
]


def ep(service: str, route: str) -> str:
    return f"{service}:{route}"


DEPENDS_ON = [
    ("globex-web", "globex-api", "/v1/public", 40, 2000),
    ("globex-web", "globex-auth", "/v1/verify", 10, 2000),
    ("globex-api", "globex-auth", "/v1/verify", 10, 3000),
    ("globex-api", "globex-orders", "/v1/orders", 30, 1500),
    ("globex-orders", "globex-billing", "/v1/bills", 50, 800),
    ("globex-billing", "globex-mailer", "/v1/mail", 90, 200),
    ("globex-reports", "globex-export", "/v1/export", 300, 20),
    ("globex-reports", "globex-orders", "/v1/orders", 60, 50),
]

ENGINEERS = [
    ("hana", "Hana Kim", "Asia/Seoul", "core"),
    ("ivan", "Ivan Novak", "Europe/Prague", "core"),
    ("jo", "Jo Mensah", "Africa/Accra", "data"),
    ("kai", "Kai Lindqvist", "Europe/Stockholm", "data"),
]

CONTRIBUTED_TO = [
    ("hana", "globex-auth", 80), ("ivan", "globex-orders", 60), ("jo", "globex-export", 45),
    ("kai", "globex-reports", 30), ("hana", "globex-api", 50), ("ivan", "globex-billing", 40),
    ("jo", "globex-mailer", 10),
]

DEFERRED_TO = [("ivan", "hana", "review"), ("ivan", "hana", "review"), ("jo", "hana", "review"), ("kai", "jo", "review")]

THREADS = [
    ("G-T1", "data", "Legacy export unused since the new export shipped", "2026-07-15"),
    ("G-T2", "core", "All API calls verify tokens", "2026-01-10"),
]

DECISIONS = [
    ("G-ADR-1", "Retire legacy export", "The /v1/legacy-export endpoint can be removed; reports use /v1/export.",
     "2026-07-15", "jo", "G-T1", [ep("globex-export", "/v1/legacy-export")], False),
    ("G-ADR-2", "API calls are authenticated", "Every API call verifies its token with globex-auth.",
     "2026-01-10", "hana", "G-T2", [ep("globex-auth", "/v1/verify")], False),
]

SUPERSEDED: list[tuple[str, str, str, str]] = []

HISTORICAL_PRS = [
    (2001, "Add /v1/export", "jo", ep("globex-export", "/v1/export"), "modify", ["G-ADR-1"], "2026-06-30"),
    (2002, "Token checks on the API", "hana", ep("globex-auth", "/v1/verify"), "modify", ["G-ADR-2"], "2026-01-12"),
]


def demo_prs(numbers: tuple[int, int, int]) -> list[dict]:
    return [
        {
            "number": numbers[0], "title": "Remove /v1/legacy-export", "author": "jo", "created_at": "2026-10-17",
            "description": "Reports moved to /v1/export (G-ADR-1).\n\nRemoves: /v1/legacy-export\nCites: G-ADR-1",
            "target": ep("globex-export", "/v1/legacy-export"), "change": "remove", "cites": ["G-ADR-1"],
        }
    ]
