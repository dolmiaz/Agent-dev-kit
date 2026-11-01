#!/usr/bin/env bash
# .agent-kit/bin/setup-deps.sh — リポジトリの依存をロックファイルから判定して入れる(両サービス共通)。
#
#   setup-deps.sh deps     言語ごとの依存(ロックが変わらず成果物もそろっていればスキップ)
#   setup-deps.sh extras   ブラウザ(Playwright)など、使っているときだけ
#   setup-deps.sh all      deps + extras
#   setup-deps.sh check    必要な CLI と導入済み設定の形式を読み取り専用で点検
#   setup-deps.sh doctor   check と同じ(値・絶対パスを表示せず、状態も作らない)
#   setup-deps.sh plan     必要な CLI・接続先・サービスの一覧(導入前の確認用。何も入れない)
#
# 方針:
# - 再実行しても壊れない。スタンプ(ロックのハッシュ)はチェックアウトごとに分けて保存し、
#   さらに成果物(node_modules 等)が実在するかを確かめてからスキップする。
# - ロックファイルを書き換えない(frozen / ci / --locked)。ロックが無い言語は警告して飛ばす。
# - 失敗しても既定では止めない。AGENT_SETUP_STRICT=1(環境変数か config.env)で終了コード1。
set -u
set -o pipefail

SELF_PARENT="${BASH_SOURCE[0]%/*}"
[ "$SELF_PARENT" != "${BASH_SOURCE[0]}" ] || SELF_PARENT=.
[ -n "$SELF_PARENT" ] || SELF_PARENT=/
SELF_DIR="$(cd -- "$SELF_PARENT" 2>/dev/null && pwd)" || { echo 'invalid: setup installation' >&2; exit 1; }
# Read-only diagnostics must run before config loading or state/lock creation.
case "${1:-}" in
  check|doctor)
    [ "$#" -eq 1 ] || { echo 'usage: setup-deps.sh {check|doctor}' >&2; exit 2; }
    command -v python3 >/dev/null 2>&1 || { echo 'missing: tool python3'; exit 1; }
    [ -f "$SELF_DIR/../lib/agentdoctor.py" ] || { echo 'invalid: doctor installation'; exit 1; }
    exec python3 -B "$SELF_DIR/../lib/agentdoctor.py"
    ;;
esac
# shellcheck source=../lib/common.sh
. "$SELF_DIR/../lib/common.sh"
REPO_ROOT="$(agent_repo_root)"
export AGENT_REPO_ROOT="$REPO_ROOT"
cd "$REPO_ROOT" || exit 1
load_config "$REPO_ROOT"

STEP_TIMEOUT="${AGENT_STEP_TIMEOUT:-600}"
STRICT="${AGENT_SETUP_STRICT:-0}"
FAILED=0
step() { run_step "$@" || FAILED=$((FAILED+1)); }

# ---- Node.js ---------------------------------------------------------------
select_node_version() {
  local want=""
  [ -f .nvmrc ] && want="$(tr -d ' \nv' < .nvmrc)"
  [ -z "$want" ] && [ -f .node-version ] && want="$(tr -d ' \nv' < .node-version)"
  [ -z "$want" ] && return 0
  local major="${want%%.*}" cur
  case "$major" in ''|*[!0-9]*) log_warn "Node の版指定を解釈できない: $want"; return 0 ;; esac
  cur="$(node -v 2>/dev/null | sed 's/^v//; s/\..*//')"
  [ "$cur" = "$major" ] && return 0
  if [ -x "/opt/node$major/bin/node" ]; then             # Claude Code on the web
    export PATH="/opt/node$major/bin:$PATH"
    printf 'export PATH="/opt/node%s/bin:$PATH"\n' "$major" > "$(agent_state_dir)/node-path.sh"
    log_info "Node $major に切り替え (/opt/node$major)"; return 0
  fi
  if [ -s "${NVM_DIR:-$HOME/.nvm}/nvm.sh" ]; then        # codex-universal 系
    # shellcheck disable=SC1091
    . "${NVM_DIR:-$HOME/.nvm}/nvm.sh" && nvm use "$major" >/dev/null 2>&1 && { log_info "nvm で Node $major"; return 0; }
  fi
  log_warn "Node $major が見つからない(現在 v$cur)。環境設定側で版を指定してください"
}

node_artifact_ok() {
  case "$1" in
    pnpm) [ -f node_modules/.modules.yaml ] ;;
    yarn) [ -f node_modules/.yarn-integrity ] || [ -f node_modules/.yarn-state.yml ] || [ -f .yarn/install-state.gz ] ;;
    bun)  [ -d node_modules ] && [ -n "$(ls -A node_modules 2>/dev/null)" ] ;;
    npm)  [ -f node_modules/.package-lock.json ] ;;
    *) return 1 ;;
  esac
}

node_deps() {
  [ -f package.json ] || return 0
  have node || { log_warn "package.json があるが node が無い"; return 1; }
  select_node_version
  local pm key
  if [ -f pnpm-lock.yaml ]; then pm=pnpm; key="$(stamp_key pnpm-lock.yaml package.json)"
  elif [ -f yarn.lock ]; then pm=yarn; key="$(stamp_key yarn.lock package.json .yarnrc.yml)"
  elif [ -f bun.lock ] || [ -f bun.lockb ]; then pm=bun; key="$(stamp_key bun.lock bun.lockb package.json)"
  elif [ -f package-lock.json ] || [ -f npm-shrinkwrap.json ]; then pm=npm; key="$(stamp_key package-lock.json npm-shrinkwrap.json package.json)"
  else
    log_warn "package.json にロックファイルが無い。ロックを作らないため導入を飛ばす(AGENT_NODE_ALLOW_NO_LOCK=1 で npm install)"
    [ "${AGENT_NODE_ALLOW_NO_LOCK:-0}" = "1" ] || return 0
    pm=nolock; key="nolock-$(stamp_key package.json)"
  fi
  key="$key-$(node -v)"
  if [ "$pm" != nolock ] && stamp_ok node "$key" && node_artifact_ok "$pm"; then log_info "$pm: 変更なし(スキップ)"; return 0; fi
  stamp_clear node
  case "$pm" in
    pnpm) have corepack && corepack enable >/dev/null 2>&1
          have pnpm || npm install -g pnpm >/dev/null 2>&1 || { log_warn "pnpm を用意できない"; return 1; }
          retry 3 pnpm install --frozen-lockfile --prefer-offline || return 1 ;;
    yarn) have corepack && corepack enable >/dev/null 2>&1
          if [ -f .yarnrc.yml ]; then retry 3 yarn install --immutable || return 1
          else retry 3 yarn install --frozen-lockfile --prefer-offline || return 1; fi ;;
    bun)  have bun || { log_warn "bun.lock があるが bun が無い"; return 1; }
          retry 3 bun install --frozen-lockfile || return 1 ;;
    npm)  retry 3 npm ci --no-audit --no-fund --prefer-offline || return 1 ;;
    nolock) retry 3 npm install --no-audit --no-fund --no-package-lock || return 1 ;;
  esac
  if [ "$pm" != nolock ] && ! node_artifact_ok "$pm"; then log_warn "$pm: 導入後に成果物が見つからない"; return 1; fi
  stamp_set node "$key"
}

# ---- Python ----------------------------------------------------------------
venv_ok() { [ -x .venv/bin/python ] && .venv/bin/python -c 'import sys' >/dev/null 2>&1; }

python_deps() {
  local key
  if [ -f uv.lock ]; then
    have uv || pip install --quiet uv >/dev/null 2>&1 || { log_warn "uv を用意できない"; return 1; }
    key="$(stamp_key uv.lock pyproject.toml .python-version)"
    if stamp_ok python "$key" && venv_ok; then log_info "uv: 変更なし(スキップ)"; return 0; fi
    stamp_clear python
    retry 3 uv sync --frozen --all-extras || retry 2 uv sync --frozen || return 1
  elif [ -f poetry.lock ]; then
    have poetry || pip install --quiet poetry >/dev/null 2>&1 || { log_warn "poetry を用意できない"; return 1; }
    key="$(stamp_key poetry.lock pyproject.toml)"
    if stamp_ok python "$key" && venv_ok; then log_info "poetry: 変更なし(スキップ)"; return 0; fi
    stamp_clear python
    POETRY_VIRTUALENVS_IN_PROJECT=true retry 3 poetry install --no-interaction --no-root || return 1
  elif ls requirements*.txt >/dev/null 2>&1; then
    # shellcheck disable=SC2046
    key="$(stamp_key $(ls requirements*.txt))"
    if stamp_ok python "$key" && venv_ok; then log_info "pip: 変更なし(スキップ)"; return 0; fi
    stamp_clear python
    venv_ok || python3 -m venv .venv || return 1
    local f
    for f in requirements*.txt; do retry 3 .venv/bin/pip install --quiet -r "$f" || return 1; done
  elif [ -f pyproject.toml ]; then
    log_warn "pyproject.toml にロックが無い。ロックを作らないため導入を飛ばす(uv lock をコミットすると自動化される)"
    return 0
  else
    return 0
  fi
  venv_ok || { log_warn "python: 導入後に .venv が見つからない"; return 1; }
  stamp_set python "$key"
}

# ---- Go / Rust / Ruby / PHP / JVM ------------------------------------------
go_deps() {
  [ -f go.mod ] || return 0
  have go || { log_warn "go.mod があるが go が無い"; return 1; }
  local key; key="$(stamp_key go.mod go.sum)-$(go env GOVERSION 2>/dev/null)"
  if stamp_ok go "$key" && GOFLAGS=-mod=mod GOPROXY=off go mod download >/dev/null 2>&1; then log_info "go: 変更なし(スキップ)"; return 0; fi
  retry 3 go mod download || return 1
  stamp_set go "$key"
}
rust_deps() {
  [ -f Cargo.toml ] || return 0
  have cargo || { log_warn "Cargo.toml があるが cargo が無い"; return 1; }
  [ -f Cargo.lock ] || { log_warn "Cargo.lock が無い。fetch を飛ばす"; return 0; }
  local key; key="$(stamp_key Cargo.lock rust-toolchain.toml rust-toolchain)"
  if stamp_ok rust "$key" && cargo fetch --locked --offline >/dev/null 2>&1; then log_info "cargo: 変更なし(スキップ)"; return 0; fi
  retry 3 cargo fetch --locked || return 1
  stamp_set rust "$key"
}
ruby_deps() {
  [ -f Gemfile.lock ] || return 0
  have bundle || { log_warn "Gemfile.lock があるが bundler が無い"; return 1; }
  local key; key="$(stamp_key Gemfile.lock .ruby-version)"
  if stamp_ok ruby "$key" && bundle check >/dev/null 2>&1; then log_info "bundler: 変更なし(スキップ)"; return 0; fi
  BUNDLE_FROZEN=true retry 3 bundle install --jobs 4 || return 1
  stamp_set ruby "$key"
}
php_deps() {
  [ -f composer.lock ] || return 0
  have composer || { log_warn "composer.lock があるが composer が無い"; return 1; }
  local key; key="$(stamp_key composer.lock)"
  if stamp_ok php "$key" && [ -f vendor/autoload.php ]; then log_info "composer: 変更なし(スキップ)"; return 0; fi
  retry 3 composer install --no-interaction --no-progress --prefer-dist || return 1
  stamp_set php "$key"
}
jvm_deps() {
  [ "${AGENT_JVM_WARMUP:-0}" = "1" ] || return 0
  local key
  if [ -x ./gradlew ]; then
    key="$(stamp_key gradle/wrapper/gradle-wrapper.properties build.gradle build.gradle.kts gradle.lockfile)"
    stamp_ok jvm "$key" && return 0
    retry 2 ./gradlew --no-daemon -q dependencies >/dev/null || return 1
  elif [ -f pom.xml ] && have mvn; then
    key="$(stamp_key pom.xml)"
    stamp_ok jvm "$key" && return 0
    retry 2 mvn -q -B dependency:go-offline || return 1
  else
    return 0
  fi
  stamp_set jvm "$key"
}

# ---- 追加 ------------------------------------------------------------------
uses_playwright() {
  case "${AGENT_PLAYWRIGHT:-auto}" in 0) return 1 ;; 1) return 0 ;; esac
  grep -qE '"(@playwright/test|playwright|playwright-core)"' package.json 2>/dev/null && return 0
  grep -qiE '(^|[^a-z-])(pytest-)?playwright' requirements*.txt pyproject.toml 2>/dev/null && return 0
  return 1
}
browsers_present() {   # browsers_present <name...> — ブラウザ本体が実在するか
  local dir="${PLAYWRIGHT_BROWSERS_PATH:-$HOME/.cache/ms-playwright}" b
  for b in "$@"; do ls -d "$dir/$b"-* >/dev/null 2>&1 || return 1; done
}
playwright_browsers() {
  uses_playwright || return 0
  local browsers="${AGENT_PLAYWRIGHT_BROWSERS:-chromium}" ver key bin
  if [ -x node_modules/.bin/playwright ]; then bin=node_modules/.bin/playwright
  elif [ -x .venv/bin/playwright ]; then bin=.venv/bin/playwright
  else log_warn "Playwright を使う設定だが、まだ依存が入っていない(deps を先に)"; return 0; fi
  ver="$($bin --version 2>/dev/null)"; key="$ver-$browsers"
  # shellcheck disable=SC2086
  if stamp_ok playwright "$key" && browsers_present $browsers; then log_info "playwright: 導入済み(スキップ)"; return 0; fi
  stamp_clear playwright
  local deps=""
  [ "$(id -u)" = "0" ] && deps="--with-deps"
  # shellcheck disable=SC2086
  retry 2 "$bin" install $deps $browsers || return 1
  # shellcheck disable=SC2086
  browsers_present $browsers || { log_warn "playwright: 導入後にブラウザが見つからない"; return 1; }
  stamp_set playwright "$key"
}
compose_file() {
  local f
  for f in compose.yaml compose.yml docker-compose.yml docker-compose.yaml; do [ -f "$f" ] && { echo "$f"; return; }; done
}
docker_images() {
  [ "${AGENT_DOCKER_PULL:-0}" = "1" ] || return 0
  local f; f="$(compose_file)"; [ -n "$f" ] || return 0
  have docker || { log_warn "docker が無い"; return 0; }
  docker info >/dev/null 2>&1 || { log_warn "docker デーモンが動いていない"; return 0; }
  local key; key="$(stamp_key "$f")"
  stamp_ok docker "$key" && return 0
  retry 2 docker compose -f "$f" pull --quiet || return 1
  stamp_set docker "$key"
}

# plan: 導入前にまとめて確認するための一覧(CLI・接続先・サービス)
plan() {
  local clis=() domains=() services=() notes=()
  add() { local -n arr="$1"; local v; for v in "${@:2}"; do [[ " ${arr[*]} " == *" $v "* ]] || arr+=("$v"); done; }
  add domains github.com codeload.github.com objects.githubusercontent.com release-assets.githubusercontent.com
  add clis git
  if [ -f package.json ]; then
    add clis node
    add domains registry.npmjs.org
    [ -f pnpm-lock.yaml ] && add clis pnpm
    [ -f yarn.lock ] && add clis yarn && add domains registry.yarnpkg.com repo.yarnpkg.com
    { [ -f bun.lock ] || [ -f bun.lockb ]; } && add clis bun
  fi
  if [ -f uv.lock ] || [ -f poetry.lock ] || ls requirements*.txt >/dev/null 2>&1 || [ -f pyproject.toml ]; then
    add clis python3; add domains pypi.org files.pythonhosted.org
    [ -f uv.lock ] && add clis uv
    [ -f poetry.lock ] && add clis poetry
  fi
  [ -f go.mod ] && add clis go && add domains proxy.golang.org sum.golang.org
  [ -f Cargo.toml ] && add clis cargo && add domains crates.io index.crates.io static.crates.io static.rust-lang.org
  [ -f Gemfile.lock ] && add clis bundle && add domains rubygems.org index.rubygems.org
  [ -f composer.lock ] && add clis composer && add domains repo.packagist.org packagist.org
  { [ -x ./gradlew ] || [ -f pom.xml ]; } && add clis java && add domains repo.maven.apache.org repo1.maven.org plugins.gradle.org services.gradle.org downloads.gradle.org
  if uses_playwright; then
    add domains cdn.playwright.dev playwright.download.prss.microsoft.com
    add notes "Playwright: root なら --with-deps で apt(archive.ubuntu.com, security.ubuntu.com)も使う"
  fi
  local cf; cf="$(compose_file)"
  if [ -n "$cf" ]; then
    add clis docker; add domains registry-1.docker.io auth.docker.io production.cloudflare.docker.com
    grep -q 'ghcr.io' "$cf" && add domains ghcr.io
    grep -qiE 'image:\s*\S*postgres' "$cf" && add services postgres
    grep -qiE 'image:\s*\S*redis' "$cf" && add services redis
    add notes "Docker: Codex Cloud では未記載のため使えない前提。Claude では利用可"
  fi
  grep -rqsE 'gitlab\.com' package.json go.mod Cargo.toml pyproject.toml requirements*.txt .gitmodules 2>/dev/null \
    && add domains gitlab.com registry.gitlab.com && add notes "GitLab からの取得あり(通信の許可のみ。標準連携ではない)"
  # 外部 API の手掛かり(テストで実際に呼ぶなら、ホストの許可と network secret が要る)
  local apis=() k
  for k in OPENAI_API_KEY ANTHROPIC_API_KEY SUPABASE_URL STRIPE_SECRET_KEY FIREBASE GOOGLE_APPLICATION_CREDENTIALS; do
    grep -rqs --include='*.example' --include='.env.sample' --include='*.md' "$k" . 2>/dev/null && apis+=("$k")
  done
  echo "## 導入前の確認(setup-deps.sh plan)"
  echo "- CLI: ${clis[*]:-なし}"
  echo "- 接続先(通常は既定の許可リストで足りる): ${domains[*]}"
  echo "- 作業ログ送信: script.google.com script.googleusercontent.com"
  echo "- サービス: ${services[*]:-なし}"
  echo "- 外部 API の手掛かり: ${apis[*]:-なし}(テストで呼ぶ場合だけ、ホストとテスト用キーの登録を確認)"
  local n; for n in "${notes[@]}"; do echo "- 注: $n"; done
  return 0
}

main() {
  local cmd="${1:-all}"
  case "$cmd" in
    plan) plan; return 0 ;;
    deps|extras|all) ;;
    *) echo "usage: $0 {deps|extras|all|check|doctor|plan}" >&2; return 2 ;;
  esac
  local t0; t0=$(date +%s)
  if [ "$cmd" = deps ] || [ "$cmd" = all ]; then
    local l
    for l in node python go rust ruby php jvm; do
      step "$l deps" "$STEP_TIMEOUT" bash "$SELF_DIR/setup-deps.sh" __fn "${l}_deps"
    done
  fi
  if [ "$cmd" = extras ] || [ "$cmd" = all ]; then
    step "playwright"    "$STEP_TIMEOUT" bash "$SELF_DIR/setup-deps.sh" __fn playwright_browsers
    step "docker images" "$STEP_TIMEOUT" bash "$SELF_DIR/setup-deps.sh" __fn docker_images
  fi
  write_summary "$(agent_state_dir)/last-setup.json"
  log_info "done in $(( $(date +%s) - t0 ))s, failed steps: $FAILED (summary: $(agent_state_dir)/last-setup.json)"
  if [ "$FAILED" -gt 0 ] && [ "$STRICT" = "1" ]; then return 1; fi
  return 0
}

# 各段は自分自身を子プロセス(__fn)として呼ぶ(timeout は関数を直接実行できないため)。
if [ "${1:-}" = "__fn" ]; then
  "$2"
  exit $?
fi
with_lock setup-deps main "$@"
