/**
 * agent-log 受け口 v2(Google Apps Script のウェブアプリ。追加料金なし)
 *
 * 役割: クラウドのエージェントから届いた作業ログ(Markdown)を、サービスごとに固定した Drive フォルダへ
 *       「新規ファイルとして」置くだけ。読み出し・上書き・削除・移動はしない。
 *
 * 2つの権限を区別すること:
 *  - 送信キー(KEY_CODEX / KEY_CLAUDE)でできること: そのサービスのフォルダに、作業ログ形式の新規ファイルを
 *    作ることだけ(1時間・1日の件数上限、200KB 上限)。読む・消す・上書き・別フォルダは不可。
 *  - このスクリプト自身の Drive 権限: DriveApp の仕様上 https://www.googleapis.com/auth/drive(本人の Drive 全体)。
 *    コードが書き込み先を2つのフォルダに限定している。コードを変えられるのはプロジェクトの所有者だけで、
 *    公開 URL は「版を固定したデプロイ」なので、エディタで保存しただけでは公開中の動作は変わらない。
 *
 * 一意性と重複防止:
 *  - 各ログは UUID4 の log_id を持つ。台帳(Script Properties)に log_id → 内容の sha256 を記録する。
 *  - 同じ log_id・同じ内容 → duplicate(成功扱い)。取り込み後に Drive から削除された後の再送も、台帳で弾く。
 *  - 同じ log_id で内容が違う、または同名で内容が違うファイルがある → conflict(書かない)。
 *  - 応答に log_id と sha256 を返す。クライアントは両方が一致したときだけ成功とみなす。
 *
 * 上限: 件数上限・Drive 容量不足・Apps Script のクォータ超過は ok:false で返し、クライアントは未送信のまま保持する。
 *
 * Script Properties(プロジェクトの設定 > スクリプト プロパティ):
 *   KEY_CODEX / KEY_CLAUDE   … 32文字以上の別々のランダム値(チャット・ログ・Git に出さない)
 *   FOLDER_CODEX  = <Codex のログを置くフォルダの ID>
 *   FOLDER_CLAUDE = <Claude のログを置くフォルダの ID>
 *   DENY_FOLDERS  = <書き込んではいけないフォルダの ID をカンマ区切り>(この配下も拒否)
 *
 * 設定が消えても拒否が外れないよう、同じプロジェクトに Deny.gs を置いて、拒否するフォルダの ID を
 * コードにも固定できる(endpoint/Deny.gs.example を参照。個人の ID はそこに書き、このファイルには書かない):
 *   var PERSONAL_DENY = ['<ID>', ...];
 *   任意: MAX_PER_HOUR(120) MAX_PER_DAY(1000) LEDGER_MAX(3000)
 */

var MAX_BYTES = 200 * 1024;
var NAME_RE = /^\d{4}-\d{2}-\d{2}-\d{4}-(codex|claude)-[A-Za-z0-9]{1,16}-t\d{2,4}-([0-9a-f]{8})\.md$/;
var UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
var SERVICE_LONG = { codex: 'codex-cloud', claude: 'claude-code-web' };
// コードに固定した拒否フォルダ(Deny.gs の PERSONAL_DENY)。ファイルの読み込み順に依らないよう、呼ぶたびに読む
function builtinDeny_() {
  return (typeof PERSONAL_DENY !== 'undefined' && PERSONAL_DENY && PERSONAL_DENY.slice) ? PERSONAL_DENY.slice() : [];
}

function doPost(e) {
  var req;
  try {
    req = JSON.parse((e && e.postData && e.postData.contents) || '{}');
  } catch (err) {
    return out_({ ok: false, status: 'bad-json' });
  }
  var base = { log_id: String(req.log_id || ''), sha256: '' };
  try {
    var props = PropertiesService.getScriptProperties();
    var svc = String(req.service || '');
    if (!SERVICE_LONG[svc]) return out_(merge_(base, { ok: false, status: 'bad-service' }));
    var key = props.getProperty(svc === 'codex' ? 'KEY_CODEX' : 'KEY_CLAUDE') || '';
    // 2つの鍵が同じ値だと片方の鍵で両方のフォルダに書けてしまうため、設定の誤りとして全て拒否する
    if (props.getProperty('KEY_CODEX') === props.getProperty('KEY_CLAUDE')) return out_(merge_(base, { ok: false, status: 'unauthorized' }));
    if (key.length < 32 || !safeEqual_(String(req.key || ''), key)) return out_(merge_(base, { ok: false, status: 'unauthorized' }));

    var name = String(req.filename || '');
    var content = String(req.content || '');
    var logId = base.log_id;
    var nm = NAME_RE.exec(name);
    if (!nm) return out_(merge_(base, { ok: false, status: 'bad-filename' }));
    if (nm[1] !== svc) return out_(merge_(base, { ok: false, status: 'service-mismatch' }));
    if (!UUID_RE.test(logId) || nm[2] !== logId.slice(0, 8)) return out_(merge_(base, { ok: false, status: 'bad-log-id' }));
    var bytes = Utilities.newBlob(content).getBytes().length;
    if (bytes > MAX_BYTES) return out_(merge_(base, { ok: false, status: 'too-large' }));
    if (!/^kind:\s*"?agent-worklog"?\s*$/m.test(content)) return out_(merge_(base, { ok: false, status: 'not-a-worklog' }));
    var fmSrc = /^source:\s*"?([a-z-]+)"?\s*$/m.exec(content);
    var fmSvc = /^service:\s*"?([a-z-]+)"?\s*$/m.exec(content);
    if (!fmSrc || fmSrc[1] !== svc || !fmSvc || fmSvc[1] !== SERVICE_LONG[svc]) {
      return out_(merge_(base, { ok: false, status: 'service-mismatch' }));
    }
    var fmId = /^log_id:\s*"?([0-9a-f-]{36})"?\s*$/m.exec(content);
    if (!fmId || fmId[1] !== logId || content.indexOf('<!-- log: ' + logId + ' -->') < 0) {
      return out_(merge_(base, { ok: false, status: 'log-id-mismatch' }));
    }
    var hash = sha256_(content);
    base.sha256 = hash;
    if (req.sha256 && req.sha256 !== hash) return out_(merge_(base, { ok: false, status: 'bad-hash' }));

    var folderId = props.getProperty(svc === 'codex' ? 'FOLDER_CODEX' : 'FOLDER_CLAUDE');
    if (!folderId) return out_(merge_(base, { ok: false, status: 'no-folder' }));
    var deny = builtinDeny_().concat((props.getProperty('DENY_FOLDERS') || '').split(',').map(function (s) { return s.trim(); })
      .filter(function (s) { return s; }));
    var folder = DriveApp.getFolderById(folderId);
    if (deny.indexOf(folderId) >= 0 || isUnder_(folder, deny)) return out_(merge_(base, { ok: false, status: 'folder-denied' }));

    var lock = LockService.getScriptLock();
    lock.waitLock(20000);
    try {
      var ledgerKey = 'L:' + logId;
      var rec = props.getProperty(ledgerKey);
      if (rec) {
        var parts = rec.split('|');
        if (parts[0] === hash && parts[1] === svc) return out_(merge_(base, { ok: true, status: 'duplicate' }));
        return out_(merge_(base, { ok: false, status: 'conflict' }));
      }
      var same = folder.getFilesByName(name);
      if (same.hasNext()) {
        var existing = same.next();
        if (sha256_(existing.getBlob().getDataAsString('UTF-8')) === hash) {
          record_(props, ledgerKey, hash, svc);
          return out_(merge_(base, { ok: true, status: 'duplicate' }));
        }
        return out_(merge_(base, { ok: false, status: 'conflict' }));
      }
      var counts = countCheck_(props, svc, Number(props.getProperty('MAX_PER_HOUR') || 120), Number(props.getProperty('MAX_PER_DAY') || 1000));
      if (!counts) return out_(merge_(base, { ok: false, status: 'rate-limited' }));
      if (!storageOk_(bytes)) return out_(merge_(base, { ok: false, status: 'storage-full' }));
      var f = folder.createFile(name, content, 'text/markdown');
      f.setDescription('agent-log ' + logId);
      countCommit_(props, svc, counts);   // 作成に成功した分だけ数える
      record_(props, ledgerKey, hash, svc);
      pruneLedger_(props, Number(props.getProperty('LEDGER_MAX') || 3000));
      return out_(merge_(base, { ok: true, status: 'created', id: f.getId() }));
    } finally {
      lock.releaseLock();
    }
  } catch (err) {
    var msg = String(err);
    var status = /too many times|Limit Exceeded|quota|Service invoked/i.test(msg) ? 'quota'
      : /storage|容量/i.test(msg) ? 'storage-full' : 'error';
    return out_(merge_(base, { ok: false, status: status, detail: msg.slice(0, 200) }));
  }
}

function doGet() {
  return out_({ ok: true, status: 'alive' });   // 何も書かない・鍵不要
}

function out_(o) {
  return ContentService.createTextOutput(JSON.stringify(o)).setMimeType(ContentService.MimeType.JSON);
}

function merge_(a, b) {
  var o = {};
  for (var k in a) o[k] = a[k];
  for (var j in b) o[j] = b[j];
  return o;
}

function sha256_(s) {
  var d = Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256, s, Utilities.Charset.UTF_8);
  return d.map(function (b) { var v = (b < 0 ? b + 256 : b).toString(16); return v.length === 1 ? '0' + v : v; }).join('');
}

function safeEqual_(a, b) {
  if (a.length !== b.length) return false;
  var r = 0;
  for (var i = 0; i < a.length; i++) r |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return r === 0;
}

function record_(props, key, hash, svc) {
  props.setProperty(key, hash + '|' + svc + '|' + Math.floor(Date.now() / 86400000));
  props.setProperty('LEDGER_COUNT', String(Number(props.getProperty('LEDGER_COUNT') || 0) + 1));
}

// 台帳が LEDGER_MAX を超えたら古い順に1割消す(Script Properties は合計 500KB まで)
function pruneLedger_(props, max) {
  if (Number(props.getProperty('LEDGER_COUNT') || 0) <= max) return;
  var all = props.getProperties();
  var rows = [];
  for (var k in all) if (k.indexOf('L:') === 0) rows.push([k, Number(all[k].split('|')[2] || 0)]);
  rows.sort(function (a, b) { return a[1] - b[1]; });
  var n = Math.ceil(rows.length / 10);
  for (var i = 0; i < n; i++) props.deleteProperty(rows[i][0]);
  props.setProperty('LEDGER_COUNT', String(rows.length - n));
}

function countCheck_(props, svc, perHour, perDay) {
  var now = new Date();
  var hourKey = 'H:' + svc + ':' + Utilities.formatDate(now, 'UTC', 'yyyyMMddHH');
  var dayKey = 'D:' + svc + ':' + Utilities.formatDate(now, 'UTC', 'yyyyMMdd');
  var h = Number(props.getProperty(hourKey) || 0);
  var d = Number(props.getProperty(dayKey) || 0);
  if (h + 1 > perHour || d + 1 > perDay) return null;
  return { hourKey: hourKey, dayKey: dayKey, h: h + 1, d: d + 1 };
}

function countCommit_(props, svc, c) {
  var lastH = props.getProperty('LASTH:' + svc);
  if (lastH && lastH !== c.hourKey) props.deleteProperty(lastH);
  var lastD = props.getProperty('LASTD:' + svc);
  if (lastD && lastD !== c.dayKey) props.deleteProperty(lastD);
  props.setProperty(c.hourKey, String(c.h));
  props.setProperty(c.dayKey, String(c.d));
  props.setProperty('LASTH:' + svc, c.hourKey);
  props.setProperty('LASTD:' + svc, c.dayKey);
}

function storageOk_(bytes) {
  try {
    var limit = DriveApp.getStorageLimit();
    var used = DriveApp.getStorageUsed();
    if (limit > 0 && used + bytes > limit) return false;
  } catch (e) {
    // 取得できなければ作成を試み、失敗したら catch 側で storage-full / quota として返す
  }
  return true;
}

function isUnder_(folder, denyIds) {
  if (!denyIds.length) return false;
  var seen = {};
  var queue = [folder];
  while (queue.length) {
    var f = queue.shift();
    var id = f.getId();
    if (seen[id]) continue;
    seen[id] = true;
    if (denyIds.indexOf(id) >= 0) return true;
    var parents = f.getParents();
    while (parents.hasNext()) queue.push(parents.next());
  }
  return false;
}

/** 初回の設定確認(エディタで実行。書き込みはしない。鍵の値は表示しない) */
function checkSetup() {
  var props = PropertiesService.getScriptProperties();
  var deny = builtinDeny_().concat((props.getProperty('DENY_FOLDERS') || '').split(',').map(function (s) { return s.trim(); })
    .filter(function (s) { return s; }));
  ['FOLDER_CODEX', 'FOLDER_CLAUDE'].forEach(function (p) {
    var id = props.getProperty(p);
    var f = DriveApp.getFolderById(id);
    var parents = f.getParents();
    var parentNames = [];
    while (parents.hasNext()) parentNames.push(parents.next().getName());
    Logger.log(p + ' = ' + f.getName() + ' (親: ' + parentNames.join(',') + ') denied=' + (deny.indexOf(id) >= 0 || isUnder_(f, deny)));
  });
  ['KEY_CODEX', 'KEY_CLAUDE'].forEach(function (p) {
    var k = props.getProperty(p) || '';
    Logger.log(p + ': ' + (k.length >= 32 ? '設定あり' : '未設定または短すぎる'));
  });
  Logger.log('KEY_CODEX と KEY_CLAUDE が同じ値: ' + (props.getProperty('KEY_CODEX') === props.getProperty('KEY_CLAUDE')));
  Logger.log('Drive 使用量: ' + DriveApp.getStorageUsed() + ' / ' + DriveApp.getStorageLimit());
}
