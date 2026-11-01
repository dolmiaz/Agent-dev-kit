---
name: publish-polish
description: 対外文章(PR のタイトル・本文、リリースノート、README など外に出す文書)を公開する前に、Gemini で推敲し、差分を確かめてから公開するときの手順。PR を作る・PR の本文やタイトルを変える・リリースを作る・README を含む push をするときに必ず使う。技術レビュー・承認・マージ判断には使わない。
---

# publish-polish — 対外文章を推敲してから公開する

対象: PR のタイトル・本文、リリースノート、README など外へ出す文書(`AGENT_PUBLIC_DOC_GLOBS`)。
コミットメッセージ・Issue のコメント・作業ログ・コードは対象外。
**推敲は、技術レビュー・Opus のレビュー・PR の承認・マージ判断の代わりにならない**(それぞれ別の工程)。

## 1. 下書きを書く

- 本文はリポジトリの外の一時ファイルに書く(例: `/tmp/pr-body.md`)。README はリポジトリの中のファイルのまま。
- Gemini に渡す事実メモ(`--facts`)は、文章を理解するのに要る最小限だけ(例: 「Issue #12 の受入条件を満たす」)。
  リポジトリの中身・会話・秘密・非公開の情報を書かない。

## 2. 推敲する

```bash
python3 .agent-kit/bin/agent-publish polish --kind pr-title --file /tmp/pr-title.txt
python3 .agent-kit/bin/agent-publish polish --kind pr-body --file /tmp/pr-body.md --facts /tmp/facts.md
python3 .agent-kit/bin/agent-publish polish --kind readme --file README.md --path README.md
```
- 記録の ID(16桁)と、下書きとの差分が出る。
- 終了コード 4(不採用): 秘密・非公開の情報らしいもの、または数値・URL・コード・識別子・主張の語が変わった。
  下書きを直して polish し直すか、`agent-publish retry <ID>`。
- 終了コード 3(保留): Gemini が使えない(承認・設定が無い・認証切れ・上限・別モデルの応答・失敗)。
  **下書きの作業は続けてよい**。公開(PR・リリース・README の push)だけが止まる。別のモデル・有料の API に切り替えない。
  本人に保留の理由を伝える。

## 3. 差分を自分で確かめる(機械の検査は意味が同じことの保証ではない)

- 数値・機能の有無・試験の結果・約束・否定(「ない」「未」)が下書きと同じ意味か、差分を読んで確かめる。
- 推敲稿を直すときは、直したファイルで `agent-publish revise <ID> --file <ファイル>`(検証し直し、確認もやり直す)。
- 確かめたら: `python3 .agent-kit/bin/agent-publish confirm <ID> --reviewed-diff --note "確かめたこと"`

## 4. 公開する(確認済みの本文だけが使われる。確認のあとで本文を変えたら止まる)

```bash
python3 .agent-kit/bin/agent-publish pr-create --issue <番号> --branch feature/... --title-id <ID> --body-id <ID> \
  --agent <claude|codex> --session "<セッションURL>"
python3 .agent-kit/bin/agent-publish pr-edit <PR番号> --body-id <ID>
python3 .agent-kit/bin/agent-publish release <タグ> --notes-id <ID>          # 既定は下書きのリリース
python3 .agent-kit/bin/agent-publish apply <READMEのID> && git commit -am "README" && python3 .agent-kit/bin/agent-publish push origin feature/...
```
- `gh pr create`・`gh pr edit --body`・`gh release create`・`gh api` で PR・リリースを直接作る/変える・
  `agent-gh pr-create` の直接の呼び出しはしない(Claude Code では hook が止める。Codex では止める仕組みが無いので、この手順を守る)。
- 公開そのものの可否(main への変更・マージ・正式なリリース)は、これまでどおり本人が決める。
