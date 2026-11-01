#!/usr/bin/env bash
# SessionStart hook(Claude Code on the web)。リポジトリの .claude/settings.json から呼ばれる。
# クラウド(CLAUDE_CODE_REMOTE=true)でだけ動き、ローカルでは何もしない。失敗してもセッションは止めない。
# - 開始時の入力にあるモデル名を記録(レビュー担当のモデル確認に使う)
# - 作者名の設定、依存の導入(変化が無ければ数秒)、未送信ログの再送
# - 状況を1段落だけ Claude の文脈に足す
set -u
[ "${CLAUDE_CODE_REMOTE:-}" = "true" ] || [ "${AGENT_HOOKS_LOCAL:-0}" = "1" ] || exit 0
ROOT="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
KIT="$ROOT/.agent-kit"
[ -d "$KIT" ] || exit 0
export AGENT_REPO_ROOT="$ROOT"
# shellcheck source=../lib/common.sh
. "$KIT/lib/common.sh"
load_config "$ROOT"
STATE="$(agent_state_dir "$ROOT")"
LOG="$STATE/session-start.log"

input="$(cat)"
# 開始時のモデルをセッションごとに記録する(sessions/<session_id>.json)。リポジトリ共通の1ファイルにはしない。
# これは「開始時点」の情報なので、レビューの判定には使わない(途中で /model を変えられるため)。
# レビューの判定は agent-gh が、そのセッションの transcript を読んで行う(lib/agentstate.py)。
printf '%s' "$input" | python3 -c '
import json,sys,datetime,os,re
try: j=json.load(sys.stdin)
except Exception: j={}
m=j.get("model")
if isinstance(m,dict): m=m.get("id") or m.get("display_name")
sid=j.get("session_id","") or "unknown"
rec={"model": m or "", "session_id": sid, "transcript_path": j.get("transcript_path",""),
     "source": j.get("source",""), "remote_session_id": os.environ.get("CLAUDE_CODE_REMOTE_SESSION_ID",""),
     "at": datetime.datetime.now().astimezone().isoformat(timespec="seconds")}
d=os.path.join(sys.argv[1],"sessions"); os.makedirs(d,exist_ok=True)
json.dump(rec, open(os.path.join(d, re.sub(r"[^A-Za-z0-9._-]","_",sid)[:80]+".json"),"w"))
json.dump(rec, open(os.path.join(sys.argv[1],"last-session.json"),"w"))
' "$STATE" 2>/dev/null || true

{
  echo "=== $(TZ=Asia/Tokyo date '+%F %T') session-start ==="
  AGENT_SERVICE=claude bash "$KIT/bin/git-identity.sh" || true
  timeout 540 bash "$KIT/bin/setup-deps.sh" all || echo "setup-deps: exit $?"
  AGENT_SERVICE=claude timeout 30 python3 "$KIT/bin/agent-log" sync --quiet || true
} >>"$LOG" 2>&1

python3 - "$STATE" "$LOG" <<'EOF'
import json, sys, glob, os
state, log = sys.argv[1], sys.argv[2]
parts = ["このリポジトリはクラウド用の作業ログを使う。実質的な作業をしたターンの終わりに skill cloud-worklog の手順で記録する。"]
try:
    j = json.load(open(os.path.join(state, "last-setup.json")))
    bad = [s["name"] for s in j.get("steps", []) if s.get("status") != "ok"]
    if bad:
        parts.append(f"開始時の依存導入で失敗・時間切れ: {', '.join(bad)}(詳細 {log})。必要なら bash .agent-kit/bin/setup-deps.sh deps を再実行する。")
except Exception:
    pass
pend = glob.glob(os.path.join(state, "outbox", "pending", "*.md"))
if pend:
    parts.append(f"未送信の作業ログが {len(pend)} 件ある(outbox)。削除しない。")
try:
    m = json.load(open(os.path.join(state, "last-session.json"))).get("model", "")
    parts.append(f"このセッションの開始時のモデル: {m or '不明(hook の入力に無い)'}(途中で変えた場合は反映されない)。")
except Exception:
    pass
print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": " ".join(parts)}}, ensure_ascii=False))
EOF
exit 0
