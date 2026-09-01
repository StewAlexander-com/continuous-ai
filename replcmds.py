#!/usr/bin/env python3
"""seedling/replcmds.py — the ONLY list of in-chat colon-command verbs.

WHY THIS EXISTS (RCA — five+ whys)
----------------------------------
1. Why does a mistype (`:them dark`, `:hel`) become a chat turn?
   Because dispatch is an exact-match if-chain. Anything that does not match
   falls through to session.chat() and is treated as user prose.
2. Why only exact matches?
   Each command was bolted onto the loop as another `if`. There is no leftover
   "this is the command channel" gate after the chain.
3. Why is there no leftover gate?
   `inputsafe.looks_like_command` exists, but the chat loop does not call it,
   and the helper itself was a partial snapshot — :search / :scan / :allow /
   :enable / :quiet / :caps were missing. Awareness drifted from the if-chain.
4. Why did the snapshot drift?
   Adding a command meant editing the if-chain and maybe :help. No single
   registry. The model was never the dispatcher, so nobody noticed until a
   typo leaked.
5. Why does Aida then "think it's something else"?
   Colon lines that leak are just tokens in a user turn. She has no complete
   command table (on purpose — the runtime owns commands). So she interprets
   `:them dark` as conversation, not as a mistyped `:theme`.
6. Why is that a correctness bug, not a UX nit?
   A leading `:` on a single line is a reserved control channel (pasted
   blocks are never commands). Sending control-channel noise to the model
   lets her confabulate about commands, contradict :help, and spend a turn.
   A typo is a typo; honesty is not asking the model to guess the verb.
7. Why not teach the model the list instead?
   Same rule as integrity guards: the model must not be the dispatcher.
   Runtime intercept is the non-regressive fix.

CONTRACT
--------
* VERBS is the complete set of command tokens (including aliases).
* A single-line, command-shaped `:verb` that the if-chain did not consume is
  intercepted here and NEVER sent as chat.
* Close typos get a did-you-mean. Nothing is auto-run (so `:forget-doc` cannot
  fire from a guess). Smileys (`:)`) are not command-shaped.
* A line that is a known verb with the leading `:` left off is offered as that
  command first ([Y/n]); `n` sends the original text to Aida. English that
  merely starts with a verb (`help me…`, `theme of…`) is not offered.
"""
from __future__ import annotations

import difflib
import re

# Canonical verbs plus aliases the if-chain already accepts.
# Keep in lockstep with seedling.py dispatch + :help. Tests freeze the set.
VERBS: frozenset[str] = frozenset({
    "help", "?",
    "status", "setup", "dispositions", "learning",
    "model", "models",
    "read", "more",
    "search", "scan", "allow",
    "capabilities", "caps",
    "enable", "disable",
    "reflect", "forget-doc",
    "voice", "quiet",
    "theme",
    "tune",
    "q",
})

# Shown to the user instead of the alias they might have mistyped toward.
_CANONICAL = {
    "models": "model",
    "caps": "capabilities",
    "?": "help",
}

_VERB_TOKEN = re.compile(r"^[a-z?][a-z0-9_-]*$", re.IGNORECASE)
_CUTOFF = 0.72
_PREFIX_MIN = 3

# These commands take no arguments. Extra words ⇒ English, not a missed colon.
_NO_ARG_VERBS = frozenset({
    "help", "?", "status", "setup", "learning", "dispositions",
    "capabilities", "caps", "more", "reflect", "quiet",
})

# First extra token that means this is a sentence, not `:verb <args>`.
_ENGLISH_STARTERS = frozenset({
    "a", "an", "the", "this", "that", "these", "those",
    "me", "my", "mine", "you", "your", "yours", "we", "our", "ours",
    "i", "i'm", "im", "it's", "its", "it",
    "to", "for", "of", "with", "about", "from", "into", "on", "at", "as",
    "if", "when", "how", "why", "what", "who", "which", "whether",
    "please", "just", "some", "any", "all", "and", "or", "but",
    "is", "are", "was", "were", "be", "been",
})


def colon_verb(line: str) -> str | None:
    """Command token after the leading colon, or None if this is not command-shaped.

    `:theme dark` → theme
    `:theme:dark` → theme
    `:forget-doc x` → forget-doc
    `:)` → None (not a verb)
    """
    s = (line or "").strip()
    if not s.startswith(":"):
        return None
    body = s[1:].strip()
    if not body:
        return ""
    token = body.split()[0]
    if ":" in token:
        token = token.split(":", 1)[0]
    token = token.lower()
    if not token:
        return ""
    if not _VERB_TOKEN.fullmatch(token):
        return None
    # Single letters are smileys (`:D`, `:P`) except the real one-char commands.
    if len(token) == 1 and token not in ("q", "?"):
        return None
    return token


def is_known_verb(verb: str | None) -> bool:
    return bool(verb) and verb in VERBS


def looks_like_colon_command(line: str) -> bool:
    """True when the line is a known colon command (verb only; args ignored)."""
    return is_known_verb(colon_verb(line))


def _args_after_verb(line: str, verb: str) -> str:
    body = (line or "").strip()
    if body.startswith(":"):
        body = body[1:]
    low = body.lower()
    if low.startswith(verb):
        return body[len(verb):].lstrip(" :")
    return ""


def suggest_verb(typed: str) -> str | None:
    """Closest known verb, or None if nothing is close enough to be honest."""
    t = (typed or "").strip().lower()
    if not t or t in VERBS:
        return _CANONICAL.get(t, t) if t in VERBS else None
    if len(t) >= _PREFIX_MIN:
        prefixed = sorted(v for v in VERBS if v.startswith(t) and v not in _CANONICAL)
        if len(prefixed) == 1:
            return prefixed[0]
    pool = sorted(v for v in VERBS if v not in _CANONICAL)
    hits = difflib.get_close_matches(t, pool, n=1, cutoff=_CUTOFF)
    if not hits:
        return None
    return hits[0]


def colon_fallthrough_notice(line: str) -> str | None:
    """User-facing intercept when a command-shaped line was not dispatched.

    None → not command-shaped (chat may proceed).
    str  → print this, do not send the line to the model.
    """
    verb = colon_verb(line)
    if verb is None:
        return None
    if verb == "":
        return "[unknown command — a lone ':' is not a command. Type :help.]"
    if verb in VERBS:
        return (
            f"[:{verb} is a command but that form wasn't recognized. "
            f"Type :help for usage. Not sent as chat.]"
        )
    sug = suggest_verb(verb)
    if sug:
        rest = _args_after_verb(line, verb)
        shown = f":{sug}" + (f" {rest}" if rest else "")
        return (
            f"[unknown :{verb} — did you mean {shown}? "
            f"Type :help for commands. Not sent as chat.]"
        )
    return (
        f"[unknown :{verb} — not a command. "
        f"Type :help for the list. Not sent as chat.]"
    )


def _leading_token(line: str) -> tuple[str, str]:
    """(first token, remainder) for a non-colon line. Empty token if none."""
    s = (line or "").strip()
    if not s or s.startswith(":"):
        return "", ""
    parts = s.split(None, 1)
    first = parts[0]
    rest = parts[1].strip() if len(parts) > 1 else ""
    if ":" in first:
        verb, inline = first.split(":", 1)
        rest = " ".join(p for p in (inline, rest) if p).strip()
        first = verb
    return first, rest


def missing_colon_offer(line: str) -> str | None:
    """If this looks like a command with the leading ':' left off, return `:{cmd}`.

    The user decides: run it, or override and send the original line to Aida.
    None ⇒ do not ask (English, smileys, already a colon command, unknown word).
    """
    token, rest = _leading_token(line)
    if not token:
        return None
    token_l = token.lower()
    if not _VERB_TOKEN.fullmatch(token_l):
        return None
    if len(token_l) == 1 and token_l not in ("q", "?"):
        return None
    if token_l == "q":
        return None  # bare q already quits; don't re-offer as :q
    verb = token_l if token_l in VERBS else suggest_verb(token_l)
    if not verb:
        return None
    verb = _CANONICAL.get(verb, verb)
    if rest and verb in _NO_ARG_VERBS:
        return None
    if rest:
        first_arg = rest.split()[0].lower().rstrip(".,;:!?")
        if first_arg in _ENGLISH_STARTERS:
            return None
    shown = f":{verb}" + (f" {rest}" if rest else "")
    return shown


# ---------------------------------------------------------------------------
# Propose → confirm → runtime run → allowed result
#
# 10-PASS RUBBER DUCK (why this shape, not "she runs :commands")
# 1. Invariant: the model is never the dispatcher. A leading `:` from the USER
#    still hits the if-chain. Aida may only PROPOSE.
# 2. Bidirectional awareness is a catalog inject (generated here) plus one
#    machine line `[offer :verb args]` — not a lecture and not prose `:read`
#    that the chat loop would confuse with a user command.
# 3. Whitelist PROPOSE_FEED (:read / :search / :more): those already have a
#    path that may put bytes in her context. Everything else is mention-only.
# 4. :scan / :forget-doc / :model / :enable / :q / :theme are never runnable
#    from an offer — scan privacy and session side-effects stay intact.
# 5. Confirm is the NEXT whole-line y/ok/yes (not default-Y). She proposed;
#    the user did not type the verb. Empty Enter is chat, not a run.
# 6. SNR: hide the tag while streaming; one dim line names the offer. At most
#    one pending. A new reply without [offer] clears a stale pending.
# 7. User-typed :commands always win and clear the pending offer.
# 8. After confirm, :read/:more send THIS turn (she must see the bytes, not
#    only stage). :search already reviews hits. :scan still never feeds findings.
# 9. Voice/TTS strips the tag so a machine line cannot trip the inline-code floor.
# 10. Tests freeze PROPOSE_FEED ⊆ VERBS, reject :scan offers, and require
#     confirm before dispatch — so this cannot silently become auto-run.
# ---------------------------------------------------------------------------

# 5-PASS POKA-YOKE (conversation first, high SNR)
# 1. Catalog is short and off unless the user named a path/search — no
#    every-turn tool lecture.
# 2. Confirm is only y/yes/ok/okay. "sure" / "go ahead" stay chat.
# 3. Chrome is one dim [offer :cmd]; no y/ok tutorial, no running/dismissed
#    banners. :help has the rest.
# 4. An offer is honored only if it is grounded in the user's last turn
#    (path they named, search they asked) or :more with an attachment.
#    Unsolicited [offer] is dropped silently.
# 5. The next chat line expires a pending offer silently. Conversation
#    never requires dismissing a prompt first.
# ---------------------------------------------------------------------------

# Results of these may reach the model after the user confirms.
PROPOSE_FEED: frozenset[str] = frozenset({"read", "search", "more"})

# She may talk about these; [offer :scan …] is dropped, never run.
PROPOSE_NEVER: frozenset[str] = frozenset({
    "scan", "forget-doc", "model", "models", "enable", "disable",
    "q", "theme", "tune", "voice", "quiet", "reflect",
})

_OFFER_RE = re.compile(
    r"\[offer\s+(:[a-z?][a-z0-9_-]*(?:[ \t]+[^\]\n]+)?)\]",
    re.IGNORECASE,
)
# Whole-line only. Conversational "sure" / "go ahead" must remain chat.
_CONFIRM_LINES = frozenset({"y", "yes", "ok", "okay"})
_DECLINE_LINES = frozenset({"n", "no", "cancel"})
_PATHISH_RE = re.compile(
    r"(?:~|/|\./|\.\./)[^\s]+|\b\S+\.\w{1,8}\b",
)
_SEARCH_ASK_RE = re.compile(
    r"\b(?:search|find|look for|where is|grep)\b",
    re.I,
)
_READ_ASK_RE = re.compile(
    r"\b(?:read|open|look at|attach)\b",
    re.I,
)


def catalog_block() -> str:
    """Rare, short. Conversation is the default; offers are optional."""
    return (
        "Stay in conversation; answer first. You cannot run colon commands. "
        "Most turns need no offer. Only if the user already named a local path "
        "or a search, you MAY append [offer :read <that path>] or "
        "[offer :search <their ask>] or [offer :more] after the reply — never "
        "instead of talking, never unsolicited. Never [offer] :scan or other "
        "verbs. Never invent files or hits."
    )


def pending_offer_block(cmd: str) -> str:
    return f"Quiet offer pending ({cmd}). Keep talking; do not mention it."


def user_turn_may_warrant_offer(text: str) -> bool:
    """True when this user turn could honestly ground a :read/:search offer."""
    t = (text or "").strip()
    if not t:
        return False
    # Attached-file turns are already the payload — don't add a tool lecture.
    if t.count("\n") > 8:
        return False
    return bool(
        _PATHISH_RE.search(t)
        or _READ_ASK_RE.search(t)
        or _SEARCH_ASK_RE.search(t)
    )


def offer_fits_conversation(
    cmd: str,
    last_user: str,
    *,
    has_attachment: bool = False,
) -> bool:
    """Runtime gate: drop unsolicited offers so chat is not interrupted."""
    verb = colon_verb(cmd)
    if verb == "more":
        return bool(has_attachment)
    last = (last_user or "").strip()
    if not last:
        return False
    rest = _args_after_verb(cmd, verb or "")
    if verb == "read":
        path = (rest.split() or [""])[0].strip("'\"")
        return _path_grounded_in_user(path, last)
    if verb == "search":
        if _SEARCH_ASK_RE.search(last) or _PATHISH_RE.search(last):
            return True
        for tok in rest.split()[:6]:
            t = tok.lower().strip(".,;:!?\"'")
            if len(t) >= 3 and t in last.lower():
                return True
        return False
    return False


def _path_grounded_in_user(path: str, last_user: str) -> bool:
    p = (path or "").strip().strip("'\"").lower()
    u = (last_user or "").lower()
    if not p or not u:
        return False
    if p in u:
        return True
    base = p.replace("\\", "/").rsplit("/", 1)[-1]
    if base and len(base) >= 3 and base in u:
        return True
    return False


def _offer_rest_ok(verb: str, rest: str) -> bool:
    r = (rest or "").strip()
    if verb == "more":
        return not r
    if not r:
        return False
    if verb == "search":
        return True
    # :read — path-shaped, not English ("this file").
    head = r.split()[0].strip("'\"")
    return (
        head.startswith(("~", "/", ".", "./", "../"))
        or "/" in r or "\\" in r
        or ("." in head and not head.endswith("."))
    )


def normalize_offer_command(raw: str) -> str | None:
    """Return a runnable `:verb …` or None if this offer must not run."""
    s = " ".join((raw or "").split())
    if not s:
        return None
    if not s.startswith(":"):
        s = ":" + s
    verb = colon_verb(s)
    if verb not in PROPOSE_FEED:
        return None
    rest = _args_after_verb(s, verb)
    if not _offer_rest_ok(verb, rest):
        return None
    if verb == "more":
        return ":more"
    return f":{verb}" + (f" {rest}" if rest else "")


def parse_offer(text: str) -> str | None:
    """Last valid [offer :verb …] in assistant text, or None."""
    found = None
    for m in _OFFER_RE.finditer(text or ""):
        cmd = normalize_offer_command(m.group(1))
        if cmd:
            found = cmd
    return found


def strip_offers(text: str) -> str:
    """Remove [offer …] tags for display / TTS. Does not change stored text."""
    if not text or "[offer" not in text.lower():
        return text or ""
    out = _OFFER_RE.sub("", text)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip() if text.strip() == text else out


def offer_reply_kind(line: str) -> str | None:
    """Whole-line confirm / decline of a pending offer. None ⇒ chat."""
    t = (line or "").strip().lower()
    t = re.sub(r"[!.?,]+$", "", t).strip()
    if t in _CONFIRM_LINES:
        return "confirm"
    if t in _DECLINE_LINES:
        return "decline"
    return None


def _prefix_hold(buf: str, needle: str) -> int:
    """How many trailing chars of buf could still complete needle (casefold)."""
    max_hold = min(len(buf), len(needle) - 1)
    low_needle = needle.lower()
    for n in range(max_hold, 0, -1):
        if low_needle.startswith(buf[-n:].lower()):
            return n
    return 0


class OfferStreamFilter:
    """Display-only: hide `[offer …]` while streaming. Capture is from the
    full reply after chat() returns — this filter never mutates stored text."""

    _START = "[offer"

    def __init__(self, sink):
        self._sink = sink
        self._buf = ""

    def __call__(self, tok: str) -> None:
        self._buf += tok or ""
        self._drain(final=False)

    def flush(self) -> None:
        self._drain(final=True)
        inner = getattr(self._sink, "flush", None)
        if callable(inner):
            try:
                inner()
            except Exception:
                pass

    def _emit(self, s: str) -> None:
        if s:
            try:
                self._sink(s)
            except Exception:
                pass

    def _drain(self, *, final: bool) -> None:
        while True:
            low = self._buf.lower()
            idx = low.find(self._START)
            if idx < 0:
                hold = 0 if final else _prefix_hold(self._buf, self._START)
                if hold:
                    emit, self._buf = self._buf[:-hold], self._buf[-hold:]
                    self._emit(emit)
                else:
                    self._emit(self._buf)
                    self._buf = ""
                return
            if idx:
                self._emit(self._buf[:idx])
                self._buf = self._buf[idx:]
                continue
            end = self._buf.find("]")
            if end < 0:
                if final:
                    self._emit(self._buf)
                    self._buf = ""
                return
            self._buf = self._buf[end + 1:]
            if self._buf.startswith("\n"):
                self._buf = self._buf[1:]
