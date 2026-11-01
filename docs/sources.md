# 出典(確認日はすべて 2026-10-29)

公式文書を優先した。WebFetch(要約経由)で読んだものが多いので、数値や細かい挙動は原文で再確認を推奨。
`developers.openai.com/codex/*` は `learn.chatgpt.com/docs/*` へ転送される。

## Anthropic(Claude Code on the web)

| 番号 | URL | 主な内容 |
|---|---|---|
| A1 | https://code.claude.com/docs/en/claude-code-on-the-web | cloud session 全般、`/model`、制限、GitLab 非対応、アカウントの skills の読み込み |
| A2 | https://code.claude.com/docs/en/cloud-environments | イメージ・リソース・setup script・キャッシュ・ネットワーク段階と既定許可リスト(gitlab.com 含む)・GitHub プロキシ・環境変数・network secrets・ユーザー設定が届かないこと |
| A3 | https://code.claude.com/docs/en/settings | クラウドで読まれる設定(ユーザー設定・settings.local.json は読まれない) |
| A4 | https://code.claude.com/docs/en/hooks | hook イベント、クラウドでも同じイベントが発火、`CLAUDE_CODE_REMOTE`、Stop の block、SessionStart の入力(`model` 等) |
| A5 | https://code.claude.com/docs/en/web-quickstart | GitHub 接続、Create PR、ブランチ選択 |
| A6 | https://code.claude.com/docs/en/permission-modes | クラウドのモード、defaultMode の扱い |
| A9 | https://code.claude.com/docs/en/skills | `.claude/skills`、アカウントの skills の同期、frontmatter、`${CLAUDE_SKILL_DIR}` |
| A11 | https://code.claude.com/docs/en/mcp | `.mcp.json` |
| A12 | https://code.claude.com/docs/en/model-config | モデル ID(`claude-opus-5-5`)、availableModels、fallbackModel |
| A13 | https://code.claude.com/docs/en/costs | 利用枠・usage credits |
| A14 | https://code.claude.com/docs/en/settings-reference | `attribution` |
| A15 | https://code.claude.com/docs/en/claude-projects | Projects の thread の制約 |
| A16 | https://code.claude.com/docs/en/routines | スケジュールタスク(`claude/` ブランチ、モデル選択) |
| A17 | https://support.claude.com/en/articles/12512180-use-skills-in-claude , https://support.claude.com/en/articles/12512198-how-to-create-custom-skills | claude.ai へのスキルのアップロード |

## OpenAI(Codex Cloud — 新しい Cloud 環境)

| 番号 | URL | 主な内容 |
|---|---|---|
| C2 | https://learn.chatgpt.com/docs/environments/cloud-environments | Install script / Start skill、Networking と Package managers、network secrets、Personal vault、VM サイズ、7日、GitLab・GHES 非対応、repo の skills |
| C3 | https://help.openai.com/de-de/articles/20001545-using-codex-cloud(英語版は取得失敗) | 保存状態、GitHub 接続、移行期間 |
| C5 | https://learn.chatgpt.com/docs/pricing | 利用枠 |
| C6 | https://learn.chatgpt.com/docs/agent-approvals-security | クラウドとローカルの承認の違い |
| C8 | https://learn.chatgpt.com/docs/agent-configuration/agents-md | AGENTS.md(クラウドでの扱いは未記載) |
| C9 | https://learn.chatgpt.com/docs/third-party/github | `@codex` は Legacy 経由 |
| C10 | https://learn.chatgpt.com/docs/third-party/gitlab | GitLab beta(MR レビュー、修正は Legacy) |
| C11 | https://learn.chatgpt.com/docs/enterprise/agent-security | Enterprise の制御 |
| C12 | https://learn.chatgpt.com/docs/extend/mcp , https://learn.chatgpt.com/docs/hooks | ローカルの MCP・hooks |
| C13 | https://github.com/openai/codex-universal | 参考実装(Ubuntu 24.04、`CODEX_ENV_*`) |
| C14 | https://learn.chatgpt.com/docs/build-skills | `.agents/skills` の探索・形式 |
| C16 | https://learn.chatgpt.com/docs/model-selection | モデル選択 |
| C17 | https://community.openai.com/t/codex-web-creates-empty-branches-in-repositories-with-required-signed-commits/1378320 | 非公式: 署名必須で空ブランチ |
| C18 | https://raw.githubusercontent.com/openai/codex/main/codex-rs/cloud-tasks/src/cli.rs | `codex cloud exec --attempts`、`diff`/`apply` |
| C20 | https://help.openai.com/en/articles/12642688-using-credits-for-flexible-usage-in-chatgpt-pluspro | クレジット・自動補充 |
| (対象外) | https://learn.chatgpt.com/docs/environments/cloud-environment , https://learn.chatgpt.com/docs/cloud/internet-access | Legacy(比較のみ) |

## Google(Apps Script / Drive)

| 番号 | URL | 主な内容 |
|---|---|---|
| G1 | https://developers.google.com/apps-script/guides/services/quotas | 実行6分、同時30、Properties 50,000回/日・値9KB・合計500KB、超過時は例外 |
| G2 | https://workspace.google.com/products/apps-script/ , https://developers.google.com/apps-script/guides/cloud-platform-projects | Gmail アカウントで利用可、課金を求める記述なし |
| G3 | https://developers.google.com/apps-script/guides/web , https://developers.google.com/apps-script/concepts/deployments , https://developers.google.com/apps-script/guides/content | イベントにヘッダの欄が無い、版固定のデプロイ、script.googleusercontent.com への転送 |
| G4 | https://developers.google.com/apps-script/reference/drive/drive-app | DriveApp の承認スコープ(auth/drive)、getStorageUsed / getStorageLimit |
| G9 | https://support.google.com/googleone/answer/9312312 | 容量超過時は新しいファイルを作れない |

## GitHub

| 番号 | URL | 主な内容 |
|---|---|---|
| G5 | https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/about-rulesets , https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches | プラン別の提供(Free の非公開は不可) |
| G6 | https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/configuring-pull-request-merges/about-merge-methods-on-github | rebase マージは署名検証なし |
| G7 | https://docs.github.com/en/authentication/managing-commit-signature-verification/about-commit-signature-verification , https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets | 署名必須と squash(未署名があるとブロックされうる) |
| G8 | https://docs.github.com/en/rest/pulls/pulls | draft PR のプラン制限(Free / Pro の非公開は不可) |
| G10 | https://docs.github.com/en/rest/git/refs | ref 作成(409 / 422) |
| G11 | https://docs.github.com/en/rest/repos/rules , https://docs.github.com/en/rest/checks/runs , https://docs.github.com/en/rest/commits/statuses , https://docs.github.com/en/rest/issues/labels | ルールセット・CI・ラベルの REST |
| G12 | https://docs.github.com/en/communities/using-templates-to-encourage-useful-issues-and-pull-requests/syntax-for-issue-forms | Issue forms |

## その他

| 番号 | 内容 |
|---|---|
| P1 | npm の `playwright-core@1.64.1` を展開して確認: 配布元 `cdn.playwright.dev`, `playwright.download.prss.microsoft.com` |
| D1 | Google Drive の検索とメタデータ(フォルダ ID と親) |

本人の Vault・Mac で確認した出典は `personal/docs/sources-personal.md`(公開版には含めない)。
