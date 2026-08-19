"""1403-chain glyph flags — period-grounded replacement of stock emoji.

Fork-only cosmetic layer (Mercury install, Peter 2026-08-19). Rewrites the
gateway's LINE-LEADING event/tool emoji to IBM 1403 print-chain flag tokens
before any text leaves for Matrix. Applied in MatrixAdapter.format_message —
the single outbound chokepoint (both send() and edit_message() route through
it), so tool-activity bubbles, lifecycle notices, slash-command reports
(/status, /usage), and streamed edits are all converted in one place.

Doctrine (grounded 2026-08-18): the RTCC's IBM 1403 line printers carried a
48-character chain — A-Z, 0-9, and twelve specials (& , . - $ * / % # @ and
the not-equal/lozenge). That set is the whole glyph vocabulary of a period
NASA printout. Attested flagging idioms: the margin asterisk (* out-of-limits,
** urgent), words-as-flags (GO / NOGO / HOLD — the loop's own vocabulary),
and VOX for voice. Chosen set: "A with VOX".

Rules:
  * LINE-LEADING ONLY. An emoji mid-sentence in agent prose is left alone;
    every gateway-emitted marker leads its line.
  * Unknown emoji pass through untouched — this is a flag map, not a
    scrubber.
  * Variation selectors (U+FE0F) on the marker are consumed with it.
  * Pure text-to-text; no markdown or color concerns (the phone styles
    tokens if it chooses; the wire stays plain).
"""

from __future__ import annotations

# Marker -> chain token. Keys are the BASE character (no variation selector);
# lookup strips U+FE0F first. Grouped by emitter class for maintenance.
GLYPH_FLAGS: dict[str, str] = {
    # -- lifecycle notices (gateway/run.py, slash_commands.py) --------------
    "\u23f3": "**",    # ⏳ working / queued
    "\u2705": "GO",    # ✅ success
    "\u274c": "NOGO",  # ❌ failure
    "\u26d4": "NOGO",  # ⛔ refused
    "\u26a0": "*",     # ⚠ caution
    "\u23f8": "HOLD",  # ⏸ paused
    "\u25b6": "GO",    # ▶ resumed
    "\u23f1": "**",    # ⏱ timer
    "\u23e9": "SKIP",  # ⏩ fast-forward / skipped
    "\u21aa": "FWD",   # ↪ forwarded / rerouted
    "\U0001f5dc": "**",  # 🗜 compressing
    "\U0001f4be": "#",   # 💾 saved / memory
    "\U0001f4cb": "LIST",  # 📋 list / sessions ("=" is NOT on the 1403 chain — corrected 2026-08-19)
    "\U0001f50c": "@",   # 🔌 link / connect
    "\U0001f504": "@",   # 🔄 reconnect / refresh
    "\U0001f399": "VOX",  # 🎙 voice
    "\U0001f4ad": "/",   # 💭 thinking / note
    "\U0001f4ac": "/",   # 💬 interim commentary
    "\U0001f4ec": "MSG",  # 📬 inbound message / mailbox
    "\u2665": "HB",    # ♥ heartbeat
    "\u267b": "RECYC", # ♻ recycle / session reuse
    "\u2695": "MED",   # ⚕ health check
    "\u2697": "RVW",   # ⚗ background review
    "\u26bf": "GATE",  # ⚿ command gate
    "\u2795": "&",     # ➕ added (& is on the chain)
    "\U0001f44d": "GO",    # 👍 approve
    "\U0001f44e": "NOGO",  # 👎 deny
    # -- slash-command report chrome (slash_commands.py, account_usage.py) --
    "\u2713": "GO",    # ✓ confirmations ("✓ paused", "✓ gate removed")
    "\u2714": "GO",    # ✔ heavy check
    "\u2717": "NOGO",  # ✗ failures
    "\u2715": "NOGO",  # ✕ close/failed
    "\U0001f4c8": "%",   # 📈 usage report header (% is on the chain; period meaning fits)
    "\U0001f4b3": "$",   # 💳 Nous balance ($ is on the chain)
    "\U0001f7e1": "*",   # 🟡 caution state
    "\U0001f534": "*",   # 🔴 alert state
    "\U0001f4ca": "%",   # 📊 context/stats
    # -- tool-activity bubbles (registry emoji via get_tool_emoji) ----------
    "\U0001f4d6": "READ",  # 📖 read_file / guides
    "\u270d": "EDIT",      # ✍ write_file
    "\U0001f527": "EDIT",  # 🔧 patch
    "\U0001f4bb": "EXEC",  # 💻 terminal
    "\u2699": "EXEC",      # ⚙ process (also the tool-emoji default)
    "\U0001f50d": "SRCH",  # 🔍 web_search
    "\U0001f50e": "SRCH",  # 🔎 search_files
    "\U0001f4da": "READ",  # 📚 skill_view
    "\U0001f310": "@",     # 🌐 web / browser
    "\U0001f3a8": "IMG",   # 🎨 image_generate
    "\U0001f3ac": "FILM",  # 🎬 video tools
    "\U0001f441": "SCAN",  # 👁 vision_analyze
    "\u26a1": "**",        # ⚡ generic tool / code execution
    "\U0001f9e0": "/",     # 🧠 memory/reasoning tools
    "\U0001f4dd": "EDIT",  # 📝 todo/notes
    "\U0001f4c4": "READ",  # 📄 documents
    # -- toolset labels (hermes_cli/tools_config.py; surface in /usage) -----
    "\U0001f9e9": "CTX",   # 🧩 context engine
    "\U0001f4c1": "FILE",  # 📁 file operations
    "\U0001f50a": "AUD",   # 🔊 text-to-speech
    "\U0001f3b5": "MUS",   # 🎵 spotify/music
    "\u23f0": "CRON",      # ⏰ cron jobs
    "\U0001f3e0": "HOME",  # 🏠 home assistant
    "\u2753": "QRY",       # ❓ clarify
    "\U0001f465": "DELEG", # 👥 delegation
    "\U0001f426": "X",     # 🐦 x_search
}

_VS16 = "\ufe0f"


def leads_with_machine_marker(text: str) -> bool:
    """True when the message's FIRST line leads with a known event emoji.

    This is the machine-provenance test, evaluated on the ORIGINAL content
    BEFORE rewrite_glyph_flags strips the emoji: every gateway-emitted
    machine line (tool bubble, lifecycle notice, report) leads with a
    mapped emoji; agent prose never does. The Matrix adapter uses this to
    type machine lines as m.notice (the spec's automated-output msgtype)
    and prose as m.text — carrying provenance to clients without content
    smuggling. Classification is a pure function of content, so streamed
    edits of the same message classify identically on every edit.
    """
    if not text:
        return False
    stripped = text.lstrip()
    return bool(stripped) and stripped[0] in GLYPH_FLAGS


def _flag_line(line: str) -> str:
    """Rewrite one line's leading marker, if known."""
    stripped = line.lstrip()
    if not stripped:
        return line
    indent = line[: len(line) - len(stripped)]
    # Markdown bold/quote prefixes occasionally precede the marker
    # ("**bold** ⚠️ ..." does NOT count — only a true leading marker).
    ch = stripped[0]
    token = GLYPH_FLAGS.get(ch)
    if token is None:
        return line
    rest = stripped[1:]
    if rest.startswith(_VS16):
        rest = rest[1:]
    rest = rest.lstrip(" \u00a0")
    return f"{indent}{token} {rest}" if rest else f"{indent}{token}"


def rewrite_glyph_flags(text: str) -> str:
    """Rewrite every line-leading known emoji in *text* to its chain token.

    Fenced code blocks (``` ... ```) are left untouched — quoted content
    is data, not chrome. Fail-safe by construction: pure string transform,
    no state; any surprise input shape degrades to pass-through per line.
    """
    if not text:
        return text
    try:
        out = []
        in_fence = False
        for line in text.split("\n"):
            if line.lstrip().startswith("```"):
                in_fence = not in_fence
                out.append(line)
                continue
            out.append(line if in_fence else _flag_line(line))
        return "\n".join(out)
    except Exception:
        return text
