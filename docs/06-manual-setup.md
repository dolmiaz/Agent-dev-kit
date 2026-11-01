# 適用手順(対象リポジトリと実環境)・手動設定

Codex Legacy は対象外(必須手順にしない。既存の Legacy 環境は消さなくてよい)。
各段に「確認すること」を付けた。**Drive に届いた**ことと **Obsidian に取り込まれた**ことは別々に確認する。

## 0. 適用の前にまとめて確認すること

| 項目 | 確認方法 | 決めること |
|---|---|---|
| 対象リポジトリ | owner/repo を決める | まず1つで試す |
| プラン・公開範囲 | GitHub の設定、または `gh api repos/{o}/{r}` の `private` と本人のプラン | ルールセットと draft PR が使えるか(docs/09 の表) |
| 既存のルール | `gh api repos/{o}/{r}/rulesets`、`gh api repos/{o}/{r}/rules/branches/main` | 足すだけで壊さないか |
| リポジトリ固有の指示 | AGENTS.md / CLAUDE.md に「main へ変更しない」「マージしない」等があるか | キットの区間より優先される(区間に明記済み)。解除しない |
| 必要な接続先・CLI | `bash .agent-kit/bin/setup-deps.sh plan`(導入後)か、ロックファイルを見る | 追加ドメインと外部 API のテスト用キーの要否 |
| 配るスキル | 正本の sets.json(docs/08) | リポジトリに合うセット |
| 必須 CI | `agent-gh ci-policy`(導入後) | ルールセット等で必須になっていなければ `.agent-kit/ci-policy.json` に必須のテストを書く |
| 本人確認(任意) | — | 使うなら、本人の SSH 鍵の公開鍵をベースブランチの `.agent-kit/human-confirm-allowed-signers` に置く(docs/09)。新しい鍵を作るかは本人の判断 |
| gh の版 | `gh --version` | 2.48 以上(`gh api --slurp` を使う)。古い環境では agent-gh の一覧取得が失敗する |

## 1. 作業ログの受け口(Apps Script)— 承認済みの A

1. https://script.google.com/ で新しいプロジェクト `agent-log-endpoint`。
2. `Code.gs` を `endpoint/Code.gs` に置き換える。プロジェクトの設定で「appsscript.json をエディタで表示」をオンにし、`endpoint/appsscript.json` に置き換える。
3. 送信キーを2本作る(Mac): `bash local/new-log-key.sh codex` → 貼る → `bash local/new-log-key.sh claude` → 貼る。
   値は 1Password に保存され、クリップボード経由で貼るだけ(画面・チャット・Git に出さない)。
4. プロジェクトの設定 > スクリプト プロパティ:
   - `KEY_CODEX` / `KEY_CLAUDE`(手順3の値。別々の値)
   - `FOLDER_CODEX` = `<Codex のログを置くフォルダの ID>`
   - `FOLDER_CLAUDE` = `<Claude のログを置くフォルダの ID>`
   - `DENY_FOLDERS` = `<同期ミラー等、書いてはいけないフォルダの ID,…>`
   - 同じ ID を `Deny.gs`(`endpoint/Deny.gs.example` をもとに作る)にも書くと、設定が消えても拒否が残る
   - 個人の値の一覧は `personal/docs/personal-setup.md`(公開版には含めない)
5. エディタで `checkSetup` を実行 → 初回は Drive の権限(本人の Drive 全体。docs/04 の説明)を承認。
   **確認**: ログに `FOLDER_CODEX = <名前> (親: …) denied=false`、`FOLDER_CLAUDE = <名前> (親: …) denied=false`、両方の鍵が「設定あり」、「同じ値: false」、拒否フォルダの件数。
6. デプロイ > 新しいデプロイ > ウェブアプリ、実行ユーザー「自分」、アクセス「全員」。URL を控える(これが `AGENT_LOG_ENDPOINT`。秘密として扱う)。
7. **確認**: `curl -sL "<URL>"` → `{"ok":true,"status":"alive"}`(何も書かない)。
8. 止めるとき: デプロイをアーカイブ、または `KEY_*` を消す(即座に全拒否)。コードを変えたら「デプロイを管理 > 編集 > 新しいバージョン」(版を固定しているので、保存だけでは公開中の動作は変わらない)。

## 2. 対象リポジトリへの導入

Mac で(キットを展開した場所を `$KIT` とする):
```bash
cd <対象リポジトリ> && git switch -c feature/0-agent-kit
python3 $KIT/install.py . --dry-run --github-templates     # 何が変わるか確認
python3 $KIT/install.py . --github-templates --profile <個人設定の env>   # 作者のメール等(個人設定は公開版に含めない)
bash .agent-kit/bin/setup-deps.sh check && bash .agent-kit/bin/setup-deps.sh plan
# 必須 CI がルールセット等で決まっていなければ: .agent-kit/ci-policy.json を作る(docs/09)
git add .agent-kit .claude .agents AGENTS.md CLAUDE.md .github
git commit -m "Add agent-kit for cloud agents" && git push -u origin feature/0-agent-kit
```
PR を作り、本人がマージする(これ自体もキットの流れの最初の題材にできる)。片方だけなら `--only codex` / `--only claude`。

**確認**: `.claude/settings.json` に permissions が足されていないこと(hooks だけ)、AGENTS.md / CLAUDE.md の既存の文が残っていること。

### 読み取り専用の点検

導入後は `bash .agent-kit/bin/setup-deps.sh doctor` (`check` も同じ)で、基本 CLI、検出した言語に必要な CLI、
設定ファイルの形式と導入先の構成を点検する。足りないもの・不正なものは固定のツール名と相対ファイル名だけで表示し、
設定値・絶対パスは表示しない。外部 API や各 CLI を実行せず、設定・依存・ロック・キャッシュ・状態ディレクトリも作らない。

終了コードは、検出した問題が無ければ0、不足・不正があれば1、余分な引数があれば2。自動修復は行わない。
ツールの版、認証、サービスへの接続、Geminiへの送信承認はこの点検の対象外。`gh` の2.48以上という前提などは別途確認する。
回帰テストは `python3 -B tests/test_doctor.py`。

## 3. スキルの配布(必要なものを選んだ後)

```bash
# 正本(非公開リポジトリ)を作る: 新しいリポジトリの作成は本人の操作。skills/bundle-template をもとにする
mkdir -p <正本> && cd <正本> && git init && cp $KIT/skills/bundle-template/*.json .
python3 $KIT/skills/skillsync.py export <手元のスキル> .       # スキルごと(Mac 専用・秘密・巨大ファイルは拒否)
# sets.json(セット)と checks.json(本人が確認した代表処理)を書く
git add -A && git commit -m "skills v1" && git tag v1   # (GitHub に非公開で push)
# 対象リポジトリへ(全部の配布先を確認 → 一時領域で準備・検証 → まとめて差し替え。失敗したら元のまま)
python3 $KIT/skills/skillsync.py vendor <正本> <対象リポジトリ> --set core,<言語など> --ref v1
python3 $KIT/skills/skillsync.py verify <対象リポジトリ> --run
```
- 対象リポジトリが公開なら、配ったスキルも公開される。`--public-target` が要り、再配布できると分かるものだけ配れる。
- 途中で止まったら `skillsync recover <対象リポジトリ>`。

## 4. Claude Code on the web の環境

claude.ai/code → 環境セレクタ → 個人環境(共有しない)。

| 欄 | 値 |
|---|---|
| Network access | **Custom**、「Also include default list」オン、追加: `script.google.com`, `script.googleusercontent.com`(+ Playwright を使うなら `cdn.playwright.dev`, `playwright.download.prss.microsoft.com`) |
| Environment variables | `AGENT_LOG_ENDPOINT=<URL>`、`AGENT_LOG_KEY=<Claude 用の値>`(1Password からクリップボード経由で貼る) |
| Network secrets | 外部 API をテストで呼ぶリポジトリのときだけ、テスト用キー |
| Setup script | `.agent-kit/claude/environment-setup.sh` の中身 |

ほか: 使用量の usage credits はオフのまま。GitHub App の対象リポジトリを確認。

## 5. Codex Cloud の環境(新方式)

Settings > Codex Cloud > Environments → 新規(Who can use: Only me、対象リポジトリを選ぶ)。

| 欄 | 値 |
|---|---|
| Internet access | 全開放オフ、Package managers オン、Additional allowed domains: `script.google.com`, `script.googleusercontent.com`、必要に応じて Playwright・GitLab・RubyGems/Packagist |
| Environment variables | `AGENT_RUNTIME=codex-cloud`, `AGENT_SERVICE=codex` |
| Personal vault | `AGENT_LOG_ENDPOINT`, `AGENT_LOG_KEY`(Codex 用の値)を Type=Environment variable、Applies to=Selected(この環境) |
| Install script | 作成の会話で「Install script は `bash .agent-kit/codex/setup.sh` を実行するだけ。Start skill は不要」と頼み、テスト結果を見て Save → Publish |

ほか: クレジットの自動補充はオフ。

## 6. GitHub(リポジトリの書き込み。本人の承認後)

```bash
python3 .agent-kit/bin/agent-gh labels            # 作るラベルの確認
python3 .agent-kit/bin/agent-gh labels --apply    # 作成
```
必須 CI の確認: `python3 .agent-kit/bin/agent-gh ci-policy`(ルールセット・ブランチ保護の必須 check と ci-policy.json を表示)。

担当確保の ref の確認(最初の1回): テスト用の Issue で `agent-gh claim` → `agent-gh claim-status`。
`refs/agent-claims/...` を作れなければ `AGENT_CLAIM_REF_PREFIX=refs/heads/agent-claims` にする(docs/09)。

ルールセット(プランが対応しているとき、新しいルールセットを1つ足す。既存は触らない):
Settings > Rules > Rulesets > New branch ruleset — 対象 Default branch、Restrict deletions、Block force pushes、Require a pull request before merging。署名必須は入れない(docs/04 の A)。Bypass list は空か本人のみ。

## 7. 実環境での確認(両サービス。結果は作業ログに残る)

最初のタスクで頼むこと(Claude・Codex それぞれ):
> `bash .agent-kit/bin/setup-deps.sh check` の結果を見せて。`.agent-kit/` と skills の一覧(使えるスキル名)を答えて。security-audit の ATTACK-CLASSES.md の見出しを3つ挙げて(配っていれば)。README に1行足してテストを走らせ、skill cloud-worklog で作業ログを記録して。最後に `python3 .agent-kit/bin/agent-log status` を見せて。

| 確認 | Claude | Codex |
|---|---|---|
| setup が通る(失敗の段が無い) | `~/.local/state/agent-kit/*/session-start.log` | Install script の結果 |
| hooks が動く | 記録前に終えようとすると1回促される。通常のコマンドで承認待ちにならない | (hooks なし) |
| AGENTS.md / CLAUDE.md の区間が読まれている | 指示どおり作業ログを書く | 同左(Codex Cloud での AGENTS.md の扱いは公式未記載なので、ここで確かめる) |
| スキルの認識・参照・実行 | 一覧に出る・参照ファイルを読める・`python3 .agent-kit/bin/skillsync verify . --run --bundle <正本の場所>` が通る | 同左 |
| レビューの証拠 | `/model claude-opus-5-5` のセッションで `agent-gh review-start` → 状態ディレクトリの `evidence/` に記録ができ、review-post が「モデル confirmed」になる | (Codex はレビュー担当にしない) |
| **Drive に届いた** | Drive の Claude フォルダに1ファイル増える | Drive の ChatGPT フォルダに1ファイル増える |
| 再送で重複しない | もう一度 `agent-log sync`(送信済みは送らない) | 同左 |
| 作者名 | 最初の PR のコミット作者 | 同左 |
| **Obsidian に入った**(別に確認) | 取り込み処理が動いた後、Vault に入ったか | 同左 |

## 7b. 対外文章の Gemini 推敲(v2.3。docs/10)

既定で `AGENT_POLISH_REQUIRED=1`。**本人が下の 1〜4 を済ませるまで、PR の作成・リリース・README を含む push は「保留」になる**
(実装・コミット・それ以外の push は続けられる)。すぐに使わないなら、先に `AGENT_POLISH_REQUIRED=0` にしておく。

1. 契約を決める(有料の Gemini API キー・Vertex AI・Code Assist Standard/Enterprise。新しい課金・認証)。
2. Gemini CLI を確かめた版で入れる。クラウドの Setup script と、ネットワークの許可(Gemini API の宛先)。
3. `~/.config/agent-kit/publish.env` に承認・モデル名・認証方式・版・専用の設定の置き場を書く(キーの値は書かない)。
4. 公開してよい短い文で一度だけ試し、`agent-publish show <ID>` で応答したモデルと版を確かめる。

## 8. Issue からの流れの試運転

1. テスト用の Issue を1件(テンプレートで受入条件まで)作り、`agent:ready` を付ける。
2. Claude の session で「Issue をやって」(skill issue-task)。`feature/<番号>-…` と draft(または WIP)PR ができること。
3. 別の session で `/model claude-opus-5-5` にして「PR #N をレビュー」(skill pr-review: review-start で SHA を固定 → review-post --sha)。`agent-gh merge-gate` の結果を見る。
   レビュー中に実装側で push すると、review-post が「保留」(終了コード 6)になることも確かめる。
4. 本人がマージする(または見送る)。

## ロールバック

| 戻すもの | 方法 |
|---|---|
| リポジトリのキット | `python3 $KIT/install.py <repo> --uninstall`(manifest にあるものだけ外す。手で変えたファイル・本人の設定・既存の .agent 等は残す。外す前の状態はリポジトリの外 `~/.local/state/agent-kit/<repo>-<hash>/install-backups/` に保存)。または PR を revert |
| 配ったスキル | `skillsync rollback --bundle <正本>` / `skillsync remove`(途中で止まったら `skillsync recover`) |
| 担当確保 | `agent-gh release <番号>`。ref 自体の削除は本人が GitHub で行う |
| 対外文章の推敲(v2.3) | `AGENT_POLISH_REQUIRED=0`(hook の拒否も止まる)。送信だけ止めるなら `AGENT_GEMINI_SEND_APPROVED=0`(公開は保留)。docs/10 の 5 |
| Claude の環境 | Setup script を空に、Network を Trusted に、環境変数を削除 |
| Codex の環境 | Install script を空にして Republish、環境変数・Personal vault を削除 |
| 受け口 | デプロイのアーカイブ、または `KEY_*` の削除 |
| 鍵の漏えい | `new-log-key.sh` で作り直し、Apps Script と各環境を差し替え。Drive の既存ファイルは「作成のみ」なので無事 |
| ラベル・ルールセット | GitHub の設定画面から削除・無効化 |
