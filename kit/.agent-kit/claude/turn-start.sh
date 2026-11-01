#!/usr/bin/env bash
# UserPromptSubmit hook: ターン開始時のリポジトリの指紋を記録する(何も出力しない)。
# 指紋は HEAD・変更済みファイルの内容・未追跡ファイルの内容から作るので、
# すでに変更済みのファイルへの追加編集も Stop hook で検出できる。
set -u
[ "${CLAUDE_CODE_REMOTE:-}" = "true" ] || [ "${AGENT_HOOKS_LOCAL:-0}" = "1" ] || exit 0
ROOT="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
[ -d "$ROOT/.agent-kit" ] || exit 0
export AGENT_REPO_ROOT="$ROOT"
. "$ROOT/.agent-kit/lib/common.sh"
sid="$(python3 -c 'import json,sys
try: print(json.load(sys.stdin).get("session_id") or "default")
except Exception: print("default")' 2>/dev/null)"
sid="${sid//[^A-Za-z0-9_-]/_}"
printf '%s %s\n' "$(date +%s.%N)" "$(repo_fingerprint "$ROOT")" > "$(agent_state_dir "$ROOT")/turn-$sid"
exit 0
