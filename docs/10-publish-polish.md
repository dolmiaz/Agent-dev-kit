# 10. 対外文章の Gemini 推敲と公開(agent-publish)

PR のタイトル・本文、リリースノート、README など外に出す文章を、Codex・Claude Code が下書きしたあと、
Gemini CLI に書き直させてから公開する。共通の入口は `.agent-kit/bin/agent-publish`(Codex・Claude Code の両方)。

```
下書き → 対象・秘密情報の検査 → Gemini の推敲 → 差分の機械検査 → 作成側エージェントの確認 → 既存の公開手順
          (agentpub.scan)        (run_gemini)      (compare)          (confirm --reviewed-diff)   (agent-gh pr-create 等)
```

推敲は、技術レビュー・Opus によるレビュー・PR の承認・マージ判断の**代わりにならない**。それぞれ別の工程のまま。

## 1. 前提の確認(2026-10-30 に公式資料で確かめたこと)

| 項目 | 内容 | 出典 |
|---|---|---|
| 版 | 最新の安定版は `@google/gemini-cli` 0.66.0(2026-10-27) | npm registry、`docs/changelogs/latest.md` |
| 利用できる契約 | 2026-06-18 から、Code Assist for individuals(無料を含む)・Google AI Pro・Ultra では応答しない。**有料の Gemini API キー**(または Gemini Enterprise Agent Platform の API キー)と、Code Assist Standard・Enterprise は引き続き使える。無料の API キーは告知に書かれておらず不明 | Google Developers Blog(2026-05-19)「transitioning Gemini CLI to Antigravity CLI」、geminicli.com/docs の告知 |
| ヘッドレス | `-p/--prompt`(標準入力があれば前に連結)、`--output-format json`(`response`・`stats`・`error`)。終了コード 0 成功 / 1 失敗 / 41 認証 / 42 入力 / 52 設定 / 53 回数上限 / 55 信頼されていないフォルダ。json のとき API の失敗は HTTP の状態コードで終わる(429 → 8 ビットで 173) | `docs/cli/headless.md`、ソース `exitCodes.ts`・`errors.ts` |
| モデル | `-m/--model` が最優先。既定は `auto`(別名 `pro`・`flash` などは振り分けられる)。失敗時の別モデルへの切り替えは、ヘッドレスでは「確認する」設定のものは起きず、「黙って」の設定のものだけが起きる | `docs/cli/model-routing.md`、ソース `fallback/handler.ts` |
| 認証 | Google でログイン(OAuth)・`GEMINI_API_KEY`・Vertex AI。ヘッドレスで認証が無ければ終了コード 41 | `docs/get-started/authentication.mdx` |
| 道具を持たせない | `tools.core: []` で組み込みのツールを登録しない。ポリシー(`--policy` の TOML、`toolName="*"`・`decision="deny"`)でも全部拒否できる。`--extensions none`、MCP サーバーは設定に書かなければ起動しない | `docs/reference/configuration.md`、`docs/reference/policy-engine.md`、ソース `config.ts` |
| 作業ディレクトリを読ませない | `context.fileName`(存在しない名前)・`context.includeDirectoryTree: false`・`context.memoryBoundaryMarkers: []`。`.env` は作業ディレクトリから上へ探すので、空の一時ディレクトリで動かし、`advanced.ignoreLocalEnv: true` | `docs/cli/gemini-md.md`、`docs/reference/configuration.md` |
| 送らない設定 | `telemetry.enabled: false`、`privacy.usageStatisticsEnabled: false`(既定は true)、`billing.overageStrategy: "never"` | 同上 |
| 入力の扱い | 無料の Gemini API: 入力と出力を製品の改善に使い、人が読むことがある(機密を送らない)。有料: 改善には使わない(不正の検出のために一定期間記録) | https://ai.google.dev/gemini-api/terms |

**今回、本物の Gemini には一度も送っていない**(有料の API キーの取得・設定は新しい課金と認証にあたるため、しない)。
本物の CLI での確認は「4. 本人の作業」の後で行う。

## 2. 作り

- 設定は `.agent-kit/config.env` < 本人の手元の上書き(利用者の本当のホームの `~/.config/agent-kit/publish.env`。
  `HOME` では場所を変えられない)< 環境変数(クラウドの環境の設定に本人が書いた値)の順に読む。
  Claude Code では、コマンドの前に付けた・`env` で付けた・`export` した `AGENT_*`・`GEMINI_*`・`HOME`・`XDG_*`・`PATH` 等を
  PreToolUse の hook が止める(`AGENT_POLISH_REQUIRED=0` や別のモデル名を、その場で付けて推敲を飛ばせない)。
  リポジトリの場所は今いる git から決める(`AGENT_REPO_ROOT` では変えない)。
- 送る前に止めるもの: 本人の承認(`AGENT_GEMINI_SEND_APPROVED=1`)が無い、モデル名が無い・別名(`auto`・`pro`・`flash` 等)、
  認証方式が無い、CLI の版が確かめた版と違う、API キーの環境変数が無い、下書き・事実メモに秘密・非公開の情報らしいもの
  (鍵・トークンの形、example 以外のメールアドレス、Drive の ID、手元のホームのパス、内部のホスト名・アドレス、「社外秘」等、
  `AGENT_PRIVATE_PATTERNS_FILE` の語)。
- 送るもの: 指示文(`.agent-kit/publish/polish-instructions.md`)、事実メモ、下書き。標準入力で渡す(コマンドの引数に載せない)。
  環境変数は PATH・言語・プロキシ・CA と、Gemini の認証に要るものだけ。作業ディレクトリは空の一時ディレクトリ。
- 本文の中の命令は実行指示として扱わない: 指示文で「データ」と明示し、乱数の印で囲む。そのうえで、従ってしまった結果も機械の検査で落とす。
- 受け取った後に確かめるもの: 応答したモデルが指定のものだけか(`stats.models`)、ツールを使っていないか、印の間に本文があるか。
  推敲稿と下書きで、コードブロック(3つ以上の ` か ~ の囲み)・字下げのコード・インラインコード・リンク(文字列が URL・コードのものは
  文字列と行き先の組)・リンク先・識別子(オプション名・パス・`#123`・ハッシュ・大文字の名前等)が同じか。数値と URL は**順序も**
  同じか(入れ替えも見つける。日本語に続く数「12件」も拾う)。日本語の文書では英字の語(`pytest` 等の道具の名前)も全部同じか。
  主張の語(合格・通過・完了・問題な・対応・passed・green・fixed 等)が増えていないか。否定の言い方の数が同じか。
  推敲稿に秘密・非公開の情報が足されていないか。大きく短くなった・長くなったときは「注意」として出す。
  **機械では見えないもの**: どの文に否定が付くかの入れ替え、言葉で書いた数(three → five)、英語の文書の中の普通の語の
  コマンドの言い換え、検査の語に無い言い方での主張の追加、数値の無い文の削除。作成側の確認で見る。
- **機械の検査は意味が同じことの保証ではない**。作成側エージェントが差分を読み、`confirm <ID> --reviewed-diff` を出すまで公開しない。
- 下書き・推敲稿・記録はリポジトリの外(状態ディレクトリの `publish/<ID>/`)。公開する本文の sha256 を記録し、公開のときに
  ファイルの中身と照らす(違えば止まる)。PR の本文・リリースノートには `<!-- agent-publish: {記録の ID とハッシュ} -->` を付ける。
  推敲稿を直したら `revise` で検証し直し、確認もやり直す。
- 保留(終了コード 3): 承認・設定が無い、認証切れ(41)、上限(429・RESOURCE_EXHAUSTED)、別モデルの応答、ツールの使用、形の違う応答、
  時間切れ、CLI が無い。**下書き・実装の作業は続けられる**。止まるのは管理対象の公開だけ。別のモデル・有料の API に切り替えない。
  再試行は `retry <ID>`(`AGENT_GEMINI_MAX_ATTEMPTS` 回まで)。

## 3. 検査する公開の経路・しない経路

| 経路 | Claude Code | Codex |
|---|---|---|
| `agent-publish pr-create` / `pr-edit` / `release` / `push`(確認済みの本文だけを使う) | 対応(本体) | 対応(本体) |
| `gh pr create`・`gh pr new` | PreToolUse で拒否 | AGENTS.md・skill の指示だけ(強制できない) |
| `gh pr edit` のタイトル・本文(`-t`・`--title`・`-b`・`--body`・`-F`・`--body-file`) | 拒否(ラベル等の変更は対象外) | 同上 |
| `gh release create`・`gh release edit` のノート・タイトル | 拒否 | 同上 |
| `gh api repos/<o>/<r>/pulls[/<n>]`・`.../releases[/<n>]` への POST・PATCH・PUT・フィールド付き | 拒否 | 同上 |
| `agent-gh pr-create` の直接の呼び出し | 拒否 | 同上 |
| README など(`AGENT_PUBLIC_DOC_GLOBS`、既定 `README* */README* CHANGELOG* RELEASE_NOTES*`)を変えたコミットの `git push` | push の範囲の公開文書が確認済みの本文と一致しなければ拒否 | 同上 |
| 名前で分かる MCP のツール(`create_pull_request`・`update_pull_request`・`create_release` 等) | 拒否 | — |
| `bash -c`・`env`・変数の前置き(`GH_REPO=… gh …`)・`( )`・`{ }`・`if`/`for` の中、gh の大域オプション(`-R`・`--repo`)、git の大域オプション(`--no-pager`・`--git-dir=`・`-c`・`-C`)の後の上のコマンド | 中を見て拒否 | — |
| `gh api graphql` の PR・リリースの mutation、`gh api` での `issues/<番号>` の本文・タイトルの書き込み(PR の本文も変わる) | 拒否 | — |
| 名前で分かる MCP のファイルの書き込み(`push_files`・`create_or_update_file` 等) | 拒否 | — |
| 引用符が閉じていない等で解析できないコマンドのうち、`gh`・`git … push`・`agent-gh`・`graphql` を含むもの | 拒否(安全側) | — |
| push で公開される文書の確認 | push する ref(refspec の左側、無ければ今のブランチ、`--all` は全部)の中身を、リモートのそのブランチ・`@{upstream}`・リモートの HEAD・main・master との merge-base からの変更で確かめる | 同上 |
| **非対応**: ブラウザ・GitHub の画面、`curl` 等での API の直接の呼び出し、別のスクリプト・Makefile・CI の中の公開、名前で分からない MCP、git の別名(alias)、Codex 全般(コマンドの前の環境変数を含む) | 止められない | 止められない |

**hook が見ない・見落とす経路(v2.3 で確かめたもの)**: `$(echo gh) pr create`・変数に入れたコマンド名(`g=gh; $g …`)、
push の範囲の途中のコミットだけにある未確認の README(最後に戻した場合。push の範囲の差分で確かめるため)、手で作り直した
`refs/remotes/origin/<branch>`(基点をずらせる)、`git push --tags`(タグは確かめない)。Contents API・git data API・graphql の
ファイルからの問い合わせ・名前で分かる MCP の書き込みは止める。キットの入っていないリポジトリの push は対象外。
配ったスキルの README(`.claude`・`.agents`・`.agent-kit` の中)は公開文書として扱わない。`.github/README.md` は対象。

**hook は最善の努力で、境界ではない**: シェルの書き方は無数にあり、hook は語の並びから推し量るだけ。v2.3 では独立レビューを
6回重ね、見つかった抜け道(cd・pushd の後の push、設定・環境変数で push の対象を変える形、env の前置き、MCP の名前など)を
1つずつ塞いだが、まだ抜け道は残り得る。GitHub 側の保護(ブランチの保護・必須の確認)と本人の確認が最後の守り。
確かめ方が安全側に倒れて誤って止める例: コマンドの始めのリポジトリの今のブランチに未確認の README があるとき、`cd` で別のリポジトリへ
移ってから refspec 無しで push する(`git -C <場所> push` か、refspec を書けば止まらない)。字下げのコードの中で箇条書きのような行から
始まる部分は、コードとして比べない(見落とし)。

**機械の検査の誤検出**: 否定の語(不・非・無・未 を含む)・問題の語・英字の語・数値の順序を厳しく比べるので、「非常に」の追加、
数の入った節の入れ替え、`github` → `GitHub` などの言い換えでも不採用になる。その場合は `retry` するか、作成側が直して `revise`。

**ツールでは防げないもの**: 記録(状態ディレクトリの `publish/`)と本人の手元の設定のファイルは、同じ利用者のプロセスなら書き換えられる。
確認済みの記録を偽って書けば、公開の検査は通ってしまう。これは事故・取り違えを防ぐ仕組みで、同じ環境の中の悪意あるプロセスへの防御ではない。
また、プロキシの環境変数(`HTTPS_PROXY` 等)は Gemini の通信に要るので渡す(プロキシの URL に認証情報が含まれていれば、それも子プロセスに渡る)。
Gemini CLI が内部で別の小さなモデルを使う作りなら、応答の `stats.models` に出て「別モデルの応答」として保留になる(本物では未確認)。

Stop hook には頼らない(公開は実行の前に止める)。hook は `CLAUDE_CODE_REMOTE=true` のとき(と試験の `AGENT_HOOKS_LOCAL=1`)だけ動く。
`AGENT_CLOUD_AUTO_ALLOW=0`(自動許可を止めた)でも、公開経路の検査はする。

## 4. 本人の作業(どれも未実施。順に)

1. 契約を決める: 有料の Gemini API キー、Vertex AI、Code Assist Standard・Enterprise のどれか。**新しい課金・認証になる**。
   無料の API キーは入力が改善に使われ、人が読むことがあるので、非公開の情報が混ざり得る文章には使わない。
2. Gemini CLI を確かめた版で入れる(例 `npm install -g @google/gemini-cli@0.66.0`)。クラウドでは Setup script に入れ、
   ネットワークの許可に Gemini API の宛先(`generativelanguage.googleapis.com`、Vertex なら `*-aiplatform.googleapis.com`)を足す。
3. 本人の手元の上書き `~/.config/agent-kit/publish.env` に書く(リポジトリに書かない。値は例):
   ```
   AGENT_GEMINI_SEND_APPROVED=1
   AGENT_GEMINI_MODEL=<本人が確かめた具体的なモデル名>
   AGENT_GEMINI_AUTH=api-key
   AGENT_GEMINI_KEY_ENV=GEMINI_API_KEY
   AGENT_GEMINI_HOME=~/.local/state/agent-kit/gemini-home
   AGENT_GEMINI_CLI_VERSION=0.66.0
   ```
   API キーの値は、クラウドの秘密の設定(環境変数)に入れる。ファイル・ログ・チャットに書かない。
4. 本物の CLI で一度だけ試す: `agent-publish polish --kind pr-title --file <公開してよい短い文>`。
   `agent-publish show <ID>` で `gemini.models_used`・`cli_version` を確かめる。設定の効き方(ツールが無いこと)は
   `~/.local/state/agent-kit/gemini-home/.gemini/settings.json` と応答の `stats.tools.totalCalls` で確かめる。

## 5. 切り戻し

- 推敲を止める: 本人の手元の上書き、またはリポジトリの `.agent-kit/config.env` に `AGENT_POLISH_REQUIRED=0`
  (hook の拒否も止まり、`agent-gh pr-create` 等が以前どおり使える)。
- 送信だけを止める: `AGENT_GEMINI_SEND_APPROVED=0`(公開は保留になる)。
- キットごと戻す: 前の版の install.py で入れ直す(docs/06)。記録(状態ディレクトリの `publish/`)は消さずに残る。
