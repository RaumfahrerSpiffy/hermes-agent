"""The `screen` tool — MercuryEyes' single core surface.

One tool, seven verbs. Registered as a CORE tool rather than a plugin on
measured grounds: `PluginContext.register_tool()` delegates to the same
`tools.registry.register()` core uses, and plugin-registered tools appear in
`get_definitions()` exactly like built-ins (MEASURED 2026-08-22: five
plugin-registered a2a_* tools were present in the schema list). A plugin
would therefore cost the same per-turn tokens while adding indirection.

What actually controls per-turn cost is `check_fn`: tools whose check returns
False are withheld from the schema list entirely (measured on the same run:
92 registered, 48 emitted). `_check()` below is false on every host that is
not a KDE/Wayland session with a writable /dev/uinput, so this tool costs
nothing anywhere it could not work.

Safety posture:
  * READ verbs (look, surfaces, cursor, wait) are ungated and never unlock
    the session — they work fine against a locked screen.
  * INPUT verbs (click, type, key) are approval-gated through the same human
    gate dangerous shell commands use, and run inside `session.unlocked()`
    so the screen is always re-locked afterwards.

Every verb returns an OBSERVATION, never a bare success flag.
"""
import json
import os
import shutil

from tools.approval import request_tool_approval
from tools.mercury_eyes import (act, capture, events, geometry, pointer,
                                session, surfaces)
from tools.registry import registry

READ_VERBS = ("look", "surfaces", "cursor", "wait")
INPUT_VERBS = ("click", "type", "key")

SCREEN_SCHEMA = {
    "type": "function",
    "function": {
        "name": "screen",
        "description": (
            "See and control the local KDE/Wayland desktop. Verbs: "
            "look (screenshot with frame-sanity check), surfaces (window "
            "inventory), cursor (compositor pointer position), wait (block "
            "until a matching window appears), click, type, key. "
            "Every action returns an OBSERVATION, not a success flag: clicks "
            "assert the cursor landed before firing and REFUSE when it did "
            "not; keystrokes have no readback channel and report as "
            "dispatched only. Input verbs require human approval and "
            "temporarily unlock the session."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["look", "surfaces", "wait", "click", "type",
                             "key", "cursor"],
                    "description": "Which verb to run.",
                },
                "x": {"type": "integer",
                      "description": "Click target X, logical pixels."},
                "y": {"type": "integer",
                      "description": "Click target Y, logical pixels."},
                "button": {"type": "string", "enum": ["left", "right", "middle"],
                           "description": "Mouse button for click. Default left."},
                "text": {"type": "string",
                         "description": "Text to type for the type verb."},
                "keys": {"type": "string",
                         "description": "Chord for the key verb, e.g. 'ctrl+s'."},
                "app": {"type": "string",
                        "description": "Substring match on application name (wait)."},
                "name": {"type": "string",
                         "description": "Substring match on window title (wait)."},
                "role": {"type": "string",
                         "description": "Substring match on AT-SPI role (wait)."},
                "timeout": {"type": "number",
                            "description": "Seconds to wait before reporting timeout."},
                "out": {"type": "string",
                        "description": "Where to write the screenshot (look)."},
            },
            "required": ["action"],
        },
    },
}


def _check() -> bool:
    """Service gate: hide this tool everywhere it cannot actually work.

    A tool withheld here costs zero tokens — it never enters the schema list
    sent to the model.
    """
    try:
        session_type = os.environ.get("XDG_SESSION_TYPE", "").lower()
        if session_type != "wayland":
            return False
        if not os.path.exists("/dev/uinput"):
            return False
        if not os.access("/dev/uinput", os.W_OK):
            return False
        return shutil.which("qdbus6") is not None
    except Exception:
        return False


def _approval_reason(action, args):
    if action == "click":
        return (f"drive the desktop: click {args.get('button', 'left')} at "
                f"({args.get('x')}, {args.get('y')}) — this unlocks the "
                f"screen for the duration and re-locks after")
    if action == "type":
        text = str(args.get("text", ""))
        shown = text if len(text) <= 60 else text[:57] + "..."
        return (f"drive the desktop: type {shown!r} ({len(text)} chars) — "
                f"this unlocks the screen for the duration and re-locks after")
    return (f"drive the desktop: press {args.get('keys')!r} — this unlocks "
            f"the screen for the duration and re-locks after")


def _run_read(action, args):
    if action == "surfaces":
        return surfaces.surfaces()
    if action == "cursor":
        return pointer.cursor_pos()
    if action == "look":
        out = args.get("out") or "/tmp/mercury_eye.png"
        return capture.capture(out=out)
    return events.wait_until(
        app=args.get("app"), name=args.get("name"), role=args.get("role"),
        timeout=float(args.get("timeout", 30.0)),
    )


def _run_input(action, args):
    if action == "click":
        return act.click(int(args.get("x", 0)), int(args.get("y", 0)),
                         button=args.get("button", "left"))
    if action == "type":
        return act.type_text(str(args.get("text", "")))
    return act.key_combo(str(args.get("keys", "")))


def handle_screen(args, **kwargs):
    """Tool entry point. ALWAYS returns a JSON string, never raises."""
    action = (args or {}).get("action", "")
    try:
        if action in READ_VERBS:
            result = _run_read(action, args)
            result = dict(result)
            result.setdefault("geometry", geometry.probe())
            return json.dumps(result, default=str)

        if action in INPUT_VERBS:
            verdict = request_tool_approval(
                "screen", _approval_reason(action, args),
                rule_key=f"screen:{action}",
                approval_callback=kwargs.get("approval_callback"),
            )
            if not verdict.get("approved"):
                return json.dumps({
                    "ok": False,
                    "action": action,
                    "performed": False,
                    "reason": verdict.get("message")
                              or "approval denied — no input was performed",
                }, default=str)

            with session.unlocked() as guard:
                result = _run_input(action, args)
            result = dict(result)
            result["action"] = action
            result["session"] = guard
            return json.dumps(result, default=str)

        return json.dumps({
            "error": f"unknown action {action!r}; expected one of "
                     f"{sorted(READ_VERBS + INPUT_VERBS)}"
        })
    except session.UnlockFailed as exc:
        return json.dumps({"error": str(exc), "action": action,
                           "performed": False})
    except Exception as exc:
        return json.dumps({"error": f"{type(exc).__name__}: {exc}",
                           "action": action})


registry.register(
    name="screen",
    toolset="screen",
    schema=SCREEN_SCHEMA,
    handler=handle_screen,
    check_fn=_check,
    requires_env=[],
    emoji="[EYE]",
    description="Local desktop perception and control (KDE/Wayland).",
)
