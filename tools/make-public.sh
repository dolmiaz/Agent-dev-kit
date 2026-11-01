#!/usr/bin/env bash
# make-public.sh <出力先> — 公開用の写しを作る(履歴は持ち込まない)。元のリポジトリは変更しない。
#   1. 公開してよいものだけを写す(下の INCLUDE)。personal/・reports/・ログ・個人の lock は写さない
#   2. publish-check で個人の値・秘密・公開しないファイルを調べる(personal/publish-deny.local.txt があれば使う)
#   3. 問題が無ければ、出力先で新しい git リポジトリを作る案内を出す(push はしない)
set -euo pipefail
SRC="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${1:?出力先のディレクトリを指定する}"
[ ! -e "$OUT" ] || [ -z "$(ls -A "$OUT" 2>/dev/null)" ] || { echo "出力先が空でない: $OUT" >&2; exit 2; }
mkdir -p "$OUT"

INCLUDE=(README.md PUBLISH.md SECURITY.md LICENSE LICENSE-NOTICE.md .gitignore .github install.py kit endpoint/Code.gs endpoint/appsscript.json endpoint/Deny.gs.example
         local skills/skillsync.py skills/bundle-template tests tools
         docs/01-codex-cloud-settings.md docs/02-claude-code-web-settings.md docs/03-network-packages-permissions.md
         docs/04-identity-signing-secrets.md docs/05-worklog-to-obsidian.md docs/06-manual-setup.md
         docs/08-skills-distribution.md docs/09-issue-to-merge-workflow.md docs/10-publish-polish.md docs/sources.md)
for p in "${INCLUDE[@]}"; do
  [ -e "$SRC/$p" ] || { echo "注: 無いので飛ばす: $p" >&2; continue; }
  mkdir -p "$OUT/$(dirname "$p")"
  cp -R "$SRC/$p" "$OUT/$p"
done
find "$OUT" \( -name '__pycache__' -o -name '.DS_Store' -o -name '*.log' \) -prune -exec rm -rf {} +

EXTRA=()
[ -f "$SRC/personal/publish-deny.local.txt" ] && EXTRA=(--extra "$SRC/personal/publish-deny.local.txt")
if python3 "$SRC/tools/publish-check.py" "$OUT" "${EXTRA[@]}"; then
  echo "公開用の写しを作った: $OUT"
  echo "次(本人が判断): LICENSE と権利表記を確認し、cd '$OUT' && git init && git add -A && git commit。"
  echo "元のリポジトリの履歴は公開しない(個人の値を含むため)。"
else
  echo "公開しない: 上の項目を直してから作り直す($OUT は確認用に残す)" >&2
  exit 1
fi
