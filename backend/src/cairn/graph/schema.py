"""Graph schema: node labels, relationship types and the indexes Cairn relies on.

All timestamps are stored as epoch milliseconds (`ts`) so range filters stay index-friendly.

Nodes
    Person   {handle, name, is_bot}
    System   {key, name, kind: repo|module, repo, path}
    Source   {uid, kind: pr|issue|discussion|comment|review|doc, repo, number, title, body, url, ts, state, author}
    Decision {uid, title, summary, category, ts, repo, status: active|superseded, confidence}
    Premise  {uid, text, ts}
    Action   {uid, kind, status: pending|approved|rejected|done|failed, payload, ts}
    Answer   {uid, question, text, ts}

Relationships
    (Person)-[:AUTHORED]->(Source)
    (Person)-[:REVIEWED {state, ts}]->(Source:pr)
    (Person)-[:MERGED {ts}]->(Source:pr)
    (Source:comment|review)-[:REPLY_TO]->(Source)
    (Source)-[:IN_REPO]->(System:repo)
    (Source:pr)-[:TOUCHES {additions, deletions}]->(System:module)
    (Source:pr)-[:CLOSES]->(Source:issue)
    (Source)-[:REFERENCES]->(Source)
    (System:module)-[:PART_OF]->(System:repo)
    (System:repo)-[:DEPENDS_ON {via}]->(System:repo)
    (Person)-[:OWNS {via}]->(System)
    (Person)-[:KNOWS_ABOUT {score, authored, reviewed, last_ts}]->(System)
    (Decision)-[:DECIDED_IN]->(Source)
    (Person)-[:DECIDED]->(Decision)
    (Decision)-[:BECAUSE]->(Premise)-[:EVIDENCED_BY {quote}]->(Source)
    (Decision)-[:REJECTED {option, reason}]->(Premise)
    (Decision)-[:AFFECTS]->(System)
    (Decision)-[:SUPERSEDES]->(Decision)
    (Source)-[:CHALLENGES {quote, verdict, confidence}]->(Premise)
    (Answer)-[:USED]->(any)
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RangeIndex:
    label: str
    prop: str

    def cypher(self) -> str:
        return f"CREATE INDEX FOR (n:{self.label}) ON (n.{self.prop})"


@dataclass(frozen=True)
class FulltextIndex:
    label: str
    props: tuple[str, ...]

    def cypher(self) -> str:
        fields = ", ".join(f"n.{p}" for p in self.props)
        return f"CREATE FULLTEXT INDEX FOR (n:{self.label}) ON ({fields})"


RANGE_INDEXES: tuple[RangeIndex, ...] = (
    RangeIndex("Person", "handle"),
    RangeIndex("System", "key"),
    RangeIndex("System", "repo"),
    RangeIndex("Source", "uid"),
    RangeIndex("Source", "ts"),
    RangeIndex("Source", "repo"),
    RangeIndex("Decision", "uid"),
    RangeIndex("Decision", "ts"),
    RangeIndex("Premise", "uid"),
    RangeIndex("Action", "uid"),
    RangeIndex("Answer", "uid"),
)

FULLTEXT_INDEXES: tuple[FulltextIndex, ...] = (
    FulltextIndex("Source", ("title", "body")),
    FulltextIndex("Decision", ("title", "summary")),
    FulltextIndex("Premise", ("text",)),
    FulltextIndex("System", ("name", "path")),
    FulltextIndex("Person", ("handle", "name")),
)

# Properties that identify each label; used to build stable UI ids and MERGE keys.
KEY_PROPS: dict[str, str] = {
    "Person": "handle",
    "System": "key",
    "Source": "uid",
    "Decision": "uid",
    "Premise": "uid",
    "Action": "uid",
    "Answer": "uid",
}
