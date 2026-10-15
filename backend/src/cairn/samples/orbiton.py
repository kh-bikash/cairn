"""Generate "Orbiton", a fictional logistics company, as a workspace in real export formats.

Everything here is invented: people, systems and events. It exists so Cairn can be evaluated on the parts of a
company brain that public GitHub data can't show (chat decisions, tickets, ADRs, postmortems, org structure,
people leaving). Output is deterministic for a given seed.

Story arcs woven through Slack, Jira and docs:
  1. Kafka (ADR-0003) chosen partly because "Priya has run Kafka in production"; Priya leaves in June; a July
     outage postmortem contradicts the premise; ADR-0011 supersedes it with managed MSK.
  2. Payments idempotency keys with a 24h TTL, agreed in Slack and never documented; a dispute-webhook duplicate
     charge months later (PAY-341) shows Stripe resends dispute events for up to 72 hours.
  3. Route optimizer moved to OR-Tools (RFC-0007); Marcus is effectively its only expert.
  4. Driver app built in React Native (ADR-0005) assuming acceptable performance on 2 GB Android devices; a
     postmortem shows ANRs on exactly those devices, leading to a native map module (ADR-0012).
  5. JWT auth for offline drivers (ADR-0006), SMS provider switch for cost, feature flags build-vs-buy (RFC-0009).
"""

from __future__ import annotations

import csv
import json
import random
import shutil
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

COMPANY = "Orbiton"
DOMAIN = "orbiton.example"
START = datetime(2025, 11, 3, tzinfo=UTC)
END = datetime(2026, 10, 9, tzinfo=UTC)

TEAMS = [
    ("platform", "Platform", "Shared infrastructure: events pipeline, auth, feature flags."),
    ("payments", "Payments", "Checkout, payments and the ledger."),
    ("routing", "Routing", "Route optimisation and dispatch."),
    ("mobile", "Mobile", "The driver app."),
    ("data", "Data", "Analytics warehouse and data pipelines."),
    ("sre", "SRE", "Reliability, on-call and incident response."),
    ("product", "Product", "Product management."),
]

# handle, name, team, role, manager, joined, left
PEOPLE = [
    ("maya", "Maya Chen", "product", "CTO", "", "2023-02-01", ""),
    ("tom", "Tom Okafor", "platform", "Engineering Manager", "maya", "2023-05-15", ""),
    ("priya", "Priya Raman", "platform", "Staff Engineer", "tom", "2023-06-01", "2026-06-30"),
    ("lena", "Lena Fischer", "platform", "Senior Engineer", "tom", "2024-01-08", ""),
    ("diego", "Diego Alvarez", "platform", "Engineer", "tom", "2025-03-03", ""),
    ("sara", "Sara Kowalski", "payments", "Engineering Manager", "maya", "2023-09-04", ""),
    ("arjun", "Arjun Mehta", "payments", "Senior Engineer", "sara", "2024-02-12", ""),
    ("hannah", "Hannah Brooks", "payments", "Engineer", "sara", "2024-10-01", ""),
    ("kenji", "Kenji Watanabe", "payments", "Engineer", "sara", "2025-06-16", ""),
    ("marcus", "Marcus Bell", "routing", "Principal Engineer", "maya", "2023-03-20", ""),
    ("aisha", "Aisha Yusuf", "routing", "Engineer", "marcus", "2025-01-13", ""),
    ("pavel", "Pavel Novak", "routing", "Engineer", "marcus", "2025-08-04", ""),
    ("elena", "Elena Rossi", "mobile", "Engineering Manager", "maya", "2023-11-06", ""),
    ("jordan", "Jordan Lee", "mobile", "Senior Engineer", "elena", "2024-03-18", ""),
    ("fatima", "Fatima Haddad", "mobile", "Engineer", "elena", "2024-09-09", ""),
    ("chris", "Chris Wong", "mobile", "Engineer", "elena", "2025-02-24", ""),
    ("noah", "Noah Schmidt", "data", "Data Engineering Lead", "maya", "2023-08-14", ""),
    ("ines", "Ines Duarte", "data", "Data Engineer", "noah", "2024-05-06", ""),
    ("omar", "Omar Farouk", "data", "Analytics Engineer", "noah", "2025-04-07", ""),
    ("rachel", "Rachel Kim", "sre", "SRE Lead", "maya", "2023-07-10", ""),
    ("ben", "Ben Carter", "sre", "SRE", "rachel", "2024-06-03", ""),
    ("yuki", "Yuki Tanaka", "sre", "SRE", "rachel", "2026-07-06", ""),
    ("grace", "Grace Liu", "product", "Product Manager, Routing", "maya", "2024-04-01", ""),
    ("david", "David Park", "product", "Product Manager, Payments", "maya", "2024-08-19", ""),
]

# key, name, kind, team, depends_on, aliases, description
SYSTEMS = [
    (
        "checkout-api",
        "checkout-api",
        "service",
        "payments",
        "payments-service;auth-service;feature-flags",
        "checkout",
        "Public checkout API used by the web and partner integrations.",
    ),
    (
        "payments-service",
        "payments-service",
        "service",
        "payments",
        "ledger-db;events-pipeline",
        "payments svc;payment service",
        "Charges, refunds and Stripe webhooks.",
    ),
    ("ledger-db", "ledger-db", "database", "payments", "", "ledger", "Postgres ledger of every money movement."),
    ("events-pipeline", "events-pipeline", "platform", "platform", "", "kafka;event bus;msk", "Company-wide event bus (Kafka, later Amazon MSK)."),
    ("auth-service", "auth-service", "service", "platform", "", "auth;jwt", "Login, tokens and sessions for drivers and staff."),
    ("feature-flags", "feature-flags", "service", "platform", "", "flags;flag service", "In-house feature flag service."),
    ("route-optimizer", "route-optimizer", "service", "routing", "", "optimizer;or-tools", "Builds daily delivery routes with time windows."),
    (
        "dispatch-api",
        "dispatch-api",
        "service",
        "routing",
        "route-optimizer;events-pipeline;auth-service",
        "dispatch",
        "Assigns routes to drivers and tracks progress.",
    ),
    (
        "driver-app",
        "driver-app",
        "app",
        "mobile",
        "dispatch-api;auth-service;notifications-service",
        "driver app;mobile app",
        "React Native app used by delivery drivers.",
    ),
    (
        "notifications-service",
        "notifications-service",
        "service",
        "platform",
        "events-pipeline",
        "notifications;sms",
        "SMS and push notifications to customers and drivers.",
    ),
    ("warehouse-db", "warehouse-db", "database", "data", "", "orders db", "Operational Postgres for orders and inventory."),
    ("analytics-warehouse", "analytics-warehouse", "data", "data", "events-pipeline;warehouse-db", "warehouse;dbt", "Analytics warehouse and dbt models."),
]

CHANNELS = {
    "platform": "Platform team: event bus, auth, flags",
    "payments": "Payments team",
    "routing": "Routing and dispatch",
    "mobile": "Driver app",
    "data": "Data team",
    "sre-oncall": "On-call coordination",
    "incidents": "Live incidents",
    "deploys": "Deploy notifications",
    "arch-review": "Architecture review requests and outcomes",
    "general": "Company-wide",
}


@dataclass
class Msg:
    channel: str
    when: datetime
    author: str
    text: str
    thread: datetime | None = None


def _d(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=UTC)


def _thread(channel: str, start: str, *lines: tuple[str, str], gap_minutes: int = 17) -> list[Msg]:
    t0 = _d(start)
    msgs = [Msg(channel, t0, lines[0][0], lines[0][1])]
    for i, (who, text) in enumerate(lines[1:], 1):
        msgs.append(Msg(channel, t0 + timedelta(minutes=gap_minutes * i + (i * 7) % 11), who, text, thread=t0))
    return msgs


# --------------------------------------------------------------------------- story threads (Slack)


def story_threads() -> list[Msg]:
    m: list[Msg] = []
    # Arc 1: Kafka.
    m += _thread(
        "platform",
        "2025-11-18T10:05:00",
        (
            "priya",
            "We need to pick the event bus for events-pipeline before the dispatch rewrite. My vote is Kafka: we need to replay at least 7 days of events when a projection has a bug, and SQS can't do replay.",
        ),
        ("lena", "Agree on replay. Kinesis also has replay though?"),
        (
            "priya",
            "It does, but 7-day retention on Kinesis costs more at our volume and none of us has run it. I ran Kafka in production for three years at my last job, and Lena has too, so we can operate it ourselves.",
        ),
        ("tom", "What peak are we sizing for?"),
        ("priya", "Around 3k events/s at peak per the growth plan. A 3-broker cluster handles that with lots of headroom."),
        ("tom", "OK. Priya, can you write it up as an ADR so arch-review can sign off?"),
        ("priya", "Will do, ADR-0003 by Friday."),
    )
    m += _thread(
        "arch-review",
        "2025-12-04T15:00:00",
        ("tom", "ADR-0003 (Kafka for events-pipeline) approved in today's review. Priya owns the rollout."),
        ("maya", "Approved from my side. Please make sure on-call has a runbook before we put payments events on it."),
        ("priya", "Runbook is in docs/runbooks/events-pipeline.md, I'll walk SRE through it next week."),
    )
    m += _thread(
        "general",
        "2026-06-26T16:00:00",
        (
            "tom",
            "Some news: Priya's last day is June 30. She's been the heart of the events pipeline and we'll miss her a lot. Lena is taking over ownership, and Yuki joins SRE in July.",
        ),
        ("priya", "Thank you all! Handover notes are in the runbook. Ping me on LinkedIn anytime."),
        ("rachel", "Thank you Priya, the pipeline would not exist without you."),
    )
    m += _thread(
        "incidents",
        "2026-07-14T02:12:00",
        ("ben", "SEV1: events-pipeline consumer lag growing fast, payments and dispatch events delayed. Two brokers dropped out of ISR."),
        ("ben", "I don't know how to safely re-elect partition leaders here. Runbook step 6 assumes you know which partitions to move."),
        ("lena", "Joining. I've only done this once with Priya watching, give me a minute."),
        ("rachel", "Promotion traffic is at ~11k events/s, way above the 3k we sized for. Throttling partner webhooks to buy time."),
        ("lena", "Leaders re-elected, lag recovering. This took us way too long."),
        ("rachel", "Recovered at 07:05. Postmortem tomorrow."),
        gap_minutes=45,
    )
    m += _thread(
        "platform",
        "2026-07-20T11:30:00",
        (
            "lena",
            "Following the postmortem: I don't think we should keep running our own Kafka. Nobody left on the team has real Kafka ops experience and it showed on the 14th.",
        ),
        ("tom", "Options are MSK (managed Kafka) or moving to Kinesis."),
        ("diego", "Kinesis means rewriting all 14 consumers. MSK keeps the Kafka protocol, consumers don't change."),
        ("lena", "Agreed, MSK. I'll open PLAT-212 to evaluate cost and write ADR-0011."),
    )
    # Arc 2: payments idempotency (never written down).
    m += _thread(
        "payments",
        "2026-01-15T14:20:00",
        (
            "arjun",
            "Proposal for webhook idempotency: store an idempotency key per Stripe event in Redis with a 24h TTL. Stripe retries webhooks for up to 24 hours, so after that the key is useless.",
        ),
        ("hannah", "Why not keep keys forever in ledger-db?"),
        ("arjun", "That table would grow by millions of rows a month, and Redis lookups are ~1ms on the hot path. 24h covers the whole retry window."),
        ("sara", "Makes sense. Let's go with 24h TTL in Redis. We'll document it later."),
        ("david", "Does this cover refunds too?"),
        ("arjun", "Yes, every webhook type goes through the same check."),
    )
    m += _thread(
        "payments",
        "2026-05-06T09:40:00",
        ("hannah", "PAY-341: a customer was charged twice after a dispute. The second charge came from a dispute webhook Stripe resent ~3 days later."),
        ("arjun", "Stripe resends dispute events for up to 72 hours, not 24. Our idempotency key had already expired."),
        ("sara", "So the 24h TTL assumption was wrong for disputes. Did we ever write that decision down?"),
        ("arjun", "No, it only lived in this channel. I'll extend dispute keys to 7 days and add a ledger-level unique constraint as a backstop."),
    )
    # Arc 3: route optimizer.
    m += _thread(
        "routing",
        "2026-01-28T10:00:00",
        ("grace", "Customers keep complaining about missed delivery windows. Ops says 18% of routes violate a time window."),
        (
            "marcus",
            "The greedy heuristic doesn't model hard time windows at all. I've been prototyping with OR-Tools: it solves our 500-stop benchmark in 4.2s with zero violations.",
        ),
        ("aisha", "What about Gurobi? It's faster."),
        ("marcus", "License is around $60k/yr and we don't need its edge yet. I'll write RFC-0007."),
    )
    m += _thread(
        "routing",
        "2026-08-19T13:10:00",
        ("pavel", "Optimizer is timing out on the 1,200-stop Saturday batch. I tried tuning the solver params but I'm not sure what most of them do."),
        ("aisha", "Same, honestly only Marcus understands the constraint model. Marcus, can you pair with us this week?"),
        ("marcus", "Yes. We really need to spread this knowledge, I'm the only one who has touched the solver config."),
    )
    # Arc 4: driver app performance.
    m += _thread(
        "mobile",
        "2025-12-09T11:00:00",
        ("elena", "Decision time for the driver app stack. With one mobile team of four, two native codebases is too much."),
        ("jordan", "React Native then. We tested the prototype on a Moto E (2 GB RAM) and the map screen held 50+ fps."),
        ("fatima", "Flutter is nice but nobody here has shipped it."),
        ("elena", "React Native it is. Jordan, ADR-0005 please."),
    )
    m += _thread(
        "mobile",
        "2026-04-20T08:30:00",
        ("chris", "Play Console shows ANR rate 4.1% on the driver app, almost all on 2 GB devices. Map screen with 150+ stop markers drops to ~15 fps."),
        ("jordan", "The prototype only had 20 markers. Real routes are 10x that."),
        ("elena", "Let's do a postmortem and look at moving the map to a native module."),
    )
    # Arc 5: auth, SMS, flags.
    m += _thread(
        "platform",
        "2026-01-12T10:15:00",
        ("lena", "Driver app needs to keep working in dead zones for a full shift. Session cookies won't work offline."),
        ("tom", "JWT with short-lived access tokens + refresh tokens?"),
        ("lena", "Yes: 15 min access tokens, 8 hour refresh tokens so a shift survives offline. ADR-0006 coming."),
    )
    m += _thread(
        "payments",
        "2026-03-03T15:45:00",
        ("david", "Twilio SMS costs for EU delivery notifications doubled after the pricing change."),
        ("kenji", "MessageBird quotes ~40% less per SMS in the EU and has a comparable delivery rate in our test."),
        ("sara", "Platform owns notifications-service, but payments pays the bill. Tom, OK to switch?"),
        ("tom", "Fine by me if the delivery rate holds. Let's switch EU first, keep Twilio for Brazil until we measure there."),
    )
    m += _thread(
        "notifications",
        "2026-06-10T09:00:00",
        ("diego", "MessageBird delivery rate in Brazil is 81% vs 97% on Twilio in the pilot."),
        ("tom", "Then Brazil stays on Twilio. Good that we didn't switch everything at once."),
    )
    m += _thread(
        "arch-review",
        "2026-02-24T14:00:00",
        ("lena", "RFC-0009: build vs buy for feature flags. LaunchDarkly quote is $48k/yr for our seat count."),
        ("tom", "Our needs are simple: boolean flags, percentage rollouts, per-tenant overrides."),
        ("maya", "Build it, but keep it boring. Revisit if we need experimentation."),
    )
    return m


# --------------------------------------------------------------------------- background chatter


_BG = {
    "deploys": [
        "Deployed {sys} {ver} to production.",
        "Rolling back {sys} {ver}: error rate up after deploy.",
        "{sys} {ver} is live in staging, prod tomorrow.",
    ],
    "sre-oncall": [
        "Handing over on-call. Quiet week except a noisy disk alert on {sys}.",
        "Paged for {sys} latency at 03:10, auto-resolved after a pod restart.",
        "Can someone from {team} look at the {sys} dashboard? p99 creeping up.",
    ],
    "data": [
        "dbt run failed on the orders model again, rerunning.",
        "New dashboard for on-time delivery rate is live.",
        "Backfilling {sys} events for last week after the schema change.",
    ],
    "general": [
        "Reminder: all-hands Thursday at 16:00.",
        "Welcome {person} to the team!",
        "Office closed Friday for the public holiday.",
    ],
}
_BG_REPLIES = ["Thanks!", "On it.", "Looking now.", "Ack.", "👍", "Done.", "Can you share the dashboard link?", "Same here."]


def _recent_joiner(when: datetime) -> str | None:
    """First name of someone who joined in the 10 days before `when`, if any."""
    for _, name, _, _, _, joined, _ in PEOPLE:
        if timedelta(0) <= when - _d(joined) <= timedelta(days=10):
            return name.split()[0]
    return None


def background(rng: random.Random) -> list[Msg]:
    """Routine chatter, posted by the people who would really post it (owning team, SRE, data team)."""
    people = [p[0] for p in PEOPLE]
    team_of = {p[0]: p[2] for p in PEOPLE}
    owner_of = {s[0]: s[3] for s in SYSTEMS}
    systems = [s[0] for s in SYSTEMS]
    data_systems = [s[0] for s in SYSTEMS if s[3] == "data"]

    def pick(team: str | None, when: datetime) -> str:
        pool = [h for h in people if (team is None or team_of[h] == team) and team_of[h] != "product" and _employed(h, when)]
        return rng.choice(pool or [h for h in people if _employed(h, when)])

    out: list[Msg] = []
    day = START
    while day < END:
        if day.weekday() < 5:
            for _ in range(rng.randint(1, 3)):
                channel = rng.choice(list(_BG))
                sys = rng.choice(data_systems if channel == "data" else systems)
                when = day.replace(hour=rng.randint(8, 18), minute=rng.randint(0, 59))
                author_team = {"deploys": owner_of[sys], "sre-oncall": "sre", "data": "data", "general": None}[channel]
                template = rng.choice(_BG[channel])
                newcomer = _recent_joiner(when)
                if "{person}" in template and newcomer is None:
                    template = _BG["general"][0]  # only welcome people who actually just joined
                text = template.format(
                    sys=sys,
                    ver=f"v{rng.randint(1, 4)}.{rng.randint(0, 30)}.{rng.randint(0, 9)}",
                    team=next(t[1] for t in TEAMS if t[0] == owner_of[sys]),
                    person=newcomer or "",
                )
                out.append(Msg(channel, when, pick(author_team, when), text))
                for i in range(rng.choice([0, 0, 1, 2])):
                    # Replies come from the system's owning team (asked to look) or the poster's own team.
                    reply_team = owner_of[sys] if channel in {"deploys", "sre-oncall"} else author_team
                    out.append(Msg(channel, when + timedelta(minutes=5 + 9 * i), pick(reply_team, when), rng.choice(_BG_REPLIES), thread=when))
        day += timedelta(days=1)
    return out


# --------------------------------------------------------------------------- Jira


@dataclass
class Ticket:
    key: str
    summary: str
    description: str
    status: str
    assignee: str
    reporter: str
    created: str
    resolved: str
    components: list[str]
    labels: list[str]
    comments: list[tuple[str, str, str]]


def story_tickets() -> list[Ticket]:
    return [
        Ticket(
            "PLAT-41",
            "Stand up 3-broker Kafka cluster for events-pipeline",
            "Per ADR-0003. Size for ~3k events/s peak with 7-day retention.",
            "Done",
            "priya",
            "tom",
            "2025-12-08 10:00",
            "2026-01-09 17:00",
            ["events-pipeline"],
            ["kafka"],
            [("2026-01-09 16:40", "priya", "Cluster live, runbook updated.")],
        ),
        Ticket(
            "PLAT-52",
            "Schema registry for events-pipeline topics",
            "Avro schemas with compatibility checks so producers can't break consumers.",
            "Done",
            "priya",
            "tom",
            "2026-01-12 10:00",
            "2026-02-06 17:00",
            ["events-pipeline"],
            ["kafka"],
            [("2026-02-06 16:00", "priya", "Registry live; all payments topics registered.")],
        ),
        Ticket(
            "PLAT-58",
            "Consumer lag alerting for events-pipeline",
            "Alert when any consumer group lags more than 5 minutes.",
            "Done",
            "priya",
            "rachel",
            "2026-01-26 10:00",
            "2026-02-10 17:00",
            ["events-pipeline"],
            ["kafka", "observability"],
            [],
        ),
        Ticket(
            "PLAT-71",
            "Tune retention to 7 days on replayable topics",
            "Per ADR-0003 replay requirement; compacted topics for projections.",
            "Done",
            "priya",
            "tom",
            "2026-02-16 10:00",
            "2026-02-27 17:00",
            ["events-pipeline"],
            ["kafka"],
            [],
        ),
        Ticket(
            "PLAT-89",
            "Kafka broker rolling upgrade to 3.8",
            "Rolling upgrade with zero downtime; document the procedure.",
            "Done",
            "priya",
            "tom",
            "2026-03-09 10:00",
            "2026-03-20 17:00",
            ["events-pipeline"],
            ["kafka"],
            [("2026-03-20 15:30", "priya", "Done. Procedure is in the runbook, but it needs someone who knows the controller quirks.")],
        ),
        Ticket(
            "PLAT-104",
            "Move dispatch events onto events-pipeline",
            "Migrate dispatch-api from direct HTTP callbacks to Kafka topics.",
            "Done",
            "priya",
            "marcus",
            "2026-04-01 10:00",
            "2026-04-24 17:00",
            ["events-pipeline", "dispatch-api"],
            ["kafka"],
            [],
        ),
        Ticket(
            "PLAT-117",
            "Partition rebalancing playbook",
            "Write down how to move partitions off a sick broker.",
            "To Do",
            "priya",
            "rachel",
            "2026-05-11 10:00",
            "",
            ["events-pipeline"],
            ["kafka", "docs"],
            [("2026-06-20 11:00", "priya", "Didn't get to this before my last day, sorry. Notes are in the runbook draft.")],
        ),
        Ticket(
            "PLAT-126",
            "Handover: events-pipeline ownership to Lena",
            "Knowledge transfer before Priya leaves.",
            "Done",
            "priya",
            "tom",
            "2026-06-02 10:00",
            "2026-06-29 17:00",
            ["events-pipeline"],
            ["handover"],
            [("2026-06-29 16:00", "lena", "Two shadow sessions done; still uncomfortable with broker recovery.")],
        ),
        Ticket(
            "RTE-81",
            "Solver: add driver break constraints",
            "Drivers must take a 30 minute break after 4.5 hours.",
            "Done",
            "marcus",
            "grace",
            "2026-04-06 10:00",
            "2026-04-17 17:00",
            ["route-optimizer"],
            ["or-tools"],
            [],
        ),
        Ticket(
            "RTE-93",
            "Solver: vehicle capacity by volume, not count",
            "Model parcel volume so vans aren't overfilled.",
            "Done",
            "marcus",
            "grace",
            "2026-05-04 10:00",
            "2026-05-22 17:00",
            ["route-optimizer"],
            ["or-tools"],
            [],
        ),
        Ticket(
            "RTE-104",
            "Solver parameter tuning for large depots",
            "First-solution strategy and local search limits per depot size.",
            "Done",
            "marcus",
            "aisha",
            "2026-06-08 10:00",
            "2026-06-19 17:00",
            ["route-optimizer"],
            ["or-tools", "performance"],
            [("2026-06-19 16:00", "marcus", "Parameters documented in config/solver.yaml comments.")],
        ),
        Ticket(
            "PLAT-198",
            "Events pipeline outage 2026-07-14: ISR shrink and consumer lag",
            "Two brokers dropped out of ISR during the promotion peak (~11k events/s). Recovery took ~5 hours because nobody on call had done partition leader re-election before.",
            "Done",
            "lena",
            "rachel",
            "2026-07-14 07:30",
            "2026-07-30 12:00",
            ["events-pipeline"],
            ["incident", "sev1"],
            [("2026-07-15 10:00", "rachel", "Postmortem PM-2026-07-14 written."), ("2026-07-16 09:00", "lena", "Action items moved to PLAT-212.")],
        ),
        Ticket(
            "PLAT-212",
            "Evaluate Amazon MSK vs Kinesis for events-pipeline",
            "After the July outage we no longer have in-house Kafka operations experience. Compare managed options; consumers should not need rewrites.",
            "Done",
            "lena",
            "tom",
            "2026-07-21 09:00",
            "2026-08-18 15:00",
            ["events-pipeline"],
            ["kafka", "msk"],
            [
                ("2026-08-12 11:00", "diego", "MSK cost is ~1.4x self-managed but removes broker ops; Kinesis would require rewriting 14 consumers."),
                ("2026-08-18 14:30", "lena", "Going with MSK, see ADR-0011."),
            ],
        ),
        Ticket(
            "PLAT-230",
            "Migrate events-pipeline to Amazon MSK",
            "Per ADR-0011. Mirror topics with MirrorMaker 2, cut over consumers one by one.",
            "In Progress",
            "lena",
            "tom",
            "2026-08-25 10:00",
            "",
            ["events-pipeline"],
            ["msk"],
            [("2026-09-15 12:00", "yuki", "Payments and dispatch topics mirrored, consumers cut over.")],
        ),
        Ticket(
            "PAY-118",
            "Idempotency for Stripe webhooks",
            "Store idempotency key per Stripe event in Redis (24h TTL) as agreed in #payments.",
            "Done",
            "arjun",
            "sara",
            "2026-01-16 09:00",
            "2026-01-29 18:00",
            ["payments-service"],
            ["stripe"],
            [],
        ),
        Ticket(
            "PAY-341",
            "Customer charged twice after a dispute webhook",
            "Duplicate charge on order 88412. The dispute webhook was resent by Stripe ~3 days after the first delivery; our idempotency key (24h TTL) had expired.",
            "Done",
            "arjun",
            "hannah",
            "2026-05-06 09:30",
            "2026-05-14 16:00",
            ["payments-service", "ledger-db"],
            ["bug", "stripe", "incident"],
            [
                ("2026-05-06 11:00", "arjun", "Stripe resends dispute events for up to 72 hours, so a 24h TTL cannot cover disputes."),
                ("2026-05-14 15:40", "arjun", "Dispute keys now kept 7 days; unique constraint on (stripe_event_id) added in ledger-db."),
            ],
        ),
        Ticket(
            "RTE-77",
            "Replace greedy routing heuristic with OR-Tools",
            "Per RFC-0007: model hard time windows.",
            "Done",
            "marcus",
            "grace",
            "2026-02-12 10:00",
            "2026-03-27 17:00",
            ["route-optimizer"],
            ["or-tools"],
            [("2026-03-27 16:00", "marcus", "Rolled out to all depots. Time-window violations down from 18% to 0.6%.")],
        ),
        Ticket(
            "RTE-140",
            "Optimizer times out on 1,200-stop Saturday batch",
            "Solver exceeds the 60s budget for the largest depot on Saturdays.",
            "In Progress",
            "pavel",
            "aisha",
            "2026-08-19 12:00",
            "",
            ["route-optimizer"],
            ["performance"],
            [("2026-08-21 10:00", "marcus", "Pairing with Pavel; first-solution strategy PATH_CHEAPEST_ARC plus guided local search gets it to 41s.")],
        ),
        Ticket(
            "MOB-12", "Driver app: React Native skeleton", "Per ADR-0005.", "Done", "jordan", "elena", "2025-12-16 10:00", "2026-01-20 17:00", ["driver-app"], [], []
        ),
        Ticket(
            "MOB-203",
            "ANRs on low-end Android in map screen",
            "ANR rate 4.1% on 2 GB RAM devices; map screen renders 150+ markers at ~15 fps.",
            "Done",
            "chris",
            "elena",
            "2026-04-20 09:00",
            "2026-05-22 15:00",
            ["driver-app"],
            ["performance", "android"],
            [("2026-05-22 14:00", "jordan", "Native map module shipped (ADR-0012); ANR rate down to 0.3%.")],
        ),
        Ticket(
            "PLAT-150",
            "JWT access + refresh tokens for drivers",
            "Per ADR-0006: 15 min access tokens, 8h refresh tokens.",
            "Done",
            "lena",
            "tom",
            "2026-01-22 10:00",
            "2026-02-20 17:00",
            ["auth-service", "driver-app"],
            ["security"],
            [],
        ),
        Ticket(
            "PLAT-188",
            "Refresh token rotation",
            "Security review: rotate refresh tokens on use and detect reuse.",
            "Done",
            "diego",
            "rachel",
            "2026-06-08 10:00",
            "2026-06-30 17:00",
            ["auth-service"],
            ["security"],
            [],
        ),
        Ticket(
            "NOT-31",
            "Switch EU SMS to MessageBird",
            "~40% cheaper per SMS in the EU.",
            "Done",
            "kenji",
            "david",
            "2026-03-04 10:00",
            "2026-03-25 17:00",
            ["notifications-service"],
            ["cost"],
            [],
        ),
        Ticket(
            "NOT-44",
            "Brazil SMS delivery rate on MessageBird",
            "Pilot shows 81% delivery vs 97% on Twilio. Keep Twilio for Brazil.",
            "Done",
            "diego",
            "tom",
            "2026-06-09 10:00",
            "2026-06-12 12:00",
            ["notifications-service"],
            [],
            [],
        ),
        Ticket(
            "PLAT-170",
            "Build in-house feature flag service",
            "Per RFC-0009: boolean flags, percentage rollouts, per-tenant overrides.",
            "Done",
            "diego",
            "lena",
            "2026-03-02 10:00",
            "2026-04-10 17:00",
            ["feature-flags"],
            [],
            [],
        ),
    ]


_BG_TICKETS = [
    ("Upgrade {sys} base image to the latest LTS", "Routine dependency hygiene.", ["chore"]),
    ("Add p99 latency alert for {sys}", "We only alert on error rate today.", ["observability"]),
    ("Flaky integration test in {sys}", "Fails ~1 in 20 CI runs, probably a race in test setup.", ["tests"]),
    ("{sys}: reduce log volume", "Debug logs left on in production are costing us.", ["cost"]),
    ("Document local setup for {sys}", "New joiners keep asking how to run it locally.", ["docs"]),
    ("{sys}: handle timeouts from upstream gracefully", "Currently surfaces a 500 to the caller.", ["bug"]),
    ("Rotate credentials used by {sys}", "Quarterly rotation.", ["security"]),
]
_PREFIX = {"platform": "PLAT", "payments": "PAY", "routing": "RTE", "mobile": "MOB", "data": "DATA", "sre": "SRE"}


def _employed(handle: str, when: datetime) -> bool:
    _, _, _, _, _, joined, left = next(p for p in PEOPLE if p[0] == handle)
    return _d(joined) <= when and (not left or when <= _d(left))


def background_tickets(rng: random.Random) -> list[Ticket]:
    team_members: dict[str, list[str]] = defaultdict(list)
    for h, _, team, _, _, _, left in PEOPLE:
        if team != "product":
            team_members[team].append(h)
    owners = {s[0]: s[3] for s in SYSTEMS}
    tickets = []
    counters = dict.fromkeys(_PREFIX, 400)
    for _ in range(110):
        sys = rng.choice(list(owners))
        team = owners[sys]
        prefix = _PREFIX.get(team, "OPS")
        counters[team] = counters.get(team, 400) + 1
        title, desc, labels = rng.choice(_BG_TICKETS)
        created = START + timedelta(days=rng.randint(0, (END - START).days - 20), hours=rng.randint(8, 17))
        done = rng.random() < 0.7
        # Only people who were at the company when the ticket was filed can file or own it.
        active = [h for h in team_members[team] if _employed(h, created)] or team_members[team]
        assignee = rng.choice(active)
        tickets.append(
            Ticket(
                f"{prefix}-{counters[team]}",
                title.format(sys=sys),
                desc,
                "Done" if done else rng.choice(["To Do", "In Progress"]),
                assignee,
                rng.choice(active),
                created.strftime("%Y-%m-%d %H:%M"),
                (created + timedelta(days=rng.randint(1, 15))).strftime("%Y-%m-%d %H:%M") if done else "",
                [sys],
                labels,
                [],
            )
        )
    return tickets


# --------------------------------------------------------------------------- docs

DOCS: dict[str, str] = {
    "adr/0003-kafka-for-events-pipeline.md": """---
type: adr
id: ADR-0003
title: "ADR-0003: Use Kafka for the events pipeline"
date: 2025-12-04
status: superseded
authors: [priya, tom]
systems: [events-pipeline]
---
## Context
- We need to replay at least 7 days of events to rebuild projections after a bug.
- Priya and Lena have operated Kafka in production before, so we can run it ourselves.
- Expected peak is about 3k events/s, well within a 3-broker cluster.

## Decision
We will run a self-managed 3-broker Kafka cluster as the company event bus behind events-pipeline.

## Alternatives considered
- **Amazon SQS**: no replay; consumers can't re-read history after a bug.
- **Amazon Kinesis**: 7-day retention costs more at our volume and nobody on the team has run it.

## Consequences
Platform owns broker operations; SRE needs a runbook before payments events move onto the bus.
""",
    "adr/0005-react-native-driver-app.md": """---
type: adr
id: ADR-0005
title: "ADR-0005: Build the driver app in React Native"
date: 2025-12-15
status: accepted
authors: [jordan, elena]
systems: [driver-app]
---
## Context
- One mobile team of four can't maintain separate iOS and Android codebases.
- Performance is acceptable on our target devices, including 2 GB RAM Android phones.
- Drivers need the same features on both platforms on day one.

## Decision
We will build the driver app in React Native with a shared codebase for iOS and Android.

## Alternatives considered
- **Native iOS and Android**: doubles the work for a four-person team.
- **Flutter**: nobody on the team has shipped a Flutter app.
""",
    "adr/0006-jwt-for-driver-auth.md": """---
type: adr
id: ADR-0006
title: "ADR-0006: JWT access and refresh tokens for driver auth"
date: 2026-01-20
status: accepted
authors: [lena]
systems: [auth-service, driver-app]
---
## Context
- Drivers lose connectivity for long stretches and must stay logged in for a full shift.
- Session cookies require a round trip to auth-service on every request.

## Decision
We will issue 15-minute JWT access tokens and 8-hour refresh tokens so a shift survives offline.

## Alternatives considered
- **Server-side sessions**: don't work offline.
- **Long-lived access tokens**: a stolen token would be valid for days.
""",
    "rfc/0007-or-tools-route-optimizer.md": """---
type: rfc
id: RFC-0007
title: "RFC-0007: Replace the greedy heuristic with OR-Tools"
date: 2026-02-10
status: accepted
authors: [marcus]
systems: [route-optimizer]
---
## Motivation
- About 40% of deliveries have hard time windows that the greedy heuristic ignores.
- 18% of routes currently violate at least one time window.
- OR-Tools solved our 500-stop benchmark in 4.2 seconds against a 30 second budget.

## Decision
We will rebuild route-optimizer on Google OR-Tools with time-window constraints.

## Alternatives considered
- **Keep the greedy heuristic**: cannot model hard time windows.
- **Gurobi**: faster, but the license is about $60k per year and we don't need the extra speed yet.
""",
    "rfc/0009-feature-flags-build-vs-buy.md": """---
type: rfc
id: RFC-0009
title: "RFC-0009: Build a simple in-house feature flag service"
date: 2026-02-24
status: accepted
authors: [lena]
systems: [feature-flags]
---
## Motivation
- We only need boolean flags, percentage rollouts and per-tenant overrides.
- LaunchDarkly quoted $48k per year for our seat count.

## Decision
We will build a small in-house feature-flags service and revisit buying if we need experimentation.

## Alternatives considered
- **LaunchDarkly**: full-featured but $48k per year.
- **Unleash (self-hosted)**: another service to operate for features we don't need.
""",
    "adr/0011-move-events-pipeline-to-msk.md": """---
type: adr
id: ADR-0011
title: "ADR-0011: Move the events pipeline to Amazon MSK"
date: 2026-08-20
status: accepted
authors: [lena, tom]
systems: [events-pipeline]
supersedes: ADR-0003
---
## Context
- Since Priya left in June, nobody on the team has deep Kafka operations experience.
- The July 14 outage took about five hours to recover because broker recovery was unfamiliar.
- Peak traffic now reaches 11k events/s during promotions.

## Decision
We will move events-pipeline to Amazon MSK (managed Kafka) and keep the Kafka protocol so consumers don't change.

## Alternatives considered
- **Amazon Kinesis**: would require rewriting all 14 consumers.
- **Keep self-managed Kafka and hire**: hiring takes months and the risk is now.
""",
    "adr/0012-native-map-module.md": """---
type: adr
id: ADR-0012
title: "ADR-0012: Render the driver map in a native module"
date: 2026-05-08
status: accepted
authors: [jordan, elena]
systems: [driver-app]
---
## Context
- The React Native map screen drops to about 15 fps with 150+ stop markers on 2 GB Android devices.
- The ANR rate on those devices reached 4.1%.

## Decision
We will render the map in a native module on Android and iOS and keep the rest of the app in React Native.

## Alternatives considered
- **Rewrite the whole app natively**: too much work for a four-person team.
- **Cluster markers in JavaScript**: helped, but not enough on the slowest devices.
""",
    "postmortems/2026-07-14-events-pipeline-outage.md": """---
type: postmortem
id: PM-2026-07-14
title: "Postmortem: events pipeline outage on 2026-07-14"
date: 2026-07-15
authors: [rachel, lena]
systems: [events-pipeline, payments-service, dispatch-api]
challenges: [ADR-0003]
---
## Summary
Two Kafka brokers dropped out of ISR during the summer promotion. Consumer lag delayed payments and dispatch events for about five hours.

## Contradicted assumptions
- After Priya left in June, nobody on call had operated Kafka in production, so recovery took five hours instead of minutes.
- Peak traffic reached 11k events/s during the promotion, more than three times the 3k events/s the cluster was sized for.

## Action items
- Evaluate managed Kafka (PLAT-212).
- Pair every on-call engineer on a broker recovery drill.
""",
    "postmortems/2026-04-22-driver-app-anrs.md": """---
type: postmortem
id: PM-2026-04-22
title: "Postmortem: driver app ANRs on low-end Android"
date: 2026-04-22
authors: [elena, chris]
systems: [driver-app]
challenges: [ADR-0005]
---
## Summary
The driver app's ANR rate reached 4.1%, almost entirely on 2 GB RAM Android devices.

## What we got wrong
- Performance was not acceptable on 2 GB RAM Android devices: the map screen dropped to about 15 fps with real routes of 150+ stops.
- The prototype we benchmarked only rendered 20 markers.

## Action items
- Move map rendering to a native module (ADR-0012).
""",
    "runbooks/events-pipeline.md": """---
type: runbook
title: "Runbook: events-pipeline"
date: 2025-12-11
authors: [priya]
systems: [events-pipeline]
---
## Consumer lag alert
1. Check broker health in the Kafka dashboard.
2. If a broker is out of ISR, check disk and network first.
3. Restart the broker only after confirming the controller is healthy.
6. Re-elect partition leaders for the affected topics.
""",
    "runbooks/route-optimizer.md": """---
type: runbook
title: "Runbook: route-optimizer"
date: 2026-04-02
authors: [marcus]
systems: [route-optimizer]
---
## Solver timeouts
The solver budget is 60 seconds per depot. If it times out, the dispatcher falls back to yesterday's routes.
Solver parameters live in config/solver.yaml; ask Marcus before changing them.
""",
    "meetings/2026-03-11-architecture-review.md": """---
type: meeting
title: "Architecture review 2026-03-11"
date: 2026-03-11
authors: [tom]
systems: [warehouse-db, analytics-warehouse]
---
## Attendees
Tom, Maya, Noah, Ines, Rachel

## Discussion
- The orders table in warehouse-db has 400M rows and vacuum takes hours.
- Analytics queries mostly filter by month.

## Decisions
- Partition the orders table in warehouse-db by month, because almost every query filters by order month and vacuum on the single table takes hours. Owner: Noah.
""",
    "meetings/2026-07-22-platform-sync.md": """---
type: meeting
title: "Platform sync 2026-07-22"
date: 2026-07-22
authors: [tom]
systems: [events-pipeline, auth-service]
---
## Discussion
- MSK evaluation in progress (PLAT-212). Kinesis would mean rewriting 14 consumers.
- Yuki is shadowing Lena on events-pipeline on-call.

## Decisions
- Every platform on-call engineer completes a broker recovery drill before the MSK cutover, because the July outage showed only one person knew the procedure.
""",
    "onboarding/platform.md": """---
type: doc
title: "Platform team onboarding"
date: 2026-07-01
authors: [lena]
systems: [events-pipeline, auth-service, feature-flags, notifications-service]
---
Welcome to Platform. We own events-pipeline, auth-service, feature-flags and notifications-service.
Start with the runbooks and ADR-0003, ADR-0006 and RFC-0009 to understand why things are the way they are.
""",
}


# --------------------------------------------------------------------------- writers


def _slack_ts(dt: datetime, seq: int = 0) -> str:
    return f"{int(dt.timestamp())}.{seq:06d}"


def write(out: Path, seed: int = 7) -> dict[str, int]:
    rng = random.Random(seed)
    out.mkdir(parents=True, exist_ok=True)
    # The generator owns these folders: clear them so files from an earlier run can't linger.
    for owned in ("slack", "docs"):
        if (out / owned).exists():
            shutil.rmtree(out / owned)
    (out / "workspace.toml").write_text(
        f'name = "{COMPANY}"\ndescription = "Fictional logistics company. Sample data in Slack, Jira and markdown export formats."\n', encoding="utf-8"
    )
    with (out / "teams.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["key", "name", "description"])
        w.writerows(TEAMS)
    user_ids = {p[0]: f"U{1000 + i:05d}" for i, p in enumerate(PEOPLE)}
    with (out / "people.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["handle", "name", "email", "team", "role", "manager", "slack_id", "jira_name", "github", "joined", "left"])
        for h, name, team, role, mgr, joined, left in PEOPLE:
            w.writerow([h, name, f"{h}@{DOMAIN}", team, role, mgr, user_ids[h], name, "", joined, left])
    with (out / "systems.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["key", "name", "kind", "team", "depends_on", "aliases", "description"])
        w.writerows(SYSTEMS)

    # Slack export.
    slack = out / "slack"
    slack.mkdir(exist_ok=True)
    names = {p[0]: p[1] for p in PEOPLE}
    (slack / "users.json").write_text(
        json.dumps([{"id": user_ids[h], "name": h, "real_name": names[h], "profile": {"email": f"{h}@{DOMAIN}", "real_name": names[h]}} for h in names], indent=1),
        encoding="utf-8",
    )
    messages = story_threads() + background(rng)
    channels = sorted({m.channel for m in messages} | set(CHANNELS))
    (slack / "channels.json").write_text(
        json.dumps([{"id": f"C{i:05d}", "name": c, "purpose": {"value": CHANNELS.get(c, "")}} for i, c in enumerate(channels)], indent=1), encoding="utf-8"
    )
    by_day: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    seq: dict[str, int] = defaultdict(int)
    root_ts: dict[tuple[str, datetime], str] = {}
    # Roots sort before their replies, so each reply can point at the exact ts its root was given.
    for msg in sorted(messages, key=lambda m: (m.when, m.thread is not None)):
        key = _slack_ts(msg.when)
        seq[key] += 1
        ts = _slack_ts(msg.when, seq[key])
        entry: dict[str, Any] = {"type": "message", "user": user_ids[msg.author], "text": msg.text, "ts": ts}
        if msg.thread is None:
            root_ts[(msg.channel, msg.when)] = ts
        else:
            entry["thread_ts"] = root_ts.get((msg.channel, msg.thread), _slack_ts(msg.thread, 1))
        by_day[(msg.channel, msg.when.strftime("%Y-%m-%d"))].append(entry)
    for (channel, day), entries in by_day.items():
        (slack / channel).mkdir(exist_ok=True)
        (slack / channel / f"{day}.json").write_text(json.dumps(entries, indent=1, ensure_ascii=False), encoding="utf-8")

    # Jira CSV export (repeated Comment columns, "date;author;text").
    tickets = story_tickets() + background_tickets(rng)
    max_comments = max(len(t.comments) for t in tickets)
    with (out / "tickets.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(
            ["Issue key", "Summary", "Description", "Status", "Assignee", "Reporter", "Created", "Resolved", "Component/s", "Labels", *["Comment"] * max_comments]
        )
        for t in tickets:
            comments = [f"{when};{names[who]};{text}" for when, who, text in t.comments]
            w.writerow(
                [
                    t.key,
                    t.summary,
                    t.description,
                    t.status,
                    names[t.assignee],
                    names[t.reporter],
                    t.created,
                    t.resolved,
                    ";".join(t.components),
                    ";".join(t.labels),
                    *comments,
                    *[""] * (max_comments - len(comments)),
                ]
            )

    for rel, text in DOCS.items():
        path = out / "docs" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    threads = len({(m.channel, m.thread or m.when) for m in messages})
    return {"people": len(PEOPLE), "systems": len(SYSTEMS), "slack_messages": len(messages), "slack_threads": threads, "tickets": len(tickets), "docs": len(DOCS)}
