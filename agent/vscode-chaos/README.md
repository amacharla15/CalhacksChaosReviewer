# Chaos Reviewer
Two-line fix + brainrot roast after every build.

## Setup
1. Ensure your webhook is running at `http://localhost:8787/chaos`.
2. Tee your build output to `.vscode/chaos_tail.log`, e.g.:
   ```sh
   make 2>&1 | tee .vscode/chaos_tail.log; exit ${PIPESTATUS[0]}
cd /mnt/c/Users/akshi/Downloads/agent

# ensure folders exist
mkdir -p vscode-chaos/src vscode-chaos/media

# package.json (extension manifest)
cat > vscode-chaos/package.json <<'EOF'
{
  "name": "chaos-reviewer",
  "displayName": "Chaos Reviewer",
  "publisher": "your-publisher-id",
  "version": "0.0.1",
  "description": "Two-line fix + brainrot roast after every build.",
  "repository": "https://github.com/you/chaos-reviewer",
  "icon": "media/icon.png",
  "engines": { "vscode": "^1.75.0" },
  "categories": ["Other"],
  "keywords": ["build", "logs", "agent", "roast"],
  "activationEvents": ["onStartupFinished", "onCommand:chaos.sendTail"],
  "contributes": {
    "commands": [{ "command": "chaos.sendTail", "title": "Chaos: Send Build Log Tail" }],
    "configuration": {
      "title": "Chaos Reviewer",
      "properties": {
        "chaos.webhookUrl": { "type": "string", "default": "http://localhost:8787/chaos" },
        "chaos.tailFile":   { "type": "string", "default": ".vscode/chaos_tail.log" },
        "chaos.lang":       { "type": "string", "default": "auto", "enum": ["auto","cpp","python","typescript"] },
        "chaos.tailLines":  { "type": "number", "default": 200 }
      }
    }
  },
  "main": "./dist/extension.js",
  "scripts": {
    "compile": "tsc -p .",
    "watch": "tsc -w -p .",
    "package": "vsce package",
    "publish:patch": "vsce publish patch"
  },
  "devDependencies": {
    "typescript": "^5.4.0",
    "@types/node": "^20.0.0",
    "@types/vscode": "1.75.0",
    "vsce": "^3.0.0"
  }
}
