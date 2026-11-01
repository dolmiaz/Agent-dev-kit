// Code.gs の検証ロジックの単体テスト(Node のモック上で本物のコードを実行)。
const fs = require('fs');
const os = require('os');
const path = require('path');
const crypto = require('crypto');
const assert = require('assert');
const { makeEnv } = require('./gas_mock');

const code = process.argv[2];
const KC = 'c'.repeat(40), KL = 'd'.repeat(40);
let pass = 0;
const check = (name, got, want) => { assert.strictEqual(got.status, want, `${name}: ${JSON.stringify(got)}`); pass++; };
const fresh = (extra = {}) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'gas-'));
  let mode = 'ok';
  const env = makeEnv({ code, dir, propsFile: path.join(dir, 'props.json'), mode: () => mode,
    props: Object.assign({ KEY_CODEX: KC, KEY_CLAUDE: KL, FOLDER_CODEX: 'F_CODEX', FOLDER_CLAUDE: 'F_CLAUDE',
      DENY_FOLDERS: 'MIRROR,GOODREADER', MAX_PER_HOUR: '5' }, extra) });
  env.dir = dir; env.setMode = (m) => { mode = m; };
  return env;
};
const uuid = () => crypto.randomUUID();
const doc = (svc, id, extra = '') => `---\nsource: "${svc}"\nkind: "agent-worklog"\nservice: "${svc === 'codex' ? 'codex-cloud' : 'claude-code-web'}"\nlog_id: "${id}"\n---\n## t\n<!-- log: ${id} -->\n${extra}`;
const name = (svc, id, t = '01') => `2026-10-29-1733-${svc}-abc123def456-t${t}-${id.slice(0, 8)}.md`;
const req = (svc, key, id, content, filename) => ({ service: svc, key, log_id: id, filename: filename || name(svc, id), content });
const sha = (s) => crypto.createHash('sha256').update(s, 'utf8').digest('hex');

let env = fresh();
let id = uuid();
check('wrong key', env.post(req('codex', 'x'.repeat(40), id, doc('codex', id))), 'unauthorized');
check('other service key', env.post(req('claude', KC, id, doc('claude', id))), 'unauthorized');
check('bad service', env.post({ service: 'x', key: KC }), 'bad-service');
check('path in name', env.post(req('codex', KC, id, doc('codex', id), '../a-t01-' + id.slice(0, 8) + '.md')), 'bad-filename');
check('claude log via codex key/folder', env.post(req('codex', KC, id, doc('claude', id), name('codex', id))), 'service-mismatch');
check('name says claude but key codex', env.post(req('codex', KC, id, doc('codex', id), name('claude', id))), 'service-mismatch');
check('non-uuid id', env.post(req('codex', KC, 'abcdef12-codex', doc('codex', 'abcdef12-codex'), '2026-10-29-1733-codex-abc-t01-abcdef12.md')), 'bad-log-id');
check('not a worklog', env.post(req('codex', KC, id, 'hello')), 'not-a-worklog');
const id2 = uuid();
check('log id mismatch', env.post(req('codex', KC, id, doc('codex', id2), name('codex', id))), 'log-id-mismatch');
check('bad hash', env.post(Object.assign(req('codex', KC, id, doc('codex', id)), { sha256: '0'.repeat(64) })), 'bad-hash');
check('too large', env.post(req('codex', KC, id, doc('codex', id, 'x'.repeat(210 * 1024)))), 'too-large');
const c1 = doc('codex', id);
let r = env.post(Object.assign(req('codex', KC, id, c1), { sha256: sha(c1) }));
check('created', r, 'created');
assert.strictEqual(r.log_id, id); assert.strictEqual(r.sha256, sha(c1)); pass++;
assert.ok(fs.existsSync(path.join(env.dir, 'F_CODEX', name('codex', id)))); pass++;
check('resend same id+content → duplicate', env.post(req('codex', KC, id, c1)), 'duplicate');
check('same id, different content → conflict', env.post(req('codex', KC, id, c1 + 'changed')), 'conflict');
assert.ok(!fs.readFileSync(path.join(env.dir, 'F_CODEX', name('codex', id)), 'utf8').includes('changed')); pass++;
// 取り込み後に Drive から消えた後の再送 → 台帳で duplicate(再作成しない)
fs.unlinkSync(path.join(env.dir, 'F_CODEX', name('codex', id)));
check('resend after import+delete → duplicate', env.post(req('codex', KC, id, c1)), 'duplicate');
assert.ok(!fs.existsSync(path.join(env.dir, 'F_CODEX', name('codex', id)))); pass++;
// 同名の本人編集ファイルがある(台帳に無い)→ conflict で上書きしない
const id3 = uuid();
fs.writeFileSync(path.join(env.dir, 'F_CODEX', name('codex', id3)), 'user edited');
check('same name, user-edited file → conflict', env.post(req('codex', KC, id3, doc('codex', id3))), 'conflict');
assert.strictEqual(fs.readFileSync(path.join(env.dir, 'F_CODEX', name('codex', id3)), 'utf8'), 'user edited'); pass++;
// Claude は Claude のフォルダへ
const id4 = uuid();
check('claude → claude folder', env.post(req('claude', KL, id4, doc('claude', id4))), 'created');
assert.ok(fs.existsSync(path.join(env.dir, 'F_CLAUDE', name('claude', id4)))); pass++;
// 件数上限(1時間5件)
let st = '';
for (let i = 0; i < 8; i++) { const x = uuid(); st = env.post(req('codex', KC, x, doc('codex', x))).status; }
assert.strictEqual(st, 'rate-limited'); pass++;
// 容量不足・クォータ
env = fresh(); env.setMode('storage-full');
let x = uuid();
check('storage full → keep pending', env.post(req('codex', KC, x, doc('codex', x))), 'storage-full');
env.setMode('quota');
check('apps script quota → keep pending', env.post(req('codex', KC, x, doc('codex', x))), 'quota');
env.setMode('ok');
check('after quota recovery → created', env.post(req('codex', KC, x, doc('codex', x))), 'created');
// ミラー配下を設定してしまった場合
env = fresh({ FOLDER_CODEX: 'MIRROR_SUB' });
x = uuid();
check('mirror folder denied', env.post(req('codex', KC, x, doc('codex', x))), 'folder-denied');
env = fresh({ FOLDER_CODEX: 'MIRROR' });
check('mirror root denied', env.post(req('codex', KC, x, doc('codex', x))), 'folder-denied');
// 台帳の刈り込み
env = fresh({ LEDGER_MAX: '10', MAX_PER_HOUR: '1000' });
for (let i = 0; i < 15; i++) { const y = uuid(); env.post(req('codex', KC, y, doc('codex', y))); }
const n = Object.keys(env.props()).filter((k) => k.startsWith('L:')).length;
assert.ok(n <= 11, `ledger pruned (${n})`); pass++;
assert.deepStrictEqual(env.get(), { ok: true, status: 'alive' }); pass++;
// DENY_FOLDERS が空でも、Deny.gs に固定したミラーの ID は拒否する
if (code.includes('Deny')) {
  env = fresh({ DENY_FOLDERS: '', FOLDER_CODEX: 'FIXED_MIRROR' });   // tests/fixtures/Deny.test.gs に固定した ID
  x = uuid();
  check('built-in deny works without DENY_FOLDERS', env.post(req('codex', KC, x, doc('codex', x))), 'folder-denied');
}
// 2つの鍵が同じ値なら設定の誤りとして全て拒否
env = fresh({ KEY_CLAUDE: KC });
x = uuid();
check('identical keys → unauthorized', env.post(req('codex', KC, x, doc('codex', x))), 'unauthorized');
// 失敗した分は件数上限を消費しない
env = fresh({ MAX_PER_HOUR: '2' }); env.setMode('storage-full');
for (let i = 0; i < 5; i++) { const y = uuid(); env.post(req('codex', KC, y, doc('codex', y))); }
env.setMode('ok'); x = uuid();
check('failed attempts do not consume the rate limit', env.post(req('codex', KC, x, doc('codex', x))), 'created');
console.log(`gas harness: ${pass} checks passed`);
