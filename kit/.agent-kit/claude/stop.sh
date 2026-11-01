#!/usr/bin/env bash
# Stop hook: ターンの終わりに作業ログを確かめて送る。
# - このターンでリポジトリが変わった(既存の変更ファイルへの追加編集を含む)のに記録が無ければ、
#   1回だけ止めて記録を促す(decision: block)。2回目(stop_hook_active=true)は止めない。
# - 記録があれば送信を試みる。送れなくても止めない(未送信として残る)。
# - 何も変えていないターンは何もしない。AGENT_LOG_ENFORCE=0(環境変数か config.env)で促しを無効化。
set -u
[ "${CLAUDE_CODE_REMOTE:-}" = "true" ] || [ "${AGENT_HOOKS_LOCAL:-0}" = "1" ] || exit 0
ROOT="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
KIT="$ROOT/.agent-kit"
[ -d "$KIT" ] || exit 0
export AGENT_REPO_ROOT="$ROOT"
. "$KIT/lib/common.sh"
load_config "$ROOT"
STATE="$(agent_state_dir "$ROOT")"

read -r sid active <<EOF
$(python3 -c 'import json,sys
try: j=json.load(sys.stdin)
except Exception: j={}
print(j.get("session_id") or "default", "1" if j.get("stop_hook_active") else "0")' 2>/dev/null)
EOF
sid="${sid//[^A-Za-z0-9_-]/_}"

start=""; fp0=""
[ -f "$STATE/turn-$sid" ] && read -r start fp0 < "$STATE/turn-$sid"
fp1="$(repo_fingerprint "$ROOT")"
changed=0
[ -n "$fp0" ] && [ "$fp0" != "$fp1" ] && changed=1

logged=0
if [ -n "$start" ]; then
  for d in "$STATE/outbox/pending" "$STATE/outbox/sent" "$ROOT/.agent-logs/pending" "$ROOT/.agent-logs/sent"; do
    [ -d "$d" ] || continue
    if [ -n "$(find "$d" -maxdepth 1 -name '*.md' -newermt "@$start" 2>/dev/null | head -1)" ]; then logged=1; break; fi
  done
fi

if [ "$changed" = 1 ] && [ "$logged" = 0 ] && [ "$active" = 0 ] && [ "${AGENT_LOG_ENFORCE:-1}" = 1 ]; then
  python3 -c 'import json; print(json.dumps({"decision": "block", "reason": "このターンでリポジトリに変更があったが、作業ログが未記録です。skill cloud-worklog の手順で python3 .agent-kit/bin/agent-log turn を1回実行してから終えてください(目的・実施内容・テスト結果・失敗・未完了を短く。秘密情報は書かない)。"}, ensure_ascii=False))'
  exit 0
fi

AGENT_SERVICE=claude timeout 25 python3 "$KIT/bin/agent-log" sync --quiet >>"$STATE/stop-hook.log" 2>&1 || true
exit 0
