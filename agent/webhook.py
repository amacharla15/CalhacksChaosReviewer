# webhook.py — smart fallback that ACTS like your agent (two lines: fix + raw brainrot)
from fastapi import FastAPI
from pydantic import BaseModel
import os, random, re

# If you later have a real agent webhook, set this env and forward instead of local logic.
AGENT_WEBHOOK = os.getenv("CHAOS_AGENT_WEBHOOK", "").strip()

app = FastAPI()

class Payload(BaseModel):
    context: dict | None = None
    log_tail: str | None = None
    text: str | None = None

# ---------- exact, raw brainrot pools (no edits)
QUIPS = {
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
        "Skibidi",
        "Sigma",
        "Chad",
        "Gigachad",
        "Fine Shyt – work hard",
    ],
    "basic": [
        "Negative Aura – A made-up term used to describe someone with a bad vibe or uncool energy. When someone loses so many aura points that they’re put in the negative, then they have negative aura."
    ],
}
ITALIAN = [
    "Ballerina Cappuccina pirouetted away with your semicolons.",
    "Bombombini Gusini says add that return, fr.",
    "Tralalero Tralala stomping the bug with Nike on.",
    "Chimpanzini Bananini approved the patch diff.",
]

GREETING_LINE = "shut your b*tch *ss up and provide the error"
PRAISE_LINE   = "u be glazing me brahh"

FILELINE_PATS = [
    re.compile(r'^(?P<file>[^:\n]+):(?P<line>\d+):\d*:?\s*error:\s*(?P<msg>.+)$', re.MULTILINE),  # gcc/clang
    re.compile(r'(?P<file>[^:\n]+)\((?P<line>\d+),\d+\):\s*error\s+TS\d+:\s*(?P<msg>.+)$', re.MULTILINE), # tsc
    re.compile(r'File "([^"]+)", line (\d+),.*\n(?P<msg>[\s\S]*?)(?:\n\s*\w.*:|\Z)', re.MULTILINE),       # python tb (msg only)
]

def detect_lang(lang_hint: str | None, text: str) -> str:
    if lang_hint in ("cpp","python","typescript"): return lang_hint
    t = text.lower()
    if ".ts" in t or "tsc" in t: return "typescript"
    if ".py" in t or "traceback" in t: return "python"
    return "cpp"

def first_finding(text: str):
    for pat in FILELINE_PATS:
        m = pat.search(text or "")
        if m and m.groupdict().get("msg"):
            g = m.groupdict()
            file_ = (g.get("file") or "(unknown)").strip()
            line_ = int(g.get("line") or 1)
            msg_  = re.sub(r"\s+", " ", (g.get("msg") or "").strip())
            return file_, line_, msg_
    # fallback: any line with "error"
    for ln in (text or "").splitlines()[-200:]:
        if "error" in ln.lower() or "failed" in ln.lower():
            return "(unknown)", 1, ln.strip()
    return None

def fix_line(find):
    if not find:
        return GREETING_LINE
    file_, line_, msg = find
    msg_l = msg.lower()
    loc = f"{file_}:{line_}"
    if "control reaches end of non-void function" in msg_l:
        tip = "add a return on all paths (e.g., return <value>;)"
    elif "undefined reference" in msg_l or "ld returned 1 exit status" in msg_l or "linker" in msg_l:
        tip = "link missing object/library or provide the definition"
    elif "not defined" in msg_l or "nameerror" in msg_l:
        tip = "define/import the symbol before use"
    elif "type 'number' is not assignable to type 'string'" in msg_l or "type mismatch" in msg_l or "cannot convert" in msg_l:
        tip = "adjust the type/cast or the function signature"
    elif "assert" in msg_l or "failed" in msg_l:
        tip = "update precondition or fix logic to satisfy the assertion"
    else:
        tip = "fix the first reported error; later ones cascade"
    return f"{loc} — {tip}"

def quip(lang: str, basic: bool, spice: float = 0.25) -> str:
    if basic:
        return random.choice(QUIPS["basic"])
    pool = QUIPS.get(lang, QUIPS["default"])
    if random.random() < spice:
        pool = pool + ITALIAN
    return random.choice(pool)

@app.post("/chaos")
async def chaos(p: Payload):
    # Praise or greeting only?
    text = (p.text or "").strip().lower()
    if text:
        if re.search(r'\b(thanks|thank\s*you|ty|appreciate|you(\s*are|\'re)? (good|great|amazing|awesome|goat|goated))\b', text):
            return {"lines": [PRAISE_LINE, quip("default", basic=False)]}
        if re.search(r'\b(hi|hello|hey|yo|sup|gm|good (morning|afternoon|evening)|what\'?s up)\b', text):
            return {"lines": [GREETING_LINE, quip("default", basic=False)]}
        # anything else non-error → treat as greeting path
        return {"lines": [GREETING_LINE, quip("default", basic=False)]}

    lang = detect_lang((p.context or {}).get("lang") if p.context else None, p.log_tail or "")
    finding = first_finding(p.log_tail or "")
    line1 = fix_line(finding)
    basic = finding is not None and finding[0] == "(unknown)"
    line2 = quip(lang, basic=basic)
    return {"lines": [line1, line2]}
