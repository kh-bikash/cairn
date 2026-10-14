"""GraphQL documents for the GitHub v4 API.

Page sizes are chosen to stay well under GitHub's node limit per query
(first * nested first must stay < 500k) while keeping the number of round trips low.
"""

_ACTOR = "author { __typename login ... on User { name } }"

PULL_REQUESTS = f"""
query($owner: String!, $name: String!, $cursor: String) {{
  rateLimit {{ cost remaining resetAt }}
  repository(owner: $owner, name: $name) {{
    pullRequests(first: 20, after: $cursor, orderBy: {{field: CREATED_AT, direction: DESC}}) {{
      pageInfo {{ hasNextPage endCursor }}
      nodes {{
        number title body url state createdAt mergedAt closedAt
        {_ACTOR}
        mergedBy {{ __typename login }}
        labels(first: 10) {{ nodes {{ name }} }}
        files(first: 100) {{ nodes {{ path additions deletions }} }}
        closingIssuesReferences(first: 10) {{ nodes {{ number }} }}
        reviews(first: 30) {{ nodes {{ {_ACTOR} state body submittedAt url }} }}
        comments(first: 50) {{ nodes {{ {_ACTOR} body createdAt url }} }}
      }}
    }}
  }}
}}
"""

ISSUES = f"""
query($owner: String!, $name: String!, $cursor: String) {{
  rateLimit {{ cost remaining resetAt }}
  repository(owner: $owner, name: $name) {{
    issues(first: 30, after: $cursor, orderBy: {{field: CREATED_AT, direction: DESC}}) {{
      pageInfo {{ hasNextPage endCursor }}
      nodes {{
        number title body url state createdAt closedAt
        {_ACTOR}
        labels(first: 10) {{ nodes {{ name }} }}
        assignees(first: 5) {{ nodes {{ __typename login }} }}
        comments(first: 50) {{ nodes {{ {_ACTOR} body createdAt url }} }}
      }}
    }}
  }}
}}
"""

DISCUSSIONS = f"""
query($owner: String!, $name: String!, $cursor: String) {{
  rateLimit {{ cost remaining resetAt }}
  repository(owner: $owner, name: $name) {{
    discussions(first: 25, after: $cursor, orderBy: {{field: CREATED_AT, direction: DESC}}) {{
      pageInfo {{ hasNextPage endCursor }}
      nodes {{
        number title body url createdAt closed closedAt
        {_ACTOR}
        category {{ name }}
        comments(first: 40) {{ nodes {{ {_ACTOR} body createdAt url }} }}
      }}
    }}
  }}
}}
"""

REPO_META = """
query($owner: String!, $name: String!) {
  rateLimit { cost remaining resetAt }
  repository(owner: $owner, name: $name) {
    name nameWithOwner description url stargazerCount createdAt pushedAt
    primaryLanguage { name }
    defaultBranchRef { name }
    codeowners1: object(expression: "HEAD:.github/CODEOWNERS") { ... on Blob { text } }
    codeowners2: object(expression: "HEAD:CODEOWNERS") { ... on Blob { text } }
    codeowners3: object(expression: "HEAD:docs/CODEOWNERS") { ... on Blob { text } }
  }
}
"""
