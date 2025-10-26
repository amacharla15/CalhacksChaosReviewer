import * as vscode from "vscode";
import * as fs from "fs";
import * as path from "path";
import * as https from "https";
import * as http from "http";

function postJSON(urlStr: string, payload: any, timeoutMs = 5000): Promise<any> {
  return new Promise((resolve, reject) => {
    try {
      const u = new URL(urlStr);
      const body = Buffer.from(JSON.stringify(payload));
      const opts: https.RequestOptions = {
        method: "POST",
        hostname: u.hostname,
        port: u.port || (u.protocol === "https:" ? "443" : "80"),
        path: u.pathname + u.search,
        headers: {
          "Content-Type": "application/json",
          "Content-Length": body.length
        }
      };
      const client = u.protocol === "https:" ? https : http;
      const req = client.request(opts, (res) => {
        const chunks: Buffer[] = [];
        res.on("data", (d) => chunks.push(d));
        res.on("end", () => {
          const txt = Buffer.concat(chunks).toString("utf8");
          try { resolve(JSON.parse(txt)); } catch { resolve({ raw: txt }); }
        });
      });
      req.setTimeout(timeoutMs, () => { req.destroy(new Error("timeout")); });
      req.on("error", reject);
      req.write(body);
      req.end();
    } catch (e) { reject(e); }
  });
}

export function activate(context: vscode.ExtensionContext) {
  const out = vscode.window.createOutputChannel("Chaos Reviewer");
  const cfg = () => vscode.workspace.getConfiguration();

  async function sendTail() {
    const tailFile = cfg().get<string>("chaos.tailFile")!;
    const tailLines = cfg().get<number>("chaos.tailLines")!;
    const webhook  = cfg().get<string>("chaos.webhookUrl")!;
    const langCfg  = cfg().get<string>("chaos.lang")!;

    // read tail
    let logTail = "";
    try {
      const ws = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath || "";
      const full = path.isAbsolute(tailFile) ? tailFile : path.join(ws, tailFile);
      const raw = fs.readFileSync(full, "utf8");
      const lines = raw.split(/\r?\n/);
      logTail = lines.slice(-tailLines).join("\n");
    } catch { /* ignore if file missing */ }

    // infer lang if auto
    const lang = (() => {
      if (langCfg !== "auto") return langCfg;
      const ws = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath || "";
      const has = (p: string) => fs.existsSync(path.join(ws, p));
      if (has("tsconfig.json")) return "typescript";
      if (has("pyproject.toml") || has("requirements.txt")) return "python";
      if (has("CMakeLists.txt") || has("compile_commands.json")) return "cpp";
      return "cpp";
    })();

    const payload = logTail ? { context: { lang }, log_tail: logTail } : { text: "hi" };

    try {
      const data = await postJSON(webhook, payload, 8000);
      const lines: string[] = (data && data.lines) || [];
      if (lines[0]) out.appendLine(lines[0]);
      if (lines[1]) out.appendLine(lines[1]);
      out.show(true);
    } catch (err: any) {
      vscode.window.showErrorMessage(`Chaos Reviewer: ${err?.message || String(err)}`);
    }
  }

  context.subscriptions.push(vscode.commands.registerCommand("chaos.sendTail", sendTail));
  vscode.tasks.onDidEndTaskProcess(async () => { await sendTail(); });
}

export function deactivate() {}
