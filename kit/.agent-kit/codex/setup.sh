#!/usr/bin/env bash
# Codex Cloud(新しい Cloud 環境)の Install script から呼ぶ。
#   環境の作成・編集の会話で「Install script は `bash .agent-kit/codex/setup.sh` を実行するだけにする」と頼み、
#   テストが通ったら Save → Publish する。
#
# 公式仕様(2026-10-29確認, learn.chatgpt.com/docs/environments/cloud-environments):
# - Install script は依存の導入と開発アセットの準備に使う。Publish 時のファイルシステムが新しいタスクの起点。
# - リポジトリの更新はバックグラウンドで行われ、install は再実行されない。
#   → ロックファイルが変わったブランチでは、エージェントが `bash .agent-kit/bin/setup-deps.sh deps` を実行する
#     (AGENTS.md に記載。変化が無ければ数秒で終わる)。
# - 時間制限・作業ディレクトリ・実行ユーザーは未記載 → 自前で打ち切り、パスに依存しない。
set -u
cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)" || exit 0
export AGENT_RUNTIME="${AGENT_RUNTIME:-codex-cloud}"
export AGENT_SERVICE="${AGENT_SERVICE:-codex}"
export AGENT_REPO_ROOT="$PWD"
. .agent-kit/lib/common.sh
load_config "$PWD"

# エージェントのシェルでも同じ判定になるよう ~/.bashrc に1回だけ書く(環境変数の設定が本筋。これは保険)
MARK="# >>> agent-kit >>>"
if ! grep -qF "$MARK" ~/.bashrc 2>/dev/null; then
  cat >> ~/.bashrc <<'EOF'
# >>> agent-kit >>>
export AGENT_RUNTIME="${AGENT_RUNTIME:-codex-cloud}"
export AGENT_SERVICE="${AGENT_SERVICE:-codex}"
# <<< agent-kit <<<
EOF
fi

bash .agent-kit/bin/git-identity.sh || true
timeout "${AGENT_SETUP_TOTAL_TIMEOUT:-1500}" bash .agent-kit/bin/setup-deps.sh all
rc=$?
if [ $rc -ne 0 ] && [ "${AGENT_SETUP_STRICT:-0}" = "1" ]; then exit $rc; fi
exit 0
