#!/usr/bin/env bash
# クラウドのエージェントのコミット作者を、このリポジトリだけに設定する(人間と区別するため)。
# - 作者名は「誰が書いたかの表示」で、認証(誰として push できるか)とも署名(暗号的な証明)とも別物。
# - 署名鍵は扱わない。--global は触らない。
# - 設定: AGENT_GIT_IDENTITY / AGENT_GIT_NAME_<CLAUDE|CODEX> / AGENT_GIT_EMAIL_<...>
#   (優先順位: 環境変数 > .agent-kit/config.env > 既定値)
set -u
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$SELF_DIR/../lib/common.sh"
ROOT="$(agent_repo_root)"
load_config "$ROOT"
[ "${AGENT_GIT_IDENTITY:-1}" = "1" ] || { log_info "git-identity: 無効(AGENT_GIT_IDENTITY=${AGENT_GIT_IDENTITY})"; exit 0; }
git -C "$ROOT" rev-parse --git-dir >/dev/null 2>&1 || exit 0

case "$(agent_service)" in
  claude) name="${AGENT_GIT_NAME_CLAUDE:-Claude Code (cloud)}"; email="${AGENT_GIT_EMAIL_CLAUDE:-claude-cloud@example.com}" ;;
  codex)  name="${AGENT_GIT_NAME_CODEX:-Codex (cloud)}";        email="${AGENT_GIT_EMAIL_CODEX:-codex-cloud@example.com}" ;;
  *) log_info "git-identity: サービスを判定できないため設定しない"; exit 0 ;;
esac
if [ "$(git -C "$ROOT" config --local user.name 2>/dev/null)" != "$name" ] || \
   [ "$(git -C "$ROOT" config --local user.email 2>/dev/null)" != "$email" ]; then
  git -C "$ROOT" config --local user.name "$name"
  git -C "$ROOT" config --local user.email "$email"
  log_info "git-identity: $name <$email>(このリポジトリのみ)"
fi
exit 0
