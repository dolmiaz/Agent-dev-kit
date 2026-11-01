<!-- agent-kit:begin (この区間は agent-kit の install.py が管理。手で書き足す内容は区間の外へ) -->
## agent-kit: クラウド実行のルール

**この節は Claude Code on the web(環境変数 `CLAUDE_CODE_REMOTE=true`)で動いているときだけ適用する。**
ローカルの Claude Code ではこの節を使わない(hooks もクラウド判定で何もしない)。
このリポジトリの他の節にある個別の制限(例:「main へ変更しない」「マージしない」)は、常にこの節より優先する。

### 環境
- セッション開始時に SessionStart hook が依存を入れる(ログ: `bash .agent-kit/bin/setup-deps.sh check` の state 欄の場所)。
  ロックファイルを変えたときは `bash .agent-kit/bin/setup-deps.sh deps`。
- PostgreSQL / Redis は入っているが停止している。必要なら `bash .agent-kit/bin/services.sh start postgres|redis`。
- `gh pr` / `gh issue` はクラウドのプロキシで使えない(GraphQL)。`python3 .agent-kit/bin/agent-gh` か `gh api` の REST を使う。

### Issue と PR
- Issue を実装するときは skill `issue-task`、レビューするときは skill `pr-review` の手順に従う。
- ブランチは `feature/<Issue番号>-<短い名前>`(`agent-gh claim` が作る)。独立した変更ごとに分け、依存を PR 本文に書く。
- レビューは実装と別のセッションで、指定モデル(`claude-opus-5-5`)で行う。使えなければ本人に報告する。

### 対外文章(PR のタイトル・本文、リリースノート、README など外に出す文書)
- 公開する前に skill `publish-polish`(`python3 .agent-kit/bin/agent-publish`)で Gemini の推敲と差分の確認を済ませる。
  直接の `gh pr create`・`gh pr edit --body`・`gh release create`・`gh api` の書き込み・`agent-gh pr-create`・
  確認していない README を含む push は、PreToolUse の hook が止める(`AGENT_POLISH_REQUIRED=1` のとき)。
- 保留(Gemini が使えない)のときは公開だけを止め、作業は続ける。推敲はレビュー・承認・マージ判断の代わりにしない。

### 作業ログ(毎ターン)
- 実質的な作業をしたターンの終わりに skill `cloud-worklog` の手順で `python3 .agent-kit/bin/agent-log turn ...` を1回実行する。
- 書き忘れると Stop hook が1回だけ促す。送信に失敗しても作業は続ける。最終返答の末尾に `📝 作業ログ:` の1行。

### 権限
- 通常の開発コマンドはクラウドでだけ自動で許可される(PreToolUse hook `.agent-kit/claude/pretool.py` のホワイトリスト)。外れたものは選んだモードに任せる。
- 公開・マージ・デプロイ・クラウド CLI・鍵やシークレットの操作は確認を求める(ask)。
- force push・ブランチ削除・履歴の書き換え・秘密ファイルの読み取りはしない(deny)。

### コミット
- 作者は SessionStart で `Claude Code (cloud)` に設定してある(作者名・認証・署名は別物)。変えない。
<!-- agent-kit:end -->
