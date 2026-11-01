# shellcheck shell=bash
# .agent-kit/lib/common.sh — Codex Cloud / Claude Code on the web 共通の小道具。source して使う。
# 秘密情報を表示しない: 環境変数の値は出力しない。

AGENT_KIT_VERSION="2.0.0"

# ---- リポジトリと設定 ------------------------------------------------------
# agent_repo_root: AGENT_REPO_ROOT > CLAUDE_PROJECT_DIR > git の作業ツリー > このファイルの2つ上
agent_repo_root() {
  if [ -n "${AGENT_REPO_ROOT:-}" ]; then printf '%s\n' "$AGENT_REPO_ROOT"; return; fi
  if [ -n "${CLAUDE_PROJECT_DIR:-}" ] && [ -d "$CLAUDE_PROJECT_DIR/.agent-kit" ]; then printf '%s\n' "$CLAUDE_PROJECT_DIR"; return; fi
  local here; here="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
  git -C "$here" rev-parse --show-toplevel 2>/dev/null || printf '%s\n' "$here"
}

# load_config <repo-root>
# 優先順位: 環境変数 > .agent-kit/config.env > 各スクリプトの既定値。
# 読むのは AGENT_ で始まる KEY=value だけ(コードは実行しない)。
load_config() {
  local root="${1:-$(agent_repo_root)}" f line k v
  f="$root/.agent-kit/config.env"
  [ -f "$f" ] || return 0
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line%$'\r'}"                                   # CRLF
    case "$line" in ''|[[:space:]]*\#*|\#*) continue ;; esac
    case "$line" in *=*) ;; *) continue ;; esac
    k="${line%%=*}"; v="${line#*=}"
    k="${k#"${k%%[![:space:]]*}"}"; k="${k%"${k##*[![:space:]]}"}"     # 前後の空白
    case "$k" in AGENT_[A-Z0-9_]*) ;; *) continue ;; esac
    case "$k" in *[!A-Z0-9_]*) continue ;; esac
    v="${v#"${v%%[![:space:]]*}"}"
    case "$v" in
      \"*\"*) v="${v#\"}"; v="${v%%\"*}" ;;                # "値"(後ろのコメントは捨てる)
      \'*\'*) v="${v#\'}"; v="${v%%\'*}" ;;
      *) v="${v%%[[:space:]]#*}"; v="${v%"${v##*[![:space:]]}"}" ;;   # 値 # コメント
    esac
    if [ -z "${!k+x}" ]; then export "$k=$v"; fi
  done < "$f"
}

# ---- 実行環境の判定 -------------------------------------------------------
# 戻り値: claude-cloud | codex-cloud | ci | local
# 明示指定(環境設定の AGENT_RUNTIME)を最優先。Codex Cloud には公式の判定変数が無い。
agent_runtime() {
  if [ -n "${AGENT_RUNTIME:-}" ]; then echo "$AGENT_RUNTIME"; return; fi
  if [ "${CLAUDE_CODE_REMOTE:-}" = "true" ]; then echo "claude-cloud"; return; fi
  if [ -n "${CI:-}" ]; then echo "ci"; return; fi
  echo "local"
}

agent_service() {
  if [ -n "${AGENT_SERVICE:-}" ]; then echo "$AGENT_SERVICE"; return; fi
  case "$(agent_runtime)" in
    claude-cloud) echo "claude" ;;
    codex-cloud) echo "codex" ;;
    *) echo "unknown" ;;
  esac
}

# ---- 出力 ----------------------------------------------------------------
_ts() { TZ=Asia/Tokyo date "+%F %H:%M:%S"; }
log_info()  { printf '[agent-kit %s] %s\n' "$(_ts)" "$*" >&2; }
log_warn()  { printf '[agent-kit %s] WARN: %s\n' "$(_ts)" "$*" >&2; }
log_error() { printf '[agent-kit %s] ERROR: %s\n' "$(_ts)" "$*" >&2; }
have() { command -v "$1" >/dev/null 2>&1; }

# ---- 状態ディレクトリ(チェックアウトごと) --------------------------------
# リポジトリの外に置き、チェックアウトの実パスで分ける(別リポジトリ・別checkoutで混ざらない)。
agent_checkout_key() {
  local root="${1:-$(agent_repo_root)}" real
  real="$(cd "$root" 2>/dev/null && pwd -P)" || real="$root"
  # agent-log(Python)の checkout_key と同じ計算にする
  printf '%s-%s\n' "$(printf '%s' "$(basename "$real")" | tr -c 'A-Za-z0-9._-' '_' | cut -c1-40)" \
    "$(printf '%s' "$real" | sha256sum | cut -c1-12)"
}
agent_state_base() {
  printf '%s\n' "${AGENT_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/agent-kit}"
}
agent_state_dir() {
  local d; d="$(agent_state_base)/$(agent_checkout_key "${1:-}")"
  mkdir -p "$d" 2>/dev/null || { d="/tmp/agent-kit-$(id -u)/$(agent_checkout_key "${1:-}")"; mkdir -p "$d"; }
  printf '%s\n' "$d"
}

# ---- 時間制限・再試行 ------------------------------------------------------
AGENT_STEP_RESULTS=()
# run_step <名前> <秒> <コマンド...>  — 失敗しても戻る(戻り値で判断)。結果を記録する。
run_step() {
  local name="$1" secs="$2"; shift 2
  local start end rc
  start=$(date +%s)
  log_info "start: $name (timeout ${secs}s)"
  if have timeout; then timeout --kill-after=10 "$secs" "$@"; rc=$?; else "$@"; rc=$?; fi
  end=$(date +%s)
  if [ $rc -eq 0 ]; then
    log_info "ok: $name ($((end-start))s)"; AGENT_STEP_RESULTS+=("ok|$name|$((end-start))")
  elif [ $rc -eq 124 ] || [ $rc -eq 137 ]; then
    log_warn "timeout: $name (${secs}s)"; AGENT_STEP_RESULTS+=("timeout|$name|$((end-start))")
  else
    log_warn "failed: $name (exit $rc)"; AGENT_STEP_RESULTS+=("failed|$name|$((end-start))")
  fi
  return $rc
}

# retry <回数> <コマンド...> — 一時的なネットワーク失敗に備えて、待ち時間を倍にしながら再試行する。
retry() {
  local n="$1"; shift
  local i=1 rc=0 wait=2
  while :; do
    "$@" && return 0
    rc=$?
    [ $i -ge "$n" ] && return $rc
    log_warn "retry $i/$n after ${wait}s: $1"
    sleep "$wait"; i=$((i+1)); wait=$((wait*2))
  done
}

# ---- 冪等化のためのスタンプ(状態ディレクトリはチェックアウトごと) ------
stamp_key() {
  local f
  for f in "$@"; do
    if [ -f "$f" ]; then sha256sum "$f"; else echo "missing $f"; fi
  done | sha256sum | cut -c1-16
}
stamp_ok()  { local s; s="$(agent_state_dir)/stamp-$1"; [ -f "$s" ] && [ "$(cat "$s")" = "$2" ]; }
stamp_set() { printf '%s' "$2" > "$(agent_state_dir)/stamp-$1"; }
stamp_clear() { rm -f "$(agent_state_dir)/stamp-$1"; }

# ---- 排他 ------------------------------------------------------------------
with_lock() {  # with_lock <名前> <コマンド...>
  local name="$1"; shift
  local lf; lf="$(agent_state_dir)/lock-$name"
  if have flock; then
    ( flock -w 900 9 || { log_warn "lock wait timeout: $name"; exit 75; }; "$@" 9>&- ) 9>"$lf"
  else
    "$@"
  fi
}

# ---- リポジトリの状態の指紋(ターン中の変更検出用) ------------------------
# HEAD・変更済みファイルの内容・未追跡ファイルの内容を含める。すでに変更済みのファイルへの
# 追加編集も検出できる。.agent-logs/ は除外する。
repo_fingerprint() {
  local root="${1:-$(agent_repo_root)}"
  (
    cd "$root" || exit 0
    git rev-parse HEAD 2>/dev/null
    git diff HEAD --binary -- . ':(exclude).agent-logs' 2>/dev/null | sha256sum
    git ls-files -o --exclude-standard -z -- . ':(exclude).agent-logs' 2>/dev/null \
      | head -z -n 2000 | xargs -0 -r sha256sum 2>/dev/null
  ) | sha256sum | cut -c1-32
}

# ---- 結果の要約 ------------------------------------------------------------
write_summary() {  # write_summary <ファイル>
  local out="$1" r first=1
  {
    echo "{"
    echo "  \"kit_version\": \"$AGENT_KIT_VERSION\","
    echo "  \"runtime\": \"$(agent_runtime)\","
    echo "  \"finished_at\": \"$(TZ=Asia/Tokyo date -Iseconds)\","
    echo "  \"steps\": ["
    for r in "${AGENT_STEP_RESULTS[@]}"; do
      IFS='|' read -r st nm sec <<<"$r"
      [ $first -eq 1 ] || echo ","
      first=0
      printf '    {"status": "%s", "name": "%s", "seconds": %s}' "$st" "$nm" "$sec"
    done
    echo ""
    echo "  ]"
    echo "}"
  } > "$out"
}
