#!/usr/bin/env bash
# new-log-key.sh — 作業ログの送信キーを Mac で作る(本人が対話的に実行する)。
#   bash new-log-key.sh codex     # Codex 用
#   bash new-log-key.sh claude    # Claude 用(Codex と別の値)
#
# 値を画面・シェル履歴・ファイルに出さない:
# - 1Password CLI(op)が使え、サインイン済みなら、1Password に新しい項目として保存し、クリップボードにも入れる。
#   (op の解除は本人の操作。無人処理からは呼ばない)
# - op が無ければクリップボードにだけ入れる。貼り付け先(Apps Script のスクリプト プロパティ、各クラウド環境の設定)
#   に貼ったら、1Password に手で保存し、クリップボードを消す(このスクリプトが 60 秒後に消す)。
set -euo pipefail
svc="${1:-}"
case "$svc" in codex|claude) ;; *) echo "usage: $0 codex|claude" >&2; exit 2 ;; esac
command -v pbcopy >/dev/null || { echo "pbcopy が無い(macOS で実行してください)" >&2; exit 1; }
title="agent-log key ($svc)"
key=""
if command -v op >/dev/null 2>&1 && op whoami >/dev/null 2>&1; then
  if op item get "$title" >/dev/null 2>&1; then
    echo "1Password に「$title」が既にある。作り直すなら古い項目の名前を変えてから実行してください" >&2
    exit 1
  fi
  # 値は 1Password に生成させる(このスクリプトの引数や履歴に値が出ない)
  if op item create --category=password --title="$title" --generate-password='letters,digits,48' >/dev/null 2>&1; then
    key="$(op item get "$title" --fields password --reveal 2>/dev/null)"
    echo "1Password に「$title」を作成した"
  else
    echo "1Password への保存に失敗。openssl で作り、クリップボードにだけ入れる(貼った後で手で保存)" >&2
  fi
fi
[ -n "$key" ] || key="$(openssl rand -hex 24)"
printf '%s' "$key" | pbcopy
unset key
echo "クリップボードに $svc 用のキーを入れた(60 秒後に消す)。貼る場所: Apps Script の KEY_$(printf %s "$svc" | tr a-z A-Z)、$svc の環境の AGENT_LOG_KEY"
( sleep 60; printf '' | pbcopy ) >/dev/null 2>&1 &
