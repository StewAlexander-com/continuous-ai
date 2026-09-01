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
