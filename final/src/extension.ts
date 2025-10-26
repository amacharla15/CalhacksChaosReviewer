// src/extension.ts
import * as vscode from "vscode";
import { createHash } from "crypto";

// ----------------------------- Types -----------------------------
type ChaosReq = {
  ts: number;
  context: { lang?: "cpp" | "python" | "typescript" } | null;
  log_tail: string;
  text: string;
};

type ChaosResp = {
  ts: number;
  lines: string[]; // [Bubble1, Bubble2]
};

// ----------------------------- Globals -----------------------------
let panel: vscode.WebviewPanel | undefined;
let output = vscode.window.createOutputChannel("Chaos Reviewer");

// Cache raw_url per filename to avoid hitting /gists/{id} every poll
const cachedRawUrls = new Map<string, string>();

// Rate-limit helpers (debounce + dedup of payload)
const MIN_PATCH_INTERVAL_MS_DEFAULT = 15000; // 15s between writes
let lastPatchAt = 0;
let lastRequestHash = ""; // exact payload hash (filename + full JSON)

// Content-level dedup & in-flight gate (ignores ts)
let inflightRequestTs: number | null = null;
let lastContentHash = ""; // hash of semantic content (lang + log_tail + text)

// ----------------------------- Activate -----------------------------
export function activate(context: vscode.ExtensionContext) {
  context.subscriptions.push(
    vscode.commands.registerCommand("chaosReviewer.sendSelectionAsLog", sendSelectionAsLog),
    vscode.commands.registerCommand("chaosReviewer.sendText", sendFreeText),
    vscode.commands.registerCommand("chaosReviewer.openPanel", openPanel)
  );
}
export function deactivate() {}

// ----------------------------- Config -----------------------------
function getConfig() {
  const cfg = vscode.workspace.getConfiguration("chaosReviewer");
  const gistToken = cfg.get<string>("gistToken") || "";
  const gistId = cfg.get<string>("gistId") || "";
  const requestFile = cfg.get<string>("requestFile") || "chaos-request.json";
  const responseFile = cfg.get<string>("responseFile") || "chaos-response.json";
  const pollSeconds = cfg.get<number>("pollSeconds") || 10; // bumped default
  const responseTimeoutSeconds = cfg.get<number>("responseTimeoutSeconds") || 60; // bumped default
  const minPatchIntervalSeconds =
    cfg.get<number>("minPatchIntervalSeconds") || MIN_PATCH_INTERVAL_MS_DEFAULT / 1000;

  if (!gistToken || !gistId) {
    vscode.window.showErrorMessage("Chaos Reviewer: Please set gistToken and gistId in settings.");
  }
  return {
    gistToken,
    gistId,
    requestFile,
    responseFile,
    pollSeconds,
    responseTimeoutSeconds,
    minPatchIntervalMs: Math.max(1000, Math.floor(minPatchIntervalSeconds * 1000)),
  };
}

// ------------------------ Lang inference -------------------------
function inferLangFromActiveEditor(): "cpp" | "python" | "typescript" | undefined {
  const ed = vscode.window.activeTextEditor;
  const langId = ed?.document.languageId;
  switch (langId) {
    case "cpp":
    case "c":
    case "c++":
      return "cpp";
    case "python":
      return "python";
    case "typescript":
    case "typescriptreact":
    case "javascript":
    case "javascriptreact":
      return "typescript";
    default:
      return undefined;
  }
}

// ----------------------------- Commands -----------------------------
async function sendSelectionAsLog() {
  const cfg = getConfig();
  if (!cfg.gistToken || !cfg.gistId) return;

  const editor = vscode.window.activeTextEditor;
  if (!editor) {
    const typed = await vscode.window.showInputBox({
      placeHolder: "No editor open. Paste error/output to send…",
    });
    if (!typed || !typed.trim()) {
      vscode.window.showWarningMessage("Nothing to send.");
      return;
    }
    await sendChaosReq(cfg, { log_tail: typed, text: "", lang: undefined });
    return;
  }

  // 1) Selection
  let text = editor.document.getText(editor.selection);

  // 2) Current line
  if (!text.trim()) {
    const line = editor.document.lineAt(editor.selection.active.line);
    text = line.text || "";
  }

  // 3) Whole file (last 5000 chars)
  if (!text.trim()) {
    const whole = editor.document.getText() || "";
    text = whole.length > 5000 ? whole.slice(-5000) : whole;
  }

  // 4) Prompt
  if (!text.trim()) {
    const typed = await vscode.window.showInputBox({
      placeHolder: "Selection is empty. Paste error/output to send…",
    });
    if (!typed || !typed.trim()) {
      vscode.window.showWarningMessage("Nothing to send.");
      return;
    }
    text = typed;
  }

  const lang = inferLangFromActiveEditor();
  await sendChaosReq(cfg, { log_tail: text, text: "", lang });
}

async function sendFreeText() {
  const cfg = getConfig();
  if (!cfg.gistToken || !cfg.gistId) return;

  const text = await vscode.window.showInputBox({
    placeHolder: "Type the message to send (e.g., 'hey', 'thanks', 'GG KING', or any text)",
  });
  if (text === undefined) return;

  await sendChaosReq(cfg, { log_tail: "", text: text || "", lang: undefined });
}

async function openPanel() {
  await ensurePanel();
}

// ----------------------------- Webview -----------------------------
async function ensurePanel() {
  if (panel) {
    panel.reveal();
    return;
  }
  panel = vscode.window.createWebviewPanel(
    "chaosReviewer",
    "Chaos Reviewer",
    vscode.ViewColumn.Beside,
    { enableScripts: true, retainContextWhenHidden: true }
  );
  panel.onDidDispose(() => {
    panel = undefined;
  });
  render(panel, { ts: 0, lines: ["Send something to the agent…", ""] });
}

function render(target: vscode.WebviewPanel, resp: ChaosResp) {
  const [bubble1, bubble2] = resp.lines ?? ["", ""];
  const html = `
  <!doctype html>
  <html>
  <head>
    <meta charset="utf-8" />
    <style>
      body { font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, sans-serif; margin: 0; padding: 16px; background: #0e0e10; color: #e5e5e5; }
      .wrap { display: flex; flex-direction: column; gap: 16px; max-width: 900px; margin: 0 auto; }
      .bubble { padding: 14px 16px; border-radius: 14px; box-shadow: 0 2px 20px rgba(0,0,0,.3); }
      .one { background: #1a1a1f; border: 1px solid #2a2a33; }
      .two { background: #121217; border: 1px dashed #2a2a33; }
      .title { font-weight: 600; letter-spacing: .3px; margin-bottom: 8px; opacity:.9 }
      pre, code { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace; white-space: pre-wrap; }
      a { color: #7aa2ff; text-decoration: none; }
      a:hover { text-decoration: underline; }
    </style>
  </head>
  <body>
    <div class="wrap">
      <div class="bubble one">
        <div class="title">Bubble #1 — FIX</div>
        <pre>${escapeHtml(bubble1 || "")}</pre>
      </div>
      <div class="bubble two">
        <div class="title">Bubble #2 — RAW quip</div>
        <pre>${escapeHtml(bubble2 || "")}</pre>
      </div>
    </div>
  </body>
  </html>
  `;
  target.webview.html = html;
}

// ----------------------------- HTTP/Gist -----------------------------
function sha256(s: string): string {
  return createHash("sha256").update(s).digest("hex");
}

// PATCH with debounce (min interval), payload dedup, and retry/backoff on 403
async function patchGistFile(
  cfg: ReturnType<typeof getConfig>,
  filename: string,
  content: string
) {
  const now = Date.now();
  const waitLeft = lastPatchAt + cfg.minPatchIntervalMs - now;
  if (waitLeft > 0) {
    await sleep(waitLeft);
  }

  // Payload-level dedup (exact same JSON and filename)
  const payloadHash = sha256(`${cfg.gistId}|${filename}|${content}`);
  if (payloadHash === lastRequestHash) {
    output.appendLine(`[REQ] Skipped PATCH (duplicate payload)`);
    return;
  }

  const url = `https://api.github.com/gists/${cfg.gistId}`;
  const body = { files: { [filename]: { content } } };

  let delayMs = 2000; // start 2s
  for (let attempt = 1; attempt <= 4; attempt++) {
    const res = await fetch(url, {
      method: "PATCH",
      headers: {
        Authorization: `Bearer ${cfg.gistToken}`,
        Accept: "application/vnd.github+json",
        "Content-Type": "application/json",
        "X-GitHub-Api-Version": "2022-11-28",
      },
      body: JSON.stringify(body),
    });

    if (res.ok) {
      lastPatchAt = Date.now();
      lastRequestHash = payloadHash;
      return;
    }

    if (res.status === 403) {
      const retryAfter = parseInt(res.headers.get("Retry-After") || "", 10);
      const waitMs = Number.isFinite(retryAfter) ? retryAfter * 1000 : delayMs;
      output.appendLine(`[REQ] PATCH 403, backing off ${waitMs}ms (attempt ${attempt}/4)`);
      await sleep(waitMs);
      delayMs = Math.min(delayMs * 2, 30000); // cap 30s
      if (attempt < 4) continue;
      const t = await safeText(res as any);
      throw new Error(`PATCH 403 after retries: ${t}`);
    }

    const t = await safeText(res as any);
    throw new Error(`PATCH ${res.status}: ${t}`);
  }
}

async function getRawUrl(gistId: string, token: string, filename: string): Promise<string> {
  const cached = cachedRawUrls.get(filename);
  if (cached) return cached;

  const url = `https://api.github.com/gists/${gistId}`;
  const res = await fetch(url, {
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
    },
  });
  if (!res.ok) throw new Error(`Gist get failed: ${res.status}`);
  const data: any = await res.json();
  const file = data.files?.[filename];
  if (!file) throw new Error(`File ${filename} not found in gist`);
  if (file.raw_url) {
    cachedRawUrls.set(filename, file.raw_url);
    return file.raw_url;
  }
  if (file.content) {
    // Fallback if raw_url missing
    return `data:application/json,${encodeURIComponent(file.content)}`;
  }
  throw new Error("raw_url unavailable");
}

async function readResponse(gistId: string, token: string, filename: string): Promise<ChaosResp | null> {
  const rawUrl = await getRawUrl(gistId, token, filename);
  // Bust caches by appending a timestamp
  const url = `${rawUrl}${rawUrl.includes("?") ? "&" : "?"}t=${Date.now()}`;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Read response failed: ${res.status}`);
  const text = await res.text();
  try {
    return JSON.parse(text) as ChaosResp;
  } catch {
    return null;
  }
}

// ----------------------------- Polling -----------------------------
function toMillis(t: number): number {
  return t < 1e12 ? t * 1000 : t; // treat small numbers as seconds
}

async function pollForResponseAndRender(
  cfg: ReturnType<typeof getConfig>,
  requestTs: number
) {
  const deadline = Date.now() + cfg.responseTimeoutSeconds * 1000;
  let lastShownTs = 0;

  while (Date.now() < deadline) {
    try {
      const resp = await readResponse(cfg.gistId, cfg.gistToken, cfg.responseFile);
      if (resp && resp.ts) {
        const respTsMs = toMillis(resp.ts);
        if (respTsMs >= requestTs && respTsMs !== lastShownTs) {
          output.appendLine(`[RESP] ts=${resp.ts} (ms=${respTsMs})`);
          if (panel) render(panel, resp);
          lastShownTs = respTsMs;
          return; // first matching/newer response only
        }
      }
    } catch (e: any) {
      output.appendLine(`[RESP] read error: ${e?.message || e}`);
    }
    await sleep(cfg.pollSeconds * 1000);
  }

  vscode.window.showWarningMessage("Chaos Reviewer: response timeout.");
}

// ----------------------------- Utils -----------------------------
function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, (m) =>
    m === "&" ? "&amp;" : m === "<" ? "&lt;" : m === ">" ? "&gt;" : m === '"' ? "&quot;" : "&#39;"
  );
}
function truncate(s: string, n: number) {
  return s.length <= n ? s : s.slice(0, n - 1) + "…";
}
function sleep(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
async function safeText(r: any) {
  try {
    return await r.text();
  } catch {
    return "";
  }
}

// --------------------- Common send helper (DRY) ---------------------
async function sendChaosReq(
  cfg: ReturnType<typeof getConfig>,
  args: { log_tail: string; text: string; lang: "cpp" | "python" | "typescript" | undefined }
) {
  // Content-only key (ignore ts)
  const contentKey = JSON.stringify({
    lang: args.lang ?? null,
    log_tail: args.log_tail || "",
    text: args.text || "",
  });
  const contentHash = sha256(contentKey);

  // Dedup on content (don’t resend same content)
  if (contentHash === lastContentHash) {
    output.appendLine(`[REQ] Skipped (same content as last send)`);
    await ensurePanel();
    await pollForResponseAndRender(cfg, inflightRequestTs ?? Date.now() - 1);
    return;
  }

  // Don’t spam if we’re still waiting for a response
  if (inflightRequestTs !== null) {
    output.appendLine(`[REQ] Skipped (waiting for response ts=${inflightRequestTs})`);
    vscode.window.showInformationMessage(
      "Chaos Reviewer: still waiting on the last response; try again in a few seconds."
    );
    return;
  }

  const tsNow = Date.now();
  const req: ChaosReq = {
    ts: tsNow,
    context: args.lang ? { lang: args.lang } : null,
    log_tail: args.log_tail,
    text: args.text,
  };

  try {
    inflightRequestTs = tsNow; // mark in-flight before PATCH
    await patchGistFile(cfg, cfg.requestFile, JSON.stringify(req));
    lastContentHash = contentHash;

    const kind = args.text ? "text" : args.lang ?? "unknown";
    const len = (args.log_tail || args.text).length;
    output.appendLine(`[REQ] Sent ${kind} ts=${req.ts} len=${len}`);

    await ensurePanel();
    await pollForResponseAndRender(cfg, req.ts);
  } catch (e: any) {
    vscode.window.showErrorMessage(`Send failed: ${e?.message || e}`);
  } finally {
    inflightRequestTs = null;
  }
}
