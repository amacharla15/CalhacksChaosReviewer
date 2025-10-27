Youtube Link: https://youtu.be/6zm-sZNBaZI?si=T544jo37gzvgoSuM

# Chaos Reviewer — A brain rot code REVIEWER
**Handle:** @chaos-reviewer  
**Category/Tags:** build-triage, compiler-errors, logs, chat-protocol, devtools, gcc, clang, pytest, typescript, cpp, tsc

---

## Overview
Chaos Reviewer turns noisy build/test logs into **two compact chat bubbles**:  
1) a one-line fix/summary with a short **Solution** (2–3 sentences), optionally with an image;  
2) a RAW “brainrot” quip for vibes.  

It auto-detects C++/GCC/Clang, Python/pytest, and TypeScript/tsc, extracts the **first actionable error**, and proposes a minimal change. Uses **ASI:One** if configured; otherwise can fall back to a local **Ollama** model. Implements the **Agent Chat Protocol** and acknowledges each message for a snappy UX.

---

## Capabilities
- Regex extraction for **gcc/clang**, **Python** tracebacks, and **tsc** diagnostics  
- One-liner fix + concise **Solution** rationale (2–3 sentences)  
- **Per-language image pools** (C++ / Python / Java) with round-robin selection  
- Robust image handling (stored resource when supported; **link fallback** otherwise)  
- Strict **two-bubble** output and **ChatAcknowledgement** compliance

---

## Inputs → Outputs
**Input options**
- Paste plain text from your latest build/test logs (last ~300 lines recommended), **or**
- Send JSON (examples):

```json
{
  "context": { "lang": "cpp" },
  "log_tail": "main.cpp:42:5: error: control reaches end of non-void function\n   int foo() {\n       if (x) return 1;\n       // missing return\n   }\n"
}
```

```json
{
  "context": { "lang": "python" },
  "log_tail": "Traceback (most recent call last):\n  File \"app.py\", line 18, in <module>\n    print(1/zero)\nNameError: name 'zero' is not defined\n"
}
```

```json
{
  "context": { "lang": "typescript" },
  "log_tail": "src/index.ts(12,15): error TS2322: Type 'number' is not assignable to type 'string'.\n"
}
```

**Output**
- **Bubble 1:** `file:line — <one-line fix>` + `Solution: <2–3 short sentences>` (+ optional image)  
- **Bubble 2:** RAW quip

---

## How to Use
1. Say “hi” to check reachability (you’ll hit the greeting path).  
2. Paste failing compiler/test output (gcc/clang/pytest/tsc) or use the JSON format above.  
3. Apply the one-line fix or follow the Solution tip; re-run your build/tests.  
4. Repeat with fresh log tails until green.

---

## Configuration (env)
Set only what you need; all values are optional.

```bash
# Minimal: greeting image only
export GREET_IMAGE_URL="https://your.app/sybau.jpg"

# Per-language pools (round-robin)
export IMG_CPP_URLS="https://your.app/cpp/cpp1.jpg,https://your.app/cpp/cpp2.jpg"
export IMG_PY_URLS="https://your.app/python/py.jpg,https://your.app/python/py2.webp"
export IMG_JAVA_URLS="https://your.app/java/java1.jpg,https://your.app/java/java2.png"
export IMG_PROB=1.0

# ASI:One (preferred) or Ollama (fallback)
export ASI1_API_KEY="***"; export ASI1_MODEL="asi1-mini"
# or
export FREE_LLM_BASE="http://localhost:11434"; export FREE_LLM_MODEL="deepseek-coder:6.7b"
```

---

## Protocols
**Agent Chat Protocol v0.3.0**

**ChatMessage**
- `content`: array  
- `msg_id`: string  
- `timestamp`: string

**ChatAcknowledgement**
- `acknowledged_msg_id`: string  
- `metadata`: object  
- `timestamp`: string

---

## Use Cases
- CI red builds: paste the last 100–300 lines to get one actionable fix.  
- Local dev loops: send pytest/tsc errors and iterate faster.  
- On-call triage: compress noisy compiler output into a single, actionable line.

---

## Troubleshooting
- **No reply to “hi”?** Ensure the agent is **Active + Hosted** and redeploy after README edits.  
- **Images not rendering?** URLs must return **200** and be **jpg/png/webp**; some hosts only show links if resource storage isn’t supported.  
- **Empty fix?** Provide more context (last **100–300 lines**) or include `{"context":{"lang":"..."}}`.  
- **Still 4/5 discoverability?** Keep **Overview** concise, include at least one **JSON example**, and ensure **Protocols** appears **once**.

---

## Limitations
- Focuses on the first actionable error; later errors may be cascades.  
- Falls back to heuristics if no LLM is reachable.  
- Image may post as a link if resource storage isn’t supported.  
- Not a full static analyzer; it’s a fast triage assistant.

---

## Privacy & Safety
- Paste only logs you’re comfortable sharing (paths/usernames may appear).  
- No persistent log storage beyond the session; no PII required.  
- You control outbound model usage via env vars.

---

## License
MIT License — you can copy, modify, and distribute with attribution.

---

## Contact
Questions / feedback: open an issue on your repo or ping **@chaos-reviewer** in Agentverse.

---

## Acknowledgments
- Fetch.ai **Agent Chat Protocol**  
- GCC/Clang, Python/pytest, TypeScript/tsc ecosystems  
- Community contributors who tested CI flows

---

## Keywords
build logs, compiler errors, gcc, clang, pytest, tsc, typescript, cpp, ci/cd, chat protocol, ollama, asi-one

---

### AgentChatProtocol
This agent speaks **Agent Chat Protocol v0.3.0**. It emits **exactly two bubbles per interaction**:  
1) a `ChatMessage` with one-line fix + `Solution:` text (and optional image), then  
2) a `ChatMessage` with a RAW quip, followed by a **`ChatAcknowledgement`** referencing the incoming message ID.  
The protocol fields used are minimal: `content[]`, `msg_id`, `timestamp` on `ChatMessage`, and `acknowledged_msg_id` (+ optional `metadata`, `timestamp`) on `ChatAcknowledgement`. This keeps UX snappy and makes the agent compatible with Agentverse chat surfaces.
