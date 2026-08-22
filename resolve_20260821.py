#!/usr/bin/env python3
"""2026-08-21 merge-probe resolver. Run from /tmp/merge-probe."""
import ast, re, sys, pathlib

ROOT = pathlib.Path("/tmp/merge-probe")
# NOTE: marker lines must use [^\n]* — a bare .* under DOTALL swallows the
# file tail (data loss observed on first run of this script).
MARK = re.compile(r"^<{7} [^\n]*\n(.*?)^={7}\n(.*?)^>{7} [^\n]*\n", re.S | re.M)

def resolve(path, fns):
    """fns: list of callables (ours, theirs) -> replacement, one per region in order."""
    p = ROOT / path
    text = p.read_text()
    regions = list(MARK.finditer(text))
    assert len(regions) == len(fns), f"{path}: {len(regions)} regions, {len(fns)} resolvers"
    out, pos = [], 0
    for m, fn in zip(regions, fns):
        out.append(text[pos:m.start()])
        out.append(fn(m.group(1), m.group(2)))
        pos = m.end()
    out.append(text[pos:])
    new = "".join(out)
    assert "<<<<<<<" not in new and ">>>>>>>" not in new, f"{path}: markers survive"
    p.write_text(new)
    if path.endswith(".py"):
        ast.parse(new, filename=path)
    print(f"OK {path}: {len(fns)} region(s)")

ours = lambda o, t: o
theirs = lambda o, t: t
union = lambda o, t: o + t

# 1. auxiliary_client — theirs (upstream translates response_format; supersedes fork drop)
resolve("agent/auxiliary_client.py", [theirs])

# 2. cli.py — union: fork skin brand + upstream C-02 labels
resolve("cli.py", [union])

# 3. stream_consumer — fork transform hook FIRST, then upstream pre-fence capture
resolve("gateway/stream_consumer.py", [union])

# 4. _startup_fast — theirs (empty); brand re-expressed in fallback below
resolve("hermes_cli/_startup_fast.py", [theirs])
sf = ROOT / "hermes_cli/_startup_fast.py"
t = sf.read_text()
old = '''    except Exception:
        from hermes_cli import __release_date__, __version__

        print(f"Hermes Agent v{__version__} ({__release_date__})")
'''
new = '''    except Exception:
        from hermes_cli import __release_date__, __version__

        # MERCURY FORK (823235584): brand name from build_info even on the
        # banner-import fallback path; primary path already brands via
        # format_banner_version_label -> get_brand_name.
        try:
            from hermes_cli.build_info import get_brand_name

            _brand = get_brand_name()
        except Exception:
            _brand = "Hermes Agent"
        print(f"{_brand} v{__version__} ({__release_date__})")
'''
assert t.count(old) == 1, f"_startup_fast fallback: {t.count(old)} matches"
sf.write_text(t.replace(old, new))
ast.parse(sf.read_text())
print("OK _startup_fast fallback re-expression")

# 5. hermes_state_common — theirs x3 (superseding _RESET_CHILD_SQL fix)
resolve("hermes_state_common.py", [theirs, theirs, theirs])

# 6. kimi plugin — theirs' K3 import block prefixed with the fork's
#    mutual-exclusion comment (keep="all" already auto-merged into the tail)
def kimi(o, t):
    fork_comment = (
        "        # thinking when no recognized effort is requested. thinking and\n"
        "        # reasoning_effort are mutually exclusive (Moonshot HTTP 400\n"
        "        # otherwise), so set exactly one branch — and apply Preserved\n"
        '        # Thinking (keep="all") only on the branch where thinking\n'
        "        # actually survives (MERCURY FORK).\n"
    )
    # theirs' first line duplicates the shared comment lead-in; drop it
    t_lines = t.splitlines(keepends=True)
    assert "no recognized effort" in t_lines[0], t_lines[0]
    return fork_comment + "".join(t_lines[1:])
resolve("plugins/model-providers/kimi-coding/__init__.py", [kimi])

# 7. test_resume_command — adopt reset-child tests, prune the _exact_lane_ test
def resume_tests(o, t):
    assert o.strip() == "", "expected empty ours side"
    lines = t.splitlines(keepends=True)
    idx = next(i for i, l in enumerate(lines)
               if "test_sessions_busy_platform_lists_exact_lane_and_excludes_current_tip" in l)
    # back up over decorator(s)
    while idx > 0 and lines[idx - 1].strip().startswith("@"):
        idx -= 1
    kept = "".join(lines[:idx]).rstrip() + "\n\n"
    assert "keeps_legacy_reset_child" in kept and "created_by_gateway_resets" in kept
    assert "exact_lane" not in kept
    return kept
resolve("tests/gateway/test_resume_command.py", [resume_tests])

# 8. ws_auth_retry — ours x2 (fork harness tests need Platform import + 401 test)
resolve("tests/gateway/test_ws_auth_retry.py", [ours, ours])

# 9. tui_gateway/server — union: fork session_key + upstream turn_started_at
resolve("tui_gateway/server.py", [union])

print("ALL RESOLVED")
