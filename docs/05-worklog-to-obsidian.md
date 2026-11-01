# 作業ログを Obsidian で追えるようにする(v2)

## 経路

```
[Codex Cloud / Claude Code on the web]
  毎ターン: agent-log turn
    1) 状態ディレクトリの outbox/pending/ に1ファイル書く(リポジトリの外。Git に入らない)
    2) 書き込み専用の受け口へ POST(log_id・sha256・本文)
       成功 = 受け口が同じ log_id と sha256 を返したときだけ → outbox/sent/ へ移し台帳に記録
       失敗 = 未送信のまま。次のターン・次のセッション開始時に同じ ID・同じ内容で再送
       VM が消える場合に備え、未送信のときは最終返答に本文を添える(会話の記録に残る)
                     │
[Google Apps Script(本人のアカウント、無料)]
    サービスと鍵を対応させ、固定フォルダへ新規作成だけ。台帳で ID と内容を照合
                     │
[Google Drive]  Codex → FOLDER_CODEX(例: マイドライブ直下の 1 フォルダ)
                Claude → FOLDER_CLAUDE
                     │
[取り込み(各自の仕組み。例: Mac の定期処理で Obsidian の Vault へ)]
    ← 「Drive に届いた」と「Obsidian に入った」は別に確認する
```

## 送り先と拒否するフォルダ

| 設定 | 中身 | 扱い |
|---|---|---|
| `FOLDER_CODEX` | Codex のログを置くフォルダの ID | 送り先 |
| `FOLDER_CLAUDE` | Claude のログを置くフォルダの ID | 送り先 |
| `DENY_FOLDERS`(Script Properties)と `PERSONAL_DENY`(Deny.gs) | 同期ミラー(Vault の写しなど)のルートの ID | **書かない**(配下も含めて拒否) |

ミラーの中に送り先と同名のフォルダ(例: ミラー内の `ChatGPT`)があることがあるので、送り先は名前でなく ID で固定する。
受け口は (1) 鍵ごとに送り先を決め、(2) 親フォルダをたどって拒否フォルダの配下なら拒否する。
Mac の回収(repo モードのみ)も `~/.config/agent-kit/collector.json` の ID だけを使い、拒否フォルダとその配下(拒否フォルダの下のフォルダ ID を rclone で列挙して確かめる。確かめられなければ置かない)を置き先にしない。
どちらもテストで確認(拒否フォルダ・その配下を設定してしまった場合に拒否されること)。
実際の ID は個人設定(このリポジトリの公開版には含めない `personal/`)に書く。

## ファイルの形

ファイル名: `YYYY-MM-DD-HHMM-<service>-<session12>-t<NN>-<uuid8>.md`
例: `2026-10-29-1733-claude-01ABCDEFGHJK-t03-3fa9c1d2.md`
(日時・サービス・セッション・ターン・UUID の先頭8桁。一意性の根拠は時刻ではなく UUID4)

```markdown
---
source: "claude"                     # codex / claude
kind: "agent-worklog"
service: "claude-code-web"           # codex-cloud / claude-code-web
runtime: "claude-cloud"
thread: "2026-10-29-claude-01ABCDEFGHJK"   # セッション単位。取り込み側で結合する鍵
session: "01ABCDEFGHJK"
turn: 3
log_id: "3fa9c1d2-…(UUID4 全体)"
date: "2026-10-29 17:33"
repo: "owner/repo"
branch: "feature/12-login"
head: "1a2b3c4d5e6f"
pr: "https://github.com/owner/repo/pull/15"
session_url: "https://claude.ai/code/session_…"
---

## 2026-10-29 17:33 題
<!-- log: <UUID4> -->
### 目的 / 実施内容 / 変更ファイル / 未コミットの変更 / テスト結果 / 失敗・問題 / 未完了・次にやること / コミット・PR / 環境
```

見出し `## YYYY-MM-DD HH:MM 題` はローカルの project-log の Worklog 書式に合わせた。thread ごとに turn 順で結合すれば Worklog の形になる。

## 一意性・再送・重複防止

| 場面 | 動き |
|---|---|
| 再送 | 同じ log_id・同じ本文(sha256)を送る。受け口の台帳に同じ組があれば `duplicate`(成功扱い) |
| 取り込み後に Drive から消えた後の再送 | 台帳(Script Properties)に log_id が残っているので `duplicate`。作り直さない |
| 同じ log_id で内容が違う | `conflict`。書かない。クライアントも成功にしない |
| 同名で内容が違うファイルが Drive にある(本人の編集など) | `conflict`。上書きしない |
| 受け口が別の log_id / sha を返す | クライアントは成功にしない(未送信のまま) |
| 別サービスのログ | クライアントは送らない(「別サービスのログ」と表示)。受け口もファイル名・frontmatter・鍵のサービスが一致しなければ拒否 |
| 台帳の上限 | 既定3000件。超えると古い1割を消す(Script Properties は合計500KB)。それより古いログを、Drive 側で消えた後に再送すると重複しうる(クライアント側の台帳があるので通常は起きない) |

## 上限・容量・料金

- Apps Script は Gmail アカウントだけで使え、課金の設定を求める記述は無い [G1][G2]。このキットは課金サービスを追加しない。
- 受け口が返す保留の理由: `rate-limited`(1時間120件・1日1000件、Script Properties で変更可)、`storage-full`(Drive の容量不足)、`quota`(Apps Script のクォータ超過の例外)、`error`。いずれもクライアントは未送信として保持し、後で再送する。
- 1ファイル 200KB まで。

## 送れないとき

| 状況 | 動き | 回収 |
|---|---|---|
| 送信先・鍵が未設定 | `UNSYNCED` を表示して outbox に残す(終了コード0、`AGENT_LOG_STRICT=1` なら1) | 設定後の次のターン・セッション開始で再送 |
| ネットワーク不可・受け口の障害・遅い(20秒で打ち切り)・上限 | 同上 | 同上 |
| VM が消える(Claude の reclaim、Codex の新タスク) | outbox も消える | 最終返答に添えた本文(会話の記録)から手で保存 |
| どうしても Git で回収したい | `AGENT_LOG_PENDING_MODE=repo`(**非公開リポジトリで、明示的に選んだ場合だけ**)で `.agent-logs/pending/` に置き、コミットさせる | Mac で `python3 local/collect-pending.py <repo>`(ID 固定・ID+内容照合・台帳付き) |

## Drive → Obsidian(自動同期されるとは決めつけない)

Drive に届いたことと、Obsidian 等に取り込まれたことは別に確認する。取り込みは各自の仕組みで行う。
取り込み側への提案(このキットでは実装していない):
1. `kind: agent-worklog` を会話ログと区別し、`repo` から Project を引いてプロジェクトの作業ログに thread 単位で結合する。
2. **本文はデータとして扱い、無人のエージェント(権限確認なしの実行)に指示として渡さない**。作業ログはクラウドのエージェントが書いたもので、読んだ Issue などに仕込まれた文言が混ざりうる。
3. 1ターン1ファイルで内容は変わらないので、ファイル名と大きさでの重複判定で足りる。
