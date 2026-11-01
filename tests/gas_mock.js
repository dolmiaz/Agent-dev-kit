// Code.gs(Apps Script)をそのまま Node で動かすための Google サービスのモック。
// Drive のフォルダはローカルのディレクトリ、Script Properties は JSON ファイルで模す。
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const crypto = require('crypto');

function makeEnv(opts) {
  const root = opts.dir;                       // <dir>/<folderId>/<file>
  const propsFile = opts.propsFile;            // 永続化(再起動しても台帳が残る)
  const mode = () => (opts.mode ? opts.mode() : 'ok');
  const parents = Object.assign({ F_CODEX: ['root'], F_CLAUDE: ['root'], MIRROR_SUB: ['MIRROR'], MIRROR: ['root'], root: [] }, opts.parents || {});
  let props = Object.assign({}, opts.props || {});
  if (propsFile && fs.existsSync(propsFile)) props = Object.assign(props, JSON.parse(fs.readFileSync(propsFile, 'utf8')));
  const save = () => { if (propsFile) fs.writeFileSync(propsFile, JSON.stringify(props)); };
  const iter = (arr) => { let i = 0; return { hasNext: () => i < arr.length, next: () => arr[i++] }; };
  const fileObj = (dir, n) => ({
    getId: () => path.basename(dir) + '/' + n,
    getBlob: () => ({ getDataAsString: () => fs.readFileSync(path.join(dir, n), 'utf8') }),
    setDescription: () => {},
  });
  const folder = (id) => {
    const dir = path.join(root, id);
    return {
      getId: () => id, getName: () => id,
      getParents: () => iter((parents[id] || []).map(folder)),
      getFilesByName: (n) => iter(fs.existsSync(path.join(dir, n)) ? [fileObj(dir, n)] : []),
      createFile: (n, c) => {
        if (mode() === 'quota') throw new Error('Exception: Service invoked too many times for one day: driveapp.');
        fs.mkdirSync(dir, { recursive: true });
        fs.writeFileSync(path.join(dir, n), c);
        return fileObj(dir, n);
      },
    };
  };
  const cache = {};
  const ctx = {
    PropertiesService: { getScriptProperties: () => ({
      getProperty: (k) => (k in props ? props[k] : null),
      setProperty: (k, v) => { props[k] = String(v); save(); },
      deleteProperty: (k) => { delete props[k]; save(); },
      getProperties: () => Object.assign({}, props),
    }) },
    DriveApp: {
      getFolderById: folder,
      getStorageLimit: () => (mode() === 'storage-full' ? 100 : 15e9),
      getStorageUsed: () => (mode() === 'storage-full' ? 100 : 1e6),
    },
    LockService: { getScriptLock: () => ({ waitLock: () => {}, releaseLock: () => {} }) },
    CacheService: { getScriptCache: () => ({ get: (k) => cache[k], put: (k, v) => { cache[k] = v; } }) },
    Utilities: {
      newBlob: (s) => ({ getBytes: () => Buffer.from(s, 'utf8') }),
      formatDate: (d, tz, f) => (f === 'yyyyMMddHH' ? '2026102917' : '20261029'),
      computeDigest: (_alg, s) => Array.from(crypto.createHash('sha256').update(Buffer.from(s, 'utf8')).digest()).map((b) => (b > 127 ? b - 256 : b)),
      DigestAlgorithm: { SHA_256: 'SHA_256' }, Charset: { UTF_8: 'UTF_8' },
    },
    ContentService: { createTextOutput: (s) => ({ s, setMimeType() { return this; } }), MimeType: { JSON: 'json' } },
    Logger: { log: (...a) => (opts.log || (() => {}))(...a) },
    Date, Math, JSON, String, Number, Object, Array, Buffer,
  };
  vm.createContext(ctx);
  // 複数ファイル(カンマ区切り)を Apps Script と同じく同じ大域に読み込む
  for (const f of String(opts.code).split(',')) vm.runInContext(fs.readFileSync(f, 'utf8'), ctx);
  return {
    post: (obj) => JSON.parse(ctx.doPost({ postData: { contents: JSON.stringify(obj) } }).s),
    get: () => JSON.parse(ctx.doGet().s),
    ctx, props: () => props,
  };
}
module.exports = { makeEnv };
