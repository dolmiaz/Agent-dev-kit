# Issue から実装・レビュー・マージ可否までの分担

## 役割

| 担当 | すること | しないこと |
|---|---|---|
| **GrokBot** | 問題や改善案を Issue にする(`source:grokbot` を付ける) | `agent:ready` を付けない。マージ可否を判断しない |
| **本人** | Issue を確認して `agent:ready` を付ける。最終的なマージを行う | — |
| **dot**(既存の定期確認) | `agent:ready` の Issue を見つけ、担当を確保して Claude / Codex へ引き継ぐ。Codex Cloud に頼むときは確保とブランチ名の指示を dot が行う | マージ。将来機能(Duet 等)を前提にしない |
| **Claude / Codex(実装)** | skill `issue-task`: 確保 → 実装 → テスト → draft PR → レビュー依頼 | 自分の実装のレビュー判定、マージ |
| **Opus 5.5(レビュー、別セッション)** | skill `pr-review`: SHA を固定してレビューし、判定を記録。`merge-gate` で技術判定 | マージ操作。指定モデルでないときは別モデルで代行せず報告 |

リポジトリ固有の指示(例:「main へ変更しない・マージしない」)は、キットが入れる AGENTS.md / CLAUDE.md の区間より常に優先する(区間の冒頭に明記)。

## ラベル

| ラベル | 意味 | 付ける人 |
|---|---|---|
| `source:grokbot` | GrokBot が作った Issue | GrokBot |
| `agent:ready` | 着手してよい(受入条件まで書かれている) | 本人 |
| `agent:blocked` | 依存待ち・判断待ち | 誰でも |
| `agent:stack-ok` | 前提 PR が未マージでも、その上に積んで着手してよい | 本人 |
| `agent:claimed` | 担当確保済み(表示用。判定は `refs/agent-claims/issue-<番号>`) | ツール |
| `agent:wip` | 実装中(draft PR が使えないプラン用) | ツール |
| `agent:needs-review` | 実装完了、レビュー待ち | 実装担当 |
| `agent:changes-requested` | 修正が必要 | レビュー担当 |
| `agent:review-passed` | 最新コミットのレビューと必須 CI を通過(技術的にマージ可) | レビュー担当 |

作成は `python3 .agent-kit/bin/agent-gh labels --apply`(リポジトリへの書き込みなので本人の承認後)。

## Issue に書くこと(`.github/ISSUE_TEMPLATE/agent-task.yml`)

問題 / 期待する動作 / **受入条件・確認方法** / **依存**(Issue・PR)/ 背景(任意。Vault の文脈の要約)/ 範囲外(任意)。
「期待する動作」と「受入条件」が無い Issue は、`agent-gh eligible` が着手不可と判定する。

## 着手条件と二重着手の防止

`agent-gh eligible` が着手可とするのは次をすべて満たす Issue:
- `agent:ready` があり、確保されていない(下の確保の ref で判定。ラベルは表示用)、`agent:blocked` が無い
- 期待する動作・受入条件がある
- 依存の Issue が完了(closed かつ completed)、依存の PR がマージ済み
  - 例外: `agent:stack-ok` があり、依存が同じリポジトリの open な PR なら、その PR のブランチを基点にして着手(積み上げ)

### 担当の確保(Issue 単位)

識別子は **リポジトリ + Issue 番号** で固定: ref `refs/agent-claims/issue-<番号>`(`AGENT_CLAIM_REF_PREFIX`)。
実装ブランチ(`feature/<番号>-<slug>`)とは別物なので、slug を変えても同じ Issue を2人が確保することはない。
ブランチではないので、push を契機にした CI は動かない。

| 操作 | 仕組み | 同時に起きたら |
|---|---|---|
| 最初の確保 | ref を**作成**(既にあれば GitHub が 409/422) | 1つだけ成功 |
| 外した後の再確保・引き継ぎ・外す | 今の状態の commit を親にした commit へ ref を **fast-forward で更新**(force しない) | 後の方は fast-forward にならず 422 → 1つだけ成功 |

確保の前に、この Issue の作業が既にあるか(open な PR で head が `feature/<番号>-` か本文に `Closes #<番号>`、
`feature/<番号>-*` ブランチ)を調べ、あれば確保しない(`--adopt <ブランチ>` で引き取れる)。

| 状況 | 使うもの |
|---|---|
| 着手 | `agent-gh claim <番号> --agent <名前> [--slug S] [--session URL]` |
| 自分の確保の続き(途中で止まった・ブランチを作れなかった) | `claim <番号> --agent <同じ名前> --resume`。**同じセッション**ならすぐ再開できる。**別のセッション**からは、確保・ブランチ・PR に `AGENT_CLAIM_LEASE_MINUTES`(既定60分)動きが無いときだけ(稼働中の横取りを防ぐ)。再開のときも blocked と依存を確かめ直す |
| 稼働中であることを示す(リース) | 確保したセッションの hook(PreToolUse)が、`AGENT_CLAIM_LEASE_MINUTES` の 1/3 ごとに裏で `claim-renew --mine` を呼び、確保の時刻を更新する(比較して交換。確保したのと同じセッションからだけ)。間隔は、保持中の確保の記録に書いたリース(一番短いもの)の 1/3(手元の設定を後で変えても、確保のリースに合わせる)。成功したときだけ次の間隔を数え直し、失敗したら `AGENT_CLAIM_RENEW_RETRY_SECONDS`(既定は間隔の 1/4 か 60 秒の短い方)の後で再試行する。長い作業でコミットが無くても、別セッションからの --resume で横取りされない。止めるなら `AGENT_CLAIM_RENEW=0`。手で呼ぶなら `claim-renew <番号>` |
| 別のセッションへ明示的に渡す | 元のセッションで `release <番号> --agent <名前> --handoff`(**確保したセッションからだけ**。同じ agent 名の別のセッションからの release・--handoff・--force は、確保が止まっていなければ断る。hook の証拠が無い実行(本人の端末)は --force のときだけ外せる)。リースの長さと止まったとみなす時間は、確保した側が確保の記録に書いた値を使う(再開・引き継ぐ側の環境変数では短くできない)(確保は残り、同じ agent 名の別セッションが --resume で続けられる。他の agent には渡らない) |
| 止まった他の確保を引き継ぐ | `claim-status <番号>` で確かめ、確保・ブランチの最終コミット・PR の更新がどれも `AGENT_CLAIM_STALE_HOURS`(既定24時間)より古ければ `claim --takeover` |
| やめる | `release <番号> --agent <名前>`(ブランチは消さない。本人以外が外すときは `--force`) |

- Codex Cloud の VM には GitHub のトークンを渡さないので、Codex に頼むときは dot か本人が先に確保し、ブランチ名を指示に書く。
- ラベル `agent:claimed` / `agent:ready` とコメントは表示用。判定は ref で行う(ラベルが戻っても二重に着手しない)。
- **未検証**: GitHub の API で `refs/heads` 以外(`refs/agent-claims/...`)の ref を作れること、Claude のプロキシがそれを通すこと。
  通らなければ `AGENT_CLAIM_REF_PREFIX=refs/heads/agent-claims` にする(その場合はブランチになるので、push で動く CI の対象から外す)。

## ブランチと PR

- ブランチ: `feature/<Issue番号>-<短い英語の名前>`(課題名。番号で一意にする)。
- 独立した変更ごとにブランチと draft PR を分ける。PR 本文に `Closes #N` と「依存」を書く(`agent-gh pr-create` が書く)。
- draft PR は GitHub Free / Pro の非公開リポジトリでは使えない [G8]。その場合は通常 PR + タイトル `[WIP]` + `agent:wip` にする(ツールが自動で切り替え)。

## レビューとマージ可否(技術判定)

### レビューの手順(SHA を固定する)

1. `agent-gh review-start <PR>`: その時点の PR の HEAD を「レビューする SHA」として記録し、出力する。この SHA を checkout して読む。
2. `agent-gh review-post <PR> --sha <SHA> ...`: 固定した SHA を必須にする。投稿の直前に PR の HEAD を取り直し、
   変わっていれば**投稿しない**(終了コード 6、「保留・再レビュー」)。「開始 → 追加 push → 古い差分のレビュー投稿」は通らない。

### モデルと独立性(自己申告で合格にしない)

- Claude Code on the web では、キットの PreToolUse hook が `agent-gh` のコマンドを見たときに、出したセッションの
  `session_id` と transcript の場所を記録する(状態ディレクトリの `evidence/`。1回の hook = 1回のコマンドで使い切り)。
- review-post は、そのセッション全体(サブエージェントの transcript を含む)で応答したモデルを読む。すべて `claude-opus-5-5` なら
  「確認済み」、別のモデルが混ざれば**記録を拒否**(終了コード 4、本人に報告)、読めない・記録が無い・自己申告だけ・
  サブエージェントの中から実行した、なら「未確認」。**レビューのセッションは最初から Opus 5.5 で始める**。
- review-start は同じセッション・同じ SHA の2回目でも最初の記録を使う。証拠つきで開始したレビューは、証拠の無い実行
  (hook を通らないスクリプト等)からは投稿できない。
- 独立性: そのセッションでこの Issue の claim / pr-create / PR のブランチへの push をしていないこと、実装側の ID
  (PR 本文の `agent-impl` の hook / remote セッション ID、コミットの `Claude-Session:`)と一致しないこと。
  実装側が分からないときは、PR のコミットがすべて Codex の作者名で、レビューが Claude のセッションなら独立とみなす。それ以外は「未確認」。
- **開始時の記録(`sessions/<id>.json`)だけでは確認にしない**(途中で `/model` を変えられるため)。
- 限界: transcript の書式は Claude Code の公開 API ではない。読めなくなったら「未確認」になる(確認できたふりはしない)。
  証拠は同じ VM 内のエージェントなら書き換えられる(誤判定を防ぐための仕組みで、改ざん対策ではない)。
- 本人による確認(任意): 本人がレビューのセッションを開いてモデル(画面の表示)と、実装と別のセッションであることを確かめたら、
  **本人の端末で**、本人の SSH 鍵で署名して記録する:
  `agent-gh human-confirm <PR> --sha <SHA> --items model,session --key <鍵> --principal <名前>`
  (`--key` は秘密鍵か、ssh-agent・1Password の SSH エージェントに入っている鍵の公開鍵ファイル。署名は `ssh-keygen -Y sign`。OpenSSH 8.0 以上の `ssh-keygen -Y sign/verify` が必要)。
  merge-gate は、**ベースブランチ**の `.agent-kit/human-confirm-allowed-signers`(公開鍵だけ。`<名前> namespaces="agent-kit-human-confirm" <公開鍵>`)
  で署名を確かめたものだけを使う。ファイルが無ければ本人確認は使わない。秘密鍵はクラウドに持ち込まない
  (クラウドのエージェントは本人の GitHub の権限でコメントできるので、投稿者では本人を区別できないため署名にした)。

### 判定に使う設定は PR が変えられない場所から読む

merge-gate と review-post は、指定モデル(`AGENT_REVIEW_MODEL`)・CI なしの許可(`AGENT_ALLOW_NO_CI`)・
受け付けるレビューの投稿者(`AGENT_REVIEWER_LOGINS`)・`ci-policy.json`・`human-confirm-allowed-signers` を、
**ベースブランチのファイルを GitHub の API で読む**。手元(PR のブランチの checkout)の変更や環境変数は効かない。
PR がこれらのファイルを変えていれば注記を出す。読めない(1MB 超・取得失敗)ときは合格にしない。

### 記録の偽造について(限界)

レビューや本人確認の記録は PR のコメントの目印。編集されたコメント、リポジトリの所有者・メンバー・共同作業者以外
(公開リポジトリの第三者)のコメント、形の正しくない目印は使わない。
それでも、同じ GitHub の権限を共有する構成では、エージェントが新しいコメントで目印を書くことはツールでは防げない。
減らす方法: レビュー担当を別のアカウント・アプリにして、ベースの `AGENT_REVIEWER_LOGINS` で限定する。最終的な防御は、
GitHub の保護(PR 必須・必須 CI)と、正式な承認とマージを人が行うこと。

### merge-gate が「マージ可」とする条件(すべて)

| 条件 | 内容 |
|---|---|
| 最新コミットのレビュー | PR の head の SHA についてのレビュー記録がある(古いコミットのレビューは使わない) |
| モデル | 記録が「確認済み」で `claude-opus-5-5`(ベースの設定)、または署名つきの本人確認(model)。セッション全体(サブエージェントを含む)の応答がすべて指定モデルのとき確認済み |
| 別セッション | 記録が「確認済み」、または署名つきの本人確認(session) |
| 判定 | `pass`、未解決の重大な指摘 0、マージ前に必要な未検証事項なし |
| 必須 CI | ルールセット・ブランチ保護・ベースの `.agent-kit/ci-policy.json` の和集合(ci-policy で GitHub 側の必須は外せない)の、すべての必須 check が同じ SHA で成功。app を指定した必須 check はその app の check run だけで満たす。同名の check が再実行・別 suite で複数あれば、どれか1つでも未完了・失敗なら不可。未起動(結果が無い)・実行中・取得失敗・ページ未取得は不可。skipped / neutral は ci-policy の項目ごとに `allow` に書いたときだけ成功扱い。必須でない check の失敗も既定で不可(`block_on_other_failures`) |
| 必須 CI が決まっていない | 不可(成功している check があっても、それが必要なテストかは判断しない)。CI の無いリポジトリは `AGENT_ALLOW_NO_CI=1` |
| 依存 | PR 本文の依存がマージ済み・完了 |
| 状態 | open、draft / WIP でない、mergeable が true(null・unknown は再取得し、それでも不明なら不可) |
| 判定中の更新 | 判定の始めにマージ先(base ref)・その先頭の SHA・head・merge-base を固定し、条件(ベースの設定)はその SHA の時点で読む。最後に取り直し、どれかが変わっていたら最初から判定し直す(3回まで。それでも動くなら不可) |
| レビューした差分 | レビューの記録のマージ先と merge-base が、今のものと同じ(ベースが動いて差分が変わったら、レビューし直す)。v2.2 以前のレビューの記録には無いので、レビューし直しが要る |

`.agent-kit/ci-policy.json` の例(`agent-gh ci-policy` で GitHub 側の必須 check を表示できる):
```json
{"required": [{"name": "test"}, {"name": "e2e", "allow": ["success", "skipped"]}],
 "use_platform_required": true, "block_on_other_failures": true}
```

- 修正後は最新コミットで必ず再レビューし、そのコミットの CI を確認する。
- **マージ可否の判定と、正式な承認・実際のマージ操作は分ける**。キットのツールはマージ API を呼ばない(テストで確認)。Claude の権限判定でも `gh pr merge` や merge API は ask。
- この仕組みを入れても、全リポジトリで自動承認・自動マージを始めるわけではない。マージの実行者と権限は別途決める。

## main の保護(D)

| プラン・公開範囲 | ルールセット / ブランチ保護 | draft PR |
|---|---|---|
| Free・公開 | ○ | ○ |
| Free・非公開 | **×** | × |
| Pro・公開 / 非公開 | ○ / ○ | ○ / **×** |
| Team・Enterprise | ○ | ○ |

出典 [G5][G8]。対象リポジトリで適用する前に、プラン・公開範囲・既存のルール(`GET /repos/{o}/{r}/rulesets`、`GET /repos/{o}/{r}/rules/branches/main`、旧来の保護)を読み、無関係な設定を壊さないよう**新しいルールセットを1つ足す**形にする(既存を書き換えない)。内容: 対象 main、`deletion` 禁止、`non_fast_forward`(force push)禁止、`pull_request` 必須(承認0件でも可)。署名必須は入れない(docs/04 の A)。
Free の非公開リポジトリでは保護が使えないので、AGENTS.md / CLAUDE.md の指示と Claude のクラウド権限判定(main への push は ask)だけが防御になる。この差は適用時に報告する。
