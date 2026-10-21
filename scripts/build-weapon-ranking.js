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
// キーはプレイヤー×シーズン。分母は同じシーズンの全ブキ記録、分子は対象ブキ系統の記録。
const playerSeasonUsage = new Map();
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
        const playerKey = String(event[1] ?? '');
        const rawWeapon = event[5];
        const power = Number(event[4]);
        if (!playerKey || rawWeapon == null || rawWeapon === '' || !Number.isFinite(power)) continue;
        const weapon = canonicalWeapon(rawWeapon);
        totalRows++;
        usage.set(weapon, (usage.get(weapon) || 0) + 1);
        const playerSeasonKey = JSON.stringify([playerKey, String(season)]);
        let seasonUsage = playerSeasonUsage.get(playerSeasonKey);
        if (!seasonUsage) { seasonUsage = { total: 0, weapons: new Map() }; playerSeasonUsage.set(playerSeasonKey, seasonUsage); }
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
if (!best.size) {
  console.error('No weapon-bearing archive records were found; refusing to write an empty ranking.');
  process.exit(1);
}
const rows = [...best.values()].map(row => {
  const seasonUsage = playerSeasonUsage.get(JSON.stringify([row.player_key, String(row.season)]));
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
  schema_version: 2,
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
