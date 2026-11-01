---
name: issue-task
description: GitHub の Issue を拾って実装するときの手順(担当確保・feature ブランチ・draft PR・依存の確認・テスト・レビュー依頼まで)。「Issue をやって」「#123 を実装」「次の Issue を拾って」で使う。レビューやマージ判定には使わない(それは pr-review)。
---

# issue-task — Issue から PR まで(実装担当)

複数のエージェント(dot・Claude・Codex)が同じ Issue に二重に着手しないよう、確保してから始める。
**マージはしない。** マージ可否の判定は別セッションのレビュー担当(pr-review)が行い、マージは本人が行う。
リポジトリの AGENTS.md / CLAUDE.md に個別の制限(例:「main へ変更しない・マージしない」)があれば、そちらが常に優先。

## 1. 着手できる Issue を選ぶ

```bash
python3 .agent-kit/bin/agent-gh eligible
```
- 条件: `agent:ready` が付き、確保されておらず(`refs/agent-claims/issue-<番号>`)、`agent:blocked` が無く、
  本文に「期待する動作」「受入条件」があり、「依存」に書かれた Issue が完了・PR がマージ済み。
- 前提 PR が未マージのものは着手しない。例外は `agent:stack-ok` が付いた Issue だけ(前提 PR のブランチの上に積む)。
- 指定された Issue が条件を満たさないときは、満たさない理由を返して止まる(勝手に条件を緩めない)。

## 2. 担当を確保する(ここで失敗したら着手しない)

```bash
python3 .agent-kit/bin/agent-gh claim <番号> --agent <claude|codex|dot> --session "<セッションURL>"
```
- 確保の識別子は Issue 番号で固定(`refs/agent-claims/issue-<番号>`)。名前(slug)を変えても、同じ Issue を2人が確保することはない。
  成功したら `feature/<番号>-<短い英語の名前>` ブランチを API で作り、その名前を出力する。
- 終了コード 3 = 他が確保済み・既存の PR / ブランチがある。**着手しない**(別の Issue を選ぶか本人に伝える)。
- 自分の確保の続きは `--resume`(途中で止まってブランチが無ければ作り直す)。止まっている他の確保は、
  `agent-gh claim-status <番号>` で確かめ、動きが無い(既定 24 時間)ときだけ `--takeover`。既存 PR を引き取るときは `--adopt <ブランチ>`。
- やめるときは `agent-gh release <番号> --agent <名前>`(ブランチは消さない)。
- Codex Cloud には GitHub のトークンが無いので、確保は依頼側(dot または本人)が Mac 等で行い、
  ブランチ名を指示に書いて渡す。Codex はそのブランチで作業する。

## 3. 実装

- 独立した変更ごとにブランチと PR を分ける。1つの PR に別の目的の変更を混ぜない。
- 読みやすい名前・責務の分離・保守性。巨大な関数や責務の混在を避け、必要のない抽象化も足さない。
- 受入条件に対応するテスト、エラー処理、必要なドキュメントをそろえる。
- 依存を変えたら `bash .agent-kit/bin/setup-deps.sh deps`。
- 区切りごとにコミットし、`git push -u origin feature/...`(force push しない)。

## 4. draft PR を作る

タイトルと本文は、skill `publish-polish` の手順で推敲・確認してから作る(`AGENT_POLISH_REQUIRED=1` のとき。既定):
```bash
python3 .agent-kit/bin/agent-publish polish --kind pr-title --file /tmp/pr-title.txt
python3 .agent-kit/bin/agent-publish polish --kind pr-body --file /tmp/pr-body.md --facts /tmp/facts.md
# 差分を読んで確かめてから、それぞれ confirm <ID> --reviewed-diff
python3 .agent-kit/bin/agent-publish pr-create --issue <番号> --branch feature/... --title-id <ID> --body-id <ID> \
  --agent claude --session "<セッションURL>"
```
- Gemini が使えず保留(終了コード 3)になったら、PR の作成だけを止め、実装・コミット・push(README を含まないもの)は続ける。
  保留の理由を最終返答に書く。
- 推敲しない設定(`AGENT_POLISH_REQUIRED=0`)のリポジトリでは、これまでどおり
  `python3 .agent-kit/bin/agent-gh pr-create --issue <番号> --branch feature/... --title "..." --body-file /tmp/pr-body.md`。
- 本文には、変更概要・テスト結果・確認方法・未検証事項を書く(`.github/pull_request_template.md` の見出し)。
- draft が使えないプラン(Free / Pro の private リポジトリ)では、自動で通常 PR + `agent:wip` になる。
- Claude Code on the web で `gh pr` は使えない(GraphQL)。このツールか画面の「Create PR」を使う。

## 5. レビュー依頼

- 実装が終わったら draft を外し(または `agent:wip` を外し)、`agent:needs-review` を付ける。
- レビューは**別セッション**の pr-review が行う。自分の実装を自分でレビューして「マージ可」と書かない。
- 指摘を直したら、最新コミットで再レビューを依頼する(古いレビューは無効)。

## 6. 記録

ターンの終わりに cloud-worklog で記録する(PR の URL を `--pr` に入れる)。
