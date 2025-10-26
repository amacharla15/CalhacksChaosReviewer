// extension.ts
import * as vscode from "vscode";
import * as fs from "fs";
import * as path from "path";

// If your VS Code runtime is Node <18, uncomment next line and add `undici`
// import { fetch as undiciFetch } from "undici";
// const fetchFn: typeof fetch = (globalThis as any).fetch ?? undiciFetch as any;
const fetchFn: typeof fetch = (globalThis as any).fetch;

export function activate(context: vscode.ExtensionContext) {
  const out = vscode.window.createOutputChannel("Chaos Reviewer");
  const cfg = () => vscode.workspace.getConfiguration();

  // --- tiny helper: fetch with timeout so we can fallback gracefully ---
  const withTimeout = <T>(p: Promise<T>, ms = 2500) =>
    Promise.race<T>([
      p,
      new Promise<T>((_, rej) => setTimeout(() => rej(new Error("timeout")), ms)) as unknown as Promise<T>,
    ]);

  // --- local quips + heuristics for offline fallback (agent down) ---
  const QUIPS: Record<string, string[]> = {
    cpp: [
      "Ohio code moment: missing return cooked the build.",
      "6-7… and the function dipped before returning. Skibidi no return.",
      "Control path NPC’d out. Add a return, blud.",
      "Cooked path. Low taper fade your logic and return something, fr.",
    ],
    python: [
      "Traceback doing doom scrolling. Add the check, keep it sigma.",
      "Cringe None bug. Did you pray today? Add a guard.",
      "Sussy exception arc — tighten that branch, biggest bird style.",
    ],
    typescript: [
      "Types won’t glaze themselves — narrow it, gigachad dev.",
      "TS said ‘string’, you gave ‘number’. Ohio assign, fr.",
      "Rizz up the types: refine that union, shmlawg.",
    ],
    default: [
      "Cooked build. Not the mosquito again.",
      "Goofy ahh pipeline crashed out — apply the fix and mew on.",
      "Put the fries in the bag… and the return in the function.",
      "Goated with the sauce once tests pass, blud.",
    ],
  };
  const pickQuip = (lang: string) => {
    const pool = QUIPS[lang] || QUIPS.default;
    return pool[Math.floor(Math.random() * pool.length)];
  };

  function heuristicFix(raw: string): string {
    const first = raw.split(/\r?\n/).find((ln) => /error|traceback|exception/i.test(ln)) || "";
    const low = first.toLowerCase();
    if (low.includes("control reaches end of non-void function")) return "add a return on all paths (e.g., return <value>;)";
    if (low.includes("zerodivisionerror")) return "guard divisor == 0 before dividing";
    if (low.includes("undefined reference") || low.includes("linker")) return "link the missing object/library or provide the definition";
    if (low.includes("nameerror") || low.includes("not defined")) return "define/import the symbol before use";
    if (low.includes("type mismatch") || low.includes("cannot convert") || low.includes("typeerror")) return "adjust the type/cast or the function signature";
    if (low.includes("modulenotfounderror")) return "install the missing module or add to requirements.txt";
    if (low.includes("cannot import name")) return "pin compatible versions; the symbol moved/changed";
    if (low.includes("attributeerror") && low.includes("nonetype")) return "guard for None or ensure factory returns an instance";
    if (low.includes("assert")) return "fix the logic or precondition to satisfy the assertion";
    return "fix the first reported error; later ones cascade";
  }

  async function sendTail() {
    const tailFile = cfg().get<string>("chaos.tailFile")!;
    const tailLines = cfg().get<number>("chaos.tailLines")!;
    const webhook  = cfg().get<string>("chaos.webhookUrl")!;
    const backup   = cfg().get<string>("chaos.backupWebhookUrl")!;
    const langCfg  = cfg().get<string>("chaos.lang")!;

    // Resolve tail file relative to workspace if not absolute
    const ws = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath || "";
    const tailPath = path.isAbsolute(tailFile) ? tailFile : path.join(ws, tailFile);

    // Read last N lines
    let raw = "";
    let logTail = "";
    try {
      raw = fs.readFileSync(tailPath, "utf8");
      const lines = raw.split(/\r?\n/);
      logTail = lines.slice(-tailLines).join("\n");
    } catch {
      // ignore missing file or read errors
    }

    // Infer language if set to auto
    const lang = (() => {
      if (langCfg !== "auto") return langCfg;
      const has = (p: string) => fs.existsSync(path.join(ws, p));
      if (/\bTS\d+:/m.test(logTail)) return "typescript";
      if (/File ".*\.py", line \d+/m.test(logTail)) return "python";
      if (has("tsconfig.json")) return "typescript";
      if (has("pyproject.toml") || has("requirements.txt")) return "python";
      if (has("CMakeLists.txt") || has("compile_commands.json")) return "cpp";
      return "cpp";
    })();

    const payload = logTail ? { context: { lang }, log_tail: logTail } : { text: "hi" };

    // Try online agent; then backup webhook; then heuristics
    let lines: string[] = [];
    async function tryPost(url?: string | null) {
      if (!url) return null;
      try {
        const res = await withTimeout(
          fetchFn(url, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
          }),
          2500
        );
        const data = await (res as Response).json();
        return (data?.lines as string[]) || null;
      } catch {
        return null;
      }
    }

    lines = (await tryPost(webhook)) || (await tryPost(backup)) || [];

    if (lines.length < 2) {
      const fix = logTail ? heuristicFix(logTail) : "paste a build/test error (or JSON with {context, log_tail})";
      lines = [fix, pickQuip(lang)];
    }

    // Only show when we actually have two lines
    if (lines.length >= 2) {
      out.appendLine(lines[0] ?? "");
      out.appendLine(lines[1] ?? "");
      out.show(true);

      const msg = `${lines[0]}\n${lines[1]}`;
      await vscode.window.withProgress(
        { location: vscode.ProgressLocation.Notification, title: "Chaos Reviewer" },
        async (progress) => {
          progress.report({ message: msg });
          await new Promise((r) => setTimeout(r, 4000));
        }
      );

      vscode.window.setStatusBarMessage("$(flame) Chaos: 2 lines ready", 4000);
    }
  }

  // --- Debounce wrapper to prevent duplicate emits ---
  let lastSent = 0;
  async function sendTailDebounced(ms = 1200) {
    const now = Date.now();
    if (now - lastSent < ms) return; // skip duplicate fires
    lastSent = now;
    await sendTail();
  }

  // Register command + task listener, both disposable
  context.subscriptions.push(
    vscode.commands.registerCommand("chaos.sendTail", () => sendTailDebounced())
  );
  const taskDisp = vscode.tasks.onDidEndTaskProcess(async () => { await sendTailDebounced(); });
  context.subscriptions.push(taskDisp);
}

export function deactivate() {}
