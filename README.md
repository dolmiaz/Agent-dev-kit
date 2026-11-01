# agent-dev-kit — Codex Cloud / Claude Code on the web 用のセットアップ一式

外出中でも承認待ちや環境構築の不備で止まりにくくするためのキット。
依存はロックファイルから自動で入れ、通常の開発操作は止めず、公開・マージ・デプロイ・削除・新しい認証情報だけを止める。
作業ログはターンごとに Markdown で残し、書き込み専用の受け口経由で Drive(ChatGPT / Claude フォルダ)へ届ける。
Issue → 実装 → 別セッションのレビュー → マージ可否の技術判定までを、二重着手せずに回す道具も含む。

対象: **新しい Codex Cloud 環境** と **Claude Code on the web**(Codex Legacy・ローカル CLI・Remote Control は対象外)。確認日 2026-10-29。

公開版の検証方法は下記の「検証」を参照。元の個人設定・検証ログ・Git 履歴は含めない。

## 中身

```
agent-dev-kit/
├── README.md / install.py(導入・更新・取り外し。manifest に記録したものだけ外す)
├── kit/.agent-kit/                       対象リポジトリへ入る中身
│   ├── VERSION, config.env               設定(秘密なし。環境変数 > config.env > 既定値)
│   ├── lib/common.sh                     共通: 実行環境・設定の読み込み・チェックアウトごとの状態・timeout・再試行・ロック・指紋
│   ├── lib/agentstate.py                 共通: 状態置き場、hook が残すセッションの証拠、transcript の読み取り
│   ├── lib/agentgh/                      agent-gh の本体(api / issues / claims / review / gate)
│   ├── bin/setup-deps.sh                 共通: 依存の判定と導入(deps / extras / all / check / plan)
│   ├── bin/agent-log                     共通: 作業ログ(UUID・秘密の除去・送信・ID+内容照合・outbox)
│   ├── bin/agent-gh                      共通: Issue 単位の確保・PR・SHA を固定したレビュー記録・必須 CI を含むマージ可否(マージはしない)
│   ├── bin/skillsync                     共通: 個人スキルの棚卸し・正本への取り込み・セット単位の配布・検証・ロールバック・recover
│   ├── bin/git-identity.sh, services.sh  共通: 作者名、PostgreSQL / Redis
│   ├── codex/setup.sh                    Codex: Install script から呼ぶ
│   ├── claude/environment-setup.sh       Claude: 環境の Setup script に貼る(OS ライブラリだけ)
│   ├── claude/session-start.sh, turn-start.sh, stop.sh, pretool.py   Claude: hooks(クラウドでだけ動く)
│   ├── claude/settings.fragment.json     Claude: .claude/settings.json に足す hooks(許可ルールは足さない)
│   ├── claude/cloud-permissions.json     Claude: クラウドだけの allow / ask / deny
│   ├── skills/cloud-worklog, issue-task, pr-review   両サービス共通のスキル
│   ├── snippets/AGENTS.md, CLAUDE.md     指示ファイルに入れる区間(各サービスで動いているときだけ適用)
│   └── github/ISSUE_TEMPLATE/agent-task.yml, pull_request_template.md
├── endpoint/Code.gs, appsscript.json     Drive への書き込み専用の受け口(Apps Script、無料)
├── endpoint/Deny.gs.example              書き込まないフォルダ ID をコードに固定する個人用ファイルの例
├── skills/skillsync.py                   skillsync の入口(本体は kit/.agent-kit/bin/skillsync)
├── skills/bundle-template/               スキルの正本の雛形(sets.json・checks.json)
├── tools/publish-check.py, make-public.sh 公開前の点検・公開用の写しの作成(個人の値・履歴を持ち込まない)
├── local/new-log-key.sh                  Mac: 送信キーを作る(値を画面に出さない)
├── local/collect-pending.py              Mac: repo モードを選んだ場合の回収(ID 固定・ID+内容照合)
├── tests/                                回帰テスト(本物の Code.gs をモックで実行、fake gh / rclone)
└── docs/
    ├── 01-codex-cloud-settings.md        Codex Cloud の設定一覧と推奨構成
    ├── 02-claude-code-web-settings.md    Claude Code on the web の設定一覧と推奨構成
    ├── 03-network-packages-permissions.md パッケージ・接続先・権限(最初にまとめて決める)
    ├── 04-identity-signing-secrets.md    作者名・認証・署名・秘密(署名必須の採否を含む)
    ├── 05-worklog-to-obsidian.md         ログの経路・形式・一意性・上限・Obsidian への取り込み
    ├── 06-manual-setup.md                適用手順(対象リポジトリと実環境)・手動設定・ロールバック
    ├── 08-skills-distribution.md         個人スキルの棚卸しと配布(セット・途中失敗への強さ・ライセンス)
    ├── 09-issue-to-merge-workflow.md     Issue → 実装 → レビュー → マージ可否の分担
    └── sources.md                        出典
```

## 共通部分とサービス固有部分

| | 共通 | Codex Cloud | Claude Code on the web |
|---|---|---|---|
| 依存 | setup-deps.sh | Install script → `codex/setup.sh` | 環境の Setup script(OS ライブラリ)+ SessionStart hook |
| 毎ターンの記録 | agent-log、skill cloud-worklog | hooks が無い前提 → AGENTS.md と skill | Stop hook が記録漏れを1回促し、送信も行う |
| 権限 | — | 承認の設定が無い → AGENTS.md + GitHub の保護 | PreToolUse hook がクラウドでだけ判定(ローカルは無変化)+ GitHub の保護 |
| 秘密 | 作業ログの除去 | network secrets / Personal vault | network secrets |
| Issue・レビュー | agent-gh、skills issue-task / pr-review | 確保は依頼側(dot / 本人) | REST で確保・PR・記録 |

## 使い方(概要)

```bash
python3 install.py <repo> --dry-run --github-templates
python3 install.py <repo> --github-templates          # --only codex / --only claude も可
python3 install.py <repo> --profile <個人設定の env>   # 作者のメール等を個人の値に(個人設定は公開版に入れない)
python3 install.py <repo> --uninstall                 # キットが入れたものだけ外す
bash tests/run-tests.sh                                # 回帰テスト(NO_NET=1 でネットワークなし)
bash tools/make-public.sh <出力先>                    # 公開用の写し(点検に通ったときだけ。履歴は持ち込まない)
```
手順の全体は [docs/06-manual-setup.md](docs/06-manual-setup.md)。

## 設計上の約束

- プラットフォームの制限(許可リスト・プロキシ・シークレットの段階)をシェルから迂回しない。
- ロックファイルを書き換えない。ロックが無い言語は導入を飛ばして警告する。
- セットアップやログの失敗で作業全体を止めない(既定)。止めたいときは `AGENT_SETUP_STRICT=1` / `AGENT_LOG_STRICT=1`。
- 未送信ログは既定で Git に入れない(`AGENT_LOG_PENDING_MODE=repo` を明示したときだけ)。
- クラウド向けの許可をローカルの Claude に適用しない(設定ファイルに許可ルールを書かず、hook がクラウドでだけ判定する)。
- 人間用の 1Password 鍵・YubiKey はクラウドに持ち込まない。クラウドではコミット署名しない。
- ツールはマージしない。マージ可否の技術判定と、正式な承認・マージの実行者・権限は分ける。
- モデル・独立したセッション・必須 CI・mergeable は、確かめられなければ「未確認」で、合格にしない(確認できたふりをしない)。
- 個人の値(メール・Drive のフォルダ ID・個人のパス・非公開リポジトリの名前)は personal/ に置き、汎用のコードと分ける。
- リポジトリ固有の指示(「main へ変更しない」「マージしない」等)はキットの区間より優先する。
- 存在しない設定や終了フックを作らない(Claude の SessionEnd、Codex の hooks は使わない)。

## 検証

Python 3、Bash、Git、Node.js が必要。外部サービスを呼ばないモック中心の検証は以下で実行する。

```bash
NO_NET=1 bash tests/run-tests.sh
python3 tests/test_agent_gh.py "$(mktemp -d)"
python3 tests/test_publish.py "$(mktemp -d)"
python3 tests/test_public_release.py
python3 tools/publish-check.py . --history
```

`NO_NET=1` は実際の依存ダウンロード試験を省く。本番の Codex Cloud、Claude Code on the web、GitHub、Drive、Gemini への接続確認の代わりにはならない。
モック試験は一時的なローカル HTTP サーバーを使用する。制限環境ではその試験が失敗する場合がある。

## 公開版の境界

- 配布版は 2.3.0 を基礎とする。内部ディレクトリ名 `.agent-kit` と設定キーは互換性のため変更しない。
- 個人設定、送信キー、Drive フォルダ ID、既存 Git 履歴、個人スキルの実体は含まない。
- 作業ログ送信は利用者が送信先と認証を設定してから使う。自動的に第三者の送信先へ接続する初期値はない。
- Gemini 推敲は元設計どおり公開前のゲート。送信同意・認証・モデル・CLI 版が未設定なら送信せず公開を保留する。詳細は [docs/10](docs/10-publish-polish.md)。
- ライセンスはまだ選定していない。[LICENSE-NOTICE.md](LICENSE-NOTICE.md) を参照。公開されていることと再利用の許諾は別。
- 秘密情報検査の限界と事故時の対応は [SECURITY.md](SECURITY.md) を参照。
