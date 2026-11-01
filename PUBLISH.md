# 公開について

このキットは、個人の環境の値を外した写しだけを公開する。元のリポジトリ(履歴を含む)は公開しない。

## 作り方

```bash
bash tools/make-public.sh <出力先>      # 写す → 点検 → 通ったときだけ「作った」と出す
```

1. 下の「公開する」ものだけを写す。
2. `tools/publish-check.py` で調べる: example 以外のメールアドレス、Google Drive の ID らしい文字列、`/Users/<名前>`、
   Apps Script の公開 URL、秘密鍵・トークンの形、公開しないファイル、個人の文字列の一覧(`personal/publish-deny.local.txt`。それ自体は公開しない)。
3. 検出値は表示せず、場所と種類だけを報告する。見つかれば作らない。見つからないことは安全の証明ではないので、最後に本人が目で確かめる。
4. 出力先で新しい git リポジトリを作る(元の履歴は持ち込まない)。push とリポジトリの作成は本人が行う。

## 公開する / しない

| 公開する | 公開しない |
|---|---|
| `install.py`、`kit/`(設定の値は例: `*@example.com`) | `personal/`(メール・フォルダ ID・個人の記録・スキルの一覧) |
| `endpoint/Code.gs`・`appsscript.json`・`Deny.gs.example` | `personal/endpoint/Deny.gs`(実際のミラーの ID) |
| `local/`(フォルダ ID は設定ファイルから読む) | `~/.config/agent-kit/collector.json`(手元の設定) |
| `skills/skillsync.py`・`skills/bundle-template/` | スキルの正本と個人スキルそのもの |
| `tests/`(値は架空)、`tools/` | `reports/`(検証の記録・テストのログ) |
| `docs/01`〜`06`、`08`〜`10`、`sources.md`、公開用の安全・ライセンス状態の説明 | 元のリポジトリの git 履歴 |

## 本人が決めること

- キットのライセンス(LICENSE を置く)。
- 公開先のリポジトリ(公開リポジトリの作成は本人の操作)。

## 個人スキルについて

個人スキルはキットに含めない。スキルの正本は非公開のまま。**公開リポジトリへ vendor すると、そのスキルは公開される**ので、
`skillsync vendor` は配布先が公開なら `--public-target` を求め、ライセンスで再配布できると分かるものだけを配る。
上流のあるスキルは、出所(`UPSTREAM.json`)・ライセンス・再配布の条件をスキルごとに確かめる(本人の判断)。
