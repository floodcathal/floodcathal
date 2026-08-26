#!/usr/bin/env python3
"""Render GitHub stat cards as static SVGs.

The public github-readme-stats instance is shared by millions of profiles and
regularly returns 503 once its API quota is gone. Because GitHub proxies README
images through Camo, the owner keeps seeing a cached copy while everyone else
gets a broken image.

This queries the GraphQL API directly and writes the cards into the repository,
so they are served by GitHub itself and cannot be rate-limited by a third party.

Usage:
    GITHUB_TOKEN=... python scripts/generate_stats.py --user floodcathal
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

API = "https://api.github.com/graphql"

QUERY = """
query($login: String!) {
  user(login: $login) {
    name
    followers { totalCount }
    contributionsCollection {
      totalCommitContributions
    }
    pullRequests(first: 1) { totalCount }
    issues(first: 1) { totalCount }
    repositories(first: 100, ownerAffiliations: OWNER, isFork: false, privacy: PUBLIC) {
      totalCount
      nodes {
        stargazerCount
        languages(first: 10, orderBy: {field: SIZE, direction: DESC}) {
          edges { size node { name color } }
        }
      }
    }
  }
}
"""

# Palettes chosen so each card stays legible in whichever theme GitHub renders.
THEMES = {
    "light": {
        "bg": "#ffffff", "border": "#d0d7de", "title": "#0969da",
        "label": "#1f2328", "value": "#57606a", "icon": "#57606a",
    },
    "dark": {
        "bg": "#0d1117", "border": "#30363d", "title": "#58a6ff",
        "label": "#e6edf3", "value": "#8b949e", "icon": "#8b949e",
    },
}


def fetch(login, token):
    body = json.dumps({"query": QUERY, "variables": {"login": login}}).encode()
    req = urllib.request.Request(
        API,
        data=body,
        headers={
            "Authorization": "bearer " + token,
            "Content-Type": "application/json",
            "User-Agent": "profile-stats-generator",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.load(resp)
    if "errors" in payload:
        raise SystemExit("GraphQL error: {}".format(payload["errors"]))
    return payload["data"]["user"]


def summarise(user):
    repos = user["repositories"]["nodes"]
    stars = sum(r["stargazerCount"] for r in repos)

    # Public commit contributions only - see the note in the GraphQL query.
    commits = user["contributionsCollection"]["totalCommitContributions"]

    langs = {}
    for repo in repos:
        for edge in repo["languages"]["edges"]:
            name = edge["node"]["name"]
            if name not in langs:
                langs[name] = {"size": 0, "color": edge["node"]["color"] or "#858585"}
            langs[name]["size"] += edge["size"]

    total = sum(v["size"] for v in langs.values()) or 1
    ranked = sorted(langs.items(), key=lambda kv: kv[1]["size"], reverse=True)

    return {
        "commits": commits,
        "stars": stars,
        "prs": user["pullRequests"]["totalCount"],
        "issues": user["issues"]["totalCount"],
        "repos": user["repositories"]["totalCount"],
        "followers": user["followers"]["totalCount"],
        "languages": [
            (name, meta["color"], 100.0 * meta["size"] / total) for name, meta in ranked
        ],
    }


def esc(text):
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


FONT = (
    "-apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif"
)


def frame(width, height, theme, title, body):
    c = THEMES[theme]
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" \
viewBox="0 0 {width} {height}" role="img" aria-label="{esc(title)}">
  <style>
    .t {{ font: 600 15px {FONT}; fill: {c['title']}; }}
    .l {{ font: 400 13px {FONT}; fill: {c['label']}; }}
    .v {{ font: 600 13px {FONT}; fill: {c['value']}; }}
  </style>
  <rect x="0.5" y="0.5" width="{width - 1}" height="{height - 1}" rx="8"
        fill="{c['bg']}" stroke="{c['border']}"/>
  <text x="24" y="34" class="t">{esc(title)}</text>
{body}
</svg>
"""


def overview_card(stats, user, theme):
    # Commits and repo count always show. The rest only appear once they are
    # non-zero - a card advertising "Stars earned: 0" is worse than no card.
    rows = [
        ("Total commits", stats["commits"], True),
        ("Public repositories", stats["repos"], True),
        ("Pull requests", stats["prs"], False),
        ("Issues opened", stats["issues"], False),
        ("Stars earned", stats["stars"], False),
        ("Followers", stats["followers"], False),
    ]
    rows = [(label, f"{n:,}") for label, n, always in rows if always or n > 0]

    body = []
    y = 68
    for label, value in rows:
        body.append(f'  <text x="24" y="{y}" class="l">{esc(label)}</text>')
        body.append(f'  <text x="376" y="{y}" class="v" text-anchor="end">{esc(value)}</text>')
        y += 26
    title = f"{user['name'] or 'GitHub'} — overview"
    return frame(400, y - 4, theme, title, "\n".join(body))


def languages_card(stats, theme, top=8):
    c = THEMES[theme]
    langs = stats["languages"][:top]
    body = []

    # Stacked proportion bar
    x = 24.0
    bar_w = 352.0
    shown = sum(p for _, _, p in langs) or 1
    body.append('  <g>')
    for _, color, pct in langs:
        w = bar_w * (pct / shown)
        body.append(
            f'    <rect x="{x:.2f}" y="52" width="{max(w, 0.5):.2f}" height="9" fill="{color}"/>'
        )
        x += w
    body.append('  </g>')
    body.append(f'  <rect x="24" y="52" width="{bar_w}" height="9" rx="4.5" fill="none" stroke="{c["border"]}"/>')

    # Two-column legend
    y = 88
    for i, (name, color, pct) in enumerate(langs):
        col_x = 24 if i % 2 == 0 else 208
        if i % 2 == 0 and i:
            y += 24
        body.append(f'  <circle cx="{col_x + 5}" cy="{y - 4}" r="5" fill="{color}"/>')
        body.append(f'  <text x="{col_x + 18}" y="{y}" class="l">{esc(name)}</text>')
        body.append(
            f'  <text x="{col_x + 160}" y="{y}" class="v" text-anchor="end">{pct:.1f}%</text>'
        )
    return frame(400, y + 20, theme, "Most used languages", "\n".join(body))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", required=True)
    ap.add_argument("--out", default="stats")
    args = ap.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        sys.exit("GITHUB_TOKEN is not set")

    user = fetch(args.user, token)
    stats = summarise(user)

    os.makedirs(args.out, exist_ok=True)
    written = []
    for theme in ("light", "dark"):
        for name, svg in (
            ("overview", overview_card(stats, user, theme)),
            ("languages", languages_card(stats, theme)),
        ):
            path = os.path.join(args.out, f"{name}-{theme}.svg")
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(svg)
            written.append(path)

    print("Wrote:")
    for p in written:
        print("  " + p)
    print(
        "commits={commits} repos={repos} prs={prs} issues={issues} "
        "stars={stars} followers={followers}".format(**stats)
    )
    print("languages: " + ", ".join(f"{n} {p:.1f}%" for n, _, p in stats["languages"][:8]))


if __name__ == "__main__":
    main()
