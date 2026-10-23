#!/usr/bin/env node
'use strict';

// Run from the repository root: node scripts/build-weapon-ranking.js
const fs = require('node:fs');
const path = require('node:path');

// 実行時のカレントフォルダに依存しないよう、リポジトリのルートを自動判定する。
const candidateRoots = [...new Set([process.cwd(), path.resolve(__dirname, '..')])];
const root = candidateRoots.find(candidate =>
  fs.existsSync(path.join(candidate, 'data', 'archive-history')) && fs.existsSync(path.join(candidate, 'app.js'))
) || path.resolve(__dirname, '..');
const archiveRoot = path.join(root, 'data', 'archive-history');
const appPath = path.join(root, 'app.js');
const outputPath = path.join(root, 'data', 'weapon-ranking.json');
const sourceXrankRoot = path.join(root, 'data', 'source-xrank');

const WEAPON_GROUPS = [
  ['スプラシューター', ['スプラシューター','ヒーローシューター レプリカ','ヒーローシューターレプリカ','オーダーシューター レプリカ','オーダーシューターレプリカ','Hero Shot Replica','Order Shot Replica']],
  ['スプラシューターコラボ', ['スプラシューターコラボ','オクタシューター レプリカ','オクタシューターレプリカ','PETシューターレプリカ','Tentatek Splattershot','Octo Shot Replica','PET Shooter Replica']],
  ['スプラローラー', ['スプラローラー','オーダーローラー レプリカ','オーダーローラーレプリカ','Splat Roller','Order Roller Replica']],
  ['スプラチャージャー', ['スプラチャージャー','オーダーチャージャー レプリカ','オーダーチャージャーレプリカ','Splat Charger','Order Charger Replica']],
  ['スプラマニューバー', ['スプラマニューバー','オーダーマニューバー レプリカ','オーダーマニューバーレプリカ','Splat Dualies','Order Dualie Replica']],
  ['パラシェルター', ['パラシェルター','オーダーシェルター レプリカ','オーダーシェルターレプリカ','Splat Brella','Order Brella Replica']],
  ['トライストリンガー', ['トライストリンガー','オーダーストリンガー レプリカ','オーダーストリンガーレプリカ','Tri-Stringer','Order Stringer Replica']],
  ['ジムワイパー', ['ジムワイパー','オーダーワイパー レプリカ','オーダーワイパーレプリカ','Splatana Stamper','Order Splatana Replica']],
  ['バケットスロッシャー', ['バケットスロッシャー','オーダースロッシャー レプリカ','オーダースロッシャーレプリカ','Slosher','Order Slosher Replica']],
  ['ノヴァブラスター', ['ノヴァブラスター','オーダーブラスター レプリカ','オーダーブラスターレプリカ','Luna Blaster','Order Blaster Replica']],
  ['ホクサイ', ['ホクサイ','オーダーブラシ レプリカ','オーダーブラシレプリカ','Octobrush','Orderbrush Replica']],
  ['バレルスピナー', ['バレルスピナー','オーダースピナー レプリカ','オーダースピナーレプリカ','Heavy Splatling','Order Splatling Replica']]
];
const norm = value => String(value ?? '').trim().replace(/\s+/g, ' ').replace(/[’‘]/g, "'").toLocaleLowerCase('en-US');
const groupLookup = new Map();
for (const [canonical, aliases] of WEAPON_GROUPS) for (const alias of aliases) groupLookup.set(norm(alias), canonical);

if (!fs.existsSync(archiveRoot)) {
  console.error(`Archive folder not found: ${archiveRoot}`);
  process.exit(1);
}
if (!fs.existsSync(appPath)) {
  console.error(`app.js not found: ${appPath}`);
  process.exit(1);
}

// app.js の既存日本語武器名辞書を再利用し、フロント側との表記差を防ぐ。
const app = fs.readFileSync(appPath, 'utf8');
const weaponMapMatch = app.match(/const WEAPON_NAMES_JA = (\{[\s\S]*?\});\n\s*const WEAPON_NAMES_JA_NORMALIZED/);
if (!weaponMapMatch) {
  console.error('app.js から WEAPON_NAMES_JA を読み取れません。');
  process.exit(1);
}
let weaponNames;
try { weaponNames = JSON.parse(weaponMapMatch[1]); }
catch (error) {
  console.error('WEAPON_NAMES_JA を JSON として解析できません:', error.message);
  process.exit(1);
}
const weaponNamesNormalized = new Map(Object.entries(weaponNames).map(([name, ja]) => [norm(name), ja]));
function canonicalWeapon(raw) {
  const cleaned = String(raw ?? '').trim().replace(/\s+/g, ' ');
  const ja = weaponNamesNormalized.get(norm(cleaned)) || cleaned;
  return groupLookup.get(norm(ja)) || groupLookup.get(norm(cleaned)) || ja;
}

// Same-person aggregation uses only permanent source identifiers. Four-digit
// nameId values, names, and ranks are never used as identity fallbacks.
function persistentPlayerKey(value) {
  const raw = String(value ?? '').trim();
  if (!raw) return '';
  if (/^uid:[^:]+$/i.test(raw)) return `uid:${raw.slice(4)}`;
  if (/^(?:nameId|name|fest-player|unidentified|unverified|record):/i.test(raw)) return '';
  if (/^id:/i.test(raw)) {
    const inner = raw.slice(3).trim();
    return inner && !/^\d{4}$/.test(inner) ? `id:${inner}` : '';
  }
  if (/^\d{4}$/.test(raw)) return '';
  try {
    const decoded = Buffer.from(raw, 'base64url').toString('utf8');
    const match = decoded.match(/:u-([^:]+)$/);
    if (match) return `uid:${match[1]}`;
  } catch (_) { /* Treat non-base64 source IDs as opaque. */ }
  return `id:${raw}`;
}

function listJsonl(dir) {
  const found = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) found.push(...listJsonl(full));
    else if (entry.isFile() && entry.name.endsWith('.jsonl')) found.push(full);
  }
  return found;
}

const files = listJsonl(archiveRoot);
if (!files.length) {
  console.error(`No .jsonl archive files found under ${archiveRoot}`);
  process.exit(1);
}
const best = new Map();
const usage = new Map();
// キーはプレイヤー×シーズン×ルール。最高XPを記録したルール内だけで使用率を計算。
const playerSeasonRuleUsage = new Map();
const archiveSeasonRules = new Set();
let totalRows = 0;
let filesFailed = 0;
let processedFiles = 0;

for (const file of files) {
  try {
    const content = fs.readFileSync(file, 'utf8');
    for (const line of content.split(/\r?\n/)) {
      if (!line.trim()) continue;
      let day;
      try { day = JSON.parse(line); } catch { continue; }
      const seasonFromPath = path.relative(archiveRoot, file).split(path.sep)[0] || '';
      const ruleFromPath = path.relative(archiveRoot, file).split(path.sep)[1] || '';
      const season = day.season || seasonFromPath;
      const rule = day.rule || ruleFromPath;
      if (!['splatzones', 'towercontrol', 'rainmaker', 'clamblitz'].includes(rule)) continue;
      for (const event of (Array.isArray(day.events) ? day.events : [])) {
        if (!Array.isArray(event) || event.length < 6) continue;
        const rawPlayerKey = String(event[1] ?? '');
        const playerKey = persistentPlayerKey(rawPlayerKey);
        const rawWeapon = event[5];
        const power = Number(event[4]);
        if (rawWeapon == null || rawWeapon === '' || !Number.isFinite(power)) continue;
        const weapon = canonicalWeapon(rawWeapon);
        archiveSeasonRules.add(`${season}\u0000${rule}`);
        totalRows++;
        usage.set(weapon, (usage.get(weapon) || 0) + 1);
        // Keep global weapon counts, but don't create player rows from non-unique keys.
        if (!playerKey) continue;
        const playerSeasonRuleKey = JSON.stringify([playerKey, String(season), rule]);
        let seasonUsage = playerSeasonRuleUsage.get(playerSeasonRuleKey);
        if (!seasonUsage) { seasonUsage = { total: 0, weapons: new Map() }; playerSeasonRuleUsage.set(playerSeasonRuleKey, seasonUsage); }
        seasonUsage.total++;
        seasonUsage.weapons.set(weapon, (seasonUsage.weapons.get(weapon) || 0) + 1);
        const candidate = {
          player_key: playerKey,
          name: String(event[2] ?? '名前不明'),
          rank: event[3] ?? null,
          x_power: power,
          weapon_label: weapon,
          name_id: event[6] ?? null,
          rule,
          season,
          at: event[0] ?? null,
          date: day.date ?? null,
          source: 'archive'
        };
        const key = JSON.stringify([playerKey, weapon]);
        const old = best.get(key);
        const oldRank = Number(old?.rank ?? Infinity), newRank = Number(candidate.rank ?? Infinity);
        if (!old || power > old.x_power || (power === old.x_power && newRank < oldRank)) best.set(key, candidate);
      }
    }
  } catch (error) {
    filesFailed++;
    console.warn(`Failed to read ${path.relative(root, file)}: ${error.message}`);
  }
  processedFiles++;
  if (processedFiles % 250 === 0 || processedFiles === files.length) {
    process.stdout.write(`\rProcessed ${processedFiles.toLocaleString()} / ${files.length.toLocaleString()} archive files`);
  }
}
process.stdout.write('\n');

// assets.splatoon3.ink の P シーズン公開ランキングは、アーカイブが欠けている
// season/rule のみ補完に使う。アーカイブがある組み合わせに混ぜると、1回だけの
// スナップショットが使用率を歪めるため、存在する組み合わせは必ずアーカイブ優先。
const ruleNodeKeys = {
  splatzones: 'xRankingArea',
  towercontrol: 'xRankingLift',
  rainmaker: 'xRankingGl',
  clamblitz: 'xRankingCl'
};
function listJson(dir) {
  if (!fs.existsSync(dir)) return [];
  const found = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) found.push(...listJson(full));
    else if (entry.isFile() && /^xrank\.detail\.p-\d+\.(splatzones|towercontrol|rainmaker|clamblitz)\.json$/i.test(entry.name)) found.push(full);
  }
  return found;
}
let importedRows = 0;
for (const file of listJson(sourceXrankRoot)) {
  try {
    const rel = path.relative(sourceXrankRoot, file).split(path.sep);
    const season = rel[0] || (path.basename(file).match(/xrank\.detail\.(p-\d+)\./i) || [])[1];
    const rule = (path.basename(file).match(/\.(splatzones|towercontrol|rainmaker|clamblitz)\.json$/i) || [])[1];
    if (!season || !rule || archiveSeasonRules.has(`${season}\u0000${rule}`)) continue;
    const payload = JSON.parse(fs.readFileSync(file, 'utf8'));
    const nodeKey = ruleNodeKeys[rule];
    const edges = payload?.data?.node?.[nodeKey]?.edges;
    if (!Array.isArray(edges)) continue;
    for (const edge of edges) {
      const p = edge?.node;
      const power = Number(p?.xPower);
      const rawWeapon = p?.weapon?.name;
      if (!p || !Number.isFinite(power) || !rawWeapon) continue;
      const playerKey = persistentPlayerKey(p.id);
      const weapon = canonicalWeapon(rawWeapon);
      totalRows++;
      importedRows++;
      usage.set(weapon, (usage.get(weapon) || 0) + 1);
      if (!playerKey) continue;
      const playerSeasonRuleKey = JSON.stringify([playerKey, String(season), rule]);
      let seasonUsage = playerSeasonRuleUsage.get(playerSeasonRuleKey);
      if (!seasonUsage) { seasonUsage = { total: 0, weapons: new Map() }; playerSeasonRuleUsage.set(playerSeasonRuleKey, seasonUsage); }
      seasonUsage.total++;
      seasonUsage.weapons.set(weapon, (seasonUsage.weapons.get(weapon) || 0) + 1);
      const candidate = {
        player_key: playerKey,
        name: String(p.name || p.byname || '名前不明'),
        rank: p.rank ?? null,
        x_power: power,
        weapon_label: weapon,
        name_id: p.nameId ?? null,
        rule,
        season,
        at: null,
        date: null,
        source: 'season-endpoint'
      };
      const key = JSON.stringify([playerKey, weapon]);
      const old = best.get(key);
      const oldRank = Number(old?.rank ?? Infinity), newRank = Number(candidate.rank ?? Infinity);
      if (!old || power > old.x_power || (power === old.x_power && newRank < oldRank)) best.set(key, candidate);
    }
  } catch (error) {
    filesFailed++;
    console.warn(`Failed to read ${path.relative(root, file)}: ${error.message}`);
  }
}
console.log(`Imported fallback P-season rows: ${importedRows.toLocaleString()}`);
if (!best.size) {
  console.error('No weapon-bearing archive records were found; refusing to write an empty ranking.');
  process.exit(1);
}
const rows = [...best.values()].map(row => {
  const seasonUsage = playerSeasonRuleUsage.get(JSON.stringify([row.player_key, String(row.season), row.rule]));
  const seasonWeaponCount = seasonUsage?.weapons.get(row.weapon_label) || 0;
  const seasonTotalCount = seasonUsage?.total || 0;
  return {
    ...row,
    season_weapon_count: seasonWeaponCount,
    season_total_count: seasonTotalCount,
    season_usage_rate: seasonTotalCount ? seasonWeaponCount / seasonTotalCount * 100 : 0
  };
});
const payload = {
  schema_version: 3,
  generatedAt: new Date().toISOString(),
  filesTotal: files.length,
  filesFailed,
  totalRows,
  usage: Object.fromEntries([...usage.entries()].sort(([a], [b]) => a.localeCompare(b, 'ja'))),
  rows
};
fs.mkdirSync(path.dirname(outputPath), { recursive: true });
fs.writeFileSync(outputPath, `${JSON.stringify(payload)}\n`);
console.log(`Wrote ${path.relative(root, outputPath)}`);
console.log(`Archive files: ${files.length}; failed: ${filesFailed}; records: ${totalRows}; player/weapon best rows: ${rows.length}`);
console.log(`Size: ${(fs.statSync(outputPath).size / 1024 / 1024).toFixed(2)} MiB`);
