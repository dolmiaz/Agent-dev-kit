# Claude Code on the web の設定一覧と推奨構成

確認日: 2026-10-29(出典は [sources.md](sources.md) の [A*])。
対象は Anthropic-hosted の **cloud session**(claude.ai/code・モバイル・Desktop の Cloud・`claude --cloud`)。
ローカル CLI、Remote Control、Desktop の Local/SSH、GitHub Actions、self-hosted 環境は別物。

> 前提
> - ユーザーの `~/.claude/settings.json`・`~/.claude/skills`・ユーザー MCP・ユーザーの hooks は**クラウドで読まれない**と公式に明記 [A3][A4]。setup script で VM 内に `~/.claude/settings.json` を書いた場合も公式の記載は無いので、**権限や hooks はそこに置かない**。
> - リポジトリの `.claude/settings.json` の hooks・permissions は**単一リポジトリの通常 session** で効く。Projects の thread では効かない [A15]。
> - リポジトリの `.claude/settings.json` は**ローカルの Claude Code にも効く**。そこでキットは許可ルールを書かず、hooks だけを置き、hooks の中で `CLAUDE_CODE_REMOTE=true` のときだけ判定を返す(ローカルの権限は変わらない)。

凡例: ○ / × / ?(公式に記載なし)。

## 1. 実行環境

| 項目 | 状態 | 設定場所 | 適用範囲 | 推奨値 | 理由・副作用 | 必要なユーザー操作 |
|---|---|---|---|---|---|---|
| VM・OS | ○(置換不可) | — | Anthropic-hosted | 既定 | 毎回新しい VM、Ubuntu 24.04 x86_64、約 4 vCPU / 16 GB / 30 GB [A2] | なし |
| プリインストール | ○ | — | — | — | Python 3(uv, poetry, pytest, ruff)、Node 20/21/22、Ruby、PHP、Java 21、Go、Rust、Docker、PostgreSQL 16・Redis 7(停止)、gh, jq, rg [A2] | なし |
| Node の版 | ○ | — | セッション | `.nvmrc` があれば `/opt/nodeNN` に自動で切替 | [A2] | なし |
| Docker | ○ | — | — | 使える。事前 pull は `AGENT_DOCKER_PULL=1` | Docker Hub・ghcr は既定の許可リストに入っている [A2] | 必要時 |
| ブラウザ | ?(記載は chromedriver のみ) | — | — | Playwright をリポジトリ側で導入、OS ライブラリは環境の Setup script | 配布元の許可が必要 | 追加ドメイン |

## 2. セットアップ・hooks

| 項目 | 状態 | 設定場所 | 適用範囲 | 推奨値 | 理由・副作用 | 必要なユーザー操作 |
|---|---|---|---|---|---|---|
| Setup script | ○ | 環境セレクタ > 環境を編集 > Setup script | 環境 | `.agent-kit/claude/environment-setup.sh` の中身(OS ライブラリだけ) | Claude 起動前に root で実行、約5分以内に成功でスナップショット(変更時・約7日で再構築)。非0でセッション不起動。**環境変数はまだ入っていない** [A2] | 貼る |
| SessionStart hook | ○ | リポジトリ `.claude/settings.json` | 単一リポジトリの session | `.agent-kit/claude/session-start.sh` | 開始・再開ごと。依存・作者名・未送信ログの再送・開始時のモデル名をセッションごとに記録(`sessions/<id>.json`。入力の `model`)[A4] | なし(install.py) |
| UserPromptSubmit / Stop hook | ○(クラウドでも同じイベントが発火と公式に記載) | 同上 | 同上 | `turn-start.sh` / `stop.sh` | 変更があったのに記録が無いターンで1回だけ促す(block)。2回目は止めない [A4] | なし |
| PreToolUse hook | ○ | 同上 | 同上 | `pretool.py`(クラウドでだけ判定) | 通常の開発コマンドを allow、公開・マージ・デプロイ等を ask、force push・削除・秘密ファイルを deny。ローカルでは何も返さない | なし |
| SessionEnd | ○(pause/reclaim 時の発火は ?) | — | — | **使わない** | 終了時の実行は保証されていない。毎ターンの記録と次回の再送で補う | なし |
| 判定用の変数 | ○ | 自動 | — | `CLAUDE_CODE_REMOTE=true`、`CLAUDE_CODE_REMOTE_SESSION_ID=cse_…` | [A4][A2] | なし |

## 3. ネットワーク

| 項目 | 状態 | 設定場所 | 適用範囲 | 推奨値 | 理由・副作用 | 必要なユーザー操作 |
|---|---|---|---|---|---|---|
| アクセスレベル | ○ | 環境 > Network access(None / Trusted / Full / Custom) | 環境 | **Custom +「Also include default list」+ 追加ドメイン** | Trusted(既定)は主要レジストリ・GitHub・**GitLab(gitlab.com, registry.gitlab.com)**・Docker・Ubuntu 等 [A2]。Full は持ち出し経路が広がる | 設定 |
| 追加ドメイン | ○ | 同上 | 環境 | `script.google.com`, `script.googleusercontent.com`(作業ログ)、`cdn.playwright.dev`, `playwright.download.prss.microsoft.com`(Playwright を使うとき) | 変更は約1分で既存セッションにも反映 [A2] | 追加 |
| GitHub | ○(専用プロキシ) | 変更不可 | — | — | プロキシが資格情報を付与。**ブランチ削除・タグ等の push を拒否**、どのブランチへの push かは制限しない(`feature/...` は可)。GraphQL は遮断(`gh pr` / `gh issue` は 403、REST の `gh api` は可)[A2] | なし |
| GitLab | 通信○ / 標準連携× | — | — | — | Trusted に含まれるが資格情報は付かない。クローン・PR 作成は GitHub が必要 [A1][A2] | — |

## 4. 環境変数・シークレット

| 項目 | 状態 | 設定場所 | 適用範囲 | 推奨値 | 理由・副作用 | 必要なユーザー操作 |
|---|---|---|---|---|---|---|
| 環境変数 | ○ | 環境 > Environment variables | 個人環境は本人のみ / 共有環境は全員が読める | `AGENT_LOG_ENDPOINT`, `AGENT_LOG_KEY`(Claude 用の鍵) | 実行するコマンドからも読める [A2]。ログの鍵は「決まったフォルダに新規作成だけ」の権限なので許容(docs/04) | 登録 |
| Network secrets | ○(既定はヘッダ注入) | 環境 > Network secrets | 環境 | **外部 API のテスト用キーはこちら** | 値は VM に入らない。GitHub・公開レジストリ・setup script には付かない [A2]。Apps Script はヘッダを読めないのでログの鍵には使えない | 必要時 |
| GH_TOKEN | ○ | — | — | **設定しない** | 未設定ならプロキシが付与 [A2] | なし |

## 5. GitHub・コミット・署名

| 項目 | 状態 | 設定場所 | 適用範囲 | 推奨値 | 理由・副作用 | 必要なユーザー操作 |
|---|---|---|---|---|---|---|
| 接続 | ○ | Claude GitHub App | 本人 | App の対象を使うリポジトリだけに | [A5] | 確認 |
| 作業ブランチ | 一部 | — | — | `feature/<番号>-<名前>`(`agent-gh claim` が API で作る) | 対話セッションの命名は未記載。スケジュールタスクは `claude/` [A16]。プロキシは feature/ への push を妨げない [A2] | — |
| PR | ○ | Create PR / `gh api` REST | — | `agent-gh pr-create`(draft、使えないプランでは通常 PR + `agent:wip`) | REST で PR を作れる明記は無い(?)。画面の Create PR は確実 [A5] | 最初の PR で確認 |
| 作者 | ? | — | — | SessionStart で `Claude Code (cloud) <claude-cloud@example.com>` | `Claude-Session:` トレーラーが付く [A2][A14] | 最初の PR で確認 |
| 署名 | ? | — | — | 署名しない | docs/04 | — |

## 6. 承認・権限・モデル

| 項目 | 状態 | 設定場所 | 適用範囲 | 推奨値 | 理由・副作用 | 必要なユーザー操作 |
|---|---|---|---|---|---|---|
| モード | ○ | 開始時のドロップダウン | セッション | **Auto**(使えれば)。無ければ Accept edits | クラウドは Accept edits / Plan / Auto。Auto はリポジトリ設定では指定できない [A6] | 開始時に選ぶ |
| クラウドだけの許可 | ○(hook による) | `.agent-kit/claude/pretool.py`(ホワイトリスト)と `cloud-permissions.json`(追加の ask/deny・秘密ファイル名) | 単一リポジトリの session | `AGENT_CLOUD_AUTO_ALLOW=1` | Accept edits でも通常の開発コマンドで止まらない。設定ファイルの deny/ask は hook の allow より強い(Claude Code の仕様) | なし |
| モデル | ○ `/model claude-opus-5-5` | セッション内 | セッション | 実装は既定、**レビューは `claude-opus-5-5`** | 引数形式のみ。組織の availableModels で弾かれることがある。fallbackModel は過負荷時のみ [A12] | レビュー時に指定 |
| レビューのモデルの確認 | 一部(hook の入力の `session_id`・`transcript_path` は ○ [A4]。transcript の中身の書式は ?) | PreToolUse hook(`pretool.py`)が `agent-gh` のコマンドを見たときに記録 | セッション | 既定で有効(`AGENT_CLOUD_AUTO_ALLOW=0` でも記録は残す) | セッション全体(サブエージェントを含む)の応答のモデルを transcript から読む。読めない行・モデル名の無い応答の記録があれば「未確認」= merge-gate は不合格。hook は agent-gh のコマンドに一意な実行 ID(AGENT_EVIDENCE_ID)を付けて書き換え(updatedInput)、agent-gh はその ID の証拠だけを使う。書き換えが効かない版では、別セッションの候補が並ぶと未確認になる(updatedInput がクラウドで効くかは未検証)。ID を付けて渡した記録は、ID を持たないコマンドからは使わない。本体の transcript が無い・サブエージェントの一覧を読めないときも未確認。独立性の判定(このセッションで実装していないか)も transcript を厳しく読み、読めない行があれば未確認。証拠は 60 秒で期限切れになるので、確認(ask)の要る agent-gh のコマンドを承認するのに 60 秒以上かかると、そのコマンドは証拠なし(未確認)で動く。1つのコマンドの中で agent-gh を2回呼ぶと、2回目は証拠なし。どちらも「未確認」の側に倒れるだけで、合格にはならない。開始時のモデル(SessionStart の `model`)はセッションごとのファイルに残すが、判定には使わない | 最初のレビューで「confirmed」になるか確認 |
| 費用 | ○ | 設定 > 使用量 | アカウント | **usage credits(追加利用)はオフ** | VM 料金は無くプラン枠を共有 [A13] | 確認 |

## 7. skills・CLAUDE.md・MCP

| 項目 | 状態 | 推奨値 | 理由 |
|---|---|---|---|
| CLAUDE.md(リポジトリ) | ○ | キットの区間(クラウドのときだけ適用と明記) | [A3] |
| `.claude/skills/` | ○(Projects 以外) | キットの3つ+配布する個人スキル(skillsync) | 版を固定して配れる [A9] |
| claude.ai アカウントの skills | ○(セッション開始時に同期と公式記載。非公式に「読まれない」報告あり) | 補助手段(`skillsync zip` で作って手動アップロード) | 版管理が無い。frontmatter は6キーのみ受け付け [A9] |
| `~/.claude/skills` | × | — | [A9] |
| `.mcp.json` | ○(承認なしで読み込まれる) | 使わない | [A11] |

## 推奨構成(まとめ)

1. 個人環境: Network = Custom + 既定リスト + 必要な追加ドメイン、Setup script = `environment-setup.sh`、環境変数 = `AGENT_LOG_ENDPOINT` / `AGENT_LOG_KEY`。
2. リポジトリ: `install.py` で hooks(`.claude/settings.json`)、`.claude/skills/`、CLAUDE.md の区間、`.agent-kit/` を入れる。許可ルールは設定ファイルに書かない。
3. 開始時に Auto(無ければ Accept edits)。レビューの session は `/model claude-opus-5-5`。
4. usage credits はオフのまま。
