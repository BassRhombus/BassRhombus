#!/usr/bin/env python3
"""
Generates the self-hosted, NLC-themed profile cards into assets/:

  hero.svg       animated header + terminal that types out live stats
  stats.svg      stars / commits / PRs / issues / contributed-to + rank ring
  streak.svg     total contributions, current and longest streak
  activity.svg   last 31 days of contributions as an animated area chart
  trophies.svg   tiered trophy row
  footer.svg     closing banner

Replaces the third-party Vercel/Heroku card services, which rate-limit and break.

Environment variables:
  METRICS_TOKEN / GITHUB_TOKEN  - GitHub token (required; a PAT includes private contributions)
  GITHUB_USER                   - username (default: BassRhombus)
  OUT_DIR                       - output folder (default: assets)
  STATS_FILE                    - code stats cache from generate_stats.py (default: stats.json)
"""

from __future__ import annotations

import json
import math
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import nlc  # noqa: E402
from nlc import esc, fmt  # noqa: E402

TOKEN = os.environ.get("METRICS_TOKEN") or os.environ.get("GITHUB_TOKEN")
USERNAME = os.environ.get("GITHUB_USER", "BassRhombus")
OUT_DIR = Path(os.environ.get("OUT_DIR", "assets"))
STATS_FILE = Path(os.environ.get("STATS_FILE", "stats.json"))
TAGLINE = "Building bots, games, and infrastructure — one commit at a time."


# --------------------------------------------------------------------------- data

def graphql(query: str, variables: dict) -> dict:
    resp = requests.post(
        "https://api.github.com/graphql",
        json={"query": query, "variables": variables},
        headers={"Authorization": f"bearer {TOKEN}"},
        timeout=30,
    )
    resp.raise_for_status()
    payload = resp.json()
    if payload.get("errors"):
        raise RuntimeError(payload["errors"])
    return payload["data"]


PROFILE_QUERY = """
query($login: String!) {
  user(login: $login) {
    login
    createdAt
    followers { totalCount }
    pullRequests { totalCount }
    issues { totalCount }
    repositoriesContributedTo(contributionTypes: [COMMIT, PULL_REQUEST, ISSUE, REPOSITORY]) { totalCount }
    repositories(ownerAffiliations: OWNER, isFork: false, first: 100, orderBy: {field: PUSHED_AT, direction: DESC}) {
      totalCount
      nodes { name description isPrivate stargazerCount pushedAt primaryLanguage { name } }
    }
  }
}
"""


def fetch_profile() -> dict:
    user = graphql(PROFILE_QUERY, {"login": USERNAME})["user"]

    created = datetime.fromisoformat(user["createdAt"].replace("Z", "+00:00"))
    now = datetime.now(timezone.utc)
    # One contributionsCollection per calendar year (the API caps a window at 1 year).
    parts = []
    for year in range(created.year, now.year + 1):
        start = max(created, datetime(year, 1, 1, tzinfo=timezone.utc))
        end = min(now, datetime(year, 12, 31, 23, 59, 59, tzinfo=timezone.utc))
        parts.append(
            f'y{year}: contributionsCollection(from: "{start.isoformat()}", to: "{end.isoformat()}") {{'
            " totalCommitContributions restrictedContributionsCount"
            " contributionCalendar { totalContributions weeks { contributionDays { date contributionCount } } } }"
        )
    years = graphql(
        "query($login: String!) { user(login: $login) { " + " ".join(parts) + " } }",
        {"login": USERNAME},
    )["user"]

    days: dict[str, int] = {}
    total_commits = 0
    for key in sorted(years):
        col = years[key]
        total_commits += col["totalCommitContributions"]
        for week in col["contributionCalendar"]["weeks"]:
            for d in week["contributionDays"]:
                days[d["date"]] = d["contributionCount"]

    this_year = years[f"y{now.year}"]
    repos = user["repositories"]["nodes"]
    return {
        "followers": user["followers"]["totalCount"],
        "prs": user["pullRequests"]["totalCount"],
        "issues": user["issues"]["totalCount"],
        "contributed_to": user["repositoriesContributedTo"]["totalCount"],
        "repo_count": user["repositories"]["totalCount"],
        "stars": sum(r["stargazerCount"] for r in repos),
        "public_repos": [r for r in repos if not r["isPrivate"] and r["name"].lower() != USERNAME.lower()],
        "commits_all_time": total_commits,
        "commits_this_year": this_year["totalCommitContributions"] + this_year["restrictedContributionsCount"],
        "contribs_this_year": this_year["contributionCalendar"]["totalContributions"],
        "days": dict(sorted(days.items())),
        "year": now.year,
    }


def load_code_stats() -> dict:
    try:
        data = json.loads(STATS_FILE.read_text(encoding="utf-8"))
        return {k: data.get(k, 0) for k in ("total_lines_added", "total_commits", "total_repos_analyzed")} | {
            "languages": data.get("languages", [])
        }
    except (OSError, ValueError):
        return {"total_lines_added": 0, "total_commits": 0, "total_repos_analyzed": 0, "languages": []}


def compute_streaks(days: dict[str, int]) -> dict:
    today = datetime.now(timezone.utc).date()
    ordered = [(date.fromisoformat(d), c) for d, c in days.items() if date.fromisoformat(d) <= today]

    longest = (0, None, None)
    run, run_start = 0, None
    for d, c in ordered:
        if c > 0:
            run_start = run_start if run else d
            run += 1
            if run > longest[0]:
                longest = (run, run_start, d)
        else:
            run = 0

    # Current streak survives an empty "today" (the day isn't over yet).
    lookup = dict(ordered)
    cursor = today if lookup.get(today, 0) > 0 else today - timedelta(days=1)
    current_end = cursor
    current = 0
    while lookup.get(cursor, 0) > 0:
        current += 1
        cursor -= timedelta(days=1)
    return {
        "total": sum(c for _, c in ordered),
        "first": ordered[0][0] if ordered else today,
        "current": current,
        "current_start": cursor + timedelta(days=1) if current else None,
        "current_end": current_end if current else None,
        "longest": longest[0],
        "longest_start": longest[1],
        "longest_end": longest[2],
    }


def compute_rank(p: dict) -> tuple[str, float]:
    """Same model as github-readme-stats: weighted CDFs against typical medians."""
    def exp_cdf(x: float) -> float:
        return 1 - 2 ** -x

    def log_cdf(x: float) -> float:
        return x / (1 + x)

    weights = {"commits": 2, "prs": 3, "issues": 1, "stars": 4, "followers": 1}
    score = (
        weights["commits"] * exp_cdf(p["commits_all_time"] / 1000)
        + weights["prs"] * exp_cdf(p["prs"] / 50)
        + weights["issues"] * exp_cdf(p["issues"] / 25)
        + weights["stars"] * log_cdf(p["stars"] / 50)
        + weights["followers"] * log_cdf(p["followers"] / 10)
    ) / sum(weights.values())
    percentile = (1 - score) * 100
    for threshold, level in zip(
        (1, 12.5, 25, 37.5, 50, 62.5, 75, 87.5, 100), ("S", "A+", "A", "A-", "B+", "B", "B-", "C+", "C")
    ):
        if percentile <= threshold:
            return level, percentile
    return "C", percentile


def short_date(d: date | None) -> str:
    return d.strftime("%b %-d" if os.name != "nt" else "%b %#d") if d else ""


# --------------------------------------------------------------------------- hero

def render_hero(p: dict, code: dict, streak: dict) -> str:
    w, h = 1200, 360
    css = f"""
  .title {{ font-family: {nlc.HEADING}; font-size: 64px; font-weight: 700; letter-spacing: -0.02em; }}
  .sub {{ font-size: 17px; fill: {nlc.TEXT_MUTED}; }}
  .stat-v {{ font-family: {nlc.HEADING}; font-size: 22px; font-weight: 700; fill: {nlc.TEXT}; }}
  .stat-l {{ font-size: 11px; fill: {nlc.TEXT_MUTED}; letter-spacing: 0.05em; }}
  .term {{ font-family: {nlc.MONO}; font-size: 14px; }}
  .prompt {{ fill: {nlc.SECONDARY_LIGHT}; }}
  .cmd {{ fill: {nlc.TEXT}; }}
  .out {{ fill: {nlc.TEXT_MUTED}; }}
  .hl {{ fill: {nlc.ACCENT_LIGHT}; }}
  .d1 {{ animation-delay: 0.1s; }} .d2 {{ animation-delay: 0.25s; }} .d3 {{ animation-delay: 0.4s; }}
  .d4 {{ animation-delay: 0.55s; }}
  .cursor {{ animation: blink 1s step-end infinite; }}
  @keyframes blink {{ 50% {{ opacity: 0; }} }}
  @media (prefers-reduced-motion: reduce) {{ .cover {{ display: none; }} }}
"""

    # ---- left column
    left = [
        nlc.chip(60, 58, "BOTS · GAME SERVERS · INFRASTRUCTURE")[0].replace("<g ", '<g class="fade-in" ', 1),
        '<defs><linearGradient id="tg" x1="0" x2="1" y1="0" y2="0">'
        f'<stop offset="0" stop-color="{nlc.PRIMARY_LIGHT}"/><stop offset="0.55" stop-color="{nlc.ACCENT_LIGHT}"/>'
        f'<stop offset="1" stop-color="{nlc.SECONDARY_LIGHT}"/></linearGradient></defs>',
        f'<g class="slide-up d1"><text x="56" y="150" class="title" fill="url(#tg)">{esc(USERNAME)}</text></g>',
        f'<g class="slide-up d2"><text x="60" y="186" class="sub">{esc(TAGLINE)}</text></g>',
    ]
    stats = [
        (fmt(code["total_lines_added"]), "LINES SHIPPED"),
        (fmt(p["commits_all_time"]), "COMMITS"),
        (str(p["repo_count"]), "REPOSITORIES"),
    ]
    x = 60
    for i, (value, label) in enumerate(stats):
        bw = 160
        left.append(
            f'<g class="slide-up d{min(i + 2, 4)}" transform="translate({x},222)">'
            f'<rect width="{bw}" height="76" rx="12" fill="{nlc.BG_PAPER}" fill-opacity="0.85" stroke="{nlc.DIVIDER}"/>'
            f'<rect x="16" y="0" width="28" height="3" rx="1.5" fill="{(nlc.PRIMARY_LIGHT, nlc.ACCENT_LIGHT, nlc.SECONDARY_LIGHT)[i]}"/>'
            f'<text x="16" y="40" class="stat-v">{esc(value)}</text>'
            f'<text x="16" y="60" class="stat-l">{esc(label)}</text></g>'
        )
        x += bw + 14

    # ---- terminal
    tx, ty, tw, th = 640, 40, 500, 280
    latest = p["public_repos"][0] if p["public_repos"] else None
    top_lang = next((l["name"] for l in code["languages"]), None) if code["languages"] else None
    if latest:
        desc = latest["description"] or (latest["primaryLanguage"] or {}).get("name") or "pushed recently"
        latest_line = f"{latest['name']} — {desc}"
    else:
        latest_line = "new things in the works..."
    stats_line = (f"{fmt(p['commits_all_time'])} commits · {p['repo_count']} repos · "
                  f"{fmt(p['stars'])} star{'s' if p['stars'] != 1 else ''}")
    if top_lang:
        stats_line += f" · {top_lang}"

    script = [
        ("cmd", "whoami"),
        ("out", f"{USERNAME} — bot & game-server developer"),
        ("cmd", "cat stats.txt"),
        ("out", stats_line),
        ("cmd", "git log --oneline -1"),
        ("hl", latest_line),
        ("cmd", "./build_something_awesome.sh"),
        ("out", ">> Compiling dreams into reality..."),
    ]
    max_chars = 52
    char_w = 7.8
    loop = 20.0
    t = 0.6
    lines, covers, frames = [], [], []
    y = ty + 70
    for i, (kind, text) in enumerate(script):
        text = text if len(text) <= max_chars else text[: max_chars - 1] + "…"
        x0 = tx + 24
        if kind == "cmd":
            lines.append(f'<text x="{x0}" y="{y}" class="term"><tspan class="prompt">$ </tspan><tspan class="cmd">{esc(text)}</tspan></text>')
            n = len(text) + 2
            dur = 0.07 * n
        else:
            lines.append(f'<text x="{x0}" y="{y}" class="term {"hl" if kind == "hl" else "out"}">{esc(text)}</text>')
            n, dur = 1, 0.01
            t += 0.25
        cover_w = n * char_w + 12 if kind == "cmd" else tw
        a, b = t / loop * 100, (t + dur) / loop * 100
        covers.append(
            f'<rect class="cover c{i}" x="{x0 - 2}" y="{y - 17}" width="{cover_w:.0f}" height="24" fill="{nlc.BG_PAPER}"/>'
        )
        frames.append(
            f"  .c{i} {{ animation: k{i} {loop}s infinite; }}\n"
            f"  @keyframes k{i} {{ 0%, {a:.2f}% {{ transform: translateX(0); animation-timing-function: steps({n}, end); }}"
            f" {b:.2f}%, 95% {{ transform: translateX({cover_w:.0f}px); animation-timing-function: step-end; }}"
            f" 100% {{ transform: translateX(0); }} }}"
        )
        t += dur + (0.5 if kind != "cmd" else 0.15)
        y += 23 if kind == "cmd" else 31

    # blinking cursor after the last prompt, revealed with the last output
    reveal = t / loop * 100
    frames.append(
        f"  .typed {{ animation: wipe {loop}s infinite; }}\n"
        f"  @keyframes wipe {{ 0%, 90% {{ opacity: 1; }} 94%, 100% {{ opacity: 0; }} }}\n"
        f"  .tail {{ animation: tail {loop}s infinite; }}\n"
        f"  @keyframes tail {{ 0%, {reveal:.2f}% {{ opacity: 0; }} {reveal + 0.1:.2f}%, 90% {{ opacity: 1; }} 94%, 100% {{ opacity: 0; }} }}"
    )
    css += "\n".join(frames)

    dots = "".join(
        f'<circle cx="{tx + 22 + i * 18}" cy="{ty + 20}" r="5.5" fill="{c}"/>'
        for i, c in enumerate((nlc.ERROR, nlc.WARNING, nlc.SUCCESS))
    )
    terminal = (
        f'<g class="fade-in d2">'
        f'<rect x="{tx}" y="{ty}" width="{tw}" height="{th}" rx="12" fill="{nlc.BG_PAPER}" stroke="{nlc.DIVIDER}"/>'
        f'<path d="M{tx} {ty + 40}H{tx + tw}" stroke="{nlc.DIVIDER}"/>'
        f"{dots}"
        f'<text x="{tx + tw / 2}" y="{ty + 25}" text-anchor="middle" class="mono" font-size="12" fill="{nlc.TEXT_MUTED}">'
        f"{esc(USERNAME.lower())}@dev: ~</text>"
        f'<clipPath id="term-clip"><rect x="{tx + 1}" y="{ty + 41}" width="{tw - 2}" height="{th - 42}" rx="11"/></clipPath>'
        f'<g clip-path="url(#term-clip)"><g class="typed">{"".join(lines)}{"".join(covers)}'
        f'<g class="tail"><rect class="cursor" x="{tx + 28 + len(script[-1][1]) * char_w:.0f}" y="{y - 31 - 14}" '
        f'width="9" height="18" fill="{nlc.SECONDARY_LIGHT}"/></g>'
        f"</g></g></g>"
    )

    body = nlc.background(w, h, "hero") + "".join(left) + terminal
    return nlc.svg(w, h, body, css, f"{USERNAME} — {TAGLINE}")


# --------------------------------------------------------------------------- cards

def card_header(title: str, x: int = 25, y: int = 38) -> str:
    return (
        f'<rect x="{x}" y="{y - 22}" width="4" height="26" rx="2" fill="{nlc.PRIMARY_LIGHT}"/>'
        f'<text x="{x + 14}" y="{y - 2}" class="h" font-size="17">{esc(title)}</text>'
    )


def render_stats(p: dict) -> str:
    w, h = 495, 210
    level, pct = compute_rank(p)
    rows = [
        ("Total Stars Earned", p["stars"], nlc.WARNING),
        (f"Commits ({p['year']})", p["commits_this_year"], nlc.PRIMARY_LIGHT),
        ("Commits (all time)", p["commits_all_time"], nlc.ACCENT_LIGHT),
        ("Pull Requests", p["prs"], nlc.SECONDARY_LIGHT),
        ("Issues", p["issues"], nlc.INFO),
        ("Contributed to", p["contributed_to"], nlc.TEXT_MUTED),
    ]
    out = [nlc.background(w, h, "stats"), card_header(f"{USERNAME}'s GitHub Stats")]
    for i, (label, value, color) in enumerate(rows):
        y = 70 + i * 23
        out.append(
            f'<g class="slide-up" style="animation-delay:{0.1 + i * 0.08:.2f}s">'
            f'<circle cx="34" cy="{y - 4}" r="4" fill="{color}"/>'
            f'<text x="48" y="{y}" font-size="13.5" fill="{nlc.TEXT}" font-weight="500">{esc(label)}:</text>'
            f'<text x="290" y="{y}" font-size="13.5" fill="{nlc.TEXT}" font-weight="700" text-anchor="end">{value:,}</text></g>'
        )
    # rank ring
    cx, cy, r = 400, 118, 50
    circ = 2 * math.pi * r
    filled = circ * max(0.04, 1 - pct / 100)
    out.append(
        f'<defs><linearGradient id="ring" x1="0" y1="0" x2="1" y2="1">'
        f'<stop offset="0" stop-color="{nlc.PRIMARY_LIGHT}"/><stop offset="1" stop-color="{nlc.SECONDARY_LIGHT}"/></linearGradient></defs>'
        f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{nlc.BG_TERTIARY}" stroke-width="7"/>'
        f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="url(#ring)" stroke-width="7" stroke-linecap="round" '
        f'stroke-dasharray="{filled:.1f} {circ:.1f}" transform="rotate(-90 {cx} {cy})" class="ring"/>'
        f'<text x="{cx}" y="{cy + 10}" text-anchor="middle" class="h" font-size="30">{level}</text>'
        f'<text x="{cx}" y="{cy + 76}" text-anchor="middle" font-size="11" class="muted">Top {max(pct, 0.1):.1f}%</text>'
    )
    css = (
        f"  .ring {{ animation: ring 1.2s ease-out both; }}\n"
        f"  @keyframes ring {{ from {{ stroke-dasharray: 0 {circ:.1f}; }} }}"
    )
    return nlc.svg(w, h, "".join(out), css, "GitHub stats")


def render_streak(s: dict) -> str:
    w, h = 495, 210
    cols = [
        (f"{s['total']:,}", "Total Contributions", f"{short_date(s['first'])}, {s['first'].year} – Present", nlc.PRIMARY_LIGHT),
        (str(s["current"]), "Current Streak",
         f"{short_date(s['current_start'])} – {short_date(s['current_end'])}" if s["current"] else "Start one today", nlc.SECONDARY_LIGHT),
        (str(s["longest"]), "Longest Streak",
         f"{short_date(s['longest_start'])} – {short_date(s['longest_end'])}" if s["longest"] else "—", nlc.ACCENT_LIGHT),
    ]
    out = [nlc.background(w, h, "streak")]
    for i, (value, label, sub, color) in enumerate(cols):
        cx = 82 + i * 165
        if i:
            out.append(f'<path d="M{cx - 82} 40V170" stroke="{nlc.DIVIDER}"/>')
        delay = f"animation-delay:{0.1 + i * 0.15:.2f}s"
        if i == 1:
            out.append(
                f'<circle cx="{cx}" cy="86" r="40" fill="none" stroke="{color}" stroke-width="5" class="fade-in" style="{delay}"/>'
                f'<rect x="{cx - 13}" y="36" width="26" height="16" fill="{nlc.BG_DEFAULT}"/>'
                f'<path class="pulse-slow" transform="translate({cx - 9},26)" d="M9 0C11 6 18 8 18 14a9 9 0 0 1-18 0c0-4 3-6 4-9 1 3 3 4 3 4 0-4 1-6 2-9z" fill="{nlc.WARNING}"/>'
            )
        out.append(
            f'<g class="slide-up" style="{delay}">'
            f'<text x="{cx}" y="96" text-anchor="middle" class="h" font-size="28" style="fill:{color}">{esc(value)}</text>'
            f'<text x="{cx}" y="152" text-anchor="middle" font-size="14" font-weight="600" fill="{nlc.TEXT}">{esc(label)}</text>'
            f'<text x="{cx}" y="174" text-anchor="middle" font-size="11.5" class="muted">{esc(sub)}</text></g>'
        )
    return nlc.svg(w, h, "".join(out), "", "Contribution streak")


def render_activity(days: dict[str, int]) -> str:
    w, h = 1000, 300
    today = datetime.now(timezone.utc).date()
    series = [(today - timedelta(days=30 - i)) for i in range(31)]
    counts = [days.get(d.isoformat(), 0) for d in series]
    peak = max(counts) or 1
    top = int(math.ceil(peak / 4.0) * 4) or 4

    left, right, ptop, pbottom = 60, 30, 70, 50
    pw, ph = w - left - right, h - ptop - pbottom
    pts = [(left + i * pw / 30, ptop + ph - c / top * ph) for i, c in enumerate(counts)]

    def smooth(points: list[tuple[float, float]]) -> str:
        d = f"M{points[0][0]:.1f} {points[0][1]:.1f}"
        for (x0, y0), (x1, y1) in zip(points, points[1:]):
            mx = (x0 + x1) / 2
            d += f" C{mx:.1f} {y0:.1f} {mx:.1f} {y1:.1f} {x1:.1f} {y1:.1f}"
        return d

    line = smooth(pts)
    area = f"{line} L{pts[-1][0]:.1f} {ptop + ph} L{pts[0][0]:.1f} {ptop + ph} Z"

    out = [
        nlc.background(w, h, "act"),
        card_header(f"{USERNAME}'s Contribution Graph · last 31 days"),
        f'<text x="{w - 30}" y="36" text-anchor="end" font-size="12" class="muted">{sum(counts):,} contributions · peak {max(counts)}/day</text>',
        f'<defs><linearGradient id="area" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0" stop-color="{nlc.PRIMARY_LIGHT}" stop-opacity="0.45"/>'
        f'<stop offset="1" stop-color="{nlc.PRIMARY_LIGHT}" stop-opacity="0"/></linearGradient>'
        f'<linearGradient id="stroke" x1="0" x2="1"><stop offset="0" stop-color="{nlc.PRIMARY_LIGHT}"/>'
        f'<stop offset="1" stop-color="{nlc.ACCENT_LIGHT}"/></linearGradient></defs>',
    ]
    for i in range(5):
        y = ptop + ph - i * ph / 4
        dash = "" if i == 0 else 'stroke-dasharray="3 5"'
        out.append(
            f'<path d="M{left} {y:.1f}H{w - right}" stroke="{nlc.DIVIDER}" stroke-opacity="{0.9 if i == 0 else 0.45}" {dash}/>'
            f'<text x="{left - 12}" y="{y + 4:.1f}" text-anchor="end" font-size="11" class="muted">{int(top * i / 4)}</text>'
        )
    for i, d in enumerate(series):
        if i % 5 == 0 or i == 30:
            out.append(
                f'<text x="{pts[i][0]:.1f}" y="{h - 22}" text-anchor="middle" font-size="11" class="muted">{short_date(d)}</text>'
            )
    out.append(f'<path d="{area}" fill="url(#area)" class="fade-in" style="animation-duration:1.4s"/>')
    out.append(f'<path d="{line}" fill="none" stroke="url(#stroke)" stroke-width="3" stroke-linecap="round" class="draw" pathLength="1000"/>')
    for i, (x, y) in enumerate(pts):
        out.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{4.5 if counts[i] == peak and peak else 3.2}" '
            f'fill="{nlc.BG_PAPER}" stroke="{nlc.SECONDARY_LIGHT if counts[i] == peak else nlc.PRIMARY_LIGHT}" stroke-width="2" '
            f'class="fade-in" style="animation-delay:{0.6 + i * 0.03:.2f}s"><title>{d_title(series[i], counts[i])}</title></circle>'
        )
    css = (
        "  .draw { stroke-dasharray: 1000; animation: draw 1.8s ease-out both; }\n"
        "  @keyframes draw { from { stroke-dashoffset: 1000; } to { stroke-dashoffset: 0; } }"
    )
    return nlc.svg(w, h, "".join(out), css, "Contribution activity, last 31 days")


def d_title(d: date, c: int) -> str:
    return f"{d.isoformat()}: {c} contribution{'s' if c != 1 else ''}"


TROPHY_TIERS = [("S", 0, nlc.WARNING), ("A", 1, nlc.SECONDARY_LIGHT), ("B", 2, nlc.PRIMARY_LIGHT), ("C", 3, nlc.TEXT_MUTED)]


def render_trophies(p: dict, s: dict) -> str:
    trophies = [
        ("Commits", p["commits_all_time"], (2000, 1000, 250)),
        ("Stars", p["stars"], (200, 50, 10)),
        ("Followers", p["followers"], (100, 25, 5)),
        ("Repositories", p["repo_count"], (100, 50, 10)),
        ("Pull Requests", p["prs"], (200, 50, 10)),
        ("Longest Streak", s["longest"], (100, 30, 7)),
    ]
    w, h = 1000, 150
    bw, gap = 150, 14
    x0 = (w - (6 * bw + 5 * gap)) / 2
    out = [nlc.background(w, h, "tro")]
    for i, (label, value, (s_, a_, b_)) in enumerate(trophies):
        tier = 0 if value >= s_ else 1 if value >= a_ else 2 if value >= b_ else 3
        letter, _, color = TROPHY_TIERS[tier]
        x = x0 + i * (bw + gap)
        out.append(
            f'<g class="slide-up" style="animation-delay:{i * 0.08:.2f}s" transform="translate({x:.1f},20)">'
            f'<rect width="{bw}" height="110" rx="12" fill="{nlc.BG_PAPER}" stroke="{nlc.DIVIDER}"/>'
            f'<rect x="{bw / 2 - 18}" y="0" width="36" height="3" rx="1.5" fill="{color}"/>'
            # trophy cup
            f'<g transform="translate({bw / 2 - 16},14)" fill="{color}">'
            f'<path d="M6 0h20v9a10 10 0 0 1-20 0z"/>'
            f'<path d="M6 3H1v3a6 6 0 0 0 6 6M26 3h5v3a6 6 0 0 1-6 6" fill="none" stroke="{color}" stroke-width="2"/>'
            f'<rect x="14" y="18" width="4" height="6"/><rect x="9" y="24" width="14" height="4" rx="1"/></g>'
            f'<text x="{bw - 14}" y="30" text-anchor="end" class="h" font-size="18" style="fill:{color}">{letter}</text>'
            f'<text x="{bw / 2}" y="68" text-anchor="middle" class="h" font-size="20">{fmt(value)}</text>'
            f'<text x="{bw / 2}" y="90" text-anchor="middle" font-size="11.5" class="muted">{esc(label)}</text></g>'
        )
    return nlc.svg(w, h, "".join(out), "", "Trophies")


def render_footer() -> str:
    w, h = 1200, 130
    body = (
        nlc.background(w, h, "foot")
        + f'<path d="M{w / 2 - 40} 34H{w / 2 + 40}" stroke="url(#fg)" stroke-width="3" stroke-linecap="round"/>'
        f'<defs><linearGradient id="fg"><stop offset="0" stop-color="{nlc.PRIMARY_LIGHT}"/>'
        f'<stop offset="1" stop-color="{nlc.SECONDARY_LIGHT}"/></linearGradient></defs>'
        f'<text x="{w / 2}" y="72" text-anchor="middle" class="h" font-size="22">“{esc(TAGLINE)}”</text>'
        f'<text x="{w / 2}" y="100" text-anchor="middle" font-size="12" class="muted pulse-slow">'
        "⚡ auto-updated by GitHub Actions</text>"
    )
    return nlc.svg(w, h, body, "", TAGLINE)


# --------------------------------------------------------------------------- main

def main() -> int:
    if not TOKEN:
        print("error: set METRICS_TOKEN or GITHUB_TOKEN", file=sys.stderr)
        return 1
    profile = fetch_profile()
    code = load_code_stats()
    # The calendar only counts commits from linked emails; the repo scanner sees more.
    profile["commits_all_time"] = max(profile["commits_all_time"], code["total_commits"])
    streak = compute_streaks(profile["days"])

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    outputs = {
        "hero.svg": render_hero(profile, code, streak),
        "stats.svg": render_stats(profile),
        "streak.svg": render_streak(streak),
        "activity.svg": render_activity(profile["days"]),
        "trophies.svg": render_trophies(profile, streak),
        "footer.svg": render_footer(),
    }
    for name, content in outputs.items():
        (OUT_DIR / name).write_text(content, encoding="utf-8")
        print(f"wrote {OUT_DIR / name} ({len(content):,} bytes)")
    print(
        f"contribs {profile['year']}: {profile['contribs_this_year']} | commits all-time: {profile['commits_all_time']} | "
        f"streak: {streak['current']} (longest {streak['longest']}) | rank: {compute_rank(profile)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
