// 本物の Code.gs をモックの Google サービスの上で動かし、Apps Script と同じく
// POST → 302 → GET の流れで応答する HTTP サーバ(テスト用)。
//   node gas_server.js <port> <Code.gs> <dir> [mode-file]
// mode-file に ok / quota / storage-full / down を書くと挙動が変わる(down は 500)。
const http = require('http');
const fs = require('fs');
const path = require('path');
const { makeEnv } = require('./gas_mock');

const [port, code, dir, modeFile] = process.argv.slice(2);
const mode = () => (modeFile && fs.existsSync(modeFile) ? fs.readFileSync(modeFile, 'utf8').trim() : 'ok');
const env = makeEnv({
  code, dir, propsFile: path.join(dir, 'props.json'), mode,
  props: {
    KEY_CODEX: process.env.MOCK_KEY_CODEX, KEY_CLAUDE: process.env.MOCK_KEY_CLAUDE,
    FOLDER_CODEX: 'F_CODEX', FOLDER_CLAUDE: 'F_CLAUDE', DENY_FOLDERS: 'MIRROR,GOODREADER',
    MAX_PER_HOUR: process.env.MOCK_MAX_PER_HOUR || '120',
  },
});
const results = {};
let n = 0;
http.createServer((req, res) => {
  if (mode() === 'down') { res.writeHead(500); return res.end(); }
  if (mode() === 'slow') { return setTimeout(() => { res.writeHead(500); res.end(); }, 5000); }
  if (req.method === 'POST') {
    let body = '';
    req.on('data', (c) => { body += c; });
    req.on('end', () => {
      let out;
      try { out = env.ctx.doPost({ postData: { contents: body } }).s; } catch (e) { out = JSON.stringify({ ok: false, status: 'error' }); }
      const id = String(++n);
      results[id] = out;
      res.writeHead(302, { Location: `/result/${id}` });
      res.end();
    });
    return;
  }
  const id = req.url.split('/').pop();
  res.writeHead(200, { 'Content-Type': 'application/json' });
  res.end(results[id] || env.ctx.doGet().s);
}).listen(Number(port), '127.0.0.1');
