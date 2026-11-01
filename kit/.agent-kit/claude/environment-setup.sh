#!/bin/bash
# Claude Code on the web の「環境」の Setup script 欄に貼る内容(リポジトリに依存しない部分だけ)。
#
# 公式仕様(2026-10-29確認, code.claude.com/docs/en/cloud-environments):
# - 新しいセッションの開始時、Claude Code の起動前に root で実行。約5分以内に成功すると
#   スナップショットになり、以後は再実行されない(この欄か許可ホストを変えたとき・約7日ごとに作り直し)。
# - 非0で終わるとセッションが起動しない → 重要でない処理は `|| true`。
# - 実行時点では環境変数(Environment variables 欄)はまだ入っていない。
# - リポジトリの依存は、リポジトリの .claude/settings.json の SessionStart hook で入れる。
# - 権限や hooks はここ(VM の ~/.claude/settings.json)には書かない。クラウドはユーザー設定を
#   読まないと公式に書かれているため、リポジトリの hooks(CLAUDE_CODE_REMOTE で判定)で行う。
set -u
export DEBIAN_FRONTEND=noninteractive

# Chromium を動かす OS ライブラリ(ブラウザ本体はリポジトリの Playwright の版に合わせて別に入れる)
timeout 240 npx -y playwright@1 install-deps chromium >/tmp/agent-env-setup.log 2>&1 \
  || echo "playwright install-deps skipped (see /tmp/agent-env-setup.log)"

command -v uv   >/dev/null 2>&1 || timeout 60 pip install --quiet uv || true
command -v pnpm >/dev/null 2>&1 || timeout 60 npm install -g pnpm >/dev/null 2>&1 || true
exit 0
