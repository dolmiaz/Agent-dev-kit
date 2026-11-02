#!/usr/bin/env bash
# agent-kit v2 の回帰テスト。一時ディレクトリだけを使い、本物の Drive・GitHub・Apps Script には触れない。
#   bash tests/run-tests.sh            # 全部(npm・PyPI から実際に依存を取る試験を含む)
#   NO_NET=1 bash tests/run-tests.sh   # ネットワークを使う試験を飛ばす
# モック: Apps Script = 本物の Code.gs を Node 上のモック Google サービスで実行(tests/gas_server.js)
#         GitHub = tests/fake_gh.py、rclone = tests/fake_rclone.py
set -u
KITROOT="$(cd "$(dirname "$0")/.." && pwd)"
W="$(mktemp -d "${TMPDIR:-/tmp}/agentkit-test.XXXXXX")"
PASS=0; FAIL=0; SKIP=0; RESULTS=()
ok() { PASS=$((PASS+1)); RESULTS+=("PASS [$SEC] $*"); echo "  PASS $*"; }
ng() { FAIL=$((FAIL+1)); RESULTS+=("FAIL [$SEC] $*"); echo "  FAIL $*"; }
sk() { SKIP=$((SKIP+1)); RESULTS+=("SKIP [$SEC] $*"); echo "  SKIP $*"; }
expect() { local name="$1"; shift; if "$@" >/dev/null 2>&1; then ok "$name"; else ng "$name"; fi; }
section() { SEC="$1"; echo; echo "== $* =="; }
SEC=0

export HOME="$W/home"; mkdir -p "$HOME"
export AGENT_STATE_DIR="$W/state" XDG_CACHE_HOME="$W/cache"
for v in AGENT_LOG_ENDPOINT AGENT_LOG_KEY AGENT_RUNTIME AGENT_SERVICE CLAUDE_CODE_REMOTE CLAUDE_CODE_REMOTE_SESSION_ID \
         CLAUDE_PROJECT_DIR AGENT_REPO_ROOT CI; do unset "$v"; done
git config --global user.email tester@example.invalid
git config --global user.name tester
git config --global init.defaultBranch main
NOPX=(env NO_PROXY=127.0.0.1 no_proxy=127.0.0.1)

mkrepo() {
  local d="$1"
  git init -q --bare "$d.origin.git"; git init -q "$d"; git -C "$d" remote add origin "$d.origin.git"
  echo "# demo" > "$d/README.md"
  git -C "$d" add -A && git -C "$d" commit -qm init && git -C "$d" push -q -u origin main
}
statedir() { (export AGENT_REPO_ROOT="$1"; . "$1/.agent-kit/lib/common.sh"; agent_state_dir "$1"); }

# ---------------------------------------------------------------------------
section 1 "install.py: 既存設定のある導入先・再実行・更新・取り外し"
R="$W/repo"; mkrepo "$R"
mkdir -p "$R/.claude" "$R/.agent"
cat > "$R/.claude/settings.json" <<'EOF'
{"permissions": {"allow": ["Bash(echo user-rule)"]}, "model": "sonnet",
 "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo user-stop-hook"}]}]}}
EOF
echo "user file" > "$R/.agent/notes.md"
mkdir -p "$R/.claude/skills" "$R/.github/ISSUE_TEMPLATE"; touch "$R/.claude/skills/.keep" "$R/.github/ISSUE_TEMPLATE/.keep"
printf 'user note: keep me\nmain へ変更しない・マージしない\n' > "$R/AGENTS.md"
git -C "$R" add -A && git -C "$R" commit -qm "user setup" && git -C "$R" push -q
python3 "$KITROOT/install.py" "$R" > "$W/i1.log" 2>&1
expect "kit placed under .agent-kit" test -x "$R/.agent-kit/bin/agent-log"
expect "manifest written" test -f "$R/.agent-kit/install-manifest.json"
expect "existing .agent/ untouched" grep -q "user file" "$R/.agent/notes.md"
expect "no permissions added to project settings" python3 -c "import json;j=json.load(open('$R/.claude/settings.json'));assert j['permissions']=={'allow':['Bash(echo user-rule)']}"
expect "user hook kept" grep -q user-stop-hook "$R/.claude/settings.json"
expect "kit hooks added" grep -q 'pretool.py' "$R/.claude/settings.json"
expect "AGENTS.md user rule kept" grep -q 'マージしない' "$R/AGENTS.md"
expect "AGENTS.md block says repo rules win" grep -q '常にこの節より優先' "$R/AGENTS.md"
expect "skills for both services" test -f "$R/.agents/skills/pr-review/SKILL.md" -a -f "$R/.claude/skills/issue-task/SKILL.md"
m1=$(stat -c %Y "$R/.agent-kit/install-manifest.json"); sleep 1
python3 "$KITROOT/install.py" "$R" > "$W/i2.log" 2>&1
expect "second install: no changes" grep -q '変更なし' "$W/i2.log"
expect "second install: manifest untouched" test "$(stat -c %Y "$R/.agent-kit/install-manifest.json")" = "$m1"
echo "# local tweak" >> "$R/.agent-kit/bin/services.sh"
python3 "$KITROOT/install.py" "$R" > "$W/i3.log" 2>&1
expect "upgrade keeps hand-modified kit file" grep -q 'keep(手で変更あり): .agent-kit/bin/services.sh' "$W/i3.log"
R2="$W/repo-foreign"; mkrepo "$R2"; mkdir -p "$R2/.agent-kit"; echo x > "$R2/.agent-kit/own.txt"
python3 "$KITROOT/install.py" "$R2" > "$W/i4.log" 2>&1; rc=$?
expect "foreign .agent-kit without manifest: refuse" test $rc -ne 0
python3 - "$R/.claude/settings.json" <<'PYEOF'
import json,sys
p=sys.argv[1]; j=json.load(open(p))
for g in j["hooks"]["Stop"]:
    if any("stop.sh" in h["command"] for h in g["hooks"]):
        g["hooks"].append({"type":"command","command":"echo user-added-into-kit-group"})
json.dump(j,open(p,"w"),indent=2)
PYEOF
rm -f "$R/.claude/skills/.keep" "$R/.github/ISSUE_TEMPLATE/.keep"     # もとからある空ディレクトリ
cp -r "$R" "$W/repo-uninstall"
python3 "$KITROOT/install.py" "$W/repo-uninstall" --uninstall > "$W/u.log" 2>&1
U="$W/repo-uninstall"
expect "uninstall: user allow rule kept" grep -q 'echo user-rule' "$U/.claude/settings.json"
expect "uninstall: user hook kept" grep -q user-stop-hook "$U/.claude/settings.json"
expect "uninstall: kit hooks removed" bash -c "! grep -q pretool.py '$U/.claude/settings.json'"
expect "uninstall: existing .agent/ kept" grep -q "user file" "$U/.agent/notes.md"
expect "uninstall: AGENTS.md user text kept, block removed" bash -c "grep -q 'keep me' '$U/AGENTS.md' && ! grep -q agent-kit:begin '$U/AGENTS.md'"
expect "uninstall: hand-modified kit file kept" test -f "$U/.agent-kit/bin/services.sh"
expect "uninstall: other kit files removed" test ! -e "$U/.agent-kit/bin/agent-log"
expect "uninstall: kit skills removed" test ! -e "$U/.claude/skills/pr-review"
expect "uninstall: user hook added inside a kit hook group is kept" grep -q user-added-into-kit-group "$U/.claude/settings.json"
expect "uninstall: pre-existing (empty) dirs are kept" test -d "$U/.claude/skills" -a -d "$U/.github/ISSUE_TEMPLATE"
R3="$W/repo-fresh"; mkrepo "$R3"
python3 "$KITROOT/install.py" "$R3" >/dev/null 2>&1; python3 "$KITROOT/install.py" "$R3" --uninstall >/dev/null 2>&1
expect "uninstall on fresh repo: settings.json created by kit is removed" test ! -e "$R3/.claude/settings.json"
expect "uninstall on fresh repo: nothing left (backups live outside the repo)" test -z "$(git -C "$R3" status --porcelain)"
git -C "$R" checkout -q -- .agent-kit/bin/services.sh 2>/dev/null || git -C "$R" show HEAD:.agent-kit/bin/services.sh >/dev/null 2>&1 || true
python3 - "$R" <<'EOF'
import json,hashlib,sys,pathlib
r=pathlib.Path(sys.argv[1]); m=json.loads((r/".agent-kit/install-manifest.json").read_text())
p=r/".agent-kit/bin/services.sh"; s=p.read_text().replace("# local tweak\n",""); p.write_text(s)
EOF
git -C "$R" add -A && git -C "$R" commit -qm "add kit" && git -C "$R" push -q

# ---------------------------------------------------------------------------
section 2 "設定の優先順位: 環境変数 > config.env > 既定値(書式の揺れも同じに読む)"
cp "$R/.agent-kit/config.env" "$W/config.env.keep"
printf 'AGENT_LOG_ENFORCE = 0\r\nAGENT_GIT_IDENTITY=0  # 無効\nAGENT_GIT_NAME_CLAUDE='"'"'Claude X'"'"'\nAGENT_REVIEW_MODEL="m1" # c\n' > "$R/.agent-kit/config.env"
sh_vals=$(cd "$R" && bash -c '. .agent-kit/lib/common.sh; load_config .; printf "%s|%s|%s|%s" "$AGENT_LOG_ENFORCE" "$AGENT_GIT_IDENTITY" "$AGENT_GIT_NAME_CLAUDE" "$AGENT_REVIEW_MODEL"')
py_vals=$(cd "$R" && python3 -c "
m={'__name__':'x'}; exec(compile(open('.agent-kit/bin/agent-log').read(),'al','exec'),m)
print('|'.join(m['cfg'](k) for k in ('AGENT_LOG_ENFORCE','AGENT_GIT_IDENTITY','AGENT_GIT_NAME_CLAUDE','AGENT_REVIEW_MODEL')))")
expect "config parsing: shell == python ($sh_vals)" test "$sh_vals" = "$py_vals"
expect "config parsing: spaces/CRLF/comment/quotes handled" test "$sh_vals" = "0|0|Claude X|m1"
cp "$W/config.env.keep" "$R/.agent-kit/config.env"
cfgset() { sed -i "s/^$1=.*/$1=$2/" "$R/.agent-kit/config.env"; }
cp "$R/.agent-kit/config.env" "$W/config.env.orig"
cfgset AGENT_GIT_IDENTITY 0
git -C "$R" config --local --unset user.name 2>/dev/null
( cd "$R" && AGENT_SERVICE=claude bash .agent-kit/bin/git-identity.sh ) >/dev/null 2>&1
expect "config AGENT_GIT_IDENTITY=0 disables" test -z "$(git -C "$R" config --local user.name)"
( cd "$R" && AGENT_GIT_IDENTITY=1 AGENT_SERVICE=claude bash .agent-kit/bin/git-identity.sh ) >/dev/null 2>&1
expect "env AGENT_GIT_IDENTITY=1 overrides config" test "$(git -C "$R" config --local user.name)" = "Claude Code (cloud)"
( cd "$R" && AGENT_GIT_NAME_CODEX="X Bot" AGENT_SERVICE=codex AGENT_GIT_IDENTITY=1 bash .agent-kit/bin/git-identity.sh ) >/dev/null 2>&1
expect "env name override for codex" test "$(git -C "$R" config --local user.name)" = "X Bot"
cp "$W/config.env.orig" "$R/.agent-kit/config.env"
printf 'not-a-real==pkg\n' > "$R/requirements.txt"
cfgset AGENT_SETUP_STRICT 1; cfgset AGENT_STEP_TIMEOUT 60
( cd "$R" && bash .agent-kit/bin/setup-deps.sh deps ) > "$W/s1.log" 2>&1; rc1=$?
( cd "$R" && AGENT_SETUP_STRICT=0 bash .agent-kit/bin/setup-deps.sh deps ) > "$W/s2.log" 2>&1; rc2=$?
expect "config AGENT_SETUP_STRICT=1 → exit 1 on failure" test $rc1 = 1
expect "env AGENT_SETUP_STRICT=0 overrides config → exit 0" test $rc2 = 0
cp "$W/config.env.orig" "$R/.agent-kit/config.env"; rm -f "$R/requirements.txt"; rm -rf "$R/.venv"

# ---------------------------------------------------------------------------
section 3 "依存: 実取得・スキップ・成果物の確認・別checkout・失敗・時間切れ・同時実行"
if [ "${NO_NET:-0}" != 1 ]; then
  ( cd "$R" && printf '{"name":"demo","version":"1.0.0","private":true,"dependencies":{"is-number":"7.0.0"}}\n' > package.json \
    && npm install --package-lock-only --no-audit --no-fund >/dev/null 2>&1 && printf 'six==1.16.0\n' > requirements.txt )
  t0=$(date +%s); ( cd "$R" && bash .agent-kit/bin/setup-deps.sh deps ) > "$W/d1.log" 2>&1; t1=$(date +%s)
  expect "npm installed" test -f "$R/node_modules/is-number/package.json"
  expect "pip installed into .venv" test -n "$(ls "$R"/.venv/lib/python*/site-packages/six.py 2>/dev/null)"
  ( cd "$R" && bash .agent-kit/bin/setup-deps.sh deps ) > "$W/d2.log" 2>&1; t2=$(date +%s)
  expect "re-run skips npm" grep -q 'npm: 変更なし' "$W/d2.log"
  expect "re-run skips pip" grep -q 'pip: 変更なし' "$W/d2.log"
  RESULTS+=("INFO deps first=$((t1-t0))s second=$((t2-t1))s"); echo "    (1回目 $((t1-t0))s / 2回目 $((t2-t1))s)"
  rm -rf "$R/node_modules"
  ( cd "$R" && bash .agent-kit/bin/setup-deps.sh deps ) > "$W/d3.log" 2>&1
  expect "missing node_modules with valid stamp → reinstall" test -f "$R/node_modules/is-number/package.json"
  # 別checkout: 同じロックファイル名・別の中身。A のスタンプで B を飛ばさない
  B="$W/checkout-b"; git clone -q "$R.origin.git" "$B"
  ( cd "$B" && printf '{"name":"demo","version":"1.0.0","private":true,"dependencies":{"is-odd":"3.0.1"}}\n' > package.json \
    && npm install --package-lock-only --no-audit --no-fund >/dev/null 2>&1 )
  ( cd "$B" && bash .agent-kit/bin/setup-deps.sh deps ) > "$W/d4.log" 2>&1
  expect "other checkout installs its own deps" test -f "$B/node_modules/is-odd/package.json"
  expect "state dirs differ per checkout" test "$(statedir "$R")" != "$(statedir "$B")"
  printf 'six==0.0.0.nonexistent\n' > "$R/requirements.txt"
  ( cd "$R" && AGENT_STEP_TIMEOUT=120 bash .agent-kit/bin/setup-deps.sh deps ) > "$W/d5.log" 2>&1; rc=$?
  expect "broken dep: exit 0 by default" test $rc = 0
  expect "broken dep: recorded" grep -q '"status": "failed", "name": "python deps"' "$(statedir "$R")/last-setup.json"
  printf 'six==1.16.0\n' > "$R/requirements.txt"
  ( cd "$R" && bash .agent-kit/bin/setup-deps.sh deps ) > /dev/null 2>&1
  expect "recovers after fix" grep -q '"status": "ok", "name": "python deps"' "$(statedir "$R")/last-setup.json"
  mkdir -p "$W/slowbin"; printf '#!/bin/sh\nsleep 30\n' > "$W/slowbin/npm"; chmod +x "$W/slowbin/npm"
  rm -rf "$R/node_modules"
  ( cd "$R" && PATH="$W/slowbin:$PATH" AGENT_STEP_TIMEOUT=2 bash .agent-kit/bin/setup-deps.sh deps ) > /dev/null 2>&1
  expect "timeout recorded" grep -q '"status": "timeout", "name": "node deps"' "$(statedir "$R")/last-setup.json"
  ( cd "$R" || exit 1; bash .agent-kit/bin/setup-deps.sh deps & bash .agent-kit/bin/setup-deps.sh deps; wait ) > "$W/d6.log" 2>&1
  expect "parallel runs both finish (lock)" test "$(grep -c 'done in' "$W/d6.log")" = 2
  ( cd "$R" && bash .agent-kit/bin/setup-deps.sh plan ) > "$W/plan.log" 2>&1
  expect "plan lists registries and log endpoint" bash -c "grep -q registry.npmjs.org '$W/plan.log' && grep -q script.google.com '$W/plan.log'"
  rm -rf "$R/node_modules" "$R/.venv" "$R/package.json" "$R/package-lock.json" "$R/requirements.txt"
else
  RESULTS+=("SKIP [3] network dependency tests (NO_NET=1)")
fi

# ---------------------------------------------------------------------------
section 4 "作業ログ: UUID・置き場・並行・再送・ID+内容照合・サービス分離・上限"
git -C "$R" checkout -qb feature/1-demo
echo "change" >> "$R/README.md"; git -C "$R" commit -qam "edit readme"
export FAKE_TOKEN="ghp_$(printf 'A%.0s' {1..36})" DB_PASSWORD="hunter2hunter2"
( cd "$R" && AGENT_RUNTIME=codex-cloud python3 .agent-kit/bin/agent-log turn --title "テスト" \
   --purpose "秘密 $FAKE_TOKEN と hunter2hunter2" --tests "pytest: 3 passed" ) > "$W/l1.out" 2>&1; rc=$?
SD="$(statedir "$R")"
f=$(ls "$SD"/outbox/pending/*.md 2>/dev/null | head -1)
expect "default: pending log outside the repo (outbox)" test -n "$f"
expect "default: nothing under the repo" bash -c "! ls '$R/.agent-logs' 2>/dev/null | grep -q ."
expect "git status clean of logs" bash -c "! git -C '$R' status --porcelain | grep -q agent-log"
expect "no endpoint: exit 0 + UNSYNCED" bash -c "test $rc = 0 && grep -q UNSYNCED '$W/l1.out'"
expect "filename has date/service/session/turn/uuid8" bash -c "basename '$f' | grep -qE '^[0-9]{4}-[0-9]{2}-[0-9]{2}-[0-9]{4}-codex-[A-Za-z0-9]{12}-t01-[0-9a-f]{8}\.md$'"
expect "frontmatter has full UUID4" grep -qE '^log_id: "[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"$' "$f"
expect "secrets redacted" bash -c "! grep -q '$FAKE_TOKEN' '$f' && ! grep -q hunter2hunter2 '$f'"
unset FAKE_TOKEN DB_PASSWORD
( cd "$R" && GIT_AUTHOR_NAME=claude-code-web MAX_THINKING_TOKENS=320000000000 AGENT_RUNTIME=codex-cloud python3 .agent-kit/bin/agent-log turn --title "誤爆確認" --no-sync ) >/dev/null 2>&1
g=$(ls -t "$SD"/outbox/pending/*.md | head -1)
expect "redaction does not touch frontmatter/benign env (GIT_AUTHOR_NAME, *_TOKENS)" bash -c "grep -q '^service: \"codex-cloud\"' '$g' && ! grep -q REDACTED '$g'"
rm -f "$g"
for i in $(seq 1 8); do ( cd "$R" && AGENT_RUNTIME=codex-cloud python3 .agent-kit/bin/agent-log turn --title "並行$i" --no-sync >/dev/null 2>&1 ) & done; wait
n=$(ls "$SD"/outbox/pending/*-codex-*.md | wc -l)
turns=$(ls "$SD"/outbox/pending/*-codex-*.md | sed -E 's/.*-t([0-9]+)-.*/\1/' | sort -u | wc -l)
expect "parallel turns: 9 files, 9 unique turn numbers" test "$n/$turns" = "9/9"
ids=$(grep -h '^log_id:' "$SD"/outbox/pending/*.md | sort -u | wc -l)
expect "parallel turns: unique UUIDs" test "$ids" = 9
( cd "$R" && AGENT_RUNTIME=claude-cloud CLAUDE_CODE_REMOTE_SESSION_ID=cse_01ABCDEFGHJKLMNOP python3 .agent-kit/bin/agent-log turn --title "claude側" --no-sync ) >/dev/null 2>&1
expect "claude log has session URL" grep -q 'claude.ai/code/session_01ABCDEFGHJKLMNOP' "$(ls "$SD"/outbox/pending/*-claude-*.md)"
# repo モード(明示したときだけ)
( cd "$R" && AGENT_LOG_PENDING_MODE=repo AGENT_RUNTIME=codex-cloud python3 .agent-kit/bin/agent-log turn --title "repo mode" ) >/dev/null 2>&1
expect "repo mode (opt-in): pending under .agent-logs/pending" test -n "$(ls "$R"/.agent-logs/pending/*.md 2>/dev/null)"
expect "repo mode: sent/ ignored by git" bash -c "cd '$R' && git check-ignore -q .agent-logs/sent/x.md"

# 受け口(本物の Code.gs をモックで実行)
PORT=$((20000 + RANDOM % 20000)); GD="$W/gas"; mkdir -p "$GD"; MODE="$W/gas.mode"; echo ok > "$MODE"
export MOCK_KEY_CODEX="$(printf 'c%.0s' {1..40})" MOCK_KEY_CLAUDE="$(printf 'd%.0s' {1..40})"
node "$KITROOT/tests/gas_server.js" $PORT "$KITROOT/tests/fixtures/Deny.test.gs,$KITROOT/endpoint/Code.gs" "$GD" "$MODE" & GP=$!; sleep 0.8
EP="http://127.0.0.1:$PORT/exec"
cx() { ( cd "$R" && "${NOPX[@]}" AGENT_RUNTIME=codex-cloud AGENT_LOG_ENDPOINT="$EP" AGENT_LOG_KEY="$MOCK_KEY_CODEX" python3 .agent-kit/bin/agent-log "$@" ); }
cl() { ( cd "$R" && "${NOPX[@]}" AGENT_RUNTIME=claude-cloud CLAUDE_CODE_REMOTE_SESSION_ID=cse_01ABCDEFGHJKLMNOP AGENT_LOG_ENDPOINT="$EP" AGENT_LOG_KEY="$MOCK_KEY_CLAUDE" python3 .agent-kit/bin/agent-log "$@" ); }
echo down > "$MODE"; cx sync > "$W/s.down" 2>&1; rc=$?
expect "endpoint down: exit 0, all pending" bash -c "test $rc = 0 && test \$(ls '$SD'/outbox/pending/*-codex-*.md | wc -l) = 9"
echo slow > "$MODE"; t0=$(date +%s); ( cd "$R" && AGENT_LOG_SYNC_TIMEOUT=1 "${NOPX[@]}" AGENT_RUNTIME=codex-cloud AGENT_LOG_ENDPOINT="$EP" AGENT_LOG_KEY="$MOCK_KEY_CODEX" python3 .agent-kit/bin/agent-log sync ) >/dev/null 2>&1; t1=$(date +%s)
expect "slow endpoint: gives up within timeout ($((t1-t0))s)" test $((t1-t0)) -le 15
echo quota > "$MODE"; cx sync > "$W/s.quota" 2>&1
expect "Apps Script quota: kept pending with reason" bash -c "grep -q 'quota' '$W/s.quota' && test \$(ls '$SD'/outbox/pending/*-codex-*.md | wc -l) = 9"
echo storage-full > "$MODE"; cx sync > "$W/s.full" 2>&1
expect "Drive storage full: kept pending" grep -q 'storage-full' "$W/s.full"
( cd "$R" && "${NOPX[@]}" AGENT_LOG_STRICT=1 AGENT_RUNTIME=codex-cloud AGENT_LOG_ENDPOINT="$EP" AGENT_LOG_KEY="$MOCK_KEY_CODEX" python3 .agent-kit/bin/agent-log sync ) >/dev/null 2>&1; rc=$?
expect "AGENT_LOG_STRICT=1 → exit 1 while unsent" test $rc = 1
# 一度送ろうとした後は、環境が変わっても内容を変えない(秘密らしい値が新たに現れたら送らずに保留)
first=$(ls "$SD"/outbox/pending/*-codex-*.md | head -1); h1=$(sha256sum "$first" | cut -c1-64)
lid=$(sed -n 's/^log_id: "\(.*\)"/\1/p' "$first"); word=$(sed -n 's/^## [0-9-]* [0-9:]* //p' "$first" | head -1)
echo down > "$MODE"; cx sync > /dev/null 2>&1
( cd "$R" && "${NOPX[@]}" NEW_API_TOKEN="${word}${word}${word}" AGENT_RUNTIME=codex-cloud AGENT_LOG_ENDPOINT="$EP" AGENT_LOG_KEY="$MOCK_KEY_CODEX" python3 .agent-kit/bin/agent-log sync ) > "$W/s.frozen" 2>&1
expect "content frozen after first attempt (no rewrite on env change)" test "$(sha256sum "$first" | cut -c1-64)" = "$h1"
echo ok > "$MODE"; cx sync > "$W/s.ok" 2>&1
expect "codex logs sent to codex folder only" test "$(ls "$GD"/F_CODEX/*.md | wc -l)" = 9
expect "claude log not sent by codex (other service)" bash -c "test -z \"\$(ls '$GD'/F_CODEX/*-claude-* 2>/dev/null)\" && ls '$SD'/outbox/pending/*-claude-*.md >/dev/null && grep -q '別サービス' '$W/s.ok'"
cl sync > "$W/s.cl" 2>&1
expect "claude log goes to claude folder" test "$(ls "$GD"/F_CLAUDE/*-claude-*.md | wc -l)" = 1
# 再送: 同じ ID・同じ内容 → duplicate(Drive から取り込みで消えた後でも作り直さない)
one=$(ls "$SD"/outbox/sent/*-codex-*.md | head -1); name=$(basename "$one")
rm -f "$GD/F_CODEX/$name"
grep -v "$(sed -n 's/^log_id: "\(.*\)"/\1/p' "$one")" "$SD/outbox/sent/ledger.jsonl" > "$W/l.tmp"; mv "$W/l.tmp" "$SD/outbox/sent/ledger.jsonl"
mv "$one" "$SD/outbox/pending/"
cx sync > "$W/s.re" 2>&1
expect "resend after Drive-side delete: accepted as duplicate, not recreated" bash -c "test ! -e '$GD/F_CODEX/$name' && grep -q '\"duplicate\"' '$SD/outbox/sent/ledger.jsonl'"
# 同じ ID で内容が違う → conflict、送信成功にしない
one=$(ls "$SD"/outbox/sent/*-codex-*.md | head -1)
cp "$one" "$SD/outbox/pending/"; echo "tampered" >> "$SD/outbox/pending/$(basename "$one")"
grep -v "$(sed -n 's/^log_id: "\(.*\)"/\1/p' "$one")" "$SD/outbox/sent/ledger.jsonl" > "$W/l.tmp"; mv "$W/l.tmp" "$SD/outbox/sent/ledger.jsonl"
cx sync > "$W/s.conf" 2>&1
expect "same id + different content: conflict, stays pending" bash -c "grep -q conflict '$W/s.conf' && test -e '$SD/outbox/pending/$(basename "$one")'"
expect "drive file not overwritten" bash -c "! grep -q tampered '$GD/F_CODEX/$(basename "$one")'"
rm -f "$SD/outbox/pending/$(basename "$one")"
# 受け口が別の log_id / sha を返す(偽の成功)→ 成功にしない
python3 - "$KITROOT" "$R" <<'EOF' > "$W/s.fake" 2>&1
import sys, json, threading, http.server, subprocess, os
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self,*a): pass
    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        b=json.dumps({"ok":True,"status":"created","log_id":"00000000-0000-4000-8000-000000000000","sha256":"0"*64}).encode()
        self.send_response(200); self.send_header("Content-Length",str(len(b))); self.end_headers(); self.wfile.write(b)
s=http.server.HTTPServer(("127.0.0.1",0),H); threading.Thread(target=s.serve_forever,daemon=True).start()
env=dict(os.environ, NO_PROXY="127.0.0.1", no_proxy="127.0.0.1", AGENT_RUNTIME="codex-cloud",
         AGENT_LOG_ENDPOINT=f"http://127.0.0.1:{s.server_port}/", AGENT_LOG_KEY="x"*40)
subprocess.run([sys.executable, ".agent-kit/bin/agent-log", "turn", "--title", "fake"], cwd=sys.argv[2], env=env)
EOF
expect "endpoint reply with other id/sha: not treated as sent" grep -q '一致しない' "$W/s.fake"
kill $GP 2>/dev/null; wait $GP 2>/dev/null
rm -f "$SD"/outbox/pending/*.md

# ---------------------------------------------------------------------------
section 5 "Claude hooks: 変更検出・促し・権限判定(クラウドだけ)"
export CLAUDE_PROJECT_DIR="$R"
hk() { ( cd "$R" && CLAUDE_CODE_REMOTE=true "$@" ); }
out=$(echo '{"session_id":"s0","source":"startup","model":"claude-opus-5-5"}' | hk bash .agent-kit/claude/session-start.sh)
expect "SessionStart: valid JSON" python3 -c "import json,sys;assert 'cloud-worklog' in json.loads(sys.argv[1])['hookSpecificOutput']['additionalContext']" "$out"
expect "SessionStart: start model recorded per session (sessions/<id>.json)" grep -q 'claude-opus-5-5' "$SD/sessions/s0.json"
echo '{"session_id":"s0b","source":"startup","model":"claude-sonnet-5-5"}' | hk bash .agent-kit/claude/session-start.sh >/dev/null
expect "SessionStart: another session does not overwrite the first" bash -c "grep -q claude-opus-5-5 '$SD/sessions/s0.json' && grep -q claude-sonnet-5-5 '$SD/sessions/s0b.json'"
echo "base" > "$R/notes.txt"; git -C "$R" add notes.txt; git -C "$R" commit -qm notes
echo "edit1" >> "$R/notes.txt"                       # ターン前から変更済み
echo '{"session_id":"s1"}' | hk bash .agent-kit/claude/turn-start.sh
echo "edit2" >> "$R/notes.txt"                       # 変更済みファイルへの追加編集
out=$(echo '{"session_id":"s1","stop_hook_active":false}' | hk bash .agent-kit/claude/stop.sh)
expect "Stop: detects extra edit to already-modified file" python3 -c "import json,sys;assert json.loads(sys.argv[1])['decision']=='block'" "$out"
out=$(echo '{"session_id":"s1","stop_hook_active":true}' | hk bash .agent-kit/claude/stop.sh)
expect "Stop: no second block (no loop)" test -z "$out"
hk python3 .agent-kit/bin/agent-log turn --title t --no-sync >/dev/null 2>&1
out=$(echo '{"session_id":"s1","stop_hook_active":false}' | hk bash .agent-kit/claude/stop.sh)
expect "Stop: no block after logging" test -z "$out"
echo '{"session_id":"s2"}' | hk bash .agent-kit/claude/turn-start.sh
out=$(echo '{"session_id":"s2","stop_hook_active":false}' | hk bash .agent-kit/claude/stop.sh)
expect "Stop: no block on Q&A-only turn" test -z "$out"
cfgset AGENT_LOG_ENFORCE 0
echo '{"session_id":"s3"}' | hk bash .agent-kit/claude/turn-start.sh; echo x >> "$R/notes.txt"
out=$(echo '{"session_id":"s3","stop_hook_active":false}' | hk bash .agent-kit/claude/stop.sh)
expect "config AGENT_LOG_ENFORCE=0 disables nudge" test -z "$out"
out=$(echo '{"session_id":"s3","stop_hook_active":false}' | hk env AGENT_LOG_ENFORCE=1 bash .agent-kit/claude/stop.sh)
expect "env AGENT_LOG_ENFORCE=1 overrides config" python3 -c "import json,sys;assert json.loads(sys.argv[1])['decision']=='block'" "$out"
cp "$W/config.env.orig" "$R/.agent-kit/config.env"
out=$(cd "$R" && echo '{"session_id":"s3","stop_hook_active":false}' | bash .agent-kit/claude/stop.sh)
expect "hooks do nothing locally" test -z "$out"
git -C "$R" checkout -q -- notes.txt
pt() { printf '%s' "$2" | ( cd "$R" && env $1 python3 .agent-kit/claude/pretool.py ); }
dec() { python3 -c "import json,sys;d=sys.stdin.read().strip();print(json.loads(d)['hookSpecificOutput']['permissionDecision'] if d else 'none')"; }
# 対外文章の推敲(AGENT_POLISH_REQUIRED=1)のときは、確認済みでない README を含む push を止める(このリポジトリには
# README を変えたまだ push していないコミットがある)。ここから下の git の規則の試験は、推敲を必須にしない設定で行う
expect "pretool cloud (polish required): push that includes an unconfirmed README change → deny (use agent-publish)" bash -c "pt() { printf '%s' \"\$2\" | ( cd '$R' && env \$1 python3 .agent-kit/claude/pretool.py ); }; pt CLAUDE_CODE_REMOTE=true '{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git push -u origin feature/12-x\"}}' | grep -q '\"deny\".*agent-publish'"
expect "pretool cloud (polish required, AGENT_CLOUD_AUTO_ALLOW=0): direct gh pr create is still denied" bash -c "printf '%s' '{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh pr create -t x -b y\"}}' | ( cd '$R' && env CLAUDE_CODE_REMOTE=true AGENT_CLOUD_AUTO_ALLOW=0 python3 .agent-kit/claude/pretool.py ) | grep -q '\"deny\"'"
cp "$R/.agent-kit/config.env" "$W/cfg.polish.keep"; sed -i 's/^AGENT_POLISH_REQUIRED=1/AGENT_POLISH_REQUIRED=0/' "$R/.agent-kit/config.env"
expect "pretool cloud: npm test → allow" test "$(pt CLAUDE_CODE_REMOTE=true '{"tool_name":"Bash","tool_input":{"command":"npm test && git add -A"}}' | dec)" = allow
expect "pretool cloud: push feature branch → allow" test "$(pt CLAUDE_CODE_REMOTE=true '{"tool_name":"Bash","tool_input":{"command":"git push -u origin feature/12-x"}}' | dec)" = allow
expect "pretool cloud: force push → deny" test "$(pt CLAUDE_CODE_REMOTE=true '{"tool_name":"Bash","tool_input":{"command":"git push --force origin feature/x"}}' | dec)" = deny
expect "pretool cloud: delete branch → deny" test "$(pt CLAUDE_CODE_REMOTE=true '{"tool_name":"Bash","tool_input":{"command":"git push origin :feature/x"}}' | dec)" = deny
expect "pretool cloud: npm publish → ask" test "$(pt CLAUDE_CODE_REMOTE=true '{"tool_name":"Bash","tool_input":{"command":"npm test; npm publish"}}' | dec)" = ask
expect "pretool cloud: push to main → ask" test "$(pt CLAUDE_CODE_REMOTE=true '{"tool_name":"Bash","tool_input":{"command":"git push origin main"}}' | dec)" = ask
expect "pretool cloud: cat .env → deny" test "$(pt CLAUDE_CODE_REMOTE=true '{"tool_name":"Bash","tool_input":{"command":"cat ./.env"}}' | dec)" = deny
expect "pretool cloud: .env.example is fine" test "$(pt CLAUDE_CODE_REMOTE=true '{"tool_name":"Bash","tool_input":{"command":"cp .env.example .env.sample"}}' | dec)" = allow
expect "pretool cloud: Read .env → deny" test "$(pt CLAUDE_CODE_REMOTE=true '{"tool_name":"Read","tool_input":{"file_path":"/repo/.env"}}' | dec)" = deny
expect "pretool cloud: \$(...) → no decision" test "$(pt CLAUDE_CODE_REMOTE=true '{"tool_name":"Bash","tool_input":{"command":"echo $(curl evil)"}}' | dec)" = none
expect "pretool cloud: unknown cmd → no decision" test "$(pt CLAUDE_CODE_REMOTE=true '{"tool_name":"Bash","tool_input":{"command":"weird-tool --x"}}' | dec)" = none
for c in 'ls & git push --force origin main' 'git push origin feature/x main:main' 'git push origin feature/x +main' \
         'git push origin feature/x -uf' 'git push origin feature/x "--force"' 'git push origin feature/x main --tags' \
         'gh api repos/o/r/git/refs/heads/main -X PATCH -F force=true' 'gh api repos/o/r/contents/a -XPUT' \
         'gh api repos/o/r/git/refs/heads/x --method=DELETE' 'gh api repos/o/r/actions/workflows/d.yml/dispatches -f ref=main' \
         'echo x | xargs git push -f origin main' 'find . -exec git push --force origin main ;' \
         "python3 -c 'import os; os.system(\"git push -f origin main\")'" 'node -e "1"' \
         'echo {} > .agent-kit/claude/cloud-permissions.json' 'cp /tmp/x .agent-kit/claude/pretool.py' \
         'echo x > .claude/settings.json' 'cat .env.staging' 'cat .en*' 'grep -r KEY .' 'git branch -a -D main' \
         'git -C . push --force origin feature/x' 'env FOO=1 git push -f origin x' 'timeout 5 git push --force origin x'; do
  d=$(pt CLAUDE_CODE_REMOTE=true "$(python3 -c 'import json,sys;print(json.dumps({"tool_name":"Bash","tool_input":{"command":sys.argv[1]}}))' "$c")" | dec)
  if [ "$d" != allow ]; then ok "pretool bypass blocked ($d): $c"; else ng "pretool bypass ALLOWED: $c"; fi
done
if python3 "$KITROOT/tests/pretool_cases.py" "$R/.agent-kit/claude/pretool.py" "$R/.agent-kit/claude/cloud-permissions.json" > "$W/ptcases.log" 2>&1; then
  ok "pretool case table: $(tail -1 "$W/ptcases.log")"; else ng "pretool case table: $(grep -v '^bad=' "$W/ptcases.log" | head -3 | tr '\n' ' ')"; fi
expect "pretool: git push -f via git -C → deny" test "$(pt CLAUDE_CODE_REMOTE=true '{"tool_name":"Bash","tool_input":{"command":"git -C . push --force origin feature/x"}}' | dec)" = deny
expect "pretool: Edit of permission file → ask" test "$(pt CLAUDE_CODE_REMOTE=true '{"tool_name":"Edit","tool_input":{"file_path":".agent-kit/claude/cloud-permissions.json"}}' | dec)" = ask
expect "pretool: plain gh api GET → allow" test "$(pt CLAUDE_CODE_REMOTE=true '{"tool_name":"Bash","tool_input":{"command":"gh api repos/o/r/pulls/1"}}' | dec)" = allow
expect "pretool LOCAL: nothing (local permissions untouched)" test "$(pt X=1 '{"tool_name":"Bash","tool_input":{"command":"npm test"}}' | dec)" = none
expect "pretool cloud with AGENT_CLOUD_AUTO_ALLOW=0: nothing" test "$(pt 'CLAUDE_CODE_REMOTE=true AGENT_CLOUD_AUTO_ALLOW=0' '{"tool_name":"Bash","tool_input":{"command":"git push --force"}}' | dec)" = none
cp "$W/cfg.polish.keep" "$R/.agent-kit/config.env"
unset CLAUDE_PROJECT_DIR

# ---------------------------------------------------------------------------
section 6 "Codex setup: 再実行"
( cd "$R" && bash .agent-kit/codex/setup.sh ) > "$W/cx1.log" 2>&1; r1=$?
( cd "$R" && bash .agent-kit/codex/setup.sh ) > "$W/cx2.log" 2>&1; r2=$?
expect "codex setup twice: exit 0" test "$r1$r2" = 00
expect "bashrc block once" test "$(grep -c '>>> agent-kit >>>' "$HOME/.bashrc")" = 1
expect "codex author set" test "$(git -C "$R" config --local user.name)" = "Codex (cloud)"

# ---------------------------------------------------------------------------
section 7 "agent-gh: Issue 単位の担当確保・レビューの SHA 固定・モデルと独立性の確認・必須 CI・マージ可否"
# 詳細は tests/test_agent_gh.py(偽の gh・偽の transcript・本物の hook)。1行ずつ PASS/FAIL/SKIP を数える
python3 "$KITROOT/tests/test_agent_gh.py" "$W/agentgh" > "$W/agentgh.out" 2>&1
while IFS= read -r line; do
  case "$line" in
    "PASS "*) ok "${line#PASS }" ;;
    "FAIL "*) ng "${line#FAIL }" ;;
    "SKIP "*) sk "${line#SKIP }" ;;
  esac
done < "$W/agentgh.out"
expect "agent-gh tests produced results" grep -qE '^(PASS|SKIP) ' "$W/agentgh.out"
# CI では ssh-keygen あり前提。SKIP が出たら失敗(飛ばさない)
if [ -n "${CI:-}" ] && grep -q '^SKIP ' "$W/agentgh.out"; then
  ng "agent-gh must not skip under CI (ssh-keygen required)"
fi

# ---------------------------------------------------------------------------
section 8 "Mac 回収(repo モードを選んだ場合): サービス別フォルダ・ID+内容照合・台帳"
mkdir -p "$W/bin" "$W/drive"; cp "$KITROOT/tests/fake_rclone.py" "$W/bin/rclone"; chmod +x "$W/bin/rclone"
git -C "$R" add -A .agent-logs 2>/dev/null; git -C "$R" commit -qm "logs (repo mode)" 2>/dev/null; git -C "$R" push -q -u origin feature/1-demo 2>/dev/null
C="$W/clone"; git clone -q "$R.origin.git" "$C"
printf '%s' '{"folders":{"codex":"TEST_FOLDER_CHATGPT","claude":"TEST_FOLDER_CLAUDE"},"deny":["TEST_MIRROR_ROOT","TEST_GOODREADER"]}' > "$W/collector.json"
col() { PATH="$W/bin:$PATH" FAKE_DRIVE="$W/drive" XDG_CACHE_HOME="$W/xdg" python3 "$KITROOT/local/collect-pending.py" --config "$W/collector.json" "$C"; }
col > "$W/c1.log" 2>&1; rc=$?
expect "collector placed codex log into ChatGPT folder id" test -n "$(ls "$W"/drive/TEST_FOLDER_CHATGPT/*-codex-*.md 2>/dev/null)"
expect "collector never wrote into mirror folder ids (only listed them for the parent check)" bash -c "! grep -E '^copyto' '$W/drive/.calls' | grep -qE 'TEST_MIRROR_ROOT|TEST_GOODREADER'"
rm -f "$W"/drive/TEST_FOLDER_CHATGPT/*.md     # Drive 側で取り込み後に削除された想定
col > "$W/c2.log" 2>&1
expect "after Drive-side delete: not re-sent (ledger by log_id)" test -z "$(ls "$W"/drive/TEST_FOLDER_CHATGPT/*.md 2>/dev/null)"
rm -rf "$W/xdg"; nm=$(sed -n 's/^placed: \([^ ]*\) .*/\1/p' "$W/c1.log" | head -1)
mkdir -p "$W/drive/TEST_FOLDER_CHATGPT"; echo "user edited" > "$W/drive/TEST_FOLDER_CHATGPT/$nm"
col > "$W/c3.log" 2>&1
expect "same name, different content on Drive: conflict, not overwritten" bash -c "grep -q conflict '$W/c3.log' && grep -q 'user edited' '$W/drive/TEST_FOLDER_CHATGPT/$nm'"
printf '%s' '{"folders":{"codex":"TEST_MIRROR_ROOT","claude":"TEST_FOLDER_CLAUDE"},"deny":["TEST_MIRROR_ROOT"]}' > "$W/collector-bad.json"
PATH="$W/bin:$PATH" FAKE_DRIVE="$W/drive" python3 "$KITROOT/local/collect-pending.py" --config "$W/collector-bad.json" "$C" > "$W/c4.log" 2>&1; rc=$?
expect "collector refuses a deny-listed folder as destination" bash -c "test $rc != 0 && grep -q '置き先にできない' '$W/c4.log'"
printf '%s' '{"TEST_MIRROR_ROOT":["MIRROR_SUB"],"MIRROR_SUB":["MIRROR_SUB_CHATGPT"]}' > "$W/drive/.tree.json"
printf '%s' '{"folders":{"codex":"MIRROR_SUB_CHATGPT","claude":"TEST_FOLDER_CLAUDE"},"deny":["TEST_MIRROR_ROOT"]}' > "$W/collector-under.json"
PATH="$W/bin:$PATH" FAKE_DRIVE="$W/drive" python3 "$KITROOT/local/collect-pending.py" --config "$W/collector-under.json" "$C" > "$W/c6.log" 2>&1; rc=$?
expect "collector refuses a destination nested under a deny folder (parent check)" bash -c "test $rc != 0 && grep -q '配下にある' '$W/c6.log'"
printf '%s' '{"TEST_MIRROR_ROOT":["NOID-sub"]}' > "$W/drive/.tree.json"; : > "$W/drive/.calls"
PATH="$W/bin:$PATH" FAKE_DRIVE="$W/drive" python3 "$KITROOT/local/collect-pending.py" --config "$W/collector.json" "$C" > "$W/c7.log" 2>&1; rc=$?
expect "collector stops when a folder under the deny root has no ID (cannot prove the destination is outside)" bash -c "test $rc != 0 && grep -q 'ID の分からない' '$W/c7.log' && ! grep -q '^copyto' '$W/drive/.calls'"
rm -f "$W/drive/.tree.json"; touch "$W/drive/.lsjson-fail"; : > "$W/drive/.calls"
PATH="$W/bin:$PATH" FAKE_DRIVE="$W/drive" python3 "$KITROOT/local/collect-pending.py" --config "$W/collector.json" "$C" > "$W/c8.log" 2>&1; rc=$?
expect "collector stops (writes nothing) when listing the deny folder fails" bash -c "test $rc != 0 && grep -q '確かめられない' '$W/c8.log' && ! grep -q '^copyto' '$W/drive/.calls'"
rm -f "$W/drive/.lsjson-fail"
mkdir -p "$W/norclone"; for t in python3 git; do ln -sf "$(command -v $t)" "$W/norclone/$t"; done
PATH="$W/norclone" FAKE_DRIVE="$W/drive" "$W/norclone/python3" "$KITROOT/local/collect-pending.py" --config "$W/collector.json" "$C" > "$W/c9.log" 2>&1; rc=$?
expect "collector without rclone on PATH → stops with a reason, no traceback, nothing written" bash -c "test $rc != 0 && grep -q '見つからない' '$W/c9.log' && ! grep -q Traceback '$W/c9.log'"
mkdir -p "$W/xdgbad/agent-kit"; echo '{broken' > "$W/xdgbad/agent-kit/collected.json"; : > "$W/drive/.calls"
PATH="$W/bin:$PATH" FAKE_DRIVE="$W/drive" XDG_CACHE_HOME="$W/xdgbad" python3 "$KITROOT/local/collect-pending.py" --config "$W/collector.json" "$C" > "$W/c10.log" 2>&1; rc=$?
expect "collector with a corrupt ledger → stops with a reason, no traceback, nothing copied" bash -c "test $rc != 0 && grep -q '台帳' '$W/c10.log' && ! grep -q Traceback '$W/c10.log' && ! grep -q '^copyto' '$W/drive/.calls'"
echo '{"00000000-0000-4000-8000-000000000000": null}' > "$W/xdgbad/agent-kit/collected.json"; : > "$W/drive/.calls"
PATH="$W/bin:$PATH" FAKE_DRIVE="$W/drive" XDG_CACHE_HOME="$W/xdgbad" python3 "$KITROOT/local/collect-pending.py" --config "$W/collector.json" "$C" > "$W/c11.log" 2>&1; rc=$?
expect "collector with a ledger entry lacking md5 (null) → stops, nothing copied" bash -c "test $rc != 0 && grep -q 'md5' '$W/c11.log' && ! grep -q '^copyto' '$W/drive/.calls'"
printf '%s' '{"folders":{"codex":"A","claude":"B"}}' > "$W/collector-nodeny.json"
PATH="$W/bin:$PATH" FAKE_DRIVE="$W/drive" python3 "$KITROOT/local/collect-pending.py" --config "$W/collector-nodeny.json" "$C" > "$W/c5.log" 2>&1; rc=$?
expect "collector refuses to run without a deny list" bash -c "test $rc != 0 && grep -q 'deny が空' '$W/c5.log'"

# ---------------------------------------------------------------------------
section 9 "スキル配布: 取り込み・版固定・検証・変更検出・ロールバック・削除・代表処理"
SS="$KITROOT/skills/skillsync.py"; SRC="${SKILL_FIXTURES:-$KITROOT/tests/fixtures/skills}"
export SKILLSYNC_TARGET_VISIBILITY=private   # 試験の配布先は非公開として扱う(公開・不明の試験では上書きする)
BUN="$W/bundle"; git init -q "$BUN"
for s in "$SRC"/*/; do python3 "$SS" export "$s" "$BUN" >> "$W/sk-export.log" 2>&1; done
expect "mac-only skill refused by export" grep -q '取り込まない: vault-like' "$W/sk-export.log"
git -C "$BUN" add -A && git -C "$BUN" commit -qm v1 && git -C "$BUN" tag v1
python3 "$SS" vendor "$BUN" "$R" --skills hello-skill,doc-skill --ref v1 > "$W/sk-v1.log" 2>&1
expect "vendored to .agents/skills and .claude/skills" test -f "$R/.agents/skills/hello-skill/SKILL.md" -a -f "$R/.claude/skills/hello-skill/SKILL.md"
expect "lock records commit + hash" python3 -c "import json;l=json.load(open('$R/.agent-kit/skills.lock.json'));assert len(l['skills']['hello-skill']['commit'])==40"
python3 "$SS" verify "$R" > "$W/sk-verify.log" 2>&1; expect "verify OK" grep -q 'verify: OK' "$W/sk-verify.log"
expect "representative run of a vendored script (codex copy)" bash -c "cd '$R/.agents/skills/hello-skill' && python3 scripts/hello.py | grep -q hello-ok"
expect "skill reference file readable" test -s "$R/.claude/skills/hello-skill/references/guide.md"
echo "v2 change" >> "$BUN/skills/hello-skill/SKILL.md"; git -C "$BUN" commit -qam v2
python3 "$SS" vendor "$BUN" "$R" --skills hello-skill > /dev/null 2>&1
expect "update to v2" grep -q 'v2 change' "$R/.claude/skills/hello-skill/SKILL.md"
python3 "$SS" rollback "$R" --bundle "$BUN" > /dev/null 2>&1
expect "rollback to v1" bash -c "! grep -q 'v2 change' '$R/.claude/skills/hello-skill/SKILL.md'"
echo "hand edit" >> "$R/.claude/skills/hello-skill/SKILL.md"
python3 "$SS" verify "$R" > "$W/sk-verify2.log" 2>&1; rc=$?
expect "verify detects hand edit" test $rc = 1
python3 "$SS" vendor "$BUN" "$R" --skills hello-skill > "$W/sk-v3.log" 2>&1; rc=$?
expect "vendor refuses to overwrite hand edit" test $rc = 2
mkdir -p "$R/.claude/skills/my-own"; echo x > "$R/.claude/skills/my-own/SKILL.md"
python3 "$SS" remove "$R" --skills doc-skill,my-own > "$W/sk-rm.log" 2>&1
expect "remove only lock-managed skills" bash -c "test ! -e '$R/.claude/skills/doc-skill' && test -e '$R/.claude/skills/my-own/SKILL.md'"
expect "zip for claude.ai upload" bash -c "python3 '$SS' zip '$BUN/skills/doc-skill' '$W/doc.zip' && unzip -l '$W/doc.zip' | grep -q doc-skill/SKILL.md"

# --- 途中で失敗しても、配布先と lock が食い違わない(修正2) ---
R3="$W/skills-target"; mkrepo "$R3"
snap() { (cd "$R3" && find .agents .claude .agent-kit -type f ! -name 'skills.lock.prev.json' 2>/dev/null | sort | xargs sha256sum 2>/dev/null) | sha256sum | cut -c1-16; }  # 配布先と lock
python3 "$SS" vendor "$BUN" "$R3" --skills hello-skill,doc-skill --ref v1 > /dev/null 2>&1
echo "v3 change" >> "$BUN/skills/doc-skill/SKILL.md"; git -C "$BUN" commit -qam v3
S0="$(snap)"
echo "hand edit (codex only)" >> "$R3/.agents/skills/doc-skill/SKILL.md"; S0e="$(snap)"
python3 "$SS" vendor "$BUN" "$R3" --skills hello-skill,doc-skill > "$W/sk-part.log" 2>&1; rc=$?
expect "vendor: one target hand-edited → nothing changed (other target, other skill, lock)" bash -c "test $rc = 2 && test '$(snap)' = '$S0e' && grep -q '何も変更していない' '$W/sk-part.log'"
python3 - "$R3" <<'PY'
import sys,subprocess,pathlib
p=pathlib.Path(sys.argv[1])/".agents/skills/doc-skill/SKILL.md"
p.write_text(p.read_text().replace("hand edit (codex only)\n",""))
PY
expect "hand edit reverted for the next tests" test "$(snap)" = "$S0"
python3 "$SS" vendor "$W/no-such-bundle" "$R3" --skills hello-skill > /dev/null 2>&1; rc=$?
expect "vendor: bundle cannot be fetched → nothing changed" bash -c "test $rc = 2 && test '$(snap)' = '$S0'"
python3 "$SS" vendor "$BUN" "$R3" --skills hello-skill --ref no-such-tag > /dev/null 2>&1; rc=$?
expect "vendor: unknown ref → nothing changed" bash -c "test $rc = 2 && test '$(snap)' = '$S0'"
SKILLSYNC_FAIL_AT=2 python3 "$SS" vendor "$BUN" "$R3" --skills hello-skill,doc-skill > /dev/null 2>&1; rc=$?
expect "vendor: failure in the middle of replacing → all targets and lock restored" bash -c "test $rc = 2 && test '$(snap)' = '$S0' && test ! -e '$R3/.agent-kit/skillsync-journal.json' && test ! -e '$R3/.agent-kit/tmp'"
SKILLSYNC_CRASH_AT=3 python3 "$SS" vendor "$BUN" "$R3" --skills hello-skill,doc-skill > /dev/null 2>&1; rc=$?
expect "vendor: process killed mid-way leaves a journal" bash -c "test $rc = 9 && test -e '$R3/.agent-kit/skillsync-journal.json'"
python3 "$SS" vendor "$BUN" "$R3" --skills hello-skill > "$W/sk-j.log" 2>&1; rc=$?
expect "vendor: refuses to run while a journal is left" bash -c "test $rc = 2 && grep -q recover '$W/sk-j.log'"
python3 "$SS" recover "$R3" > /dev/null 2>&1
expect "recover: back to the state before the killed run" bash -c "test '$(snap)' = '$S0' && test ! -e '$R3/.agent-kit/skillsync-journal.json'"
python3 "$SS" vendor "$BUN" "$R3" --skills hello-skill,doc-skill > /dev/null 2>&1
expect "vendor after recover: v3 everywhere, verify OK" bash -c "grep -q 'v3 change' '$R3/.agents/skills/doc-skill/SKILL.md' && grep -q 'v3 change' '$R3/.claude/skills/doc-skill/SKILL.md' && python3 '$SS' verify '$R3' | grep -q 'verify: OK'"
S1="$(snap)"
cp "$R3/.agent-kit/skills.lock.prev.json" "$W/prev.bak"
python3 - "$R3/.agent-kit/skills.lock.prev.json" "$W/gone-bundle" <<'PY'
import json,sys; p=sys.argv[1]; j=json.load(open(p))
for e in j["skills"].values(): e["bundle"]=sys.argv[2]
json.dump(j,open(p,"w"))
PY
python3 "$SS" rollback "$R3" --bundle "$W/gone-bundle" > /dev/null 2>&1; rc=$?
expect "rollback: restore source cannot be fetched → current files kept (nothing deleted)" bash -c "test $rc = 2 && test '$(snap)' = '$S1'"
cp "$W/prev.bak" "$R3/.agent-kit/skills.lock.prev.json"; S1="$(snap)"
python3 - "$R3/.agent-kit/skills.lock.prev.json" <<'PY'
import json,sys; p=sys.argv[1]; j=json.load(open(p)); j["skills"]["doc-skill"]["sha256"]="0"*64; json.dump(j,open(p,"w"))
PY
python3 "$SS" rollback "$R3" --bundle "$BUN" > "$W/sk-rb-hash.log" 2>&1; rc=$?
expect "rollback: restore source differs from the recorded hash → nothing changed" bash -c "test $rc = 2 && test '$(snap)' = '$S1' && grep -q 'ハッシュと違う' '$W/sk-rb-hash.log'"
cp "$W/prev.bak" "$R3/.agent-kit/skills.lock.prev.json"; S1="$(snap)"
SKILLSYNC_FAIL_AT=1 python3 "$SS" rollback "$R3" --bundle "$BUN" > /dev/null 2>&1; rc=$?
expect "rollback: failure mid-way → current state kept" bash -c "test $rc = 2 && test '$(snap)' = '$S1'"
python3 "$SS" rollback "$R3" --bundle "$BUN" > /dev/null 2>&1
expect "rollback: back to v1 content in both targets" bash -c "! grep -q 'v3 change' '$R3/.agents/skills/doc-skill/SKILL.md' && ! grep -q 'v3 change' '$R3/.claude/skills/doc-skill/SKILL.md' && python3 '$SS' verify '$R3' | grep -q 'verify: OK'"
python3 "$SS" rollback "$R3" --bundle "$BUN" > /dev/null 2>&1
expect "rollback twice: returns to the state before the first rollback" bash -c "grep -q 'v3 change' '$R3/.claude/skills/doc-skill/SKILL.md'"
echo "hand edit" >> "$R3/.claude/skills/doc-skill/SKILL.md"
python3 "$SS" remove "$R3" --skills doc-skill,hello-skill > /dev/null 2>&1; rc=$?
expect "remove: skill with one hand-edited target is kept in all targets (other skill removed)" bash -c "test $rc = 1 && test -e '$R3/.agents/skills/doc-skill/SKILL.md' && test ! -e '$R3/.agents/skills/hello-skill' && python3 -c \"import json;l=json.load(open('$R3/.agent-kit/skills.lock.json'));assert 'doc-skill' in l['skills'] and 'hello-skill' not in l['skills']\""

expect "skillsync is installed into the target (.agent-kit/bin/skillsync) for in-cloud verification" bash -c "cmp -s '$R/.agent-kit/bin/skillsync' '$KITROOT/kit/.agent-kit/bin/skillsync' && python3 '$R/.agent-kit/bin/skillsync' verify --help >/dev/null"
# --- 独立レビューの指摘への回帰テスト(skillsync) ---
R6="$W/skills-target6"; mkrepo "$R6"
python3 "$SS" vendor "$BUN" "$R6" --skills hello-skill,doc-skill --ref v1 > /dev/null 2>&1
snap6() { (cd "$R6" && find .agents .claude .agent-kit -type f ! -name 'skills.lock.prev.json' 2>/dev/null | sort | xargs sha256sum 2>/dev/null) | sha256sum | cut -c1-16; }
SKILLSYNC_CRASH_AT=5 python3 "$SS" vendor "$BUN" "$R6" --skills hello-skill,doc-skill > /dev/null 2>&1; rc=$?
python3 "$SS" recover "$R6" > "$W/sk-fwd.log" 2>&1
expect "crash after the lock was written: recover rolls forward (files and lock both new, verify OK)" bash -c "test $rc = 9 && grep -q '完了していた' '$W/sk-fwd.log' && grep -q 'v3 change' '$R6/.claude/skills/doc-skill/SKILL.md' && python3 '$SS' verify '$R6' | grep -q 'verify: OK'"
P6="$(sha256sum "$R6/.agent-kit/skills.lock.prev.json" | cut -c1-16)"
python3 "$SS" remove "$R6" --skills no-such-skill > /dev/null 2>&1
expect "remove of an unknown name does not overwrite the rollback point (.prev)" test "$(sha256sum "$R6/.agent-kit/skills.lock.prev.json" | cut -c1-16)" = "$P6"
python3 "$SS" vendor "$BUN" "$R6" --skills hello-skill,doc-skill > /dev/null 2>&1
expect "re-vendor of the same version does not overwrite the rollback point" test "$(sha256sum "$R6/.agent-kit/skills.lock.prev.json" | cut -c1-16)" = "$P6"
S6="$(snap6)"; git -C "$BUN" tag -f v9 v1 >/dev/null
SKILLSYNC_CRASH_AT=2 python3 "$SS" vendor "$BUN" "$R6" --skills hello-skill,doc-skill --ref v9 > /dev/null 2>&1
rm -rf "$R6/.agent-kit/tmp"
python3 "$SS" recover "$R6" > "$W/sk-notmp.log" 2>&1; rc=$?
expect "recover refuses (changes nothing) when the temp area is gone" bash -c "test $rc = 2 && grep -q '一時領域' '$W/sk-notmp.log' && test -e '$R6/.agent-kit/skillsync-journal.json'"
rm -f "$R6/.agent-kit/skillsync-journal.json"
R7="$W/skills-target7"; mkrepo "$R7"
python3 - "$R7" "$SS" "$BUN" > "$W/sk-lock.log" 2>&1 <<'PY'
import fcntl,subprocess,sys,os
r,ss,bun=sys.argv[1:]
gd=subprocess.run(["git","-C",r,"rev-parse","--absolute-git-dir"],capture_output=True,text=True).stdout.strip()
with open(os.path.join(gd,"skillsync.lock"),"a+") as f:
    fcntl.flock(f,fcntl.LOCK_EX)
    p=subprocess.run([sys.executable,ss,"vendor",bun,r,"--skills","hello-skill"],capture_output=True,text=True)
    print(p.returncode, p.stderr)
PY
expect "two skillsync runs on the same target: the second does nothing" bash -c "grep -q '^2 ' '$W/sk-lock.log' && grep -q '実行中' '$W/sk-lock.log' && test ! -e '$R7/.claude/skills/hello-skill'"
env -u SKILLSYNC_TARGET_VISIBILITY python3 "$SS" vendor "$BUN" "$R7" --skills hello-skill > "$W/sk-unk.log" 2>&1; rc=$?
expect "visibility unknown (no GitHub origin) is treated like public: refused without --public-target" bash -c "test $rc = 2 && grep -q '確かめられない' '$W/sk-unk.log'"
expect "license allow-list is exact (Mitsubishi Proprietary / CC-BY-NC-ND / Not MIT are not redistributable)" python3 - "$KITROOT/kit/.agent-kit/bin/skillsync" <<'PY'
import runpy,sys
g=runpy.run_path(sys.argv[1],run_name="x")
r=g["redistributable"]
assert r("MIT License") and r("Apache License, Version 2.0") and r("BSD 3-Clause License") and r("ISC License")
assert not r("Mitsubishi Proprietary") and not r("CC-BY-NC-ND 4.0") and not r("Not MIT") and not r("unknown")
PY
python3 "$SS" vendor "$BUN" "$R7" --skills hello-skill --targets codex,bogus > "$W/sk-tg.log" 2>&1; rc=$?
expect "invalid --targets is reported (no traceback, nothing changed)" bash -c "test $rc = 2 && ! grep -q Traceback '$W/sk-tg.log' && test ! -e '$R7/.agents/skills'"
mkdir -p "$HOME/bundles"; git clone -q "$BUN" "$HOME/bundles/b1"
python3 "$SS" vendor "$HOME/bundles/b1" "$R7" --skills hello-skill --ref v1 > /dev/null 2>&1
python3 "$SS" vendor "$HOME/bundles/b1" "$R7" --skills hello-skill > /dev/null 2>&1
expect "lock records a home-relative bundle path (no user name)" python3 -c "import json;l=json.load(open('$R7/.agent-kit/skills.lock.json'));assert l['skills']['hello-skill']['bundle']=='~/bundles/b1', l"
mv "$HOME/bundles/b1" "$HOME/bundles/b2"
python3 "$SS" rollback "$R7" > /dev/null 2>&1; rc1=$?
python3 "$SS" rollback "$R7" --bundle "$HOME/bundles/b2" > /dev/null 2>&1; rc2=$?
expect "rollback after the bundle moved: refused by default, works with --bundle (hash still checked)" bash -c "test $rc1 = 2 && test $rc2 = 0"

expect "lock never records credentials from a bundle URL" python3 - "$KITROOT/kit/.agent-kit/bin/skillsync" <<'PY'
import runpy,sys
g=runpy.run_path(sys.argv[1],run_name="x")
u="https://x-access-token:tok" + "en123" + "@" + "github.com/me/skills.git"   # 点検に引っかからないよう分けて書く
assert g["bundle_location"](u)=="https://github.com/me/skills.git"
PY
# --- R4: lock 保存後・.prev 保存時・undo の失敗(ファイル・lock・.prev・journal を1つの状態にそろえる) ---
R8="$W/skills-target8"; mkrepo "$R8"
python3 "$SS" vendor "$BUN" "$R8" --skills hello-skill,doc-skill --ref v1 > /dev/null 2>&1
python3 "$SS" vendor "$BUN" "$R8" --skills hello-skill,doc-skill > /dev/null 2>&1     # v1 → 最新(.prev ができる)
python3 "$SS" rollback "$R8" --bundle "$BUN" > /dev/null 2>&1                                          # v1 に戻す(次の更新に差分がある状態)
snap8() { (cd "$R8" && find .agents .claude .agent-kit -type f 2>/dev/null | sort | xargs sha256sum 2>/dev/null) | sha256sum | cut -c1-16; }  # .prev も含める
for fp in after-lock prev; do
  S8="$(snap8)"
  SKILLSYNC_FAIL_POINT=$fp python3 "$SS" vendor "$BUN" "$R8" --skills hello-skill,doc-skill > "$W/sk-fp-$fp.log" 2>&1; rc=$?
  expect "R4: failure at '$fp' → files, lock and .prev all back to the previous state, no journal left" bash -c "test $rc = 2 && test '$(snap8)' = '$S8' && test ! -e '$R8/.agent-kit/skillsync-journal.json'"
done
S8="$(snap8)"
SKILLSYNC_FAIL_POINT=after-lock SKILLSYNC_FAIL_POINT_UNDO=1 python3 - "$SS" "$BUN" "$R8" > "$W/sk-undo.log" 2>&1 <<'PY'
import os,subprocess,sys
ss,bun,r=sys.argv[1:]
env=dict(os.environ); env["SKILLSYNC_FAIL_POINT"]="undo"; env.pop("SKILLSYNC_FAIL_POINT_UNDO",None)
# 差し替えの途中で失敗(FAIL_AT=1)させ、さらに undo も失敗させる
env["SKILLSYNC_FAIL_AT"]="1"
p=subprocess.run([sys.executable,ss,"vendor",bun,r,"--skills","hello-skill,doc-skill"],env=env,capture_output=True,text=True)
print(p.returncode); print(p.stderr)
PY
expect "R4: undo itself fails → journal and temp area are kept, told to run recover" bash -c "grep -q '^2' '$W/sk-undo.log' && grep -q 'recover' '$W/sk-undo.log' && test -e '$R8/.agent-kit/skillsync-journal.json'"
python3 "$SS" recover "$R8" > "$W/sk-undo-rec.log" 2>&1; rc=$?
expect "R4: recover after a failed undo returns everything (files, lock, .prev) to the state before the run" bash -c "test $rc = 0 && test '$(snap8)' = '$S8' && test ! -e '$R8/.agent-kit/skillsync-journal.json'"
SKILLSYNC_CRASH_POINT=after-lock python3 "$SS" vendor "$BUN" "$R8" --skills hello-skill,doc-skill > /dev/null 2>&1; rc=$?
python3 "$SS" recover "$R8" > "$W/sk-crash-lock.log" 2>&1
expect "R4: crash right after the new lock was saved (before .prev) → recover completes forward and fixes .prev" bash -c "test $rc = 9 && grep -q '完了していた' '$W/sk-crash-lock.log' && python3 '$SS' verify '$R8' | grep -q 'verify: OK' && python3 -c \"import json;p=json.load(open('$R8/.agent-kit/skills.lock.prev.json'));l=json.load(open('$R8/.agent-kit/skills.lock.json'));assert p!=l\""

# --- R7: スキル名による範囲外の削除・書き込み ---
R9="$W/skills-target9"; mkrepo "$R9"; mkdir -p "$W/outside"; echo keep > "$W/outside/keep.txt"
O0="$(cd "$W/outside" && find . -type f | sort | xargs sha256sum | sha256sum)"
for bad in "../outside" "/tmp/abs" ".." "a/b" ".hidden-ok?"; do
  python3 "$SS" vendor "$BUN" "$R9" --skills "$bad" > "$W/sk-bad.log" 2>&1; rc=$?
  expect "R7: vendor refuses skill name '$bad' and changes nothing outside" bash -c "test $rc = 2 && test '$(cd "$W/outside" && find . -type f | sort | xargs sha256sum | sha256sum)' = '$O0' && ! grep -q Traceback '$W/sk-bad.log'"
done
python3 "$SS" export "$SRC/hello-skill" "$BUN" --name "../outside" > "$W/sk-exp-bad.log" 2>&1; rc=$?
expect "R7: export refuses a name pointing outside the bundle (outside dir untouched)" bash -c "test $rc = 2 && test -e '$W/outside/keep.txt'"
mkdir -p "$R9/.claude/skills"; ln -s "$W/outside" "$R9/.claude/skills/linked-skill"
python3 - "$R9/.agent-kit/skills.lock.json" <<'PY'
import json,sys,os; os.makedirs(os.path.dirname(sys.argv[1]),exist_ok=True)
json.dump({"version":1,"skills":{"linked-skill":{"bundle":"x","commit":"0"*40,"sha256":"0","targets":{"claude":"0"}}}},open(sys.argv[1],"w"))
PY
python3 "$SS" remove "$R9" --skills linked-skill > "$W/sk-rm-link.log" 2>&1
expect "R7: remove never follows a symlinked skill dir out of the target (outside files kept)" bash -c "test -e '$W/outside/keep.txt' && test -L '$R9/.claude/skills/linked-skill'"
printf '%s' '{"version":1,"skills":{"../../outside":{"bundle":"x","commit":"0","sha256":"0","targets":{"claude":"0"}}}}' > "$R9/.agent-kit/skills.lock.prev.json"
python3 "$SS" rollback "$R9" --bundle "$BUN" > "$W/sk-rb-bad.log" 2>&1; rc=$?
expect "R7: rollback refuses a lock with an out-of-range skill name" bash -c "test $rc = 2 && grep -q '不正' '$W/sk-rb-bad.log' && test -e '$W/outside/keep.txt'"

# --- R2: zip にも export と同じ検査(ダミーの秘密を使う。本物の秘密値は使わない) ---
ZS="$W/zip-skills"; mkdir -p "$ZS/envskill" "$ZS/keyskill" "$ZS/bigskill"
for d in envskill keyskill bigskill; do printf -- '---\nname: %s\ndescription: test skill\n---\nbody\n' "$d" > "$ZS/$d/SKILL.md"; done
printf 'DUMMY_SETTING=not-a-real-value\n' > "$ZS/envskill/.env"
printf -- '-----BEGIN %s PRIVATE KEY-----\nDUMMYDUMMY\n' OPENSSH > "$ZS/keyskill/notes.txt"
head -c 1100000 /dev/zero | tr '\0' 'a' > "$ZS/bigskill/data.txt"
for d in envskill keyskill bigskill; do
  python3 "$SS" zip "$ZS/$d" "$W/$d.zip" > "$W/zip-$d.log" 2>&1; rc=$?
  expect "R2: zip refuses '$d' (secret / key / size check like export) and leaves no zip or temp file" bash -c "test $rc = 2 && test ! -e '$W/$d.zip' && test -z \"\$(ls '$W' | grep -F '$d.zip.tmp')\" && grep -q 'zip は作っていない' '$W/zip-$d.log'"
done

# --- 独立レビュー(4回目)の指摘: リンクの親・UTF-8 でない秘密・名前の規則・自分自身の梱包・読めない下位ディレクトリ ---
R10="$W/skills-target10"; mkrepo "$R10"; mkdir -p "$W/outside2/skills"; echo keep > "$W/outside2/keep.txt"
ln -s "$W/outside2" "$R10/.claude"
O2="$(cd "$W/outside2" && find . | sort | sha256sum)"
python3 "$SS" vendor "$BUN" "$R10" --skills hello-skill > "$W/sk-plink.log" 2>&1; rc=$?
expect "R7: target whose .claude is a symlink to outside → vendor refused, nothing written outside" bash -c "test $rc = 2 && grep -q 'シンボリックリンク' '$W/sk-plink.log' && test '$(cd "$W/outside2" && find . | sort | sha256sum)' = '$O2' && ! grep -q Traceback '$W/sk-plink.log'"
mkdir -p "$W/outside2/skills/hello-skill"; echo keep > "$W/outside2/skills/hello-skill/SKILL.md"
python3 - "$R10/.agent-kit/skills.lock.json" <<'PY'
import json,sys,os; os.makedirs(os.path.dirname(sys.argv[1]),exist_ok=True)
json.dump({"version":1,"skills":{"hello-skill":{"bundle":"x","commit":"0"*40,"sha256":"0","targets":{"claude":"0"}}}},open(sys.argv[1],"w"))
PY
python3 "$SS" remove "$R10" --skills hello-skill > "$W/sk-plink2.log" 2>&1; rc=$?
expect "R7: remove through a symlinked .claude → refused, files outside kept" bash -c "test $rc = 2 && test -e '$W/outside2/skills/hello-skill/SKILL.md'"
python3 "$SS" verify "$R10" > "$W/sk-plink3.log" 2>&1; rc=$?
expect "R7: verify does not look through a symlinked .claude either" bash -c "test $rc = 1 && grep -q 'シンボリックリンク' '$W/sk-plink3.log'"
R11="$W/skills-target11"; mkrepo "$R11"; mkdir -p "$W/outside3"; ln -s "$W/outside3" "$R11/.agent-kit"
python3 "$SS" vendor "$BUN" "$R11" --skills hello-skill > "$W/sk-klink.log" 2>&1; rc=$?
expect "R7: .agent-kit as a symlink → refused (lock and journal never written outside)" bash -c "test $rc = 2 && test -z \"\$(ls -A '$W/outside3')\""
mkdir -p "$ZS/latin" "$ZS/prodenv" "$ZS/envsh" "$ZS/envex"
for d in latin prodenv envsh envex; do printf -- '---\nname: %s\ndescription: test skill\n---\nbody\n' "$d" > "$ZS/$d/SKILL.md"; done
# UTF-8 でないファイル(Latin-1)に、ダミーのアクセスキーの形の文字列と、ダミーの鍵の見出し
printf '\xe9t\xe9 %s%s\n' AKIA DUMMYDUMMYDUMMY0 > "$ZS/latin/notes.txt"
printf '\xe9\xe9 -----BEGIN %s PRIVATE KEY-----\n' RSA > "$ZS/latin/k.dat"
printf 'DUMMY=1\n' > "$ZS/prodenv/prod.env"
printf 'export DUMMY=1\n' > "$ZS/envsh/.env.sh"
printf 'DUMMY=\n' > "$ZS/envex/.env.example"
for d in latin prodenv envsh; do
  python3 "$SS" zip "$ZS/$d" "$W/$d.zip" > "$W/zip-$d.log" 2>&1; rc=$?
  expect "R2: zip refuses '$d' (non-UTF-8 secret text / prod.env / .env.sh) and leaves no zip" bash -c "test $rc = 2 && test ! -e '$W/$d.zip' && grep -q 'zip は作っていない' '$W/zip-$d.log'"
done
python3 "$SS" zip "$ZS/envex" "$W/envex.zip" > "$W/zip-envex.log" 2>&1; rc=$?
expect "R2: a .env.example (no values) is not treated as a secret file" bash -c "test $rc = 0 && test -e '$W/envex.zip'"
python3 "$SS" zip "$ZS/envex" "$ZS/envex/self.zip" > "$W/zip-self.log" 2>&1; rc=$?
expect "zip refuses an output path inside the skill dir (would pack itself)" bash -c "test $rc = 2 && test ! -e '$ZS/envex/self.zip' && grep -q '自分自身' '$W/zip-self.log'"
mkdir -p "$ZS/unread/sub"; printf -- '---\nname: unread\ndescription: test skill\n---\nbody\n' > "$ZS/unread/SKILL.md"; echo x > "$ZS/unread/sub/a.txt"
chmod 755 "$W" "$ZS"; chmod 711 "$ZS/unread"; chmod 000 "$ZS/unread/sub"; chmod 644 "$ZS/unread/SKILL.md"; chmod 1777 "$W" 2>/dev/null
if command -v setpriv >/dev/null && [ "$(id -u)" = 0 ]; then
  setpriv --reuid=65534 --regid=65534 --clear-groups python3 "$SS" zip "$ZS/unread" "/tmp/unread-$$.zip" > "$W/zip-unread.log" 2>&1; rc=$?
  expect "unreadable subdirectory → zip refused (not silently skipped), no traceback" bash -c "test $rc = 2 && test ! -e /tmp/unread-$$.zip && grep -q '読めない' '$W/zip-unread.log' && ! grep -q Traceback '$W/zip-unread.log'"
else
  python3 "$SS" zip "$ZS/unread" "$W/unread.zip" > "$W/zip-unread.log" 2>&1; rc=$?
  expect "unreadable subdirectory → zip refused (not silently skipped), no traceback" bash -c "test $rc = 2 && test ! -e '$W/unread.zip' && ! grep -q Traceback '$W/zip-unread.log'"
fi
chmod 755 "$ZS/unread/sub"; chmod 755 "$W"

# --- 独立レビュー(5回目)の指摘 ---
# journal の書き換え: 一時領域を範囲の外にしても、recover は消さない
R12="$W/skills-target12"; mkrepo "$R12"; mkdir -p "$W/outside4"; echo keep > "$W/outside4/keep.txt"
python3 "$SS" vendor "$BUN" "$R12" --skills hello-skill > /dev/null 2>&1
printf '{"work":"%s","ops":[],"old_lock":null,"old_prev":null,"new_lock_sha":"x"}' "$W/outside4" > "$R12/.agent-kit/skillsync-journal.json"
python3 "$SS" recover "$R12" > "$W/sk-jbad.log" 2>&1; rc=$?
expect "recover refuses a journal whose paths point outside (outside dir and lock untouched)" bash -c "test $rc = 2 && test -e '$W/outside4/keep.txt' && test -e '$R12/.agent-kit/skills.lock.json' && grep -q '想定と違う' '$W/sk-jbad.log'"
python3 - "$R12/.agent-kit/skillsync-journal.json" "$R12" "$W/outside4" <<'PY'
import json,sys; w=f"{sys.argv[2]}/.agent-kit/tmp/skillsync-1-abcdef"
json.dump({"work":w,"ops":[{"dst":sys.argv[3],"new":None,"old":w+"/old/0","moved_old":True}],"old_lock":None,"old_prev":None,"new_lock_sha":"x"},open(sys.argv[1],"w"))
PY
python3 "$SS" recover "$R12" > "$W/sk-jbad2.log" 2>&1; rc=$?
expect "recover refuses a journal whose op target is outside the managed dirs" bash -c "test $rc = 2 && test -e '$W/outside4/keep.txt'"
rm -f "$R12/.agent-kit/skillsync-journal.json"
# .agent-kit の中のリンク(一時領域・lock の一時ファイル)
mkdir -p "$W/outside5"; ln -s "$W/outside5" "$R12/.agent-kit/tmp"
python3 "$SS" vendor "$BUN" "$R12" --skills doc-skill > "$W/sk-tmplink.log" 2>&1; rc=$?
expect "vendor refuses when .agent-kit/tmp is a symlink (nothing staged outside)" bash -c "test $rc = 2 && test -z \"\$(ls -A '$W/outside5')\""
rm "$R12/.agent-kit/tmp"; echo victim > "$W/victim.txt"
ln -s "$W/victim.txt" "$R12/.agent-kit/skills.lock.prev.json"
python3 "$SS" remove "$R12" --skills hello-skill > "$W/sk-prevlink.log" 2>&1; rc=$?
expect "a symlinked lock file under .agent-kit is never written through (victim file unchanged)" bash -c "test $rc = 2 && test \"\$(cat '$W/victim.txt')\" = victim"
rm "$R12/.agent-kit/skills.lock.prev.json"
# 前の内容が一時領域から消えたら、recover は「戻した」と言わず、何も変えない
R13="$W/skills-target13"; mkrepo "$R13"
python3 "$SS" vendor "$BUN" "$R13" --skills hello-skill --ref v1 > /dev/null 2>&1
SKILLSYNC_CRASH_AT=1 python3 "$SS" vendor "$BUN" "$R13" --skills hello-skill > /dev/null 2>&1
W13="$(python3 -c "import json;print(json.load(open('$R13/.agent-kit/skillsync-journal.json'))['work'])")"
rm -rf "$W13/old"
python3 "$SS" recover "$R13" > "$W/sk-lost.log" 2>&1; rc=$?
expect "recover refuses (keeps the journal) when the moved-away previous copy is gone" bash -c "test $rc = 2 && grep -q '前の内容' '$W/sk-lost.log' && test -e '$R13/.agent-kit/skillsync-journal.json'"
# 秘密: UTF-16、credentials.json・.envrc。blocked は --force でも配らない。正本がスキルの中の export
mkdir -p "$ZS/u16" "$ZS/credj" "$ZS/envrc"
for d in u16 credj envrc; do printf -- '---\nname: %s\ndescription: test skill\n---\nbody\n' "$d" > "$ZS/$d/SKILL.md"; done
python3 -c "import sys;open(sys.argv[1],'wb').write(('-----BEGIN '+'RSA PRIVATE KEY-----\nDUMMY\n').encode('utf-16'))" "$ZS/u16/notes.txt"
printf '{"dummy":true}\n' > "$ZS/credj/credentials.json"; printf 'export DUMMY=1\n' > "$ZS/envrc/.envrc"
for d in u16 credj envrc; do
  python3 "$SS" zip "$ZS/$d" "$W/$d.zip" > "$W/zip-$d.log" 2>&1; rc=$?
  expect "R2: zip refuses '$d' (UTF-16 key header / credentials.json / .envrc)" bash -c "test $rc = 2 && test ! -e '$W/$d.zip'"
done
BB="$W/bad-bundle"; mkdir -p "$BB/skills/badsk"; printf -- '---\nname: badsk\ndescription: test skill\n---\nbody\n' > "$BB/skills/badsk/SKILL.md"
printf 'key %s%s\n' AKIA DUMMYDUMMYDUMMY0 > "$BB/skills/badsk/notes.txt"; git -C "$BB" init -q; git -C "$BB" add -A; git -C "$BB" -c user.email=t@example.com -c user.name=t commit -qm b
R14="$W/skills-target14"; mkrepo "$R14"
python3 "$SS" vendor "$BB" "$R14" --skills badsk --force > "$W/sk-force.log" 2>&1; rc=$?
expect "vendor --force never deploys a skill blocked for secret-like content" bash -c "test $rc = 2 && test ! -e '$R14/.claude/skills/badsk' && grep -q 'blocked' '$W/sk-force.log'"
python3 "$SS" export "$SRC/hello-skill" "$SRC/hello-skill/bund" > "$W/sk-exp-self.log" 2>&1; rc=$?
expect "export refuses a bundle inside the source skill (no nested copy)" bash -c "test $rc = 2 && test ! -e '$SRC/hello-skill/bund'"

# --- セット・ライセンス・公開リポジトリ・代表処理 ---
R4="$W/skills-target4"; mkrepo "$R4"
printf '%s' '{"sets":{"core":["hello-skill"],"docs":["doc-skill"]}}' > "$BUN/sets.json"
printf '%s' '{"hello-skill":["python3 scripts/hello.py"]}' > "$BUN/checks.json"
git -C "$BUN" add -A && git -C "$BUN" commit -qm sets
python3 "$SS" vendor "$BUN" "$R4" --set core,docs > /dev/null 2>&1; rc=$?
expect "vendor --set: skills chosen from the bundle's sets.json" bash -c "test $rc = 0 && test -e '$R4/.claude/skills/hello-skill/SKILL.md' && test -e '$R4/.agents/skills/doc-skill/SKILL.md'"
expect "lock records license and upstream info (MIT for hello-skill, unknown for doc-skill)" python3 -c "import json;l=json.load(open('$R4/.agent-kit/skills.lock.json'))['skills'];assert l['hello-skill']['license']['name']=='MIT License' and l['doc-skill']['license']['name']=='unknown', l"
python3 "$SS" vendor "$BUN" "$R4" --set nosuch > /dev/null 2>&1; rc=$?
expect "vendor: unknown set → refused" test $rc = 2
python3 "$SS" verify "$R4" --run > "$W/sk-run0.log" 2>&1; rc0=$?
expect "verify --run without --bundle: smoke is not run (bundle location in the lock is not trusted)" bash -c "test $rc0 = 1 && grep -q -- '--bundle で指定' '$W/sk-run0.log'"
python3 "$SS" verify "$R4" --run --bundle "$BUN" > "$W/sk-run.log" 2>&1; rc=$?
expect "verify --run: runs the reviewed representative command in each copy" bash -c "test $rc = 0 && grep -q '代表処理 1 件' '$W/sk-run.log'"
printf '%s' '{"hello-skill":["python3 scripts/does-not-exist.py"]}' > "$BUN/checks.json"; git -C "$BUN" commit -qam badcheck
python3 "$SS" vendor "$BUN" "$R4" --skills hello-skill > /dev/null 2>&1
python3 "$SS" verify "$R4" --run --bundle "$BUN" > /dev/null 2>&1; rc=$?
expect "verify --run: a failing representative command is reported" test $rc = 1
python3 - "$R4/.agent-kit/skills.lock.json" "$W/pwned" <<'PY'
import json,sys; p=sys.argv[1]; l=json.load(open(p)); l["skills"]["hello-skill"]["smoke"]=[f"touch {sys.argv[2]}"]; json.dump(l,open(p,"w"))
PY
python3 "$SS" verify "$R4" --run --bundle "$BUN" > "$W/sk-run-tamper.log" 2>&1; rc=$?
expect "verify --run: a smoke command written into the lock (not in the bundle's checks.json) is never run" bash -c "test $rc = 1 && test ! -e '$W/pwned' && grep -q '正本の checks.json と違う' '$W/sk-run-tamper.log'"
# lock の正本の場所を、攻撃者の用意した場所(配布先のリポジトリ自身)に書き換え、同じコマンドの checks.json も置く
printf '{"hello-skill":["touch %s"]}' "$W/pwned2" > "$R4/checks.json"; git -C "$R4" add -A >/dev/null 2>&1; git -C "$R4" commit -qm evil >/dev/null 2>&1
python3 - "$R4/.agent-kit/skills.lock.json" "$R4" "$W/pwned2" <<'PY'
import json,sys; p=sys.argv[1]; l=json.load(open(p)); e=l["skills"]["hello-skill"]; e["bundle"]=sys.argv[2]; e["commit"]="HEAD"; e["smoke"]=[f"touch {sys.argv[3]}"]; json.dump(l,open(p,"w"))
PY
python3 "$SS" verify "$R4" --run --bundle "$BUN" > "$W/sk-run-redir.log" 2>&1; rc=$?
expect "verify --run: a lock redirected to another bundle (and commit HEAD) is never used to run commands" bash -c "test $rc = 1 && test ! -e '$W/pwned2' && grep -q '40 桁' '$W/sk-run-redir.log'"
python3 - "$R4/.agent-kit/skills.lock.json" "$(git -C "$R4" rev-parse HEAD)" <<'PY'
import json,sys; p=sys.argv[1]; l=json.load(open(p)); l["skills"]["hello-skill"]["commit"]=sys.argv[2]; json.dump(l,open(p,"w"))
PY
python3 "$SS" verify "$R4" --run --bundle "$BUN" > "$W/sk-run-redir3.log" 2>&1; rc=$?
expect "verify --run: a lock naming another bundle location (with a real SHA) is refused against --bundle" bash -c "test $rc = 1 && test ! -e '$W/pwned2' && grep -q -- '--bundle と違う' '$W/sk-run-redir3.log'"
# --- 独立レビュー(6回目)の指摘: 配布先に置いたコードを verify に実行させない ---
printf '%s' '{"hello-skill":["python3 scripts/hello.py"]}' > "$BUN/checks.json"; git -C "$BUN" commit -qam goodcheck
R15="$W/skills-target15"; mkrepo "$R15"
python3 "$SS" vendor "$BUN" "$R15" --skills hello-skill > /dev/null 2>&1
printf 'open("%s","w").write("x")\n' "$W/pwned6" > "$R15/py_compile.py"
(cd "$R15" && python3 "$SS" verify . > "$W/sk-pyc.log" 2>&1)
expect "verify run from inside the target does not import a planted py_compile.py" bash -c "test ! -e '$W/pwned6'"
rm -f "$R15/py_compile.py"
python3 "$SS" verify "$R15" --run --bundle "$BUN" > "$W/sk-good.log" 2>&1; rc=$?
expect "verify --run --bundle: the reviewed smoke runs (in a fresh copy from the trusted bundle)" bash -c "test $rc = 0 && grep -q '代表処理 1 件' '$W/sk-good.log' && test -z \"\$(find '$R15/.claude' '$R15/.agents' -name __pycache__)\""
cp "$R15/.claude/skills/hello-skill/scripts/hello.py" "$W/hello.bak"
printf 'open("%s","w").write("x")\n' "$W/pwned7" > "$R15/.claude/skills/hello-skill/scripts/hello.py"
python3 - "$R15" "$SS" <<'PY'
import json,runpy,sys
from pathlib import Path
g=runpy.run_path(str(Path(sys.argv[2]).resolve().parent.parent/"kit/.agent-kit/bin/skillsync"),run_name="x")
p=Path(sys.argv[1])/".agent-kit/skills.lock.json"; l=json.load(open(p))
l["skills"]["hello-skill"]["targets"]["claude"]=g["sha_tree"](Path(sys.argv[1])/".claude/skills/hello-skill"); json.dump(l,open(p,"w"))
PY
python3 "$SS" verify "$R15" --run --bundle "$BUN" > "$W/sk-edit.log" 2>&1; rc=$?
expect "verify --run: a deployed script changed together with the lock hash is not run" bash -c "test $rc = 1 && test ! -e '$W/pwned7' && grep -q '正本の中身と違う' '$W/sk-edit.log'"
SKILLSYNC_TARGET_VISIBILITY=private python3 "$SS" remove "$R15" --skills hello-skill > /dev/null 2>&1; rm -rf "$R15/.claude/skills/hello-skill"
python3 "$SS" vendor "$BUN" "$R15" --skills hello-skill > /dev/null 2>&1
mkdir -p "$R15/.agents/skills/hello-skill/scripts/__pycache__"; echo planted > "$R15/.agents/skills/hello-skill/scripts/__pycache__/helper.cpython-313.pyc"
python3 "$SS" verify "$R15" --run --bundle "$BUN" > "$W/sk-pycache.log" 2>&1; rc=$?
expect "verify --run: a planted file in a skipped dir (__pycache__) makes the copy differ → smoke not run" bash -c "test $rc = 1 && grep -q '正本の中身と違う' '$W/sk-pycache.log'"
rm -rf "$R15/.agents/skills/hello-skill/scripts/__pycache__"
# recover: 一時領域の中のリンク・空にされた退避先・書き換えた had_old
R16="$W/skills-target16"; mkrepo "$R16"; mkdir -p "$W/outside6/old/0"; echo precious > "$W/outside6/old/0/precious.txt"
python3 "$SS" vendor "$BUN" "$R16" --skills hello-skill --ref v1 > /dev/null 2>&1
SKILLSYNC_CRASH_AT=1 python3 "$SS" vendor "$BUN" "$R16" --skills hello-skill > /dev/null 2>&1
W16="$(python3 -c "import json;print(json.load(open('$R16/.agent-kit/skillsync-journal.json'))['work'])")"
mv "$W16/old" "$W16/old.real"; ln -s "$W/outside6/old" "$W16/old"
python3 "$SS" recover "$R16" > "$W/sk-oldlink.log" 2>&1; rc=$?
expect "recover refuses when work/old is a symlink (outside dir not moved in)" bash -c "test $rc = 2 && test -e '$W/outside6/old/0/precious.txt' && test -e '$R16/.agent-kit/skillsync-journal.json'"
rm "$W16/old"; mv "$W16/old.real" "$W16/old"
rm -rf "$W16/old/0"/*
python3 "$SS" recover "$R16" > "$W/sk-oldempty.log" 2>&1; rc=$?
expect "recover refuses when the moved-away copy was emptied (no false '戻した')" bash -c "test $rc = 2 && ! grep -q '戻した' '$W/sk-oldempty.log'"
rm -rf "$W16/old"
python3 - "$R16/.agent-kit/skillsync-journal.json" <<'PY'
import json,sys; p=sys.argv[1]; j=json.load(open(p))
for o in j["ops"]: o["had_old"]=False
json.dump(j,open(p,"w"))
PY
python3 "$SS" recover "$R16" > "$W/sk-hadold.log" 2>&1; rc=$?
expect "recover still refuses when had_old was flipped in the journal (old lock lists the target)" bash -c "test $rc = 2 && grep -q '前の内容' '$W/sk-hadold.log'"
rm -f "$R16/.agent-kit/skillsync-journal.json"
# 秘密の名前・文字列: 普通の名前は止めない、PGP の鍵・deploy の鍵は止める
FP="$W/fp-skill"; mkdir -p "$FP/assets" "$FP/references" "$FP/examples"
printf -- '---\nname: fp-skill\ndescription: test skill\n---\nTokens start with `xoxb-` or `github_pat_`.\n' > "$FP/SKILL.md"
for f in assets/tokenizer.json references/token-limits.txt references/credentials-setup.txt examples/secret_santa.csv references/guide.asc references/prod.env.md; do echo ok > "$FP/$f"; done
python3 "$SS" zip "$FP" "$W/fp.zip" > "$W/zip-fp.log" 2>&1; rc=$?
expect "ordinary names (tokenizer.json, token-limits.txt, secret_santa.csv, guide.asc, prod.env.md) and prose mentions are not treated as secrets" bash -c "test $rc = 0 && test -e '$W/fp.zip'"
mkdir -p "$ZS/pgp" "$ZS/dkey"; for d in pgp dkey; do printf -- '---\nname: %s\ndescription: test skill\n---\nbody\n' "$d" > "$ZS/$d/SKILL.md"; done
printf -- '-----BEGIN PGP %s KEY BLOCK-----\nDUMMY\n' PRIVATE > "$ZS/pgp/k.txt"; echo dummy > "$ZS/dkey/github_deploy_key"; echo dummy > "$ZS/dkey/deploy_id_rsa"
for d in pgp dkey; do
  python3 "$SS" zip "$ZS/$d" "$W/$d.zip" > "$W/zip-$d.log" 2>&1; rc=$?
  expect "zip refuses '$d' (PGP private key block / deploy key file names)" bash -c "test $rc = 2 && test ! -e '$W/$d.zip'"
done
mkdir -p "$ZS/bak" "$ZS/tok" "$ZS/ph"; for d in bak tok ph; do printf -- '---\nname: %s\ndescription: test skill\n---\nbody\n' "$d" > "$ZS/$d/SKILL.md"; done
echo dummy > "$ZS/bak/id_rsa.bak"; printf 'x %s%s\n' ghs_ DUMMYDUMMYDUMMYDUMMYDUMMYDUMMYDU > "$ZS/tok/t.txt"
printf 'Set SLACK_TOKEN=%sb-your-bot-token-here and OPENAI_API_KEY=%s-your-openai-api-key-goes-here-1234\n' xox sk > "$ZS/ph/README.md"; echo cert > "$ZS/ph/cert.der"
for d in bak tok; do python3 "$SS" zip "$ZS/$d" "$W/$d.zip" > /dev/null 2>&1; rc=$?
  expect "zip refuses '$d' (backup copy of a key file / ghs_ token)" bash -c "test $rc = 2 && test ! -e '$W/$d.zip'"; done
python3 "$SS" zip "$ZS/ph" "$W/ph.zip" > "$W/zip-ph.log" 2>&1; rc=$?
expect "placeholders in docs (xoxb-your-…-here, sk-your-…) and a public .der certificate are not blocked" bash -c "test $rc = 0"
# recover: journal の前の lock を書き換えても、今の lock と合わなければ信用しない
R17="$W/skills-target17"; mkrepo "$R17"
python3 "$SS" vendor "$BUN" "$R17" --skills hello-skill --ref v1 > /dev/null 2>&1
SKILLSYNC_CRASH_AT=1 python3 "$SS" vendor "$BUN" "$R17" --skills hello-skill > /dev/null 2>&1
python3 - "$R17/.agent-kit/skillsync-journal.json" <<'PY'
import json,sys,shutil; p=sys.argv[1]; j=json.load(open(p)); shutil.rmtree(j["work"]+"/old",ignore_errors=True)
for o in j["ops"]: o["had_old"]=False
j["old_lock"]=json.dumps({"version":1,"skills":{}}); json.dump(j,open(p,"w"))
PY
L17="$(sha256sum "$R17/.agent-kit/skills.lock.json")"
python3 "$SS" recover "$R17" > "$W/sk-oldlock.log" 2>&1; rc=$?
expect "recover refuses a journal whose old lock was edited (does not match the lock on disk); lock kept" bash -c "test $rc = 2 && test \"\$(sha256sum '$R17/.agent-kit/skills.lock.json')\" = '$L17'"
SKILLSYNC_CRASH_AT=1 python3 "$SS" vendor "$BUN" "$R17" --skills hello-skill --ref v1 > /dev/null 2>&1
python3 - "$R17/.agent-kit/skillsync-journal.json" "$R17/.agent-kit/skills.lock.json" <<'PY'
import json,sys,shutil,hashlib; p=sys.argv[1]; j=json.load(open(p)); shutil.rmtree(j["work"]+"/old",ignore_errors=True)
for o in j["ops"]: o["had_old"]=False
j["old_lock"]=json.dumps({"version":1,"skills":{}}); j["new_lock_sha"]=hashlib.sha256(open(sys.argv[2],"rb").read()).hexdigest(); json.dump(j,open(p,"w"))
PY
L17="$(sha256sum "$R17/.agent-kit/skills.lock.json")"
python3 "$SS" recover "$R17" > "$W/sk-oldlock2.log" 2>&1; rc=$?
expect "recover: journal-only tampering (old lock + new_lock_sha forged) still refused; on-disk lock never replaced by the forged one" bash -c "test $rc = 2 && test \"\$(sha256sum '$R17/.agent-kit/skills.lock.json')\" = '$L17'"
git -C "$R17" add -f .agent-kit/skillsync-journal.json >/dev/null 2>&1; git -C "$R17" commit -qm "planted journal" >/dev/null 2>&1
python3 "$SS" recover "$R17" > "$W/sk-tracked.log" 2>&1; rc=$?
expect "recover refuses a journal that is committed to git (never written by skillsync)" bash -c "test $rc = 2 && grep -q 'commit されている' '$W/sk-tracked.log'"
git -C "$R17" rm -q --cached .agent-kit/skillsync-journal.json >/dev/null 2>&1
rm -f "$R17/.agent-kit/skillsync-journal.json"

# --- v2.3 ①: 改変した journal(new_lock_sha=今の lock、op.hash=op.old_hash=今の配布先)で、退避の無い既存内容を消させない ---
R19="$W/skills-target19"; mkrepo "$R19"
python3 "$SS" vendor "$BUN" "$R19" --skills hello-skill,doc-skill --ref v1 > /dev/null 2>&1
snap19() { (cd "$R19" && find .agents .claude .agent-kit -path '*/tmp' -prune -o -type f -print 2>/dev/null | grep -v skillsync-journal | sort | xargs sha256sum 2>/dev/null) | sha256sum | cut -c1-16; }
SKILLSYNC_CRASH_AT=1 python3 "$SS" vendor "$BUN" "$R19" --skills hello-skill,doc-skill > /dev/null 2>&1; rc0=$?
S19="$(snap19)"
python3 - "$R19" "$KITROOT/kit/.agent-kit/bin/skillsync" <<'PY'
import json,sys,hashlib,runpy
from pathlib import Path
g=runpy.run_path(sys.argv[2],run_name="x"); r=Path(sys.argv[1])
jp=r/".agent-kit/skillsync-journal.json"; j=json.load(open(jp))
j["new_lock_sha"]=hashlib.sha256((r/".agent-kit/skills.lock.json").read_bytes()).hexdigest()
ops=j["ops"]
for o in ops[1:-1]:                     # 未配置の op を「新配置済み」かつ「旧内容保全済み」に見せる(最後の1つは未配置のまま)
    d=Path(o["dst"]); h=g["sha_tree"](d) if d.is_dir() else ""
    o["hash"]=h; o["old_hash"]=h
json.dump(j,open(jp,"w")); print(len(ops))
PY
python3 "$SS" recover "$R19" > "$W/sk-forge19.log" 2>&1; rc=$?
expect "v2.3①: forged journal (new_lock_sha = current lock, op hashes = current copies) → recover stops; targets, lock and .prev unchanged" bash -c "test $rc0 = 9 && test $rc = 2 && test '$(snap19)' = '$S19'"
rm -f "$R19/.agent-kit/skillsync-journal.json"; rm -rf "$R19/.agent-kit/tmp"; R19B="$W/skills-target19b"; mkrepo "$R19B"
python3 "$SS" vendor "$BUN" "$R19B" --skills hello-skill,doc-skill --ref v1 > /dev/null 2>&1
python3 "$SS" vendor "$BUN" "$R19B" --skills hello-skill,doc-skill > /dev/null 2>&1          # .prev ができる
python3 "$SS" rollback "$R19B" --bundle "$BUN" > /dev/null 2>&1
SKILLSYNC_CRASH_AT=0 python3 "$SS" vendor "$BUN" "$R19B" --skills hello-skill,doc-skill > /dev/null 2>&1; rc0=$?
snap19b() { (cd "$R19B" && find .agents .claude .agent-kit -path '*/tmp' -prune -o -type f -print 2>/dev/null | grep -v skillsync-journal | sort | xargs sha256sum 2>/dev/null) | sha256sum | cut -c1-16; }
S19B="$(snap19b)"
python3 - "$R19B" <<'PY'
import json,sys,hashlib
from pathlib import Path
r=Path(sys.argv[1]); jp=r/".agent-kit/skillsync-journal.json"; j=json.load(open(jp))
j["new_lock_sha"]=hashlib.sha256((r/".agent-kit/skills.lock.json").read_bytes()).hexdigest()
j["old_lock"]=json.dumps({"version":1,"skills":{}})             # 偽の前の lock を .prev に書かせようとする
json.dump(j,open(jp,"w"))
PY
python3 "$SS" recover "$R19B" > "$W/sk-forge19b.log" 2>&1; rc=$?
expect "v2.3①: forged journal on the roll-forward side (old_lock forged) → recover stops; .prev not overwritten, targets and lock unchanged" bash -c "test $rc0 = 9 && test $rc = 2 && test '$(snap19b)' = '$S19B'"
# 初めての配布(lock がまだ無い)の途中で落ちても recover できる。置いた写しは消さずにリポジトリの外へ移す
R20="$W/skills-target20"; mkrepo "$R20"
SKILLSYNC_CRASH_AT=1 python3 "$SS" vendor "$BUN" "$R20" --skills hello-skill > /dev/null 2>&1; rc0=$?
python3 "$SS" recover "$R20" > "$W/sk-first.log" 2>&1; rc=$?
expect "v2.3①: crash during the very first vendor (no lock yet) → recover works; placed copy moved out of the repo, not deleted" bash -c "test $rc0 = 9 && test $rc = 0 && test ! -e '$R20/.agent-kit/skillsync-journal.json' && test ! -e '$R20/.agents/skills/hello-skill' && find '$W/state/skillsync-recover-kept' -path '*hello-skill/SKILL.md' | grep -q ."
# 差し替えの無い配布の journal を偽っても、.prev は書き換えない
R21="$W/skills-target21"; mkrepo "$R21"
python3 "$SS" vendor "$BUN" "$R21" --skills hello-skill --ref v1 > /dev/null 2>&1
python3 "$SS" vendor "$BUN" "$R21" --skills hello-skill > /dev/null 2>&1
P21="$(sha256sum "$R21/.agent-kit/skills.lock.prev.json" | cut -c1-64)"
python3 - "$R21" <<'PY'
import json,sys,hashlib
from pathlib import Path
r=Path(sys.argv[1]); lock=(r/".agent-kit/skills.lock.json").read_text(); prev=(r/".agent-kit/skills.lock.prev.json").read_text()
l=json.loads(lock); l["forged"]=True
for e in l["skills"].values(): e["commit"]="0"*40
w=str(r/".agent-kit/tmp/skillsync-1-abcdef")
json.dump({"work":w,"ops":[],"old_lock":json.dumps(l),"old_prev":prev,"new_lock_sha":hashlib.sha256(lock.encode()).hexdigest()},open(r/".agent-kit/skillsync-journal.json","w"))
PY
python3 "$SS" recover "$R21" > "$W/sk-noops.log" 2>&1; rc=$?
expect "v2.3①: forged journal with no ops → .prev is not overwritten by journal content" bash -c "test \"\$(sha256sum '$R21/.agent-kit/skills.lock.prev.json' | cut -c1-64)\" = '$P21'"
# 配った写しの中のディレクトリへのリンク
R18="$W/skills-target18"; mkrepo "$R18"; python3 "$SS" vendor "$BUN" "$R18" --skills hello-skill > /dev/null 2>&1
ln -s "$W/outside" "$R18/.claude/skills/hello-skill/evil"
python3 "$SS" verify "$R18" > "$W/sk-dirlink.log" 2>&1; rc=$?
expect "verify reports a directory symlink planted inside a deployed skill (sha_tree alone would not see it)" bash -c "test $rc = 1 && grep -q 'シンボリックリンク' '$W/sk-dirlink.log'"
python3 - "$R4/.agent-kit/skills.lock.json" <<'PY'
import json,sys; p=sys.argv[1]; l=json.load(open(p)); l["skills"]["../../x"]=dict(l["skills"]["hello-skill"]); json.dump(l,open(p,"w"))
PY
python3 "$SS" verify "$R4" > "$W/sk-v-bad.log" 2>&1; rc=$?
expect "verify: an out-of-range name in the lock is reported, not followed" bash -c "test $rc = 1 && grep -q '名前が不正' '$W/sk-v-bad.log' && ! grep -q Traceback '$W/sk-v-bad.log'"
R5="$W/skills-public"; mkrepo "$R5"
SKILLSYNC_TARGET_VISIBILITY=public python3 "$SS" vendor "$BUN" "$R5" --skills hello-skill > "$W/sk-pub1.log" 2>&1; rc=$?
expect "public target without --public-target → refused (skills would become public)" bash -c "test $rc = 2 && grep -q '公開される' '$W/sk-pub1.log' && test ! -e '$R5/.claude/skills'"
SKILLSYNC_TARGET_VISIBILITY=public python3 "$SS" vendor "$BUN" "$R5" --skills hello-skill,doc-skill --public-target > "$W/sk-pub2.log" 2>&1; rc=$?
expect "public target: skill without a known redistributable license → whole run refused" bash -c "test $rc = 2 && grep -q 'doc-skill: ライセンス' '$W/sk-pub2.log' && test ! -e '$R5/.claude/skills/hello-skill'"
SKILLSYNC_TARGET_VISIBILITY=public python3 "$SS" vendor "$BUN" "$R5" --skills hello-skill --public-target > /dev/null 2>&1; rc=$?
expect "public target: MIT-licensed skill can be vendored with --public-target" bash -c "test $rc = 0 && test -e '$R5/.claude/skills/hello-skill/LICENSE'"

# ---------------------------------------------------------------------------
section 10 "Apps Script の検証ロジック(本物の Code.gs)"
if node "$KITROOT/tests/gas_harness.js" "$KITROOT/tests/fixtures/Deny.test.gs,$KITROOT/endpoint/Code.gs" > "$W/gas.log" 2>&1; then ok "with Deny.gs: $(cat "$W/gas.log")"; else ng "gas harness: $(tail -3 "$W/gas.log")"; fi
if node "$KITROOT/tests/gas_harness.js" "$KITROOT/endpoint/Code.gs" > "$W/gas2.log" 2>&1; then ok "without Deny.gs (public code alone): $(cat "$W/gas2.log")"; else ng "gas harness (no Deny.gs): $(tail -3 "$W/gas2.log")"; fi

# ---------------------------------------------------------------------------
section 12 "公開前の点検: 個人の値・秘密・公開しないファイルを見つける / 公開用の写しを作る"
PC="$KITROOT/tools/publish-check.py"; P="$W/pubtest"; mkdir -p "$P/docs" "$P/personal"
echo "ok text, mail me at someone@example.com, playwright-core@1.64.1" > "$P/docs/a.md"
python3 "$PC" "$P" --extra /dev/null > "$W/pc0.log" 2>&1; rc=$?
expect "publish-check: personal/ directory itself is flagged" bash -c "test $rc = 1 && grep -q 'personal/' '$W/pc0.log'"
rmdir "$P/personal"
python3 "$PC" "$P" > /dev/null 2>&1; rc=$?
expect "publish-check: clean tree passes (example.com and package@version are not emails)" test $rc = 0
FAKEID="1AbCdEfGhIjKl""MnOpQrStUvWxYz012345"; FAKEMAIL="bob@""corp.co.jp"; FAKEHOME="/Use""rs/bob"   # この試験の値が点検に引っかからないよう分けて書く
printf 'FOLDER = %s\nfrom %s at %s/x\n' "$FAKEID" "$FAKEMAIL" "$FAKEHOME" > "$P/docs/b.md"
python3 "$PC" "$P" > "$W/pc1.log" 2>&1; rc=$?
expect "publish-check: Drive-like ID, real email and home path are flagged" bash -c "test $rc = 1 && grep -q drive-id '$W/pc1.log' && grep -q email '$W/pc1.log' && grep -q home-path '$W/pc1.log'"
rm "$P/docs/b.md"; echo "secretname" > "$P/docs/c.md"; echo "secretname" > "$W/extra.txt"
python3 "$PC" "$P" --extra "$W/extra.txt" > /dev/null 2>&1; rc=$?
expect "publish-check: extra personal patterns are applied" test $rc = 1
rm "$P/docs/c.md"; touch "$P/run.log"
python3 "$PC" "$P" > /dev/null 2>&1; rc=$?
expect "publish-check: test logs are flagged" test $rc = 1
rm "$P/run.log"; git init -q "$P"; echo "leak $FAKEID" > "$P/docs/old.md"
git -C "$P" add -A; git -C "$P" commit -qm leak; git -C "$P" rm -q docs/old.md; git -C "$P" commit -qm clean
python3 "$PC" "$P" > /dev/null 2>&1; rc1=$?; python3 "$PC" "$P" --history > /dev/null 2>&1; rc2=$?
expect "publish-check --history: finds a value removed in a later commit" bash -c "test $rc1 = 0 && test $rc2 = 1"
python3 - "$P" <<'PY'
import sys,pathlib; pathlib.Path(sys.argv[1],"docs/a.md").unlink()
PY
Q="$W/pubtest2"; mkdir -p "$Q/sub/.git" "$Q/docs"; echo x > "$Q/sub/.git/config"
printf 'a\0b' > "$Q/docs/blob.bin"; NOREPLY="12345+octo@""users.noreply.github.com"; echo "SecretName here; mail: $NOREPLY" > "$Q/docs/n.md"; echo "secretname" > "$W/extra2.txt"
python3 "$PC" "$Q" --extra "$W/extra2.txt" > "$W/pc2.log" 2>&1
expect "publish-check: nested .git, unscanned binaries, case-insensitive personal patterns and noreply addresses are flagged" bash -c "grep -q 'sub/.git/' '$W/pc2.log' && grep -q 'blob.bin: 中身を点検できない' '$W/pc2.log' && grep -q 'personal:' '$W/pc2.log' && grep -q 'email:' '$W/pc2.log' && ! grep -q 'SecretName' '$W/pc2.log' && ! grep -q 'users.noreply' '$W/pc2.log'"
ln -s /etc/hostname "$Q/docs/link"
python3 "$PC" "$Q" --extra /dev/null --allow-binary docs/blob.bin > "$W/pc4.log" 2>&1
expect "publish-check: symlinks are flagged (their targets are not scanned)" grep -q 'docs/link: シンボリックリンク' "$W/pc4.log"
rm "$Q/docs/link"
python3 "$PC" "$Q" --extra /dev/null --allow-binary docs/blob.bin > "$W/pc3.log" 2>&1
expect "publish-check: --allow-binary accepts a binary the person checked" bash -c "! grep -q 'blob.bin' '$W/pc3.log'"
bash "$KITROOT/tools/make-public.sh" "$W/public-copy" > "$W/mp.log" 2>&1; rc=$?
expect "make-public: the kit's own public copy passes publish-check (with the personal pattern list if present)" bash -c "test $rc = 0"
expect "make-public: personal/, reports/ and logs are not copied" bash -c "test ! -e '$W/public-copy/personal' && test ! -e '$W/public-copy/reports' && test -z \"\$(find '$W/public-copy' -name '*.log')\""
expect "make-public: no git history is carried over" test ! -e "$W/public-copy/.git"

# ---------------------------------------------------------------------------
section 13 "正本の作成(make_bundle): 同名スキルの内容比較・commit 失敗・版タグ・dirty・再実行"
MB="$KITROOT/personal/skills/make_bundle.py"
if [ -f "$MB" ]; then
MR="$W/mb"; mkdir -p "$MR/rootA" "$MR/rootB"
mk() { mkdir -p "$1/scripts"; printf -- '---\nname: %s\ndescription: d\n---\nbody %s\n' "$2" "$3" > "$1/SKILL.md"; printf 'print("x")\n' > "$1/scripts/run.py"; }
mk "$MR/rootA/same" same 1; mk "$MR/rootB/same" same 1                       # 同じ中身
mk "$MR/rootA/diff" diff 1; mk "$MR/rootB/diff" diff 1; echo "changed" >> "$MR/rootB/diff/scripts/run.py"   # SKILL.md は同じで補助ファイルだけ違う
mk "$MR/rootA/solo" solo 1; mkdir -p "$MR/rootB/broken"                       # rootB/broken は SKILL.md が無い
python3 - "$MR/catalog.json" <<'PY'
import json,sys
sk=[]
for root in ("rootA","rootB"):
    for d in ("same","diff","solo","broken"):
        if root=="rootB" and d=="solo": continue
        if root=="rootA" and d=="broken": continue
        sk.append({"name":d,"dir":d,"root":root,"path":f"~/{root}/{d}","class":"as-is","class_ja":"そのまま利用","set":"core"})
json.dump({"sets":{"core":"x"},"skills":sk},open(sys.argv[1],"w"))
PY
mbr() { SKILLSYNC_TARGET_VISIBILITY=private python3 "$MB" --catalog "$MR/catalog.json" --root rootA="$MR/rootA" --root rootB="$MR/rootB" --allow-partial "$@"; }
mbr --out "$MR/bundle" --version v1 > "$W/mb1.log" 2>&1; rc=$?
expect "R6: identical copies are merged; copies differing only in scripts/ are held with the file list; unreadable copy held" bash -c "test $rc = 1 && test -e '$MR/bundle/skills/same/SKILL.md' && test ! -e '$MR/bundle/skills/diff' && grep -q 'diff: 中身の違うコピー' '$W/mb1.log' && grep -q 'scripts/run.py' '$W/mb1.log' && grep -q 'broken: .*本文を読めない' '$W/mb1.log'"
expect "R1: commit made and tag v1 placed on it" bash -c "test \"\$(git -C '$MR/bundle' rev-parse v1^{commit})\" = \"\$(git -C '$MR/bundle' rev-parse HEAD)\""
mbr --out "$MR/bundle" --version v1 > "$W/mb2.log" 2>&1
expect "R1: re-running the same operation → no new commit, tag unchanged (変更なし)" bash -c "grep -q '変更なし' '$W/mb2.log' && test \"\$(git -C '$MR/bundle' rev-list --count HEAD)\" = 1"
mbr --out "$MR/bundle" --version v2 --prefer diff=rootB > "$W/mb3.log" 2>&1
expect "R6: --prefer picks the confirmed copy; new version tag v2, v1 not moved" bash -c "grep -q changed '$MR/bundle/skills/diff/scripts/run.py' && test \"\$(git -C '$MR/bundle' rev-parse v1^{commit})\" != \"\$(git -C '$MR/bundle' rev-parse v2^{commit})\" && test \"\$(git -C '$MR/bundle' rev-list --count v1)\" = 1"
V2="$(git -C "$MR/bundle" rev-parse v2^{commit})"
echo "body 2" >> "$MR/rootA/solo/SKILL.md"
mbr --out "$MR/bundle" --version v2 --prefer diff=rootB > "$W/mb4.log" 2>&1; rc=$?
expect "R1: an issued version tag is never moved (content changed, same version name → refused)" bash -c "test $rc = 2 && grep -q '発行済み' '$W/mb4.log' && test \"\$(git -C '$MR/bundle' rev-parse v2^{commit})\" = '$V2'"
git -C "$MR/bundle" reset -q; git -C "$MR/bundle" checkout -q -- . ; git -C "$MR/bundle" clean -qfd
echo "user's unrelated work" > "$MR/bundle/NOTES.md"
mbr --out "$MR/bundle" --version v3 --prefer diff=rootB > "$W/mb5.log" 2>&1; rc=$?
expect "R1: dirty bundle (unrelated untracked file) → nothing done, nothing added" bash -c "test $rc = 2 && grep -q '未コミットの変更' '$W/mb5.log' && test \"\$(git -C '$MR/bundle' rev-parse HEAD)\" = '$V2' && ! git -C '$MR/bundle' diff --cached --quiet --exit-code -- NOTES.md 2>/dev/null; git -C '$MR/bundle' ls-files --error-unmatch NOTES.md >/dev/null 2>&1; test \$? != 0"
rm "$MR/bundle/NOTES.md"
git -C "$MR/bundle" config commit.gpgsign true; git -C "$MR/bundle" config gpg.program /bin/false
mbr --out "$MR/bundle" --version v3 --prefer diff=rootB > "$W/mb6.log" 2>&1; rc=$?
expect "R1: signing fails → stops (exit 3), no v3 tag, signing setting left as is" bash -c "test $rc = 3 && grep -q 'commit できない' '$W/mb6.log' && ! git -C '$MR/bundle' rev-parse -q --verify v3 >/dev/null && test \"\$(git -C '$MR/bundle' config commit.gpgsign)\" = true && test \"\$(git -C '$MR/bundle' rev-parse HEAD)\" = '$V2'"
expect "R1: after a failed commit the tool's own staged/written files are restored (bundle clean, retry possible)" bash -c "test -z \"\$(git -C '$MR/bundle' status --porcelain)\""
git -C "$MR/bundle" config --unset commit.gpgsign
mbr --out "$MR/bundle" --version v3 --prefer diff=rootB > "$W/mb6b.log" 2>&1; rc=$?
expect "R1: retry after fixing signing succeeds with no manual cleanup (v3 on the new commit)" bash -c "test \"\$(git -C '$MR/bundle' rev-parse v3^{commit})\" = \"\$(git -C '$MR/bundle' rev-parse HEAD)\" && test \"\$(git -C '$MR/bundle' rev-parse HEAD)\" != '$V2'"
echo "body 3" >> "$MR/rootA/solo/SKILL.md"
git -C "$MR/bundle" config tag.gpgSign true
mbr --out "$MR/bundle" --version v5 --prefer diff=rootB > "$W/mb9.log" 2>&1; rc=$?
expect "R1: tag signing (tag.gpgSign) fails after the commit → exit 3, no v5, setting left as is" bash -c "test $rc = 3 && grep -q 'タグを付けられない' '$W/mb9.log' && ! git -C '$MR/bundle' rev-parse -q --verify v5 >/dev/null && test \"\$(git -C '$MR/bundle' config tag.gpgSign)\" = true"
git -C "$MR/bundle" config --unset tag.gpgSign
mbr --out "$MR/bundle" --version v5 --prefer diff=rootB > "$W/mb10.log" 2>&1; rc=$?
expect "R1: re-running after the tag failure tags that same commit (annotated tag, no new commit)" bash -c "grep -q '前回の commit に版 v5' '$W/mb10.log' && test \"\$(git -C '$MR/bundle' rev-parse v5^{commit})\" = \"\$(git -C '$MR/bundle' rev-parse HEAD)\" && test \"\$(git -C '$MR/bundle' cat-file -t v5)\" = tag"
# 3つの置き場: A と C が同じ中身、B だけ違う。--prefer で C を選べる(A と同じ中身の群として)
M3="$W/mb3roots"; mkdir -p "$M3/A" "$M3/B" "$M3/C"
mk "$M3/A/tri" tri 1; mk "$M3/B/tri" tri 1; mk "$M3/C/tri" tri 1; echo changed >> "$M3/B/tri/scripts/run.py"
python3 - "$M3/catalog.json" <<'PY'
import json,sys
json.dump({"sets":{},"skills":[{"name":"tri","dir":"tri","root":r,"path":f"~/{r}/tri","class":"as-is","class_ja":"そのまま利用","set":""} for r in "ABC"]},open(sys.argv[1],"w"))
PY
m3() { SKILLSYNC_TARGET_VISIBILITY=private python3 "$MB" --catalog "$M3/catalog.json" --root A="$M3/A" --root B="$M3/B" --root C="$M3/C" --allow-partial "$@"; }
m3 --out "$M3/bundle" --version v1 > "$W/m3a.log" 2>&1
expect "R6: 3 copies (two identical) → held, groups listed (A+C vs B) with the differing file" bash -c "grep -q 'A+C' '$W/m3a.log' && grep -q 'scripts/run.py' '$W/m3a.log' && test ! -e '$M3/bundle/skills/tri'"
m3 --out "$M3/bundle" --version v2 --prefer tri=C > "$W/m3b.log" 2>&1
expect "R6: --prefer can pick a root whose copy is identical to another root's" bash -c "test -e '$M3/bundle/skills/tri/SKILL.md' && ! grep -q changed '$M3/bundle/skills/tri/scripts/run.py'"
# 置き場 B の tri が A の tri へのリンク(同じ実体)。--prefer で B を選べる
M4="$W/mb-alias"; mkdir -p "$M4/A" "$M4/C"; mk "$M4/A/al" al 1; mk "$M4/C/al" al 1; echo changed >> "$M4/C/al/scripts/run.py"
mkdir -p "$M4/B"; ln -s "$M4/A/al" "$M4/B/al"
python3 - "$M4/catalog.json" <<'PY'
import json,sys
sk=[{"name":"al","dir":"al","root":r,"path":f"~/{r}/al","class":"as-is","class_ja":"そのまま利用","set":""} for r in "ABC"]
sk.append({"name":"mixed","dir":"mixed","root":"A","path":"~/A/mixed","class":"as-is","class_ja":"そのまま利用","set":""})
sk.append({"name":"mixed","dir":"mixed","root":"C","path":"~/C/mixed","class":"mac-only","class_ja":"Macへ引継ぎ","set":""})
sk.append({"name":"ad","dir":"ad","root":"A","path":"~/A/ad","class":"adapt","class_ja":"クラウド向け修正","set":""})
json.dump({"sets":{},"skills":sk},open(sys.argv[1],"w"))
json.dump({"ad":{"why":"t","edits":[{"file":"no-such.md","find":"x","replace":"y"}],"drop":["../../../escape.txt"]}},open(sys.argv[1].replace("catalog","adapt"),"w"))
PY
mk "$M4/A/mixed" mixed 1; mk "$M4/C/mixed" mixed 1; mk "$M4/A/ad" ad 1; echo keep > "$M4/escape.txt"
m4() { SKILLSYNC_TARGET_VISIBILITY=private python3 "$MB" --catalog "$M4/catalog.json" --adaptations "$M4/adapt.json" --root A="$M4/A" --root B="$M4/B" --root C="$M4/C" --allow-partial "$@"; }
m4 --out "$M4/bundle" --version v1 --prefer al=B --include-adapt ad > "$W/m4.log" 2>&1
expect "R6: --prefer picks a root whose copy is a symlink alias of another root's copy" bash -c "test -e '$M4/bundle/skills/al/SKILL.md' && ! grep -q changed '$M4/bundle/skills/al/scripts/run.py'"
expect "R6: same name with copies of different classes → held, not silently exported" bash -c "grep -q 'mixed: 分類の違うコピー' '$W/m4.log' && test ! -e '$M4/bundle/skills/mixed'"
expect "adaptation naming a missing file / drop outside the skill → reported, copy discarded, no traceback, file outside kept, bundle clean" bash -c "! grep -q Traceback '$W/m4.log' && grep -q 'ad/no-such.md' '$W/m4.log' && test ! -e '$M4/bundle/skills/ad' && test -e '$M4/escape.txt' && test -z \"\$(git -C '$M4/bundle' status --porcelain)\""
printf '%s' '{"ad":["not","a","dict"]}' > "$M4/adapt-bad.json"
SKILLSYNC_TARGET_VISIBILITY=private python3 "$MB" --catalog "$M4/catalog.json" --adaptations "$M4/adapt-bad.json" --root A="$M4/A" --root B="$M4/B" --root C="$M4/C" --out "$M4/bundle2" --version v1 > "$W/m4b.log" 2>&1; rc=$?
expect "malformed adaptations.json → refused before writing anything (no traceback)" bash -c "test $rc = 2 && ! grep -q Traceback '$W/m4b.log' && test ! -e '$M4/bundle2/skills'"
echo '{broken' > "$M4/adapt-inv.json"
SKILLSYNC_TARGET_VISIBILITY=private python3 "$MB" --catalog "$M4/catalog.json" --adaptations "$M4/adapt-inv.json" --root A="$M4/A" --root B="$M4/B" --root C="$M4/C" --out "$M4/bundle3" --version v1 > "$W/m4c.log" 2>&1; rc=$?
expect "invalid JSON in adaptations.json → refused, no traceback, output not even created" bash -c "test $rc = 2 && ! grep -q Traceback '$W/m4c.log' && test ! -e '$M4/bundle3'"
SKILLSYNC_TARGET_VISIBILITY=private python3 "$MB" --catalog "$M4/catalog.json" --adaptations "$M4/adapt.json" --root A="$M4/A" --root B="$M4/B" --root C="$M4/C" --out "$M4/bundle4" --version v1 --prefer al=B --include-adapt ad > "$W/m4d.log" 2>&1; rc=$?
expect "held / failed skills without --allow-partial → no commit, no tag, bundle left clean" bash -c "test $rc = 1 && grep -q -- '--allow-partial' '$W/m4d.log' && ! git -C '$M4/bundle4' rev-parse -q --verify HEAD >/dev/null && test -z \"\$(git -C '$M4/bundle4' status --porcelain)\""
mk "$MR/rootA/adapted" adapted 1; printf 'save to /Users/user/SYNTHETIC_TEST_ONLY/Vault/x\n' >> "$MR/rootA/adapted/SKILL.md"
python3 - "$MR/catalog.json" "$MR/adapt.json" <<'PY'
import json,sys
c=json.load(open(sys.argv[1])); c["skills"].append({"name":"adapted","dir":"adapted","root":"rootA","path":"~/rootA/adapted","class":"adapt","class_ja":"クラウド向け修正","set":"core"})
json.dump(c,open(sys.argv[1],"w"))
json.dump({"adapted":{"why":"t","edits":[{"file":"SKILL.md","find":"text that is not there","replace":"x"}]}},open(sys.argv[2],"w"))
PY
mbr --out "$MR/bundle" --version v6 --prefer diff=rootB --include-adapt adapted --adaptations "$MR/adapt.json" > "$W/mb7.log" 2>&1
expect "adaptation rule whose original text is not found → skill not imported, copy discarded (no half-adapted files)" bash -c "grep -q '1 回のはず\|0 回見つかった' '$W/mb7.log' && test ! -e '$MR/bundle/skills/adapted'"
python3 - "$MR/adapt.json" <<'PY'
import json,sys
json.dump({"adapted":{"why":"t","edits":[{"file":"SKILL.md","find":"save to /Users/user/SYNTHETIC_TEST_ONLY/Vault/x","replace":"save outside the repo"}]}},open(sys.argv[1],"w"))
PY
mbr --out "$MR/bundle" --version v7 --prefer diff=rootB --include-adapt adapted --adaptations "$MR/adapt.json" > "$W/mb8.log" 2>&1
expect "adaptation applied only to the bundle copy; original unchanged; re-checked after rewrite" bash -c "grep -q 'save outside the repo' '$MR/bundle/skills/adapted/SKILL.md' && grep -q '/Users/user/SYNTHETIC_TEST_ONLY/Vault' '$MR/rootA/adapted/SKILL.md'"
fi

# ---------------------------------------------------------------------------
section 11 "静的検査"
for f in "$KITROOT"/kit/.agent-kit/bin/*.sh "$KITROOT"/kit/.agent-kit/claude/*.sh "$KITROOT"/kit/.agent-kit/codex/*.sh "$KITROOT"/kit/.agent-kit/lib/*.sh "$KITROOT"/local/*.sh; do
  bash -n "$f" && ok "bash -n $(basename "$f")" || ng "bash -n $(basename "$f")"
done
python3 -c "import sys,py_compile; [py_compile.compile(f, cfile='$W/x.pyc', doraise=True) for f in sys.argv[1:]]" \
  "$KITROOT"/kit/.agent-kit/bin/agent-log "$KITROOT"/kit/.agent-kit/bin/agent-gh "$KITROOT"/kit/.agent-kit/claude/pretool.py \
  "$KITROOT"/install.py "$KITROOT"/kit/.agent-kit/bin/skillsync "$KITROOT"/skills/skillsync.py "$KITROOT"/local/collect-pending.py \
  "$KITROOT"/kit/.agent-kit/lib/agentstate.py "$KITROOT"/kit/.agent-kit/lib/agentgh/*.py "$KITROOT"/tools/publish-check.py && ok "py_compile" || ng "py_compile"
cp "$KITROOT/endpoint/Code.gs" "$W/Code.js"; node --check "$W/Code.js" 2>/dev/null && ok "Code.gs syntax" || ng "Code.gs syntax"
cp "$KITROOT/endpoint/Deny.gs.example" "$W/Deny.js"; node --check "$W/Deny.js" 2>/dev/null && ok "Deny.gs.example syntax" || ng "Deny.gs.example syntax"
python3 -c "import json,sys;[json.load(open(f)) for f in sys.argv[1:]]" "$KITROOT"/kit/.agent-kit/claude/*.json "$KITROOT"/endpoint/appsscript.json && ok "json valid" || ng "json valid"
if command -v shellcheck >/dev/null 2>&1; then
  if shellcheck -S warning -x "$KITROOT"/kit/.agent-kit/bin/*.sh "$KITROOT"/kit/.agent-kit/claude/*.sh "$KITROOT"/kit/.agent-kit/codex/*.sh "$KITROOT"/local/*.sh "$KITROOT"/tools/*.sh > "$W/sc.log" 2>&1; then ok "shellcheck"; else ng "shellcheck: see $W/sc.log"; fi
fi

echo
echo "== 結果: PASS $PASS / FAIL $FAIL / SKIP $SKIP (作業ディレクトリ $W) =="
printf '%s\n' "${RESULTS[@]}" > "$W/results.txt"
[ "$FAIL" = 0 ]
