# Agent-dev-kit

Codex Cloud と Claude Code on the web に、依存の準備、作業ログ、Issue の確保、PR のレビュー、公開前の文章推敲を導入するキットです。
共通の処理を `.agent-kit/` にまとめ、各サービスのセットアップと hooks から呼び出します。

対象は新しい Codex Cloud 環境と Claude Code on the web です。Codex Legacy、ローカル CLI、Remote Control 向けの環境構築は対象外です。
このリポジトリの `local/` には、Mac での設定補助とログ回収の道具があります。

## キットを導入する

Python 3 と Git を用意し、まず導入先に加わる変更を確認します。

```bash
python3 install.py <repo> --dry-run --github-templates
python3 install.py <repo> --github-templates
```

`--only codex` / `--only claude` で対象を選べます。作者のメールなどは、公開しない個人設定を `--profile <個人設定の env>` で渡します。
`--profile` の値は導入先の `.agent-kit/config.env`（通常は追跡対象）に入るため、API キーなどの秘密を含めないでください。
更新と取り外しは manifest に記録した範囲で行い、既存の設定や手で変更したファイルを扱う際は確認結果を表示します。
手動設定と切り戻しは [導入手順](docs/06-manual-setup.md) を参照してください。

```bash
python3 install.py <repo> --uninstall
```

導入後は、[Codex Cloud](docs/01-codex-cloud-settings.md) または [Claude Code on the web](docs/02-claude-code-web-settings.md) の環境設定を行います。
ネットワークの許可先やパッケージの準備は [ネットワークと依存](docs/03-network-packages-permissions.md) にまとめています。
ログ送信には本人の送信先と認証の設定が必要で、自動的に第三者の送信先へ接続する初期値はありません。

## ローカルの Gemini API キーを用意する

Gemini CLI の導入と認証方式の選択は利用者が行います。このキットは CLI のインストール、ログイン、API キーの発行、課金設定を行いません。
CLI の [公式導入手順](https://geminicli.com/docs/get-started/installation/) と [認証手順](https://geminicli.com/docs/get-started/authentication/) を確認し、利用する版の設定と挙動を確かめてください。

このリポジトリで API キー方式を使う場合は、追跡対象の `.env.sample` を雛形にします。雛形は値を空のまま保ちます。
`.env` がすでにあれば、その内容を維持してください。新規作成時だけ、次の手順で上書きせずに作れます。

```bash
(umask 077; set -C; cat .env.sample > .env)
chmod 600 .env
```

本人がエディタで `.env` の `GEMINI_API_KEY=` の右にキーを貼り付けて保存します。
`.env` は Git で除外し、権限は本人だけが読み書きできる `600` にします。読み取り専用の `400` もローカル用ランチャーで扱えます。
キーを README、チャット、ログ、コミット、コマンド引数に書かないでください。

```bash
python3 local/with-gemini-env.py --check
git check-ignore -v -- .env
git ls-files -- .env
```

`--check` はキーの設定有無だけを表示します。値を表示せず、認証確認や API リクエストも行いません。
最後のコマンドは何も出力しない状態が正しく、`.env.sample` は追跡対象です。

**`agent-publish` は `.env` を自動では読み込みません。** ローカル用の `local/with-gemini-env.py` は、このチェックアウトの `.env` から
`GEMINI_API_KEY` だけをデータとして読み、子プロセスの環境変数でキットへ渡します。シェルとして実行せず、他の変数やシェル展開、重複したキーは拒否します。
通常の CLI が行う `.env` 探索には依存しません。キットの推敲実行は、空の作業ディレクトリと `ignoreLocalEnv` を使います。

このファイルはローカル用です。クラウドでは各サービスの秘密の環境変数設定にキーを登録します。ローカルの `.env` をクラウドへ送らないでください。
導入先のキットへ `.env` やローカル用ランチャーを自動配置する処理もありません。

## Gemini に文章を推敲させる

公開前の Gemini 推敲は必須ゲートです。PR のタイトル・本文、リリースノート、README などが対象です。
キーを置くだけでは送信しません。本人が入力の扱い、料金、利用可能な枠、モデル、CLI 版を確認し、送信を承認してから使います。
無料枠の有無はキーやプロジェクトによって異なるため、[公式料金表](https://ai.google.dev/gemini-api/docs/pricing) だけで本人の残量や課金を判断しないでください。

必要な設定は次のとおりです。本人の本当のホームにある `~/.config/agent-kit/publish.env`、またはクラウドの環境変数で指定します。
API キーの値をこの設定ファイルへ書く方式ではありません。

| 設定名 | 本人が確認して指定する内容 |
| --- | --- |
| `AGENT_GEMINI_SEND_APPROVED` | 入力の扱いと費用を確認し、送信を承認した場合だけ `1` |
| `AGENT_GEMINI_AUTH` | API キー方式では `api-key` |
| `AGENT_GEMINI_KEY_ENV` | `GEMINI_API_KEY`。ローカル用ランチャーもこの名前を使う |
| `AGENT_GEMINI_MODEL` | 利用可能な具体的なモデル名。`auto`・`pro`・`flash` などの別名は使わない |
| `AGENT_GEMINI_CLI_VERSION` | 設定と挙動を確認した CLI の版。実行時の版と一致すること |
| `AGENT_GEMINI_HOME` | 普段の設定と分けた推敲専用の設定置き場 |

ランチャーは送信承認、モデル、CLI 版を自動設定しません。`AGENT_POLISH_REQUIRED=1` の既定値も維持します。
設定の優先順位、認証方式、送信内容、保留の条件は [公開前の推敲](docs/10-publish-polish.md) を参照してください。

このソースリポジトリで README の推敲を実行する入口は次です。送信する文書には公開してよい内容だけを残してください。
コマンドは設定が整うと Gemini へリクエストするため、料金・送信の承認後に実行します。

```bash
python3 local/with-gemini-env.py polish --kind readme --file README.md --path README.md
```

導入先では `python3 .agent-kit/bin/agent-publish polish ...` を使い、キーはプロセスの環境変数で渡します。
返された記録 ID の差分を作成側が読み、数値、機能、試験結果、否定の意味が変わっていないことを確認してから確定します。

```bash
python3 local/with-gemini-env.py confirm <ID> --reviewed-diff
python3 local/with-gemini-env.py apply <ID>
```

未設定、認証切れ、上限、版の不一致、別モデルの応答などで推敲が保留になったら、管理対象の公開を止めます。下書きと実装は続けられます。
推敲は技術レビューや本人の承認、マージ判断の代わりにはなりません。公開には確認済みの記録を用いる既存の `agent-publish` 手順を使います。

## 構成と各機能の手順

| 場所 | 内容 |
| --- | --- |
| `kit/.agent-kit/` | 導入先へ配置する共通処理、設定、セットアップ、hooks、スキル |
| `install.py` | 導入・更新・取り外しと manifest の管理 |
| `endpoint/` | Drive への書き込み専用の Apps Script 受け口 |
| `local/` | Mac のログ回収、送信キーの準備、ローカルの Gemini キー読み込み |
| `skills/` | 個人スキルの棚卸しと配布の入口、配布セットの雛形 |
| `tests/` | 外部サービスをモックに置き換えた回帰試験 |
| `tools/` | 公開用の写しの作成と秘密・個人情報の検査 |

- [作者・署名・秘密の管理](docs/04-identity-signing-secrets.md)
- [作業ログから Obsidian まで](docs/05-worklog-to-obsidian.md)
- [スキルの配布](docs/08-skills-distribution.md)
- [Issue からマージ可否の判定まで](docs/09-issue-to-merge-workflow.md)
- [出典](docs/sources.md)

## 検証する

Python 3、Bash、Git、Node.js を使います。シェル回帰試験は GNU 系のコマンドがある Linux 環境を前提にしています。
モック試験は一時的なローカル HTTP サーバーを使います。制限環境では追加の許可が必要な場合があります。

```bash
python3 tests/test_local_env.py
NO_NET=1 bash tests/run-tests.sh
python3 tests/test_agent_gh.py "$(mktemp -d)"
python3 tests/test_publish.py "$(mktemp -d)"
python3 tests/test_public_release.py
```

これらの試験は本物の API キーを読み込まず、本物の Gemini・GitHub・Drive へ接続しません。
`NO_NET=1` は実際の依存ダウンロード試験を省きます。実サービスへの接続確認の代わりにはなりません。

`.env` のある作業用チェックアウトをそのまま公開検査にかけると、除外対象として検出されます。
公開用の写しには許可したファイルだけを含め、空の `.env.sample` も検査します。値の入った雛形は拒否します。

```bash
bash tools/make-public.sh <存在しないか空の出力先>
python3 tools/publish-check.py <公開用の写し>
```

Git の履歴を公開する場合は `python3 tools/publish-check.py <repo> --history` も必要です。
検査で見つからなかったことは、安全性の証明ではありません。[秘密情報検査の範囲](SECURITY.md) と [権利表記](LICENSE-NOTICE.md) を確認してください。

## 運用上の約束

- プラットフォームのプロキシや許可リストを迂回せず、秘密の値を表示・記録・コミットしません。
- ロックファイルを変更せず、依存の導入とログ送信の失敗は既定では作業全体を止めません。
- クラウド用の許可設定をローカルの Claude へ適用しません。人間用の 1Password 鍵や YubiKey はクラウドへ持ち込みません。
- ツールはマージしません。モデル、独立したレビュー、必須 CI、マージ可否を確認できなければ未確認として扱います。
- リポジトリ固有の制限がキットの一般的な指示より優先します。

配布版は 2.3.0 を基礎とし、互換性のため内部名 `.agent-kit` と設定キーを維持しています。
個人設定、認証情報、個人のスキルの実体は配布対象に含めません。コードと文書は [MIT License](LICENSE) です。
