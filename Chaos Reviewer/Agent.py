# agent.py — Chaos Reviewer (EXACTLY two chat bubbles: FIX + RAW brainrot quip)
# Adds:
#   • Free local code-corrector via Ollama (fallback when ASI isn't available),
#     and a 2–3 sentence "Solution:" explanation appended inside Bubble #1.
#   • Per-language quips.
#   • Gist pull bridge (no inbound ports) to receive requests and post responses.
#
# Configure (env):
#   # Gist bridge (REQUIRED)
#   export GIST_TOKEN="ghp_..."           # GitHub PAT with "gist" scope (RW)
#   export REQ_GIST_ID="<gist_id>"        # existing public or secret gist ID
#   export REQ_FILE="chaos-request.json"
#   export RESP_FILE="chaos-response.json"
#   export POLL_SEC="6"                   # poll interval seconds (decorator is fixed 6s)
#
#   # LLMs (optional)
#   export ASI1_API_KEY="..." ; export ASI1_MODEL="asi1-mini"
#   export ASI1_BASE_URL="https://api.asi1.ai/v1"
#   export FREE_LLM_BASE="http://localhost:11434"
#   export FREE_LLM_MODEL="deepseek-coder:6.7b"
#
#   # Local run (optional; if not hosted by platform)
#   export RUN_LOCAL="1"
#
# Behavior:
#   Bubble 1 = one-line fix/summary + "Solution: <2–3 sentences>"
#   Bubble 2 = RAW brainrot quip
#
# NOTE: No tunnels (Cloudflare/ngrok). Pure Gist polling for pull-requests mode.

from datetime import datetime
from uuid import uuid4
import os, json, re, random, textwrap, time
from typing import List, Dict, Any, Optional

# -------------------- Core deps --------------------
try:
    import requests
except Exception:  # lightweight bootstrap
    import sys, subprocess as _sp
    _sp.run([sys.executable, "-m", "pip", "install", "--quiet", "requests>=2.31"], check=False)
    import requests

from uagents import Context, Protocol, Agent
from uagents_core.contrib.protocols.chat import (
    ChatAcknowledgement,
    ChatMessage,
    EndSessionContent,
    TextContent,
    chat_protocol_spec,
)

# -------------------- Toggles --------------------
BRAINROT = str(os.getenv("BRAINROT", "1")).strip().lower() not in ("0", "false", "no", "off")

# -------------------- LLMs: ASI (optional) + Ollama fallback --------------------
def _mask_tail(s: str, n: int = 6) -> str:
    return ("*" * max(0, len(s) - n)) + s[-n:] if s else ""

ASI1_API_KEY  = (os.getenv("ASI1_API_KEY") or "").strip()
ASI1_MODEL    = (os.getenv("ASI1_MODEL") or "asi1-mini").strip()
ASI1_BASE_URL = ((os.getenv("ASI1_BASE_URL") or "https://api.asi1.ai/v1").strip().rstrip("/"))

_ASI_BASES = list(dict.fromkeys([ASI1_BASE_URL, ASI1_BASE_URL.rstrip("/v1"), "https://api.asi1.ai/v1", "https://api.asi1.ai"]))
_ASI_HEADERS = [
    lambda key: {"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    lambda key: {"X-API-Key": key, "Content-Type": "application/json"},
    lambda key: {"x-api-key": key, "Content-Type": "application/json"},
]
LLM_OK = False

def _asi_http(method: str, base: str, path: str, headers: dict, json_payload: Optional[dict] = None, timeout: int = 20):
    url = f"{base.rstrip('/')}/{path.lstrip('/')}"
    try:
        r = requests.request(method=method.upper(), url=url, headers=headers, json=json_payload, timeout=timeout)
        ok = 200 <= r.status_code < 300
        if not ok:
            print(f"[ASI] {method} {url} -> {r.status_code} body={r.text[:250]!r}")
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
    payload = {"model": ASI1_MODEL, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
               "max_tokens": max_tokens, "temperature": temperature}
    data = _asi_try(payload)
    try:
        return (data["choices"][0]["message"]["content"] or "").strip() if data else None
    except Exception:
        return None

FREE_LLM_BASE  = (os.getenv("FREE_LLM_BASE") or "").strip().rstrip("/")
FREE_LLM_MODEL = (os.getenv("FREE_LLM_MODEL") or "").strip()

def _free_llm_chat_once(system: str, user: str, max_tokens: int = 160, temperature: float = 0.2) -> Optional[str]:
    if not (FREE_LLM_BASE and FREE_LLM_MODEL):
        return None
    url = f"{FREE_LLM_BASE}/api/generate"
    prompt = textwrap.dedent(f"""[SYSTEM]
{system}

[USER]
{user}""").strip()
    payload = {"model": FREE_LLM_MODEL, "prompt": prompt, "temperature": temperature, "num_predict": max_tokens, "stream": False}
    try:
        r = requests.post(url, json=payload, timeout=20)
        if r.status_code != 200: return None
        data = r.json()
        return (data.get("response") or "").strip() or None
    except Exception:
        return None

# -------------------- Findings + quips --------------------
def _extract_findings(log_text: str, lang_hint: Optional[str]) -> List[Dict[str, Any]]:
    FILE_LINE_PATTERNS = [
        re.compile(r'^(?P<file>[^:\n]+):(?P<line>\d+)(?::\d+)?:\s*error:\s*(?P<msg>.+)$', re.MULTILINE),  # gcc/clang col optional
        re.compile(r'File "(?P<file>[^"]+)", line (?P<line>\d+).*\n(?P<msg>[\s\S]*?)(?:\n\s*\w.*:|\Z)', re.MULTILINE),  # Python
        re.compile(r'(?P<file>[^:\n]+)\((?P<line>\d+),\d+\):\s*error\s+TS\d+:\s*(?P<msg>.+)$', re.MULTILINE),           # tsc
    ]
    findings: List[Dict[str, Any]] = []
    for pat in FILE_LINE_PATTERNS:
        for m in pat.finditer(log_text or ""):
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
        for line in (log_text or "").splitlines()[-300:]:
            low = line.lower()
            if "error" in low or "assertionerror" in low or "failed" in low:
                findings.append({"file": "(unknown)", "line": 1, "msg": line.strip()})
                if len(findings) >= 3:
                    break
    return findings

def _guess_lang_from_findings(findings: List[Dict[str, Any]]) -> Optional[str]:
    for f in findings:
        fname = (f.get("file") or "").lower()
        if fname.endswith((".cpp", ".cc", ".cxx", ".hpp", ".h", ".c")): return "cpp"
        if fname.endswith((".py",)): return "python"
        if fname.endswith((".ts", ".tsx", ".js", ".jsx")): return "typescript"
    return None

QUIPS_BRAINROT = {
    "cpp": [
        "Ohio code moment: missing return cooked the build.",
        "6-7… and the function dipped before returning. Skibidi no return.",
        "Control path NPC’d out. Add a return, blud.",
        "Cooked path. Low taper fade your logic and return something, fr.",
    ],
    "python": [
        "Traceback doing doom scrolling. Add the check, keep it sigma.",
        "Cringe None bug. Did you pray today? Add a guard.",
        "Sussy exception arc — tighten that branch, biggest bird style.",
    ],
    "typescript": [
        "Types won’t glaze themselves — narrow it, gigachad dev.",
        "TS said ‘string’, you gave ‘number’. Ohio assign, fr.",
        "Rizz up the types: refine that union, shmlawg.",
    ],
    "default": [
        "Cooked build. Not the mosquito again.",
        "Goofy ahh pipeline crashed out — apply the fix and mew on.",
        "Put the fries in the bag… and the return in the function.",
        "Goated with the sauce once tests pass, blud.",
        "Skibidi", "Sigma", "Chad", "Gigachad", "Fine Shyt – work hard",
    ],
    "italian": [
        "Ballerina Cappuccina pirouetted away with your semicolons.",
        "Bombombini Gusini says add that return, fr.",
        "Tralalero Tralala stomping the bug with Nike on.",
        "Chimpanzini Bananini approved the patch diff.",
    ],
    "basic": ["Negative Aura"],
}

def _brainrot_quip_raw(lang: Optional[str], basic: bool = False) -> str:
    if basic:
        return random.choice(QUIPS_BRAINROT["basic"])
    pool = QUIPS_BRAINROT.get((lang or "").lower(), QUIPS_BRAINROT["default"])
    picks = pool + (QUIPS_BRAINROT["italian"] if random.random() < 0.25 else [])
    return random.choice(picks)

# -------------------- Micro-fix + "Solution:" --------------------
def _llm_one_liner(findings: List[Dict[str, Any]]) -> Optional[str]:
    if not findings:
        return None
    f0 = findings[0]

    if BRAINROT:
        system = (
            "You are a chaotic Gen Z code reviewer using playful brain-rot slang, "
            "but you still give the exact fix. Respond in 1 short line max.\n"
            "Format strictly: [emoji roast] + exact minimal fix.\n"
            "No preface, no extra sentences, no markdown code fences. Keep it ~20–40 words."
        )
        user = (
            f"Error: {f0.get('msg')}\n"
            f"Location: {f0.get('file')}:{f0.get('line')}\n"
            "Return: one line that roasts lightly and then states the exact minimal change (e.g., add a guard, import, return)."
        )
    else:
        system = "Return a single line of code or a minimal statement that fixes the error. No preface, no prose."
        user = (
            "Suggest a single-line code change for this error. "
            "Return ONLY the code change line or the minimal statement (no explanations).\n"
            f"Error: {f0.get('msg')}\n"
            f"File:  {f0.get('file')}:{f0.get('line')}\n"
            "If a one-liner isn't possible, return: Add a failing test for this case first."
        )

    reply = None
    try:
        if ASI1_API_KEY:
            reply = _asi_chat_once(system=system, user=user, max_tokens=70, temperature=0.25)
    except Exception:
        reply = None
    if not reply:
        reply = _free_llm_chat_once(system=system, user=user, max_tokens=80, temperature=0.25)

    return reply.strip() if reply else None

def _llm_solution_text(findings: List[Dict[str, Any]], lang: Optional[str]) -> Optional[str]:
    if not findings:
        return None
    f0 = findings[0]

    if BRAINROT:
        system = (
            "You are a Gen Z brain-rot code reviewer (playful, not mean). "
            "Return 1–2 short sentences total: tiny roast + clear technical fix & why it works. "
            "Use emojis like 💀🔥😭 sparingly. No markdown code fences."
        )
        user = (
            f"Language: {lang or 'unknown'}\n"
            f"First error: {f0.get('msg')}\n"
            "Write concise roast + exact fix + why. <= 45 words."
        )
    else:
        system = "Explain fixes to software errors concisely. Respond in 2–3 short sentences. No preface."
        user = (
            f"Language: {lang or 'unknown'}\n"
            f"First error message: {f0.get('msg')}\n"
            "Explain the minimal change and why it works. Keep it concrete and brief."
        )

    reply = None
    try:
        if ASI1_API_KEY:
            reply = _asi_chat_once(system=system, user=user, max_tokens=110, temperature=0.25)
    except Exception:
        reply = None
    if not reply:
        reply = _free_llm_chat_once(system=system, user=user, max_tokens=120, temperature=0.25)

    if reply:
        import re as _re
        text = _re.sub(r"\s+", " ", reply.strip())
        return text[:350] + ("…" if len(text) > 350 else "")
    return None

def _concise_fix_or_summary(findings: List[Dict[str, Any]], lang: Optional[str]) -> str:
    # Try LLM brain-rot one-liner first
    fix = _llm_one_liner(findings)
    if fix:
        f0 = findings[0]
        loc = f"{f0.get('file')}:{f0.get('line')}" if f0.get('file') and f0.get('line') else ""
        if loc and not fix.lower().startswith(loc.lower()):
            return f"{loc} — {fix}"
        return fix

    # Heuristic roast + tip if LLMs unavailable
    f0 = findings[0] if findings else {}
    msg = (f0.get('msg') or '').lower()
    loc = f"{f0.get('file')}:{f0.get('line')}" if f0.get('file') and f0.get('line') else ""

    if "zerodivisionerror" in msg or "division by zero" in msg:
        roast = "💀 math said ‘nah’ fr fr —"
        tip = "add a divisor==0 guard before the divide."
    elif "control reaches end of non-void" in msg:
        roast = "😭 function ghosted the return —"
        tip = "ensure every path returns a value."
    elif "undefined reference" in msg or "ld returned 1 exit" in msg or "linker" in msg:
        roast = "🤡 linker cooked —"
        tip = "link missing obj/lib or implement the symbol."
    elif "modulenotfounderror" in msg:
        roast = "📦 not installed, bestie —"
        tip = "pip install it / add to requirements.txt."
    elif "cannot import name" in msg:
        roast = "🔀 import beef —"
        tip = "use the moved symbol or pin compatible versions."
    elif "nonetype" in msg or ("attributeerror" in msg and "none" in msg):
        roast = "💀 null vibes —"
        tip = "guard for None or ensure the factory returns an instance."
    elif "not defined" in msg or "nameerror" in msg:
        roast = "🧠 forgot the symbol —"
        tip = "define/import it before use."
    elif "type mismatch" in msg or "cannot convert" in msg or "typeerror" in msg:
        roast = "🎭 types beefing —"
        tip = "cast/adjust signature or fix callsite type."
    elif "assert" in msg or "failed" in msg:
        roast = "🔥 assertion said ‘cap’ —"
        tip = "fix the logic or update precondition."
    else:
        roast = "🧩 first error is the boss —"
        tip = "fix the top one; others will fall in line."

    line = f"{roast} {tip}"
    return f"{loc} — {line}" if loc else line


# -------------------- Intent detectors --------------------
_GREET_PAT = re.compile(r'\b(hi|hello|hey|yo|sup|hola|namaste|what\'?s up|gm|good (morning|afternoon|evening))\b', re.I)
_PRAISE_PAT = re.compile(r'\b(thanks|thank you|ty|appreciate|you(\s*are|\'re)? (good|great|amazing|awesome|goat|goated))\b', re.I)
_SUCCESS_PAT = re.compile(r'^\s*(return\s+[01]\s*;?\s*$|gg\s*king$|build\s+passed$|ok\s*$|fixed\s*$|works\s*!*)', re.I)
def _looks_like_success(text: str) -> bool:
    return bool(_SUCCESS_PAT.search(text or ""))

# -------------------- Chat protocol (EXACTLY TWO BUBBLES) --------------------
chat_proto = Protocol(spec=chat_protocol_spec)

def _collect_text(msg: ChatMessage) -> str:
    if isinstance(msg.content, list):
        return "".join(c.text for c in msg.content if isinstance(c, TextContent) and hasattr(c, "text"))
    return getattr(msg.content, "text", str(msg.content))

async def _send_two_bubbles(
    ctx: Context,
    sender: str,
    line1: str,
    line2: str,
    extra_lines_bubble1: Optional[List[str]] = None,
):
    # Bubble #1
    c1: List[Any] = [TextContent(type="text", text=line1)]

    if extra_lines_bubble1:
        for t in extra_lines_bubble1:
            if t:
                c1.append(TextContent(type="text", text=t))

    await ctx.send(sender, ChatMessage(timestamp=datetime.utcnow(), msg_id=str(uuid4()), content=c1))

    # Bubble #2 (RAW quip only)
    if line2:
        c2 = [TextContent(type="text", text=line2)]
        await ctx.send(sender, ChatMessage(timestamp=datetime.utcnow(), msg_id=str(uuid4()), content=c2))

@chat_proto.on_message(ChatMessage)
async def on_chat_or_review(ctx: Context, sender: str, msg: ChatMessage):
    text = _collect_text(msg).strip()
    if not text:
        return

    if _looks_like_success(text):
        line1 = "GG KING"
        line2 = _brainrot_quip_raw(None)
        await _send_two_bubbles(ctx, sender, line1, line2)
        await ctx.send(sender, ChatAcknowledgement(timestamp=datetime.utcnow(), acknowledged_msg_id=msg.msg_id))
        return

    if _PRAISE_PAT.search(text):
        line1 = "u be glazing me brahh"
        line2 = _brainrot_quip_raw(None)
        await _send_two_bubbles(ctx, sender, line1, line2)
        await ctx.send(sender, ChatAcknowledgement(timestamp=datetime.utcnow(), acknowledged_msg_id=msg.msg_id))
        return

    # JSON path: {"context": {...}, "log_tail": "..."} OR {"text": "..."}
    data = None
    try:
        data = json.loads(text)
    except Exception:
        data = None

    if isinstance(data, dict) and ("log_tail" in data or "text" in data):
        lang = ((data.get("context") or {}).get("lang")) or None
        raw_payload = (data.get("log_tail") or "").strip()
        free_text   = (data.get("text") or "").strip()

        if raw_payload:
            findings = _extract_findings(raw_payload, lang)
            lang = lang or _guess_lang_from_findings(findings)
            if findings:
                line1 = _concise_fix_or_summary(findings, lang)
                basic = (findings and (findings[0].get("file") == "(unknown)"))
                line2 = _brainrot_quip_raw(lang, basic=basic)
                solution = _llm_solution_text(findings, lang)
                extra = [f"Solution: {solution}"] if solution else None
                await _send_two_bubbles(ctx, sender, line1, line2, extra_lines_bubble1=extra)
            else:
                line1 = "shut up and provide the error"
                line2 = _brainrot_quip_raw(None)
                await _send_two_bubbles(ctx, sender, line1, line2)
            await ctx.send(sender, ChatAcknowledgement(timestamp=datetime.utcnow(), acknowledged_msg_id=msg.msg_id))
            return

        if free_text:
            text = free_text  # fall through below

    # Plain text triage
    findings = _extract_findings(text, None)
    if findings:
        lang = _guess_lang_from_findings(findings)
        line1 = _concise_fix_or_summary(findings, lang)
        basic = (findings and (findings[0].get('file') == "(unknown)"))
        line2 = _brainrot_quip_raw(lang, basic=basic)
        solution = _llm_solution_text(findings, lang)
        extra = [f"Solution: {solution}"] if solution else None
        await _send_two_bubbles(ctx, sender, line1, line2, extra_lines_bubble1=extra)
        await ctx.send(sender, ChatAcknowledgement(timestamp=datetime.utcnow(), acknowledged_msg_id=msg.msg_id))
        return

    # Greeting / anything else
    if _GREET_PAT.search(text):
        line1 = "shut up and provide the error"
        line2 = _brainrot_quip_raw(None)
        await _send_two_bubbles(ctx, sender, line1, line2)
        await ctx.send(sender, ChatAcknowledgement(timestamp=datetime.utcnow(), acknowledged_msg_id=msg.msg_id))
        return

    # Default persona
    line1 = "shut  up and provide the error"
    line2 = _brainrot_quip_raw(None)
    await _send_two_bubbles(ctx, sender, line1, line2)
    await ctx.send(sender, ChatAcknowledgement(timestamp=datetime.utcnow(), acknowledged_msg_id=msg.msg_id))

@chat_proto.on_message(ChatAcknowledgement)
async def on_ack(_ctx: Context, _sender: str, _msg: ChatAcknowledgement):
    return

# -------------------- Agent bootstrap --------------------
PORT = int(os.getenv("PORT", "8787"))
agent = Agent(name="Chaos Reviewer", port=PORT, endpoint=[f"http://0.0.0.0:{PORT}/submit"])

@agent.on_event("startup")
async def _auth_check(ctx: Context):
    global LLM_OK, ASI1_API_KEY, ASI1_MODEL, ASI1_BASE_URL

    key = os.getenv("ASI1_API_KEY", "").strip()
    ASI1_API_KEY = key  # ensure global used by _asi_try() is set now
    has_key = bool(key)

    ctx.logger.info(f"DEBUG env.ASI1_API_KEY present? {'yes' if has_key else 'no'} len={len(key)}")

    if not has_key:
        ctx.logger.info("ASI: no ASI1_API_KEY set. Heuristic-only / Ollama-only mode.")
        LLM_OK = False
        return

    ctx.logger.info(f"ASI key present (len={len(key)}) tail={_mask_tail(key)} | model={ASI1_MODEL}")
    pong = _asi_chat_once(system="Reply 'pong' only.", user="ping", max_tokens=4, temperature=0.0)
    LLM_OK = bool(pong)
    ctx.logger.info("ASI: chat ping OK ✅" if LLM_OK else "ASI: chat ping failed ❌ — using heuristics.")

@agent.on_interval(period=6.0)
async def _heartbeat(ctx: Context):
    ctx.logger.info("[CHAOS] heartbeat")

# -------------------- Gist helpers --------------------
def _gist_auth_headers() -> Dict[str, str]:
    token = os.getenv("GIST_TOKEN", "").strip()
    h = {"Accept": "application/vnd.github+json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h

def _gist_get_file(gist_id: str, filename: str) -> Optional[str]:
    try:
        r = requests.get(f"https://api.github.com/gists/{gist_id}",
                         headers=_gist_auth_headers(), timeout=8)
        r.raise_for_status()
        data = r.json()
        file = (data.get("files") or {}).get(filename)
        if not file:
            return None
        raw_url = file.get("raw_url")
        if raw_url:
            r2 = requests.get(raw_url, headers=_gist_auth_headers(), timeout=8)
            r2.raise_for_status()
            text = r2.text
            return text if len(text) <= 1_000_000 else text[-1_000_000:]
        return file.get("content")
    except Exception as e:
        print(f"[GIST] get_file error: {e}")
        return None

def _gist_put_file(gist_id: str, filename: str, content: str) -> bool:
    token = os.getenv("GIST_TOKEN", "").strip()
    if not token:
        print("[GIST] put_file aborted: missing GIST_TOKEN")
        return False
    try:
        r = requests.patch(
            f"https://api.github.com/gists/{gist_id}",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
            json={"files": {filename: {"content": content}}},
            timeout=10,
        )
        r.raise_for_status()
        return True
    except Exception as e:
        print(f"[GIST] put_file error: {e}")
        return False

_last_seen_ts: Dict[str, int] = {"ts": 0}

# -------------------- Gist pull bridge --------------------
@agent.on_interval(period=6.0)  # cadence handled by decorator
async def _poll_gist_requests(ctx: Context):
    req_gid   = os.getenv("REQ_GIST_ID", "").strip()
    req_file  = os.getenv("REQ_FILE", "chaos-request.json")
    resp_file = os.getenv("RESP_FILE", "chaos-response.json")
    if not req_gid:
        return

    raw = _gist_get_file(req_gid, req_file)
    if not raw:
        return

    try:
        data = json.loads(raw)
    except Exception:
        return

    ts = int(data.get("ts") or 0)
    if ts <= _last_seen_ts["ts"]:
        return  # nothing new

    _last_seen_ts["ts"] = ts
    lang = ((data.get("context") or {}).get("lang")) or None
    log_tail = (data.get("log_tail") or "").strip()
    text_alt = (data.get("text") or "").strip()

    if log_tail:
        findings = _extract_findings(log_tail, lang)
        lang = lang or _guess_lang_from_findings(findings)
        if findings:
            line1 = _concise_fix_or_summary(findings, lang)
            basic = findings[0].get("file") == "(unknown)"
            line2 = _brainrot_quip_raw(lang, basic=basic)
            sol = _llm_solution_text(findings, lang)
            payload = {
                "ts": int(time.time()),
                "lines": [f"{line1}\nSolution: {sol}" if sol else line1, line2],
            }
        else:
            payload = {
                "ts": int(time.time()),
                "lines": ["shut up and provide the error", _brainrot_quip_raw(None)],
            }
    else:
        # Free-text path (greetings/persona)
        txt = text_alt or ""
        if _GREET_PAT.search(txt):
            payload = {"ts": int(time.time()), "lines": ["shut up and provide the error", _brainrot_quip_raw(None)]}
        else:
            findings = _extract_findings(txt, None)
            if findings:
                lang = _guess_lang_from_findings(findings)
                line1 = _concise_fix_or_summary(findings, lang)
                sol = _llm_solution_text(findings, lang)
                payload = {"ts": int(time.time()), "lines": [f"{line1}\nSolution: {sol}" if sol else line1, _brainrot_quip_raw(lang)]}
            else:
                payload = {"ts": int(time.time()), "lines": ["shut up and provide the error", _brainrot_quip_raw(None)]}

    ok = _gist_put_file(req_gid, resp_file, json.dumps(payload, ensure_ascii=False, indent=0))
    if ok:
        ctx.logger.info("[CHAOS] responded via Gist bridge")

# -------------------- Register protocol --------------------
agent.include(chat_proto, publish_manifest=True)

# -------------------- Local run (only if not hosted) --------------------
if str(os.getenv("RUN_LOCAL", "0")).strip().lower() in ("1", "true", "yes", "on"):
    agent.run()
