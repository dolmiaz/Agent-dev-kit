# スキルの正本(テンプレート)

非公開の Git リポジトリとして作り、`skills/<name>/` にスキルを置く。版はタグで付ける。

- `sets.json`: リポジトリに合わせて選ぶセット(`skillsync vendor --set`)
- `checks.json`: 本人が確認した代表処理のコマンド(`skillsync verify --run`)
- スキルの `LICENSE*`・`UPSTREAM.json` は外さない(来歴と再配布の条件)

取り込み: `python3 <キット>/skills/skillsync.py export <手元のスキル> <この正本>`。
