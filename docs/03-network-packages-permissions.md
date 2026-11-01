# パッケージ・接続先・権限の一覧(最初にまとめて決める)

毎回同じ承認を求めないよう、対象リポジトリごとに最初に1回まとめて確認する。
**ネットワーク接続 / コマンド実行 / リポジトリへの書き込み / 公開・マージ・デプロイ**を分けて整理する。

## 0. まず必要なものを洗い出す

対象リポジトリで(Mac でもクラウドでも):

```bash
bash .agent-kit/bin/setup-deps.sh plan
```

ロックファイル・Playwright・compose・GitLab からの取得・外部 API の手掛かり(`.env.example` 等の変数名)から、
必要な CLI・接続先・サービスを一覧にする。これを見て、追加ドメインと外部 API のテスト用キーの要否を1回で決める。

## 1. 依存の導入(ロックファイルを書き換えない)

| 検出 | 導入 | スキップ条件(チェックアウトごと) |
|---|---|---|
| `pnpm-lock.yaml` / `yarn.lock` / `bun.lock` / `package-lock.json` | `pnpm install --frozen-lockfile` / `yarn install --immutable`(classic は `--frozen-lockfile`)/ `bun install --frozen-lockfile` / `npm ci` | ロック+package.json+Node 版のハッシュが同じ **かつ** `node_modules` の成果物(`.package-lock.json` / `.modules.yaml` 等)がある |
| `package.json` のみ | 既定は導入しない(`AGENT_NODE_ALLOW_NO_LOCK=1` で `npm install --no-package-lock`) | — |
| `uv.lock` / `poetry.lock` / `requirements*.txt` | `uv sync --frozen` / `poetry install --no-root` / `.venv` に `pip install -r` | ハッシュ同じ **かつ** `.venv/bin/python` が動く |
| `pyproject.toml` のみ | 導入しない(警告) | — |
| `go.mod` / `Cargo.lock` / `Gemfile.lock` / `composer.lock` | `go mod download` / `cargo fetch --locked` / `bundle install`(frozen)/ `composer install` | ハッシュ同じ **かつ** オフラインで解決できる(`GOPROXY=off` / `--offline` / `bundle check` / `vendor/autoload.php`) |
| Playwright | `playwright install [--with-deps] chromium` | 版+ブラウザ名が同じ **かつ** ブラウザ本体がある |
| compose ファイル | `AGENT_DOCKER_PULL=1` のときだけ pull | ハッシュ |

スタンプはリポジトリの外(`~/.local/state/agent-kit/<repo名>-<実パスのハッシュ>/`)に置くので、別リポジトリ・別チェックアウトで混ざらない。
各段に制限時間(既定600秒)と再試行(最大3回、待ち2→4秒)。失敗しても既定は止めず記録(`AGENT_SETUP_STRICT=1` で止める)。同時実行は flock で直列化。

## 2. 接続先

| 用途 | ドメイン | Claude(Trusted) | Codex(Package managers) |
|---|---|---|---|
| npm / Yarn | registry.npmjs.org, registry.yarnpkg.com | ○ | ○ |
| PyPI | pypi.org, files.pythonhosted.org | ○ | ○ |
| Go / Rust | proxy.golang.org, sum.golang.org / crates.io 系, static.rust-lang.org | ○ | ○ |
| Maven / Gradle | repo.maven.apache.org, plugins.gradle.org 等 | ○ | ○ |
| RubyGems / Packagist | rubygems.org / repo.packagist.org | ○ | **×(追加)** |
| Ubuntu apt | archive.ubuntu.com, security.ubuntu.com | ○ | ○ |
| GitHub(取得) | github.com, codeload.github.com, release-assets.githubusercontent.com | ○ | ○ |
| **GitLab(通信)** | gitlab.com, registry.gitlab.com | ○ | **×(追加)** |
| Docker | registry-1.docker.io, ghcr.io | ○ | ×(Codex では Docker 自体が未記載) |
| **作業ログ** | script.google.com, script.googleusercontent.com | **追加** | **追加** |
| **Playwright** | cdn.playwright.dev, playwright.download.prss.microsoft.com | **追加** | **追加** |
| 外部 API(テストで実際に呼ぶ場合だけ) | 各 API のホスト | 追加 + network secret | 追加 + network secret |

GitLab は「通信の許可」と「標準連携」を分ける。標準連携(リポジトリとして選ぶ・MR を作る)は両サービスとも新しいクラウド環境では非対応。通信は許可できるが資格情報は付かない(公開リポジトリの取得のみ)。

全開放(Claude の Full / Codex の Enable all public internet access)にしない理由: 上の範囲で依存と作業ログは足りる。全開放は、Issue 本文などに仕込まれた指示でコードや情報を任意の宛先へ送れる経路になる。

## 3. コマンド実行(Claude Code on the web)

PreToolUse hook(`.agent-kit/claude/pretool.py`)が、**クラウド(`CLAUDE_CODE_REMOTE=true`)でだけ**判定を返す。ローカルでは何も返さない。設定ファイルに許可ルールを書かないので、ローカルの Claude Code の権限は変わらない。

独立レビュー(2回)を受けて、**allow は厳しいホワイトリスト**にした。コマンド・サブコマンド・使ってよいオプションを pretool.py に明示し、shlex で分けた語の配列で照合する。少しでも外れたら allow しない(判定しない = UI で選んだ Auto / Accept edits に任せる)。

| 区分 | 内容 |
|---|---|
| allow | `git status/diff/log/show/add/commit(-m)/switch/checkout <branch>/fetch/pull/rebase <upstream>/stash/restore/rm/mv/branch(一覧・作成)`、`git push [-u] origin feature/<name>`、npm/pnpm/yarn/bun の install・test・run <script>(deploy・publish 等の名前は除く)、pytest・tsc・eslint・go build/test・cargo build/test・make(deploy 等は除く)、`python3 -m pytest`、`node script.js`、uv sync、`gh api repos/…` の GET、`bash .agent-kit/bin/*`、ls/cat/head/tail/wc/grep(非再帰)/rg(既定)/`sed -n 'N,Mp'`、mkdir/touch/cp/mv(保護ファイル以外)、psql・docker ps/compose |
| allow しない | `$` `` ` `` `{` `}` `~` `..` を含む語、`VAR=値` で始まるもの、`git -c` / `-C`、リダイレクト・tee、`xargs`・`find -exec`・`bash -c`・`eval`・`python -c`・`node -e`・`npx`・`pnpm exec/dlx`・`uv run`・`poetry run`(中身は deny / ask の判定にかける)、ホワイトリスト外のオプション |
| ask | feature/ 以外・`HEAD`・`src:dst`・追加オプション付きの push、`commit --amend`、`rebase -i/--root/--exec`、`stash drop/clear`、`reset --hard`、`fetch/pull --upload-pack/--force`、`gh api` の GET 以外とデータ送信、publish・deploy・release・upload・login を含むパッケージマネージャ・make 等、ホストを触れる `docker run`(-v・--privileged 等)、**権限・hook・CI・設定ファイル(`.agent-kit/claude/*`・`.agent-kit/config.env`・`.claude/*`・`.git/hooks`・`.github/workflows/*`)への書き込み**(リダイレクト・cp/mv/sed/sort -o/tee・`--output=`・git mv/checkout/restore・Edit/Write)、`cloud-permissions.json` の ask 一覧(gh pr merge・gh release・aws 等) |
| deny | force push(`--force*` とその省略形・`-f` を含む短いフラグの組・refspec の `+`)、`--mirror`・`--delete`・`:branch`、`git branch -d/-D/--del…`、`git tag -d`、filter-repo、`rm -rf /`・`~`・`.git`、秘密ファイル(`.env`・`.env.*`(.env.example 等は除く)・`.envrc`・鍵・`.netrc`・`.git-credentials`・`hosts.yml`・`.npmrc`・`.pypirc`・`secrets/`)を含むコマンド・`rev:path`・Read/Edit/Write |

**限界(正直に)**: `npm test`・`pytest`・`node script.js`・`make` のように「コードを実行する」コマンドを allow すると、そのコード(エージェントが書き換えられる)が行う操作も通る。この hook は「普段の操作で承認待ちにしない」ことと「直接の危険な操作をはっきり止める」ためのもので、完全な防御ではない。**最終的な防御は GitHub 側の保護**(main の保護・force push と削除の禁止)と、**Claude のプロキシ**(ブランチ削除・タグ等の push の拒否)、**ネットワークの許可リスト**。

`AGENT_CLOUD_AUTO_ALLOW=0`(環境変数か config.env)でこの hook の判定を止められる。

Codex Cloud には承認の設定が無いため、同じ方針を AGENTS.md の区間に書き、GitHub 側の保護で実際に止める。

## 4. リポジトリへの書き込み・公開・マージ・デプロイ

| 操作 | Claude Code on the web | Codex Cloud | 誰が確認するか |
|---|---|---|---|
| 作業ブランチへのコミット・push | 自動(feature/) | VM 内のコミットのみ。PR はタスクで依頼 | 不要 |
| Issue の確保(`refs/agent-claims/issue-<番号>` の作成・更新、ブランチ作成・ラベル・コメント) | `agent-gh claim`(REST。ブランチ以外の ref がプロキシを通るかは未検証) | 依頼側(dot / 本人)が実施 | 不要 |
| レビューの記録・本人確認 | `agent-gh review-start` / `review-post`(コメント) | しない | 本人確認(`human-confirm`)は本人の端末だけ |
| draft PR 作成 | `agent-gh pr-create` / Create PR | タスクで依頼 | 不要 |
| main への push | ask + ルールセット(使えるプランのとき) | AGENTS.md で禁止 + ルールセット | 必要 |
| マージ | ask。キットのツールはマージしない | しない | 本人(または別途権限を与えた実行者) |
| 公開・リリース・デプロイ・課金 | ask | しない | 本人 |
| ブランチ・タグ削除 | deny + プロキシが拒否 | しない | — |

## 5. 新しい認証情報・権限(まとめて確認)

| 項目 | 目的 | 範囲 | 漏れたときの影響 | 状態 |
|---|---|---|---|---|
| Apps Script の受け口 | 作業ログを Drive へ | 2つのフォルダへの新規作成だけ(コードで限定) | — | 方針承認済み・未作成 |
| 送信キー(Codex 用・Claude 用の2本) | 受け口の認証 | そのサービスのフォルダに作業ログ形式の新規ファイルを作るだけ。1時間120件・1日1000件・200KB | 迷惑なファイルを置かれる(読む・消す・上書き・別フォルダは不可)。キーを作り直せば止まる | 未作成 |
| GitHub ラベル | Issue の状態管理 | 対象リポジトリ | — | `agent-gh labels --apply`(承認後) |
| 担当確保の ref(`refs/agent-claims/*`) | Issue 単位の二重着手防止 | 対象リポジトリ。ブランチではないので CI は動かない | 他人の確保を書き換えられる(引き継ぎ・外すは記録が残る) | 最初の claim で作られる |
| `.agent-kit/ci-policy.json` | 必須 CI の指定 | 対象リポジトリ | — | 必要なら作る(docs/09) |
| GitHub ルールセット | main の保護 | 対象リポジトリ | — | プランを確認してから(docs/06) |
| コミット署名鍵 | 作らない | — | — | — |
