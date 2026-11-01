# 作者名・認証・署名・秘密の分け方

## 3つは別物

| | 何を表すか | 人間(本人) | Codex(ローカル) | Codex Cloud | Claude Code on the web |
|---|---|---|---|---|---|
| **作者名**(commit の author/committer) | 誰が書いたかの「表示」。誰でも名乗れる | 本人 | `Codex (<model>) <codex-<model>@<個人のドメイン>>`(Mac の個人スキル codex-git-commit) | `Codex (cloud) <codex-cloud@example.com>`(例。個人の値は --profile) | `Claude Code (cloud) <claude-cloud@example.com>`(例) + `Claude-Session:` トレーラー |
| **認証**(誰として push・API 操作できるか) | GitHub が許可する主体 | 本人の SSH / 1Password / YubiKey | 本人の gh 等 | ChatGPT の GitHub 接続(プラットフォームが保持) | Claude GitHub App とプロキシ(プラットフォームが保持) |
| **署名**(暗号的な証明) | 鍵の持ち主が作ったことの証明 | 本人の鍵 | Mac 内の専用鍵 `~/.codex/signing/codex-ed25519` | **しない** | **しない** |

- 作者名は区別のための表示で、なりすましの防止にはならない。誰が push できたかは認証、改ざんされていないかは署名で判断する。
- クラウドの認証はプラットフォームが持ち、こちらからトークンを渡さない(Claude は `GH_TOKEN` を設定しない、Codex は GitHub 接続のみ)。
- **人間用の 1Password 鍵・YubiKey(5C NFC ×2)はクラウドへ一切持ち込まない。** 無人処理がタッチや 1Password の解除を待つ経路は作らない。YubiKey は本人の認証と復旧専用。
- ローカル Codex の専用署名鍵も Mac の外へ出さない。

## 署名必須(Require signed commits)の採否 — 別に決める

公式の記述(docs.github.com、2026-10-29 確認 [G6][G7]):
- 署名必須のブランチには、署名済みで検証できるコミットしか入れられない。
- **Squash マージ**: GitHub が最終コミットに署名するが、head ブランチに未署名のコミットがあるとブロックされうる。対処は head のコミットを署名し直すか、bypass 権限のある人がマージ。
- **Rebase マージ**: head のコミットが署名検証なしで載る(GitHub は代わりに署名できない)。署名必須とは両立しないと読める(「ブロックされる」と直接書いた文言は未確認)。
- **マージコミット**: Web UI のマージコミットに GitHub が署名するかの明記は確認できなかった。
- 非公式報告: 署名必須のリポジトリで Codex Web が空のブランチを作る [C17]。

したがって「main だけ署名必須にすれば、未署名のエージェントのブランチも Web で必ずマージできる」とは**言えない**。

| 選択肢 | 内容 | 長所 | 短所 |
|---|---|---|---|
| **A. 署名必須にしない(推奨)** | main は PR 必須・force push 禁止・削除禁止だけ | エージェントの未署名コミットでもマージできる。鍵をクラウドに置かない | 誰が書いたかは作者名・PR・セッション URL で追う(暗号的な証明ではない) |
| B. main を署名必須にし、マージは bypass 権限のある本人が行う | 未署名を含む PR は本人が bypass でマージ | main に署名の方針を残せる | 毎回本人の操作が要る。bypass の運用ルールが必要 |
| C. エージェントごとに専用の署名鍵をクラウドに置く | サービスごとに別の鍵を作り GitHub に登録 | 署名で区別できる | **新しい永続的な認証情報**。Claude は環境変数(エージェントから読める)にしか置けず、Codex は network secret の形式が合わない。鍵の漏えい時の影響が大きい |

このキットは A を前提に作ってある。B / C にする場合は改めて確認してから設定する。

## 秘密情報の扱い

| 種類 | 置き場所 | 理由 |
|---|---|---|
| 外部 API のテスト用キー | **network secrets**(Claude・Codex とも値が VM に入らない) | 本番キーは使わない |
| 作業ログの送信キー | Claude: 環境の環境変数 / Codex: Personal vault(Environment variable 型、対象環境だけ) | 受け口(Apps Script)がヘッダを読めないため本文で送る。キーの権限を「決まったフォルダへの新規作成」に限定して影響を抑える |
| `.env` などの秘密ファイル | リポジトリに置かない。Claude はクラウドで deny | — |
| 人間の鍵(1Password・YubiKey・SSH) | クラウドに置かない | — |

送信キーの作り方: Mac で `bash local/new-log-key.sh codex`(と `claude`)。1Password CLI が使えれば 1Password に値を生成させて保存し、クリップボードに入れて60秒後に消す。値は画面・シェル履歴・ファイル・Git・チャットに出ない。

**送信キーの権限と Apps Script 自身の権限を区別する**:
- 送信キーでできること: そのサービスのフォルダに、作業ログ形式(frontmatter・UUID・サービス名を検査)の新規ファイルを作ることだけ。読む・消す・上書き・別フォルダは不可。件数とサイズに上限。
- Apps Script 自身の Drive 権限: DriveApp の仕様で `https://www.googleapis.com/auth/drive`(本人の Drive 全体)を承認する必要がある [G4]。コードが書き込み先を2つのフォルダに限定し、ミラー配下を拒否する。公開 URL は「版を固定したデプロイ」なので、エディタで保存しただけでは動作は変わらない [G3]。コードを変えられるのは本人だけ。

作業ログは、既知のトークン形式(GitHub・GitLab・OpenAI・Anthropic・AWS・Google・Slack・Stripe・npm・PyPI・JWT・秘密鍵・Bearer・URL 内の資格情報・`password=` 形式・受け口の URL)と、名前が秘密らしい環境変数の値を `[REDACTED]` にしてから書き、送る直前にも確かめる。会話やコマンドの生出力は読まない。
