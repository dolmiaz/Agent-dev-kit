<!-- agent-kit:begin (この区間は agent-kit の install.py が管理。手で書き足す内容は区間の外へ) -->
## agent-kit: クラウド実行のルール

**この節は Codex Cloud で動いているとき(環境変数 `AGENT_RUNTIME=codex-cloud`)だけ適用する。**
ローカルの Codex ではこの節を使わない(記録はローカルの project-log)。
このリポジトリの他の節にある個別の制限(例:「main へ変更しない」「マージしない」)は、常にこの節より優先する。

### 環境
- 依存は環境の Install script(`bash .agent-kit/codex/setup.sh`)で入っている。ロックファイルが変わったとき・
  import エラーが出たときは `bash .agent-kit/bin/setup-deps.sh deps`(変化が無ければ数秒)。
- ブラウザの画面操作(computer use / browser use)はクラウドでは使えない。ヘッドレスのテストで確かめる。
- DB: `bash .agent-kit/bin/services.sh start postgres|redis`(入っていなければ「無い」と報告して進める)。

### Issue と PR
- Issue を実装するときは skill `issue-task`、レビューするときは skill `pr-review` の手順に従う。
- ブランチは `feature/<Issue番号>-<短い名前>`。確保は依頼側(dot または本人)が済ませてから渡す。
- 独立した変更ごとにブランチと PR を分け、依存する Issue / PR を PR 本文に書く。

### 対外文章(PR のタイトル・本文、リリースノート、README など外に出す文書)
- 公開する前に、必ず skill `publish-polish` の手順(`python3 .agent-kit/bin/agent-publish`)で Gemini の推敲と自分の差分の確認を済ませる。
  `gh pr create`・`gh pr edit --body`・`gh release create`・PR/リリースへの `gh api` の書き込み・`agent-gh pr-create` を直接使わない。
  README などの公開文書を変えたコミットは `agent-publish push` で push する。
- Gemini が使えず保留になったら、公開だけを止め、下書き・実装の作業は続け、保留の理由を最終返答に書く。別のモデル・有料の API に切り替えない。
- 推敲は技術レビュー・承認・マージ判断の代わりにしない。

### 作業ログ(毎ターン)
- 実質的な作業をしたターンの終わりに skill `cloud-worklog` の手順で `python3 .agent-kit/bin/agent-log turn ...` を1回実行する。
- 送信に失敗しても作業は続ける。最終返答の末尾に `📝 作業ログ:` の1行を付ける。

### してよいこと・しないこと
- してよい: 依存の取得、ビルド、テスト、lint、作業ブランチでのコミット。
- しない(必要なら最終返答で提案だけ): 公開・デプロイ・main への変更・マージ・リリース・課金が発生する操作・
  データやブランチ・タグの削除・新しい鍵やトークンや権限の作成・履歴の書き換え。
- 秘密の値を表示・記録・コミットしない。許可されたドメイン以外へ接続しない。プロキシや許可リストを回避しない。

### コミット
- 作者は Install script で `Codex (cloud)` に設定してある(作者名・認証・署名は別物)。変えない。
<!-- agent-kit:end -->
