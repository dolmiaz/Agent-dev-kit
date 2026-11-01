# Codex Cloud(新しい Cloud 環境)の設定一覧と推奨構成

確認日: 2026-10-29(出典は [sources.md](sources.md) の [C*])。
対象は **新しい Cloud 環境**(ChatGPT の Settings > Codex Cloud > Environments、または Work in > Cloud)。

- **対象外**: Codex Cloud (Legacy)(chatgpt.com/codex/settings/environments の Setup / Maintenance script、12時間キャッシュ、HTTP メソッド制限)。既存の Legacy 環境は削除・移行しなくてよい。ただし GitHub の `@codex`(review 以外)・Code Review・Linear 連携は移行期間中 Legacy 側で動く [C2][C9] ので、それらの設定はこの一覧では扱わない。
- **区別**: ローカルの Codex CLI / IDE / デスクトップアプリの `~/.codex/config.toml`・`hooks.json`・`~/.codex/skills`・ローカル MCP はクラウドに届かない [C2][C12]。

凡例: ○ 対応 / × 非対応 / ? 未確認(公式に記載なし)。「推奨値」は日常の開発を止めにくくしつつ、秘密の漏えい・想定外の課金・削除を避ける観点。

## 1. 実行環境

| 項目 | 状態 | 設定場所 | 適用範囲 | 推奨値 | 理由・副作用 | 必要なユーザー操作 |
|---|---|---|---|---|---|---|
| VM・OS・イメージ | ○(選択不可)/ OS は ? | — | 環境 | 既定 | 新方式の OS は未記載。参考実装 codex-universal は Ubuntu 24.04 [C13]。スクリプトは OS を判定し、パスに依存しない | なし |
| VM の大きさ | ○ | プランで決まる | プラン | — | Plus: 2 vCPU / 8 GiB / 8 GiB ディスク。Pro 以上: 4 vCPU / 16 GiB / 32 GiB [C2] | なし |
| 言語・ランタイム | ○(Install script で追加) | Install script | 環境 | `bash .agent-kit/codex/setup.sh` | ロックファイルから判定して入れる | 環境作成時に指示 |
| 作業ディレクトリ・実行ユーザー | ? | — | — | — | 未記載。スクリプトは `git rev-parse` で判定 | なし |
| Docker | ? | — | — | 使わない前提 | 未記載。compose が要るテストは Claude 側で実行するか、モック | — |
| ブラウザ操作 | × | — | — | ヘッドレス Playwright | computer use / browser use は未対応 [C2]。ヘッドレスのテストは VM 内のプログラムとして動く(配布元の許可が必要) | 許可ドメイン追加 |

## 2. セットアップと保持

| 項目 | 状態 | 設定場所 | 適用範囲 | 推奨値 | 理由・副作用 | 必要なユーザー操作 |
|---|---|---|---|---|---|---|
| Install script | ○ | 環境の作成・編集の会話 → Save → Publish | 環境 | `bash .agent-kit/codex/setup.sh` を実行するだけ | エージェントが検証して記録する方式。**直接タイプして編集できるかは ?**(公式は会話で直すと記載)[C2] | 会話で指示し、テスト結果を見て Publish |
| Start skill | ○ | 同上 | 環境 | 不要(DB が要るときだけ `bash .agent-kit/bin/services.sh start postgres`) | サービス起動と準備完了の確認用 [C2] | 必要時のみ |
| 時間制限 | ? | — | — | 各段 600 秒・全体 1500 秒(`AGENT_STEP_TIMEOUT` / `AGENT_SETUP_TOTAL_TIMEOUT`) | 未記載のため自前で打ち切る | なし |
| 再実行 | 一部 | — | — | ロックが変わったらエージェントが `setup-deps.sh deps` | リポジトリの更新は自動だが install は再実行されない [C2] | なし(AGENTS.md に記載) |
| Maintenance script | ×(新方式に欄が無い) | — | — | 使わない | Legacy の機能 | なし |
| 保存状態 | ○ | Publish | 環境 / タスク | 作業の区切りでコミット・PR | 新タスクは Publish 時の状態から。VM 状態は最後のターンから最大7日 [C2][C3] | なし |
| 失敗時 | ○ | 会話で修正 / Try again | 環境 | — | [C2] | 必要時 |

## 3. ネットワーク

| 項目 | 状態 | 設定場所 | 適用範囲 | 推奨値 | 理由・副作用 | 必要なユーザー操作 |
|---|---|---|---|---|---|---|
| 全開放 | ○ | 環境 > internet access「Enable all public internet access」 | 環境 | **オフ** | オンにすると細かい制御が無効 [C2]。持ち出し経路が広がる | 設定 |
| Package managers プリセット | ○ | 同上 | 環境 | **オン** | npm/Yarn・PyPI・crates・Go・Maven/Gradle・Bazel・Buf・CPAN・Ubuntu/Debian apt・GitHub(github.com, codeload, release-assets 等)・dl.google.com [C2]。RubyGems・Packagist・GitLab は含まれない | 設定 |
| 追加ドメイン | ○ | 同上「Additional allowed domains」 | 環境 | 下の表 | `www` 以外のサブドメインは個別に追加 [C2] | 追加 |
| HTTP メソッド制限 | ×(新方式に記載なし) | — | — | — | Legacy の機能。POST の作業ログ送信はそのまま通る | なし |
| プロキシ | ○(自動) | — | — | 何もしない | 変数名は未記載。curl に `--noproxy` を付けない [C2]。キットはプロキシを迂回しない | なし |
| Tailscale / OIDC | ○ | 環境 > Tailscale / Advanced > OIDC | 環境 | 使わない | 今回の要件に不要。使うと新しい永続的な認証情報 [C2] | — |

追加ドメイン(目的を限定):

| ドメイン | 目的 | 必要な場合 |
|---|---|---|
| `script.google.com`, `script.googleusercontent.com` | 作業ログの送信(Apps Script の受け口と 302 の転送先) | 作業ログを使うとき(推奨) |
| `cdn.playwright.dev`, `playwright.download.prss.microsoft.com` | Playwright のブラウザ本体 | リポジトリが Playwright を使うとき |
| `gitlab.com`, `registry.gitlab.com` | GitLab からの取得(依存・サブモジュール) | 方針として初期許可(下の注)。標準連携とは別 |
| `rubygems.org`, `index.rubygems.org`, `repo.packagist.org`, `packagist.org` | Ruby / PHP の依存 | 該当言語を使うとき |

> **GitLab の2つの話を分ける**: (1) 標準連携(リポジトリとして選ぶ・PR/MR を作る)は新 Cloud 環境では**非対応**と明記 [C2]。GitLab の beta 連携は MR レビュー用で、修正タスクは Legacy の cloud chat で動く [C10]。(2) 通信の許可(依存やサブモジュールを gitlab.com から取る)は追加ドメインで可能。資格情報は付かないので公開リポジトリの取得に限られる。

## 4. 環境変数・シークレット

| 項目 | 状態 | 設定場所 | 適用範囲 | 推奨値 | 理由・副作用 | 必要なユーザー操作 |
|---|---|---|---|---|---|---|
| 環境変数 | ○ | 環境 > Environment variables | 環境(共有すると利用者全員) | `AGENT_RUNTIME=codex-cloud`, `AGENT_SERVICE=codex` | 値はプログラムからそのまま見える [C2]。Codex Cloud に公式の判定変数が無いため明示する | 登録 |
| Network secrets | ○ | 環境 > HTTP secrets > Manage(Key / Value / Allowed domains) | 環境 | **外部 API のテスト用キーはこちら** | プログラムには placeholder だけ。プロキシが許可先への HTTPS 443 で差し替え。**置換する位置(ヘッダ / 本文)は未記載** [C2] | テスト用キーがあるとき |
| Personal vault | ○ | Settings > Codex Cloud > Personal vault(Type: Environment variable / Network secret、Applies to: All / Selected) | 本人 | `AGENT_LOG_ENDPOINT` と `AGENT_LOG_KEY` を Type=Environment variable、Selected environments でこの環境だけ | 環境を共有しても個人の値はコピーされない [C2]。受け口は本文の鍵を読む(Apps Script はヘッダを読めない [G3])ので Network secret 型は使えない | 登録 |

## 5. GitHub / GitLab・コミット・署名

| 項目 | 状態 | 設定場所 | 適用範囲 | 推奨値 | 理由・副作用 | 必要なユーザー操作 |
|---|---|---|---|---|---|---|
| GitHub 接続 | ○ | 環境作成時 Connect GitHub | 本人 | 使うリポジトリだけを選ぶ | 各ユーザーの GitHub 接続で動く [C2][C3] | GitHub 側で App の対象を絞る |
| PR 作成・push | 一部 | タスク内 | タスク | 結果を確認してから「PR を作って」と頼む | 誰がどのブランチ名で push するかは未記載 [C2]。`feature/<番号>-<名前>` を指示に書き、実際の名前を最初の PR で確認する | 最初の PR で確認 |
| エージェントからの GitHub API | × 前提 | — | — | Issue の確保・コメントは依頼側(dot / 本人)が行う | VM に GitHub のトークンを渡さない(新しい認証情報を作らない) | — |
| コミット作者 | ? | — | — | Install script で `Codex (cloud) <codex-cloud@example.com>` をリポジトリ単位に設定 | プラットフォームが作るコミットの作者は未記載。VM 内のコミットは区別できる | 最初の PR で確認 |
| コミット署名 | ? | — | — | 署名しない | docs/04 参照 | — |
| GitLab | ×(標準連携) | — | — | 使わない | §3 の注 | — |

## 6. 承認・権限

| 項目 | 状態 | 設定場所 | 適用範囲 | 推奨値 | 理由・副作用 | 必要なユーザー操作 |
|---|---|---|---|---|---|---|
| 承認プロンプト | ?(個人プランに設定なし) | — | — | AGENTS.md の「しないこと」+ GitHub 側の保護 | 承認待ちで止まる仕組みの記載なし [C6]。指示と GitHub のルールが実質の境界 | ルールセット(docs/06) |
| Enterprise の制御 | ○ Enterprise | Admin Console > Agent security | ワークスペース | — | [C11] | — |

## 7. 指示ファイル・skills・hooks・MCP

| 項目 | 状態 | 設定場所 | 適用範囲 | 推奨値 | 理由・副作用 | 必要なユーザー操作 |
|---|---|---|---|---|---|---|
| AGENTS.md | ?(クラウドでの扱いは未記載) | リポジトリ | リポジトリ | キットの区間を追加(Codex Cloud のときだけ適用と明記) | 「リポジトリと一緒に移動する」とはあるが、新 Cloud での読み込みは未記載 [C8]。**実機で確認する** | 最初のタスクで確認 |
| skills | ○(リポジトリ内のみ) | `.agents/skills/` | リポジトリ | キットの3つ+配布する個人スキル | 「リポジトリの skill はクラウドで使える」、手元の個人 skill は同期されない [C2][C14] | なし |
| hooks | ?(実質使えない前提) | — | — | 使わない | 新 Cloud 環境での記載なし。**ターン終了フックが無い前提**で、AGENTS.md と skill で毎ターン記録させる | なし |
| MCP / config.toml | ? | — | — | 使わない | 記載なし | — |

## 8. モデル・並列・費用

| 項目 | 状態 | 設定場所 | 適用範囲 | 推奨値 | 理由・副作用 | 必要なユーザー操作 |
|---|---|---|---|---|---|---|
| モデル・推論 | ? | 入力欄の model / reasoning | タスク | 既定 | クラウドでの選択は公式本文なし [C16] | — |
| 複数試行 | ○(CLI) | `codex cloud exec --attempts 1-4` | タスク | 1 | 試行数に応じて消費が増える(推定)[C18] | — |
| 並列タスク | ○(上限 ?) | — | — | 同じファイルを触るタスクを同時に走らせない(Issue の確保で防ぐ) | [C2] | — |
| 利用枠・クレジット | ○ | Settings > Usage | アカウント | **クレジットの自動補充はオフ** | ローカルと同じ枠を共有し、クラウドは多めに消費。自動補充をオンにすると追加課金 [C5][C20] | 確認 |

## 9. ログ・成果物

| 項目 | 状態 | 設定場所 | 適用範囲 | 推奨値 | 理由・副作用 | 必要なユーザー操作 |
|---|---|---|---|---|---|---|
| diff・適用 | ○ | タスク画面 / `codex cloud diff`・`apply` | タスク | — | [C18] | — |
| ファイルのダウンロード | ? | — | — | 作業ログは受け口へ送る。送れないときは最終返答に本文を添える | 公式のダウンロード機能は未記載 | — |
| 未コミットの作業 | 最大7日 | — | タスク | すぐコミット・PR | 別タスクからは戻らない [C3] | — |

## 推奨構成(まとめ)

1. 環境: Who can use = Only me。Install script = `bash .agent-kit/codex/setup.sh`(会話で指示 → テスト → Publish)。
2. ネットワーク: 全開放オフ、Package managers オン、追加ドメインは上の表から必要なものだけ。
3. 環境変数: `AGENT_RUNTIME=codex-cloud`, `AGENT_SERVICE=codex`。Personal vault: `AGENT_LOG_ENDPOINT`, `AGENT_LOG_KEY`(Codex 用の鍵)を Selected environments で。
4. リポジトリ: `install.py` で AGENTS.md の区間、`.agents/skills/`、`.agent-kit/` を入れる。
5. 費用: クレジットの自動補充はオフのまま。
