# agent.py — Chaos Reviewer (always-two-line replies: FIX + RAW brainrot quip)
# Rules you asked for:
# - EXACTLY two chat bubbles per message.
#   1) one-line fix/summary (or a specific canned line for non-error cases)
#   2) a RAW brainrot quip (picked verbatim; no edits, no defs, no safety filter)
# - If user sends hi/hello/anything-not-an-error  -> line1 = "shut your b*tch *ss up and provide the error"
# - If user sends praise/thanks                   -> line1 = "u be glazing me brahh"
# - Include exact brainrot words in the quip pool (Skibidi, Sigma, Chad, Gigachad, Fine Shyt – work hard, etc.)
# - For very “basic”/unknown errors, allow the exact “Negative Aura – …” line as a quip.

from datetime import datetime
from uuid import uuid4
import os
import json
import re
import random
from typing import List, Dict, Any, Optional, Set

import asyncio
import requests
from uagents import Context, Protocol, Agent
from uagents_core.contrib.protocols.chat import (
    ChatAcknowledgement,
    ChatMessage,
    EndSessionContent,
    StartSessionContent,
    TextContent,
    chat_protocol_spec,
)

# ---------- Helper to build chat messages (platform uses ctx.send)
def create_text_chat(text: str, end_session: bool = False) -> ChatMessage:
    content = [TextContent(type="text", text=text)]
    if end_session:
        content.append(EndSessionContent(type="end-session"))
    return ChatMessage(timestamp=datetime.utcnow(), msg_id=uuid4(), content=content)

async def send_two_lines(ctx: Context, sender: str, line1: str, line2: str):
    """Send two separate chat bubbles in order."""
    await ctx.send(sender, create_text_chat(line1))
    await asyncio.sleep(0)  # tiny yield so UI renders as two bubbles
    await ctx.send(sender, create_text_chat(line2))

# ---------- ASI raw client (no OpenAI SDK)
def _mask_tail(s: str, n: int = 6) -> str:
    return ("*" * max(0, len(s) - n)) + s[-n:] if s else ""

ASI1_API_KEY  = (os.getenv("ASI1_API_KEY") or "").strip()
ASI1_MODEL    = (os.getenv("ASI1_MODEL") or "asi1-mini").strip()
ASI1_BASE_URL = ((os.getenv("ASI1_BASE_URL") or "https://api.asi1.ai/v1").strip().rstrip("/"))

_ASI_BASES = list(dict.fromkeys([
    ASI1_BASE_URL,
    ASI1_BASE_URL.rstrip("/v1"),
    "https://api.asi1.ai/v1",
    "https://api.asi1.ai",
]))
_ASI_HEADERS = [
    lambda key: {"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    lambda key: {"X-API-Key": key, "Content-Type": "application/json"},
    lambda key: {"x-api-key": key, "Content-Type": "application/json"},
]

LLM_OK = False  # flips true at startup if chat ping succeeds

def _asi_http(method: str, base: str, path: str, headers: dict,
              json_payload: Optional[dict] = None, timeout: int = 20):
    url = f"{base.rstrip('/')}/{path.lstrip('/')}"
    try:
        r = requests.request(method=method.upper(), url=url, headers=headers,
                             json=json_payload, timeout=timeout)
        ok = 200 <= r.status_code < 300
        if not ok:
            print(f"[ASI] {method} {url} -> {r.status_code} body={r.text[:300]!r}")
        return ok, r
    except Exception as e:
        print(f"[ASI] request error {method} {url}: {e}")
        return False, None

def _asi_try(payload: dict) -> Optional[dict]:
    if not ASI1_API_KEY:
        return None
    for base in _ASI_BASES:
        for header_fn in _ASI_HEADERS:
            headers = header_fn(ASI1_API_KEY)
            ok, r = _asi_http("POST", base, "/chat/completions", headers, json_payload=payload)
            if ok:
                try:
                    return r.json()
                except Exception:
                    return None
    return None

def _asi_chat_once(system: str, user: str, max_tokens: int = 120, temperature: float = 0.5) -> Optional[str]:
    payload = {
        "model": ASI1_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    data = _asi_try(payload)
    try:
        return (data["choices"][0]["message"]["content"] or "").strip() if data else None
    except Exception:
        return None

# ---------- Agent bootstrap
try:
    agent  # type: ignore
except NameError:
    agent = Agent()

@agent.on_event("startup")
async def _auth_check(ctx: Context):
    global LLM_OK
    if not ASI1_API_KEY:
        ctx.logger.info("ASI: no ASI1_API_KEY set. Running in heuristic-only mode.")
        LLM_OK = False
        return
    ctx.logger.info(f"ASI: key present (len={len(ASI1_API_KEY)} tail={_mask_tail(ASI1_API_KEY)}) | model={ASI1_MODEL}")
    pong = _asi_chat_once(system="Reply 'pong' only.", user="ping", max_tokens=4, temperature=0.0)
    if pong:
        ctx.logger.info("ASI: chat ping OK ✅")
        LLM_OK = True
    else:
        ctx.logger.error("ASI: chat ping failed ❌ — using heuristics only.")
        LLM_OK = False

# ---------- Extract findings from logs
def _extract_findings(log_text: str, lang_hint: Optional[str]) -> List[Dict[str, Any]]:
    FILE_LINE_PATTERNS = [
        re.compile(r'^(?P<file>[^:\n]+):(?P<line>\d+):\d*:?\s*error:\s*(?P<msg>.+)$', re.MULTILINE),          # gcc/clang
        re.compile(r'File "([^"]+)", line (\d+),.*\n(?P<msg>[\s\S]*?)(?:\n\s*\w.*:|\Z)', re.MULTILINE),       # Python tb
        re.compile(r'(?P<file>[^:\n]+)\((?P<line>\d+),\d+\):\s*error\s+TS\d+:\s*(?P<msg>.+)$', re.MULTILINE), # tsc
    ]
    findings: List[Dict[str, Any]] = []
    for pat in FILE_LINE_PATTERNS:
        for m in pat.finditer(log_text):
            g = m.groupdict()
            file_ = (g.get("file") or "").strip()
            line_s = g.get("line")
            line_ = int(line_s) if line_s and line_s.isdigit() else 0
            msg_  = re.sub(r'\s+', ' ', (g.get("msg") or m.group(0)).strip())
            if file_ and line_:
                findings.append({"file": file_, "line": line_, "msg": msg_})
            if len(findings) >= 5:
                break
        if len(findings) >= 5:
            break
    if not findings:
        for line in log_text.splitlines()[-300:]:
            low = line.lower()
            if "error" in low or "assertionerror" in low or "failed" in low:
                findings.append({"file": "(unknown)", "line": 1, "msg": line.strip()})
                if len(findings) >= 3:
                    break
    return findings

# ---------- Language guess from filenames
def _guess_lang_from_findings(findings: List[Dict[str, Any]]) -> Optional[str]:
    for f in findings:
        fname = (f.get("file") or "").lower()
        if fname.endswith((".cpp", ".cc", ".cxx", ".hpp", ".h", ".c")): return "cpp"
        if fname.endswith((".py",)): return "python"
        if fname.endswith((".ts", ".tsx", ".js", ".jsx")): return "typescript"
    return None

# ---------- Brainrot quip pools (RAW, verbatim; no modifications)
QUIPS_BRAINROT = {
    "cpp": [
        "Ohio code moment: missing return cooked the build.",
        "Ballerina Cappuccina pirouetted away with your semicolons.",
        "6-7… and the function dipped before returning. Skibidi no return.",
        "Control path NPC’d out. Add a return, blud.",
        "Cooked path. Low taper fade your logic and return something, fr.",
    ],
    "python": [
        "Traceback doing doom scrolling. Add the check, keep it sigma.",
        "Bombombini Gusini says add that return, fr.",
        "Tralalero Tralala stomping the bug with Nike on."
        "Cringe None bug. Did you pray today? Add a guard.",
        "Sussy exception arc — tighten that branch, biggest bird style.",
    ],
    "typescript": [
        "Types won’t glaze themselves — narrow it, gigachad dev.",
        "TS said ‘string’, you gave ‘number’. Ohio assign, fr.",
        "Rizz up the types: refine that union, shmlawg.",
        "Ballerina Cappuccina pirouetted away with your semicolons.",
        "Bombombini Gusini says add that return, fr.",
        "Tralalero Tralala stomping the bug with Nike on.",
        "Chimpanzini Bananini approved the patch diff.",
    ],
    "default": [
        "Cooked build. Not the mosquito again.",
        "Goofy ahh pipeline crashed out — apply the fix and mew on.",
        "Put the fries in the bag… and the return in the function.",
        "Goated with the sauce once tests pass, blud.",
        # exact words you asked to include as-is:
        "Skibidi",
        "Sigma",
        "Chad",
        "Gigachad",
        "Fine Shyt – work hard",
    ],
    "italian": [
        "Ballerina Cappuccina pirouetted away with your semicolons.",
        "Bombombini Gusini says add that return, fr.",
        "Tralalero Tralala stomping the bug with Nike on.",
        "Chimpanzini Bananini approved the patch diff.",
    ],
    # special “basic error” line (exact, long form)
    "basic": [
        "Negative Aura"
    ],
}

def _brainrot_quip_raw(lang: Optional[str], basic: bool = False) -> str:
    if basic:
        return random.choice(QUIPS_BRAINROT["basic"])
    pool = QUIPS_BRAINROT.get((lang or "").lower(), QUIPS_BRAINROT["default"])
    # 25% chance to mix in italian brainrot for extra variety
    picks = pool + (QUIPS_BRAINROT["italian"] if random.random() < 0.25 else [])
    return random.choice(picks)

# ---------- Optional one-line fix via ASI LLM
def _micro_fix_suggestion(findings: List[Dict[str, Any]]) -> Optional[str]:
    if not LLM_OK or not findings:
        return None
    f0 = findings[0]
    system = "Return a single line of code or a minimal statement that fixes the error. No preface, no prose."
    user = (
        "Suggest a single-line code change for this error. "
        "Return ONLY the code change line or the minimal statement (no explanations).\n"
        f"Error: {f0.get('msg')}\n"
        f"File:  {f0.get('file')}:{f0.get('line')}\n"
        "If a one-liner isn't possible, return: Add a failing test for this case first."
    )
    try:
        reply = _asi_chat_once(system=system, user=user, max_tokens=60, temperature=0.3)
        return reply if reply else None
    except Exception:
        return None

# ---------- Heuristic one-line fix/summary
def _concise_fix_or_summary(findings: List[Dict[str, Any]], lang: Optional[str]) -> str:
    fix = _micro_fix_suggestion(findings)
    if fix:
        f0 = findings[0]
        loc = f"{f0.get('file')}:{f0.get('line')}" if f0.get('file') and f0.get('line') else ""
        return f"{loc} — {fix}" if loc else fix

    f0 = findings[0] if findings else {}
    msg = (f0.get("msg") or "").lower()
    loc = f"{f0.get('file')}:{f0.get('line')}" if f0.get('file') and f0.get('line') else ""
    if "control reaches end of non-void function" in msg:
        tip = "add a return on all paths (e.g., return <value>;)"
    elif "undefined reference" in msg or "linker" in msg:
        tip = "link missing object/library or provide the definition"
    elif "not defined" in msg or "nameerror" in msg:
        tip = "define/import the symbol before use"
    elif "type mismatch" in msg or "cannot convert" in msg:
        tip = "adjust the type/cast or the function signature"
    elif "assert" in msg or "failed" in msg:
        tip = "update precondition or fix logic to satisfy the assertion"
    else:
        tip = "fix the first reported error; later ones cascade"
    base = f"{loc} — {tip}" if loc else tip
    return re.sub(r"\s+", " ", base).strip()

# -------------------------- Simple intent detectors for greetings/praise ------
_GREET_PAT = re.compile(r'\b(hi|hello|hey|yo|sup|hola|namaste|what\'?s up|gm|good (morning|afternoon|evening))\b', re.I)
_PRAISE_PAT = re.compile(r'\b(thanks|thank you|ty|appreciate|you(\s*are|\'re)? (good|great|amazing|awesome|goat|goated))\b', re.I)

# -------------------------- Single Chat Protocol ------------------------------
chat_proto = Protocol(spec=chat_protocol_spec)

def _collect_text(msg: ChatMessage) -> str:
    if isinstance(msg.content, list):
        return "".join(c.text for c in msg.content if isinstance(c, TextContent) and hasattr(c, "text"))
    return getattr(msg.content, "text", str(msg.content))

_GREETED_MEMORY: Set[str] = set()

@chat_proto.on_message(ChatMessage)
async def on_chat_or_review(ctx: Context, sender: str, msg: ChatMessage):
    # greet once per sender when session starts (we still send the normal two-line logic afterwards)
    is_start = isinstance(msg.content, list) and any(isinstance(c, StartSessionContent) for c in msg.content)
    if is_start and sender not in _GREETED_MEMORY:
        _GREETED_MEMORY.add(sender)
        await ctx.send(sender, create_text_chat("Hey! I’m **Chaos Reviewer** — paste an error or ask for a quick code note."))

    text = _collect_text(msg).strip()
    if not text:
        return

    # Special intent: praise -> exact line1
    if _PRAISE_PAT.search(text):
        line1 = "u be glazing me brahh"
        # second line: raw quip from default pool (verbatim)
        line2 = _brainrot_quip_raw(None)
        await send_two_lines(ctx, sender, line1, line2)
        await ctx.send(sender, ChatAcknowledgement(timestamp=datetime.utcnow(), acknowledged_msg_id=msg.msg_id))
        return

    # Try structured JSON path {context, log_tail}
    data = None
    try:
        data = json.loads(text)
    except Exception:
        pass

    if isinstance(data, dict) and "log_tail" in data:
        lang = ((data.get("context") or {}).get("lang")) or None
        log_tail = data.get("log_tail") or ""
        findings = _extract_findings(log_tail, lang)
        lang = lang or _guess_lang_from_findings(findings)

        if findings:
            # error path
            line1 = _concise_fix_or_summary(findings, lang)
            # if file unknown and line=1 (generic), use the “basic” negative aura line
            basic = (findings and (findings[0].get("file") == "(unknown)"))
            line2 = _brainrot_quip_raw(lang, basic=basic)
            await send_two_lines(ctx, sender, line1, line2)
        else:
            # no error extracted -> greeting/anything-else path
            line1 = "shut your b*tch *ss up and provide the error"
            line2 = _brainrot_quip_raw(None)
            await send_two_lines(ctx, sender, line1, line2)

        await ctx.send(sender, ChatAcknowledgement(timestamp=datetime.utcnow(), acknowledged_msg_id=msg.msg_id))
        return

    # Plain text triage path
    findings = _extract_findings(text, None)

    if findings:
        lang = _guess_lang_from_findings(findings)
        line1 = _concise_fix_or_summary(findings, lang)
        basic = (findings and (findings[0].get("file") == "(unknown)"))
        line2 = _brainrot_quip_raw(lang, basic=basic)
        await send_two_lines(ctx, sender, line1, line2)
        await ctx.send(sender, ChatAcknowledgement(timestamp=datetime.utcnow(), acknowledged_msg_id=msg.msg_id))
        return

    # If no findings: greeting/anything-else path (exact line)
    if _GREET_PAT.search(text) or True:
        line1 = "shut your b*tch *ss up and provide the error"
        line2 = _brainrot_quip_raw(None)
        await send_two_lines(ctx, sender, line1, line2)
        await ctx.send(sender, ChatAcknowledgement(timestamp=datetime.utcnow(), acknowledged_msg_id=msg.msg_id))
        return

@chat_proto.on_message(ChatAcknowledgement)
async def on_ack(_ctx: Context, _sender: str, _msg: ChatAcknowledgement):
    return  # no-op

# -------------------------- Register the protocol -----------------------------
agent.include(chat_proto, publish_manifest=True)
# (Platform hosts the agent; no agent.run())
