#!/usr/bin/env python3
"""
Community Connect Four played through GitHub issues.

A visitor clicks a column link in the README, which opens an issue titled
`connect4|drop|<column>`. The connect4 workflow runs this script, which applies
the move, rewrites the README section between the CONNECT4 markers and writes a
reply for the workflow to post before closing the issue.

  python scripts/connect4.py move    # uses ISSUE_TITLE, ISSUE_AUTHOR
  python scripts/connect4.py render  # just re-render the README section

Environment variables:
  ISSUE_TITLE   - raw issue title (untrusted, parsed strictly)
  ISSUE_AUTHOR  - login of the issue author
  REPO          - owner/name (default: BassRhombus/BassRhombus)
  REPLY_FILE    - where to write the issue reply (default: connect4-reply.md)
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

ROWS, COLS = 6, 7
STATE_FILE = Path("game/connect4.json")
README = Path("README.md")
REPO = os.environ.get("REPO", "BassRhombus/BassRhombus")
OWNER = REPO.split("/")[0]
REPLY_FILE = Path(os.environ.get("REPLY_FILE", "connect4-reply.md"))
START, END = "<!-- CONNECT4:START -->", "<!-- CONNECT4:END -->"

TEAMS = {"B": ("Blue", "\U0001f535"), "G": ("Green", "\U0001f7e2")}
EMPTY = "⚫"
WIN_MARK = {"B": "\U0001f499", "G": "\U0001f49a"}
TITLE_RE = re.compile(r"^connect4\|(drop\|([1-7])|reset)$")
LOGIN_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def new_board() -> list[list[str]]:
    return [["" for _ in range(COLS)] for _ in range(ROWS)]


def load() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"board": new_board(), "turn": "B", "moves": [], "games": [], "players": {}, "last_game": None}


def save(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def drop(board: list[list[str]], col: int, piece: str) -> int | None:
    """Drops into 0-based col. Returns the row it landed in (0 = top) or None if full."""
    for row in range(ROWS - 1, -1, -1):
        if not board[row][col]:
            board[row][col] = piece
            return row
    return None


def winning_line(board: list[list[str]], row: int, col: int) -> list[tuple[int, int]]:
    piece = board[row][col]
    for dr, dc in ((0, 1), (1, 0), (1, 1), (1, -1)):
        line = [(row, col)]
        for sign in (1, -1):
            r, c = row + dr * sign, col + dc * sign
            while 0 <= r < ROWS and 0 <= c < COLS and board[r][c] == piece:
                line.append((r, c))
                r, c = r + dr * sign, c + dc * sign
        if len(line) >= 4:
            return line
    return []


def issue_url(title: str, body: str) -> str:
    return f"https://github.com/{REPO}/issues/new?title={quote(title)}&body={quote(body)}"


def render_board(board: list[list[str]], highlight: set[tuple[int, int]] | None = None, links: bool = True) -> str:
    highlight = highlight or set()
    if links:
        body = "Just press **Create** — don't change the title. A bot will drop your piece within a minute."
        header = " | ".join(
            f"[**{c + 1}**]({issue_url(f'connect4|drop|{c + 1}', body)})" if not board[0][c] else f"~~{c + 1}~~"
            for c in range(COLS)
        )
    else:
        header = " | ".join(str(c + 1) for c in range(COLS))
    lines = [f"| {header} |", "|" + ":-:|" * COLS]
    for r in range(ROWS):
        cells = [
            WIN_MARK[board[r][c]] if (r, c) in highlight else TEAMS[board[r][c]][1] if board[r][c] else EMPTY
            for c in range(COLS)
        ]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def render(state: dict) -> str:
    name, icon = TEAMS[state["turn"]]
    blue = sum(1 for g in state["games"] if g["winner"] == "B")
    green = sum(1 for g in state["games"] if g["winner"] == "G")
    draws = sum(1 for g in state["games"] if g["winner"] is None)

    out = [
        START,
        "",
        "## \U0001f3ae Community Connect Four",
        "",
        "<div align=\"center\">",
        "",
        "**Anyone can play!** Click a column number to drop the next piece. Everyone plays together, taking "
        "turns for whichever team is up.",
        "",
        f"It's **{icon} {name}'s** turn · move #{len(state['moves']) + 1}",
        "",
        render_board(state["board"]),
        "",
        f"\U0001f535 Blue **{blue}** · \U0001f7e2 Green **{green}** · \U0001f91d Draws **{draws}**",
        "",
        "</div>",
        "",
    ]

    if state["moves"]:
        out += ["<details>", "<summary><b>\U0001f4dc Last moves</b></summary>", "", "| Move | Player | Column |", "|:-:|:-|:-:|"]
        recent = state["moves"][-8:]
        for i, m in reversed(list(enumerate(recent, start=len(state["moves"]) - len(recent) + 1))):
            out.append(f"| {TEAMS[m['team']][1]} #{i} | [@{m['user']}](https://github.com/{m['user']}) | {m['col']} |")
        out += ["", "</details>", ""]

    if state["players"]:
        top = sorted(state["players"].items(), key=lambda kv: (-kv[1]["wins"], -kv[1]["moves"], kv[0]))[:5]
        out += ["<details>", "<summary><b>\U0001f3c6 Top players</b></summary>", "", "| Player | Moves | Winning moves |", "|:-|:-:|:-:|"]
        for login, s in top:
            out.append(f"| [@{login}](https://github.com/{login}) | {s['moves']} | {s['wins']} |")
        out += ["", "</details>", ""]

    last = state.get("last_game")
    if last:
        result = (f"{TEAMS[last['winner']][1]} **{TEAMS[last['winner']][0]} won** — winning move by "
                  f"[@{last['finisher']}](https://github.com/{last['finisher']})") if last["winner"] else "\U0001f91d **Draw**"
        out += [
            "<details>",
            f"<summary><b>\U0001f3c1 Previous game</b> · {last['moves']} moves · {last['ended']}</summary>",
            "",
            result,
            "",
            last["board_md"],
            "",
            "</details>",
            "",
        ]
    out.append(END)
    return "\n".join(out)


def write_readme(state: dict) -> None:
    text = README.read_text(encoding="utf-8")
    pattern = re.compile(re.escape(START) + r".*?" + re.escape(END), re.S)
    if not pattern.search(text):
        raise SystemExit("README is missing the CONNECT4 markers")
    README.write_text(pattern.sub(lambda _: render(state), text), encoding="utf-8")


def reply(message: str) -> None:
    REPLY_FILE.write_text(message + f"\n\n[⬅️ Back to the board](https://github.com/{OWNER}#-community-connect-four)\n",
                          encoding="utf-8")


def handle_move(state: dict, title: str, user: str) -> bool:
    """Applies the move. Returns True when the state changed."""
    match = TITLE_RE.match(title.strip())
    if not match or not LOGIN_RE.match(user):
        reply("\U0001f914 I couldn't read that move. Use the column links on the profile to play.")
        return False

    if match.group(1) == "reset":
        if user.lower() != OWNER.lower():
            reply("⛔ Only the profile owner can reset the game.")
            return False
        state.update(board=new_board(), moves=[], turn="B")
        reply("\U0001f504 The board has been reset. Blue starts.")
        return True

    col = int(match.group(2))
    team = state["turn"]
    row = drop(state["board"], col - 1, team)
    if row is None:
        reply(f"\U0001f6ab Column {col} is full. Pick another one!")
        return False

    state["moves"].append({"user": user, "col": col, "team": team, "at": now()})
    player = state["players"].setdefault(user, {"moves": 0, "wins": 0})
    player["moves"] += 1

    line = winning_line(state["board"], row, col - 1)
    full = all(state["board"][0][c] for c in range(COLS))
    name, icon = TEAMS[team]
    if line or full:
        winner = team if line else None
        if winner:
            player["wins"] += 1
        state["games"].append({"winner": winner, "moves": len(state["moves"]), "ended": now()})
        state["last_game"] = {
            "winner": winner,
            "finisher": user,
            "moves": len(state["moves"]),
            "ended": now(),
            "board_md": render_board(state["board"], set(line), links=False),
        }
        first = "G" if len(state["games"]) % 2 else "B"  # alternate who opens
        state.update(board=new_board(), moves=[], turn=first)
        if winner:
            reply(f"\U0001f389 **{icon} {name} wins!** You dropped the winning piece in column {col}, @{user}. "
                  f"A new game has started, and {TEAMS[first][1]} {TEAMS[first][0]} goes first.")
        else:
            reply(f"\U0001f91d The board is full. **It's a draw!** Thanks @{user}. A new game has started.")
    else:
        state["turn"] = "G" if team == "B" else "B"
        nxt = TEAMS[state["turn"]]
        reply(f"{icon} Dropped a **{name}** piece in column {col}. Thanks @{user}!\n\n"
              f"Now it's {nxt[1]} **{nxt[0]}'s** turn. Bring a friend!\n\n{render_board(state['board'], links=False)}")
    return True


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "render"
    state = load()
    if cmd == "move":
        changed = handle_move(state, os.environ.get("ISSUE_TITLE", ""), os.environ.get("ISSUE_AUTHOR", ""))
        if not changed:
            return 0
    elif cmd != "render":
        print(f"unknown command {cmd!r}", file=sys.stderr)
        return 2
    save(state)
    write_readme(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
