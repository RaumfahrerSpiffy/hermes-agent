#!/usr/bin/env python3
import re, ast

def regions_of(path):
    t = open(path).read()
    return t, list(re.finditer(r"<<<<<<< HEAD\n(.*?)=======\n(.*?)>>>>>>> upstream/main\n", t, re.S))

def splice(path, resolver, expect):
    t, rs = regions_of(path)
    assert len(rs) == expect, f"{path}: {len(rs)} != {expect}"
    out, last = [], 0
    for i, m in enumerate(rs):
        out.append(t[last:m.start()])
        out.append(resolver(i, m.group(1), m.group(2)))
        last = m.end()
    out.append(t[last:])
    res = "".join(out)
    assert "<<<<<<<" not in res and ">>>>>>>" not in res
    ast.parse(res)
    open(path, "w").write(res)
    print(f"{path}: resolved")

# ---------------- cli.py ----------------
def cli(i, ours, theirs):
    if i == 0:
        # keep-both: fork _brand_icon + upstream goal-segment helper
        assert "_brand_icon" in ours and "_status_bar_goal_segment" in theirs
        return ours + "\n" + theirs
    if i in (1, 2, 3):
        # adopt upstream battery/goal/focus; swap hardcoded glyph for skin icon
        out = theirs.replace('⚕ {snapshot', '{self._brand_icon()} {snapshot')
        assert out != theirs and '⚕' not in out.replace("f\"{battery_prefix}", "")
        return out
    if i == 4:
        # upstream's streaming-dedup gate; re-express fork skin label inside
        old = '                            label = " ⚕ Hermes "\n'
        assert theirs.count(old) == 1
        new = (
            "                            # MERCURY FORK: response label from the skin engine.\n"
            "                            try:\n"
            "                                from hermes_cli.skin_engine import get_active_skin\n"
            '                                label = get_active_skin().get_branding("response_label", " ⚕ Hermes ")\n'
            "                            except Exception:\n"
            '                                label = " ⚕ Hermes "\n'
        )
        return theirs.replace(old, new)
    raise AssertionError

splice("cli.py", cli, 5)

# ---------------- hermes_cli/main.py ----------------
def main_py(i, ours, theirs):
    if i == 0:
        # upstream fast-startup module; brand identity re-expressed there below
        assert "_startup_fast.print_fast_version_info()" in theirs
        return theirs
    if i == 1:
        # update pipeline moved to update_cmd.py; take deletion
        assert theirs.strip() == ""
        return theirs
    raise AssertionError

splice("hermes_cli/main.py", main_py, 2)

# Re-express Mercury brand name (823235584) inside _startup_fast
sf = open("hermes_cli/_startup_fast.py").read()
old = '    print(f"Hermes Agent v{__version__} ({__release_date__})")'
assert sf.count(old) == 1
new = (
    "    # MERCURY FORK (823235584): brand name from build_info, not hardcoded.\n"
    "    try:\n"
    "        from hermes_cli.build_info import get_brand_name\n"
    "        _brand = get_brand_name()\n"
    "    except Exception:\n"
    '        _brand = "Hermes Agent"\n'
    '    print(f"{_brand} v{__version__} ({__release_date__})")'
)
sf = sf.replace(old, new)
ast.parse(sf)
open("hermes_cli/_startup_fast.py", "w").write(sf)
print("_startup_fast.py: brand identity re-expressed")

# Re-express layer-3 cache clear (efc2a91c3) in update_cmd.py
uc = open("hermes_cli/update_cmd.py").read()
old = """    for home in homes:
        try:
            cache_file = home / ".update_check"
            if cache_file.exists():
                cache_file.unlink()
        except Exception:
            pass
"""
assert uc.count(old) == 1
new = """    for home in homes:
        try:
            cache_file = home / ".update_check"
            if cache_file.exists():
                cache_file.unlink()
        except Exception:
            pass

        # MERCURY FORK (efc2a91c3): also clear the default-profile cache when
        # iterating named profiles (hermes-sync may have filled it while
        # running under the default profile).
        try:
            if home != default_home:
                cache_file = default_home / ".update_check"
                if cache_file.exists():
                    cache_file.unlink()
        except Exception:
            pass
"""
uc = uc.replace(old, new)
ast.parse(uc)
open("hermes_cli/update_cmd.py", "w").write(uc)
print("update_cmd.py: default-profile cache-clear re-expressed")

# ---------------- hermes_cli/banner.py ----------------
def banner(i, ours, theirs):
    # both regions: keep fork head-hash cache key (efc2a91c3)
    if i == 0:
        assert "head_str" in ours
        return ours
    if i == 1:
        assert '"head": head_str' in ours
        # theirs carries the encoding kwarg continuation — keep ours line,
        # re-attach encoding param from theirs
        return '            json.dumps({"ts": now, "behind": behind, "rev": embedded_rev, "ver": VERSION, "head": head_str}),\n            encoding="utf-8",\n'
    raise AssertionError

splice("hermes_cli/banner.py", banner, 2)
