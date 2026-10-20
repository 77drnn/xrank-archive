(() => {
  'use strict';
  // UI navigation repair build v8: explicit page state + delegated menu handling.
  document.documentElement.dataset.xrankUiBuild = 'v8';
  const RULES = {
    splatzones: { ja: 'ガチエリア', short: 'エリア' },
    towercontrol: { ja: 'ガチヤグラ', short: 'ヤグラ' },
    rainmaker: { ja: 'ガチホコ', short: 'ホコ' },
    clamblitz: { ja: 'ガチアサリ', short: 'アサリ' }
  };
  const $ = (id) => document.getElementById(id);
  const state = { index: null, rows: [], allRows: [], currentPlayer: null, currentPage: 'ranking', previousPage: 'ranking', selectedPlayerSubpage: 'overview', selectedRule: 'splatzones', dataCache: new Map(), loadedRules: new Set(), hourlyIndex: null, nameIndex: null, nameIndexPromise: null, archiveCache: new Map(), allTimeIndex: null, selectedAllTime: 'overall', allTimeRows: [] };
  const seasonOrder = (a, b) => {
    const aa = String(a).match(/^([a-z]+)-(\d+)$/i) || ['', '', '0'];
    const bb = String(b).match(/^([a-z]+)-(\d+)$/i) || ['', '', '0'];
    return Number(bb[2]) - Number(aa[2]) || bb[1].localeCompare(aa[1]);
  };
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const fmt = (n) => n === null || n === undefined || n === '' || !Number.isFinite(Number(n)) ? '—' : Number(n).toLocaleString('ja-JP', {maximumFractionDigits: 1});
  function seasonLabel(value) {
    const match = String(value ?? '').match(/^p-(\d+)$/i);
    if (!match) return String(value ?? '—');
    const number = Number(match[1]);
    // P-2 starts in December 2022; each season advances by 3 months.
    const startIndex = 2022 * 12 + 11 + (number - 2) * 3;
    const endIndex = startIndex + 3;
    const sy = Math.floor(startIndex / 12), sm = startIndex % 12 + 1;
    const ey = Math.floor(endIndex / 12), em = endIndex % 12 + 1;
    return `${sy}-${sm}～${ey}-${em}`;
  }
  function setStatus(text, ready = false) {
    const el = $('source-status'); el.textContent = text; el.classList.toggle('ready', ready);
    const dot = document.createElement('i'); el.prepend(dot);
  }
  function toast(message) {
    document.querySelector('.toast')?.remove();
    const el = document.createElement('div'); el.className = 'toast'; el.textContent = message; document.body.append(el);
    setTimeout(() => el.remove(), 3600);
  }
  function setActivePage(page, scroll = true) {
    const availablePages = new Set(Array.from(document.querySelectorAll('[data-page-nav]'), button => button.dataset.pageNav));
    if (!availablePages.has(page)) page = 'ranking';
    if (page === 'player' && !state.currentPlayer) return;

    state.currentPage = page;
    document.documentElement.dataset.activePage = page;
    document.querySelectorAll('.page-view').forEach(view => {
      const active = view.dataset.page === page;
      view.classList.toggle('page-hidden', !active);
      view.setAttribute('aria-hidden', String(!active));
    });
    document.querySelectorAll('[data-page-nav]').forEach(button => {
      const active = button.dataset.pageNav === page;
      button.classList.toggle('active', active);
      button.setAttribute('aria-pressed', String(active));
      if (active) button.setAttribute('aria-current', 'page');
      else button.removeAttribute('aria-current');
      if (button.dataset.pageNav === 'player') button.disabled = !state.currentPlayer;
    });
    if (scroll) window.scrollTo({top: 0, behavior: window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth'});
  }
  function setPlayerSubpage(view, scroll = false) {
    state.selectedPlayerSubpage = view;
    document.querySelectorAll('.player-view-section').forEach(section => {
      section.classList.toggle('player-section-hidden', section.dataset.playerView !== view);
    });
    document.querySelectorAll('[data-player-subnav]').forEach(button => {
      const active = button.dataset.playerSubnav === view;
      button.classList.toggle('active', active);
      if (active) button.setAttribute('aria-current', 'page');
      else button.removeAttribute('aria-current');
    });
    if (scroll) $('player-panel').scrollIntoView({behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'start'});
  }

  function rankDiff(value) {
    if (value === null || value === undefined || value === '') return '<span class="diff-value diff-neutral">—</span>';
    const n = Number(value);
    if (!Number.isFinite(n) || n === 0) return `<span class="diff-value diff-neutral">${Number.isFinite(n) ? '±0' : esc(value)}</span>`;
    // rankDiff is shown as supplied by the source; interpretation may vary by source version.
    return `<span class="diff-value ${n > 0 ? 'diff-positive' : 'diff-negative'}">${n > 0 ? '+' : ''}${fmt(n)}</span>`;
  }
  function rankBadge(rank) {
    const r = Number(rank);
    const cl = r === 1 ? 'gold' : r === 2 ? 'silver' : r === 3 ? 'bronze' : '';
    return `<span class="rank-number ${cl}">${fmt(r)}</span>`;
  }
  function nodeKey(row) { return String(row.player_key || row.id || (row.name_id ? `nameId:${row.name_id}` : `name:${row.name || ''}`)); }
  function playerHistoryShard(playerKey) {
    let hash = 2166136261;
    const bytes = new TextEncoder().encode(String(playerKey));
    for (const byte of bytes) hash = Math.imul(hash ^ byte, 16777619) >>> 0;
    return hash % 32;
  }
  function normalizeLoaded(row, season, rule) {
    return {...row, season: row.season || season, rule: row.rule || rule, player_key: nodeKey(row)};
  }
  async function loadPlayerNameIndex() {
    if (state.nameIndex) return state.nameIndex;
    if (!state.nameIndexPromise) {
      state.nameIndexPromise = (async () => {
        try {
          const res = await fetch(`data/player-names-index.json?ts=${Date.now()}`, {cache:'no-store'});
          if (!res.ok) throw new Error(`player-names-index.json: HTTP ${res.status}`);
          state.nameIndex = await res.json();
        } catch (e) {
          console.warn('Historical player names are not available yet', e);
          state.nameIndex = {players:{}};
        }
        return state.nameIndex;
      })();
    }
    return state.nameIndexPromise;
  }
  function dateLabel(stamp, withTime = false) {
    if (!stamp) return '日時未確認';
    const d = new Date(stamp);
    if (!Number.isFinite(d.getTime())) return String(stamp);
    return d.toLocaleString('ja-JP', withTime
      ? {year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}
      : {year:'numeric',month:'2-digit',day:'2-digit'});
  }
  function archiveRow(row, snapshot) {
    if (Array.isArray(row)) return {
      player_key:String(row[0] ?? ''), name:String(row[1] ?? ''), rank:row[2], x_power:row[3],
      weapon:row[4], name_id:row[5], season:snapshot.season, rule:snapshot.rule, at:snapshot.at, date:snapshot.date
    };
    return {...row, season:snapshot.season, rule:snapshot.rule, at:snapshot.at, date:snapshot.date, player_key:nodeKey(row)};
  }
  let historicalNameSearchTimer = null;
  async function renderHistoricalNameSearch() {
    const host = $('name-search-results');
    const query = $('search-input').value.trim().toLocaleLowerCase();
    if (query.length < 2) { host.classList.add('hidden'); host.innerHTML=''; return; }
    host.classList.remove('hidden');
    host.innerHTML = '<div class="name-search-title">過去の名前を検索中…</div>';
    const idx = await loadPlayerNameIndex();
    // Ignore stale async searches when the user has typed a newer query.
    if ($('search-input').value.trim().toLocaleLowerCase() !== query) return;
    const hits=[];
    for (const player of Object.values(idx.players || {})) {
      const names = Array.isArray(player.names) ? player.names : [];
      const alias = names.find(n => String(n.name || '').toLocaleLowerCase().includes(query));
      const idHit = String(player.key || '').toLocaleLowerCase().includes(query) || String(player.name_id || '').toLocaleLowerCase().includes(query);
      if (alias || idHit) hits.push({player, hit:alias?.name || player.name || player.key, seen:alias?.last_seen || ''});
    }
    hits.sort((a,b)=>String(b.seen).localeCompare(String(a.seen)) || String(a.player.name).localeCompare(String(b.player.name),'ja'));
    const unique = hits.filter((hit,i,arr)=>arr.findIndex(other=>other.player.key===hit.player.key)===i).slice(0,15);
    if (!unique.length) {
      host.innerHTML = '<div class="name-search-title">過去の名前に一致するプレイヤーはいません。</div>';
      return;
    }
    host.innerHTML = `<div class="name-search-title">過去の名前で一致したプレイヤー（${unique.length}件）</div>` + unique.map(({player,hit})=>
      `<button type="button" class="name-search-item" data-historical-player-key="${esc(player.key)}"><strong>${esc(player.name || '名前不明')}</strong><small>一致した名前: ${esc(hit)} · ${esc(player.key)}</small></button>`
    ).join('');
  }
  async function renderPastNames(reset = false) {
    if (reset) { state.pastNamesLoaded = false; state.pastNameRows = []; state.pastNamesVisible = 0; }
    const body = $('past-names-body');
    if (!state.pastNamesLoaded) {
      body.innerHTML = '<tr><td colspan="5" class="empty-state">過去の名前を集計中…</td></tr>';
      const idx = await loadPlayerNameIndex();
      const rows=[];
      for (const player of Object.values(idx.players || {})) {
        for (const alias of (Array.isArray(player.names) ? player.names : [])) {
          rows.push({key:String(player.key || ''),name:String(alias.name || ''),current:String(player.name || ''),
            first_seen:alias.first_seen || '',last_seen:alias.last_seen || '',seasons:alias.seasons || [],rules:alias.rules || []});
        }
      }
      rows.sort((a,b)=>String(b.last_seen || '').localeCompare(String(a.last_seen || '')) || String(a.name).localeCompare(String(b.name),'ja'));
      state.pastNameRows=rows; state.pastNamesLoaded=true;
      $('past-names-summary').textContent=`公開アーカイブから見つかった名前 ${rows.length.toLocaleString('ja-JP')} 件。名前を選ぶとそのプレイヤーの履歴を開きます。`;
    }
    const next=state.pastNamesVisible + 100;
    state.pastNamesVisible=Math.min(next,state.pastNameRows.length);
    const visible=state.pastNameRows.slice(0,state.pastNamesVisible);
    if (!visible.length) {
      body.innerHTML='<tr><td colspan="5" class="empty-state">まだ名前履歴がありません。GitHub Actionsで過去アーカイブの取り込みが進むと一覧が増えます。</td></tr>';
    } else {
      body.innerHTML=visible.map(row=>{
        const contexts=[...(row.seasons||[]).map(x=>seasonLabel(x)),...(row.rules||[]).map(x=>RULES[x]?.short || x)];
        return `<tr><td><button class="past-name-open" type="button" data-historical-player-key="${esc(row.key)}">${esc(row.name || '名前不明')}</button></td><td>${esc(row.current || '—')}</td><td>${esc(row.first_seen ? dateLabel(row.first_seen) : '日時不明')}</td><td>${esc(row.last_seen ? dateLabel(row.last_seen) : '日時不明')}</td><td>${esc([...new Set(contexts)].join('・') || '—')}</td></tr>`;
      }).join('');
    }
    $('past-names-count').textContent=`${state.pastNamesVisible.toLocaleString('ja-JP')} / ${state.pastNameRows.length.toLocaleString('ja-JP')} 件を表示`;
    $('past-names-more').classList.toggle('hidden',state.pastNamesVisible>=state.pastNameRows.length);
  }

  function renderArchiveProgress(index) {
    const banner = $('archive-progress');
    if (!banner) return;
    const totalRaw = index.archive_days_total;
    const total = Number.isFinite(Number(totalRaw)) && totalRaw !== null && totalRaw !== '' ? Number(totalRaw) : null;
    const processed = Math.max(0, Number(index.archive_days_processed || 0));
    const remainingRaw = index.archive_days_remaining;
    const remaining = Number.isFinite(Number(remainingRaw)) && remainingRaw !== null && remainingRaw !== '' ? Number(remainingRaw) : null;
    const complete = Boolean(index.archive_backfill_complete);
    const started = Boolean(index.archive_backfill_started);
    const pct = complete ? 100 : (total && total > 0 ? Math.min(99, Math.floor(processed / total * 100)) : 0);
    $('archive-progress-fill').style.width = `${pct}%`;
    banner.classList.toggle('complete', complete);
    if (complete) {
      $('archive-progress-count').textContent = `${processed.toLocaleString('ja-JP')} / ${(total ?? processed).toLocaleString('ja-JP')} 日 · 完了`;
      $('archive-progress-note').textContent = '公開元で確認できる全日付の初期取り込みが完了しました。以後は毎時の自動収集で新しい記録を追加します。名前・XP履歴は公開アーカイブに記録された範囲に限られます。';
    } else if (started && total !== null) {
      $('archive-progress-count').textContent = `${processed.toLocaleString('ja-JP')} / ${total.toLocaleString('ja-JP')} 日 · ${pct}%`;
      $('archive-progress-note').textContent = `${remaining !== null ? `残り約 ${remaining.toLocaleString('ja-JP')} 日分。` : ''}各クラウド実行で最大30分ぶん取り込み、途中経過を保存して次回の毎時実行で自動再開します。最近の記録を優先しながら古い日付へ遡ります。`;
    } else if (started) {
      $('archive-progress-count').textContent = '全日付を調査中';
      $('archive-progress-note').textContent = '公開元のアーカイブ一覧を作成中です。完了すると、最初に残っている日付から全期間を順次取り込みます。';
    } else {
      $('archive-progress-count').textContent = '初回データ取得待ち';
      $('archive-progress-note').textContent = '初回のクラウド収集で公開元の全アーカイブを調査し、Pシーズンの名前・順位・Xパワー履歴を過去へ遡って取り込みます。';
    }
  }

  async function loadAllTimeRanking(force = false) {
    if (state.allTimeIndex && !force) { renderAllTimeRanking(); return state.allTimeIndex; }
    const body = $('alltime-ranking-body');
    if (body) body.innerHTML = '<tr><td colspan="7" class="empty-state">全期間の最高Xパワー記録を読み込んでいます…</td></tr>';
    try {
      const res = await fetch(`data/all-time-ranking.json?ts=${Date.now()}`, {cache:'no-store'});
      if (!res.ok) throw new Error(`all-time-ranking.json: HTTP ${res.status}`);
      state.allTimeIndex = await res.json();
      const rows = [];
      for (const rule of Object.keys(RULES)) {
        for (const row of (state.allTimeIndex.rules?.[rule]?.players || [])) rows.push(row);
      }
      for (const row of (state.allTimeIndex.overall?.players || [])) rows.push(row);
      state.allTimeRows = [...new Map(rows.map(row=>[String(row.player_key || ''), row])).values()];
      renderAllTimeRanking();
      return state.allTimeIndex;
    } catch (err) {
      console.warn('All-time power rankings are not available yet', err);
      if (body) body.innerHTML = `<tr><td colspan="7" class="empty-state"><span class="empty-glyph">X</span><strong>全期間ランキングはまだありません</strong><p>${esc(err.message)}。GitHub Actionsで初回データ取得が進み、all-time-ranking.jsonが生成されると表示されます。</p></td></tr>`;
      $('alltime-updated').textContent = 'データ未生成';
      $('alltime-footer').textContent = '初回のクラウド収集後に表示されます';
      return null;
    }
  }

  function renderAllTimeRanking() {
    const idx = state.allTimeIndex;
    if (!idx) return;
    const view = state.selectedAllTime || 'overall';
    const overall = view === 'overall';
    const group = overall ? idx.overall : idx.rules?.[view];
    const rows = Array.isArray(group?.players) ? group.players : [];
    const maxLabel = overall ? 1000 : 500;
    const headerName = overall ? '総合' : (RULES[view]?.ja || view);
    document.querySelectorAll('.alltime-tab').forEach(button=>button.classList.toggle('active',button.dataset.alltimeView===view));
    $('alltime-updated').textContent = idx.generated_at ? `集計 ${dateLabel(idx.generated_at, true)}` : '集計日時不明';
    $('alltime-definition').textContent = overall
      ? `総合はプレイヤーごとに4ルールの全期間ベストXPのうち最高値を1件採用し、重複を除外。上位${maxLabel.toLocaleString('ja-JP')}人を表示します。XP同点時は記録時の順位、プレイヤー識別子の順で並びます。`
      : `${headerName}のみを対象に、Pシーズン全期間で各プレイヤーが記録した最高Xパワーを集計。各プレイヤーは1回だけ登場し、上位${maxLabel}人を表示します。アーカイブに時刻がある記録は日時も表示します。`;
    $('result-count').setAttribute('aria-label','通常ランキング件数');
    const body = $('alltime-ranking-body');
    if (!rows.length) {
      body.innerHTML = '<tr><td colspan="7" class="empty-state">まだランキング記録がありません。初回アーカイブの取り込みが進むと一覧が作られます。</td></tr>';
      $('alltime-footer').textContent = `${headerName} · 0人`;
      return;
    }
    body.innerHTML = rows.map((p,i)=>{
      const key = String(p.player_key || '');
      const initials = Array.from(String(p.name || '?')).slice(0,1).join('').toUpperCase();
      const ruleLabel = RULES[p.rule]?.short || '—';
      const season = p.season ? seasonLabel(p.season) : '—';
      const observedAt = p.at ? dateLabel(p.at, true) : (p.source === 'season-endpoint' ? '日時未収録' : '—');
      const nameNote = p.name_at_record && p.name_at_record !== p.name ? `<span class="player-small">記録時: ${esc(p.name_at_record)}</span>` : (p.name_id ? `<span class="player-small">ID ${esc(p.name_id)}</span>` : '');
      return `<tr><td>${rankBadge(i+1)}</td><td><button class="player-link" type="button" data-alltime-player-key="${esc(key)}" aria-label="${esc(p.name)} の履歴を表示"><span class="avatar">${esc(initials)}</span><span><span class="player-name">${esc(p.name || '名前不明')}</span>${nameNote}</span></button></td><td><span class="power-value">${fmt(p.x_power)}</span></td><td>${esc(ruleLabel)}</td><td>${esc(season)}</td><td>${p.rank_at_record ? `#${fmt(p.rank_at_record)}` : '—'}</td><td>${esc(observedAt)}</td></tr>`;
    }).join('');
    const total = Number(group?.total_players || rows.length);
    $('alltime-footer').textContent = `${headerName} · ${rows.length.toLocaleString('ja-JP')} / ${Math.min(maxLabel,total).toLocaleString('ja-JP')} 件表示 · 全対象 ${total.toLocaleString('ja-JP')} 人`;
  }

  async function loadIndex() {
    setStatus('データを読込中…');
    try {
      const res = await fetch(`data/index.json?ts=${Date.now()}`, {cache: 'no-store'});
      if (!res.ok) throw new Error(`data/index.json: HTTP ${res.status}`);
      const index = await res.json();
      if (!Array.isArray(index.seasons) || index.seasons.length === 0) throw new Error('ランキングデータが空です');
      state.index = index;
      renderArchiveProgress(index);
      const seasons = [...index.seasons].sort((a,b) => seasonOrder(a.key,b.key));
      state.index.seasons = seasons;
      $('season-select').innerHTML = seasons.map((s,i)=>`<option value="${esc(s.key)}">${esc(seasonLabel(s.key))}${i === 0 ? '（最新）' : ''}</option>`).join('');
      $('stat-seasons').textContent = fmt(seasons.length);
      $('stat-snapshots').textContent = fmt(seasons.reduce((n,s)=>n+Object.keys(s.rules || {}).length,0));
      $('stat-updated').textContent = index.generated_at ? new Date(index.generated_at).toLocaleString('ja-JP',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}) : '未記録';
      $('stat-source').textContent = '出典: splatoon3.ink';
      setStatus(index.archive_backfill_complete ? '全履歴準備完了' : 'ランキング準備完了', true);
      await loadSeasonRows(seasons[0]?.key);
      updatePlayerCount();
      renderRanking();
    } catch (err) {
      console.error(err); setStatus('データ未読込');
      $('season-select').innerHTML = '<option value="">データがありません</option>';
      $('ranking-body').innerHTML = `<tr><td colspan="5" class="empty-state"><span class="empty-glyph">X</span><strong>ランキングデータが見つかりません</strong><p>${esc(err.message)}。初回のデータ収集がまだ終わっていない可能性があります。GitHub Actions の実行結果を確認してください。</p></td></tr>`;
      $('result-count').textContent = '0';
    }
  }
  async function loadRuleRows(seasonKey, rule, force = false) {
    const season = state.index?.seasons.find(s => s.key === seasonKey);
    const ref = season?.rules?.[rule];
    if (!season || !ref) return [];
    const key = `${seasonKey}/${rule}`;
    if (state.loadedRules.has(key) && !force) return state.dataCache.get(key) || [];
    const url = typeof ref === 'string' ? ref : ref.url;
    if (!url) return [];
    if (force) state.dataCache.delete(key);
    let normalized = [];
    try {
      const res = await fetch(`${url}${url.includes('?') ? '&' : '?'}ts=${Date.now()}`, {cache: 'no-store'});
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const json = await res.json();
      const players = Array.isArray(json) ? json : (json.players || json.ranking || []);
      normalized = players.map(p => normalizeLoaded(p, seasonKey, rule));
    } catch (e) {
      console.warn(`Skip ${key}`, e);
    }
    state.dataCache.set(key, normalized);
    state.loadedRules.add(key);
    state.allRows = state.allRows.filter(p => !(p.season === seasonKey && p.rule === rule)).concat(normalized);
    return normalized;
  }
  async function loadSeasonRows(seasonKey) {
    if (!seasonKey) return;
    await Promise.all(Object.keys(RULES).map(rule => loadRuleRows(seasonKey, rule)));
  }
  async function loadAllRows(rule = state.selectedRule) {
    // Past-season data are loaded only after a player is opened, improving mobile first-load time.
    const seasons = state.index?.seasons || [];
    for (let i=0; i<seasons.length; i+=6) {
      await Promise.all(seasons.slice(i,i+6).map(season => loadRuleRows(season.key, rule)));
    }
  }
  function updatePlayerCount() {
    const latest = state.index?.latest_season || state.index?.seasons?.[0]?.key;
    const unique = new Set(state.allRows.filter(p => p.season === latest).map(nodeKey));
    $('stat-players').textContent = fmt(unique.size);
  }
  function selectedSeason() { return $('season-select').value || state.index?.seasons?.[0]?.key; }
  function getFilteredRows() {
    const season = selectedSeason(), rule = state.selectedRule;
    const query = $('search-input').value.trim().toLocaleLowerCase();
    const limit = Number($('limit-select').value || 500);
    let rows = state.allRows.filter(p => p.season === season && p.rule === rule);
    if (query) rows = rows.filter(p => [p.name,p.id,p.player_key,p.name_id,p.discriminator,p.byname].some(v => String(v ?? '').toLocaleLowerCase().includes(query)));
    rows.sort((a,b)=>Number(a.rank ?? 1e10)-Number(b.rank ?? 1e10));
    if (limit > 0) rows = rows.slice(0,limit);
    return rows;
  }
  function renderRanking() {
    if (!state.index) return;
    const season = selectedSeason(), rule = state.selectedRule, info = RULES[rule];
    const meta = state.index.seasons.find(s=>s.key===season);
    $('result-kicker').textContent = `${seasonLabel(season)} / X MATCH`;
    $('result-title').textContent = `${seasonLabel(season)} · ${info.ja}`;
    $('result-subtitle').textContent = `収録データ ${meta?.rules?.[rule]?.count ?? '—'} 人 / 出典: splatoon3.ink`;
    const rows = getFilteredRows(); state.rows = rows;
    $('result-count').textContent = fmt(rows.length);
    $('table-footer-text').textContent = `${seasonLabel(season)} · ${info.ja} · 表示 ${rows.length.toLocaleString('ja-JP')} 人`;
    if (!rows.length) {
      $('ranking-body').innerHTML = '<tr><td colspan="5" class="empty-state"><span class="empty-glyph">?</span><strong>該当するプレイヤーがいません</strong><p>検索語を変えるか、別のシーズン・ルールを選択してください。</p></td></tr>'; return;
    }
    $('ranking-body').innerHTML = rows.map(p=>{
      const key = nodeKey(p), rank = Number(p.rank);
      const initials = Array.from(String(p.name || '?')).slice(0,1).join('').toUpperCase();
      const byname = typeof p.byname === 'string' ? p.byname : (p.byname?.name || '');
      const secondary = p.name_id ? `ID ${p.name_id}` : byname;
      return `<tr><td>${rankBadge(rank)}</td><td><button class="player-link" type="button" data-player-key="${esc(key)}" aria-label="${esc(p.name)} の履歴を表示"><span class="avatar">${esc(initials)}</span><span><span class="player-name">${esc(p.name || '名前不明')}</span>${secondary ? `<span class="player-small">${esc(secondary)}</span>` : ''}</span></button></td><td><span class="power-value">${fmt(p.x_power)}</span></td><td>${rankDiff(p.rank_diff)}</td><td><span class="weapon-name">${esc(p.weapon || '—')}</span></td></tr>`;
    }).join('');
  }
  function allSnapshotsFor(key, rule) {
    return state.allRows.filter(p=>nodeKey(p)===key && p.rule===rule).sort((a,b)=>seasonOrder(a.season,b.season));
  }
  async function showPlayer(key) {
    if (state.currentPage !== 'player') state.previousPage = state.currentPage || 'ranking';
    const selectedRow = state.rows.find(p => nodeKey(p) === key) || state.allRows.find(p => nodeKey(p) === key) || state.allTimeRows.find(p => nodeKey(p) === key);
    const idx = await loadPlayerNameIndex();
    const nameMeta = idx.players?.[key];
    if (!selectedRow && !nameMeta) return;
    state.currentPlayer = {
      key,
      name:selectedRow?.name || nameMeta?.name || '名前不明',
      name_id:selectedRow?.name_id || selectedRow?.discriminator || nameMeta?.name_id || ''
    };
    $('player-title').textContent = state.currentPlayer.name;
    $('player-meta').textContent = `ランキング識別子: ${key}${state.currentPlayer.name_id ? ` · nameId: ${state.currentPlayer.name_id}` : ''}`;
    $('history-rule').value = state.selectedRule;
    $('player-panel').classList.remove('hidden');
    setActivePage('player', false);
    setPlayerSubpage('overview', false);
    $('history-chart').innerHTML = '<div class="chart-empty">過去シーズンを読み込んでいます…</div>';
    $('archive-chart').innerHTML = '<div class="chart-empty">公開アーカイブを読み込んでいます…</div>';
    $('player-name-history').innerHTML = '<div class="chart-empty">過去の名前を読み込んでいます…</div>';
    $('hourly-chart').innerHTML = '<div class="chart-empty">時間別の記録を読み込んでいます…</div>';
    $('player-panel').scrollIntoView({behavior:'smooth',block:'start'});
    await Promise.all([renderPlayerHistory(), renderHourlyHistory(), renderArchiveHistory(), renderNameHistory()]);
  }
  async function renderNameHistory() {
    if (!state.currentPlayer) return;
    const idx = await loadPlayerNameIndex();
    const meta = idx.players?.[state.currentPlayer.key];
    const aliases = Array.isArray(meta?.names) ? [...meta.names] : [];
    aliases.sort((a,b)=>String(a.first_seen || '9999').localeCompare(String(b.first_seen || '9999')) || String(a.name).localeCompare(String(b.name),'ja'));
    if (!aliases.length) {
      $('player-name-history').innerHTML = `<div class="player-name-entry"><div><strong>${esc(state.currentPlayer.name)}</strong><span class="name-period">アーカイブで確認できる名前の履歴はまだありません。</span></div><span class="name-current">表示名</span></div>`;
      return;
    }
    $('player-name-history').innerHTML = aliases.map(alias=>{
      const first=dateLabel(alias.first_seen), last=dateLabel(alias.last_seen);
      const period=alias.first_seen && alias.last_seen && String(alias.first_seen).slice(0,10)===String(alias.last_seen).slice(0,10)
        ? first
        : `${first} 〜 ${last}`;
      const seasons=(alias.seasons || []).map(x=>seasonLabel(x)).join('・');
      const rules=(alias.rules || []).map(x=>RULES[x]?.short || x).join('・');
      const extra=[seasons,rules].filter(Boolean).join(' / ');
      return `<div class="player-name-entry"><div><strong>${esc(alias.name || '名前不明')}</strong><span class="name-period">${esc(period)}${extra ? ` · ${esc(extra)}` : ''}</span></div>${alias.name===state.currentPlayer.name?'<span class="name-current">現在表示</span>':''}</div>`;
    }).join('');
  }
  async function renderArchiveHistory() {
    if (!state.currentPlayer) return;
    const host=$('archive-chart'), body=$('archive-history-body'), rule=$('history-rule').value;
    const idx=await loadPlayerNameIndex();
    const meta=idx.players?.[state.currentPlayer.key];
    const candidates=[];
    for (const [season,rules] of Object.entries(meta?.history_months || {})) {
      for (const month of (rules?.[rule] || [])) candidates.push({season,month});
    }
    const files=[...new Map(candidates.map(x=>[`${x.season}/${rule}/${x.month}`,x])).values()]
      .sort((a,b)=>seasonOrder(a.season,b.season) || a.month.localeCompare(b.month));
    if (!files.length) {
      $('archive-history-meta').textContent='公開元の過去アーカイブから見つかった順位・XP記録はありません。';
      host.innerHTML='<div class="chart-empty">公開アーカイブに記録がありません。</div>';
      body.innerHTML='<tr><td colspan="6" class="empty-state">このプレイヤーの過去アーカイブ記録はありません。</td></tr>';
      return;
    }
    const entries=[];
    for (let i=0;i<files.length;i+=4) {
      const batch=files.slice(i,i+4);
      const groups=await Promise.all(batch.map(async file=>{
        const shard=String(playerHistoryShard(state.currentPlayer.key)).padStart(2,'0');
        const path=`data/archive-history/${encodeURIComponent(file.season)}/${encodeURIComponent(rule)}/${encodeURIComponent(file.month)}-s${shard}.jsonl`;
        if (!state.archiveCache.has(path)) {
          const p=(async()=>{
            const res=await fetch(`${path}?ts=${Date.now()}`,{cache:'no-store'});
            if(!res.ok) return [];
            const text=await res.text(); const records=[];
            for(const line of text.split(/\r?\n/)) { if(!line.trim()) continue; try { records.push(JSON.parse(line)); } catch(e){} }
            return records;
          })();
          state.archiveCache.set(path,p);
        }
        try { return await state.archiveCache.get(path); } catch(e) { return []; }
      }));
      for (const records of groups) {
        for(const dayRecord of records) {
          if(dayRecord.rule!==rule) continue;
          for (const event of (dayRecord.events || [])) {
            if (!Array.isArray(event) || String(event[1] ?? '')!==state.currentPlayer.key) continue;
            const row={player_key:String(event[1] ?? ''),name:event[2],rank:event[3],x_power:event[4],weapon:event[5],name_id:event[6]};
            entries.push({...row,at:event[0],date:dayRecord.date,season:dayRecord.season,rule:dayRecord.rule});
          }
        }
      }
    }
    entries.sort((a,b)=>String(a.at || a.date).localeCompare(String(b.at || b.date)));
    $('archive-history-meta').textContent=`${RULES[rule].ja} · 公開アーカイブ内で変化があった時刻付きスナップショット ${entries.length} 件（名前の履歴は全スナップショットから抽出）`;
    drawArchiveChart(entries);
    body.innerHTML=entries.length ? [...entries].reverse().slice(0,100).map(p=>`<tr><td>${esc(dateLabel(p.at || p.date))}</td><td>${esc(seasonLabel(p.season))}</td><td>#${fmt(p.rank)}</td><td>${fmt(p.x_power)}</td><td>${esc(p.name || '—')}</td><td>${esc(p.weapon || '—')}</td></tr>`).join('') : '<tr><td colspan="6" class="empty-state">保存済みの日次アーカイブで、このルールの記録が見つかりませんでした。</td></tr>';
  }
  async function renderPlayerHistory() {
    if (!state.currentPlayer) return;
    const rule = $('history-rule').value;
    await loadAllRows(rule);
    const entries = allSnapshotsFor(state.currentPlayer.key, rule);
    const first = entries.length ? entries[entries.length-1] : null;
    const latest = entries[0] || null;
    const bestPower = entries.reduce((v,p)=>Number.isFinite(Number(p.x_power)) ? Math.max(v,Number(p.x_power)) : v, -Infinity);
    const bestRank = entries.reduce((v,p)=>Number.isFinite(Number(p.rank)) ? Math.min(v,Number(p.rank)) : v, Infinity);
    const powerChange = first && latest && first.x_power != null && latest.x_power != null ? Number(latest.x_power)-Number(first.x_power) : null;
    $('player-kpis').innerHTML = [
      ['掲載シーズン数',fmt(entries.length)],['最新順位',latest ? `#${fmt(latest.rank)}` : '—'],['最高Xパワー',Number.isFinite(bestPower)?fmt(bestPower):'—'],['追跡期間のXP差',powerChange==null?'—':`${powerChange>0?'+':''}${fmt(powerChange)}`]
    ].map(([a,b])=>`<div class="player-kpi"><span>${a}</span><strong>${b}</strong></div>`).join('');
    drawChart(entries);
    $('history-body').innerHTML = entries.length ? entries.map(p=>`<tr><td>${esc(seasonLabel(p.season))}</td><td>#${fmt(p.rank)}</td><td>${fmt(p.x_power)}</td><td>${rankDiff(p.rank_diff)}</td><td>${esc(p.weapon || '—')}</td></tr>`).join('') : '<tr><td colspan="5" class="empty-state">このルールの記録は見つかりません。</td></tr>';
  }
  async function loadHourlyIndex() {
    if (state.hourlyIndex) return state.hourlyIndex;
    try {
      const res = await fetch(`data/hourly-index.json?ts=${Date.now()}`, {cache:'no-store'});
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      state.hourlyIndex = await res.json();
    } catch (e) {
      state.hourlyIndex = {latest_season:state.index?.latest_season,seasons:{}};
    }
    return state.hourlyIndex;
  }
  async function renderHourlyHistory() {
    if (!state.currentPlayer) return;
    const host = $('hourly-chart');
    const body = $('hourly-body');
    const rule = $('history-rule').value;
    const hindex = await loadHourlyIndex();
    const season = hindex.latest_season || state.index?.latest_season;
    const months = hindex.seasons?.[season]?.[rule] || [];
    if (!months.length) {
      $('hourly-meta').textContent = '自動収集を公開してからランキングに変化があった時点を記録します。過去へ遡った時間別データはありません。';
      host.innerHTML = '<div class="chart-empty">まだ時間別データがありません。</div>';
      body.innerHTML = '<tr><td colspan="4" class="empty-state">クラウドでの収集開始後、ランキングが変化するとここに記録されます。</td></tr>';
      return;
    }
    const entries = [];
    for (const month of months) {
      try {
        const url = `data/hourly/${encodeURIComponent(season)}/${encodeURIComponent(rule)}/${encodeURIComponent(month)}.jsonl`;
        const res = await fetch(`${url}?ts=${Date.now()}`, {cache:'no-store'});
        if (!res.ok) continue;
        const text = await res.text();
        for (const line of text.split(/\r?\n/)) {
          if (!line.trim()) continue;
          const snapshot = JSON.parse(line);
          const rows = (snapshot.players || []).map(p => Array.isArray(p) ? ({player_key:p[0], name:p[1], rank:p[2], x_power:p[3], weapon:p[4], name_id:p[5]}) : p);
          const row = rows.find(p => nodeKey(p) === state.currentPlayer.key);
          if (row) entries.push({...row, at:snapshot.at, season:snapshot.season || season});
        }
      } catch (e) { console.warn('Hourly history could not be loaded', e); }
    }
    entries.sort((a,b)=>String(a.at).localeCompare(String(b.at)));
    $('hourly-meta').textContent = `${seasonLabel(season)} / ${RULES[rule].ja} · 変化が検出された記録 ${entries.length} 件`;
    drawHourlyChart(entries);
    body.innerHTML = entries.length ? entries.slice(-30).reverse().map(p=>`<tr><td>${esc(new Date(p.at).toLocaleString('ja-JP',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}))}</td><td>#${fmt(p.rank)}</td><td>${fmt(p.x_power)}</td><td>${esc(p.weapon || '—')}</td></tr>`).join('') : '<tr><td colspan="4" class="empty-state">このプレイヤーは現在シーズンの収録済みランキングに見つかりません。</td></tr>';
  }
  function drawHourlyChart(entries) {
    const host = $('hourly-chart');
    const usable = [...entries].filter(p=>Number.isFinite(Number(p.x_power)) && p.at).sort((a,b)=>String(a.at).localeCompare(String(b.at)));
    if (usable.length < 2) { host.innerHTML = `<div class="chart-empty">時間別グラフには少なくとも2回の記録が必要です（現在 ${usable.length} 件）。</div>`; return; }
    const W=920,H=220,L=48,R=18,T=17,B=39,plotW=W-L-R,plotH=H-T-B;
    const values=usable.map(p=>Number(p.x_power)),min=Math.floor((Math.min(...values)-10)/50)*50,max=Math.ceil((Math.max(...values)+10)/50)*50,span=Math.max(50,max-min);
    const px=i=>L+(i/(usable.length-1))*plotW, py=v=>T+((max-v)/span)*plotH;
    const grid=[]; for(let j=0;j<=4;j++){const v=min+span*j/4,y=py(v);grid.push(`<line x1="${L}" y1="${y}" x2="${W-R}" y2="${y}" stroke="#d7dce2" stroke-dasharray="3 5"/><text x="${L-10}" y="${y+4}" text-anchor="end" fill="#7b8491" font-size="10">${Math.round(v)}</text>`)}
    const points=usable.map((p,i)=>`${px(i)},${py(Number(p.x_power))}`).join(' ');
    const step=Math.max(1,Math.floor(usable.length/30));
    const circles=usable.map((p,i)=>i%step===0||i===usable.length-1?`<circle cx="${px(i)}" cy="${py(Number(p.x_power))}" r="3" fill="#475569" stroke="#ffffff" stroke-width="1.5"><title>${esc(new Date(p.at).toLocaleString('ja-JP'))}: ${fmt(p.x_power)} XP · #${fmt(p.rank)}</title></circle>`:'').join('');
    const startLabel=new Date(usable[0].at).toLocaleDateString('ja-JP',{month:'2-digit',day:'2-digit'}),endLabel=new Date(usable[usable.length-1].at).toLocaleDateString('ja-JP',{month:'2-digit',day:'2-digit'});
    host.innerHTML=`<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="時間別Xパワーの推移">${grid.join('')}<polyline points="${points}" fill="none" stroke="#475569" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>${circles}<text x="${L}" y="${H-13}" text-anchor="start" fill="#667085" font-size="10">${startLabel}</text><text x="${W-R}" y="${H-13}" text-anchor="end" fill="#667085" font-size="10">${endLabel}</text></svg>`;
  }
  function drawArchiveChart(entries) {
    const host=$('archive-chart');
    const usable=[...entries].filter(p=>Number.isFinite(Number(p.x_power)) && (p.at||p.date)).sort((a,b)=>String(a.at||a.date).localeCompare(String(b.at||b.date)));
    if(!usable.length){host.innerHTML='<div class="chart-empty">グラフにできるアーカイブ記録がありません。</div>';return;}
    if(usable.length===1){host.innerHTML=`<div class="chart-empty">${esc(dateLabel(usable[0].at||usable[0].date))}：${fmt(usable[0].x_power)} XP · #${fmt(usable[0].rank)}</div>`;return;}
    const W=920,H=220,L=48,R=18,T=17,B=39,plotW=W-L-R,plotH=H-T-B;
    const values=usable.map(p=>Number(p.x_power)),min=Math.floor((Math.min(...values)-10)/50)*50,max=Math.ceil((Math.max(...values)+10)/50)*50,span=Math.max(50,max-min);
    const px=i=>L+(i/(usable.length-1))*plotW,py=v=>T+((max-v)/span)*plotH;
    const grid=[];for(let j=0;j<=4;j++){const v=min+span*j/4,y=py(v);grid.push(`<line x1="${L}" y1="${y}" x2="${W-R}" y2="${y}" stroke="#d7dce2" stroke-dasharray="3 5"/><text x="${L-10}" y="${y+4}" text-anchor="end" fill="#7b8491" font-size="10">${Math.round(v)}</text>`)}
    const points=usable.map((p,i)=>`${px(i)},${py(Number(p.x_power))}`).join(' '),step=Math.max(1,Math.floor(usable.length/35));
    const circles=usable.map((p,i)=>i%step===0||i===usable.length-1?`<circle cx="${px(i)}" cy="${py(Number(p.x_power))}" r="3" fill="#475569" stroke="#ffffff" stroke-width="1.5"><title>${esc(dateLabel(p.at||p.date))}: ${fmt(p.x_power)} XP · #${fmt(p.rank)}</title></circle>`:'').join('');
    const first=dateLabel(usable[0].at||usable[0].date),last=dateLabel(usable[usable.length-1].at||usable[usable.length-1].date);
    host.innerHTML=`<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="公開アーカイブのXパワー推移">${grid.join('')}<polyline points="${points}" fill="none" stroke="#475569" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>${circles}<text x="${L}" y="${H-13}" text-anchor="start" fill="#667085" font-size="10">${esc(first)}</text><text x="${W-R}" y="${H-13}" text-anchor="end" fill="#667085" font-size="10">${esc(last)}</text></svg>`;
  }
  function drawChart(entries) {
    const host = $('history-chart');
    const usable = [...entries].filter(p=>Number.isFinite(Number(p.x_power))).sort((a,b)=>seasonOrder(a.season,b.season));
    if (usable.length < 1) { host.innerHTML = '<div class="chart-empty">グラフにできるXパワーの記録がありません。</div>'; return; }
    const W=920,H=220,L=48,R=18,T=17,B=39,plotW=W-L-R,plotH=H-T-B;
    const values=usable.map(p=>Number(p.x_power)),min=Math.floor((Math.min(...values)-10)/50)*50,max=Math.ceil((Math.max(...values)+10)/50)*50;
    const span=Math.max(50,max-min);
    const px=i=>L+(usable.length===1?plotW/2:(i/(usable.length-1))*plotW);
    const py=v=>T+((max-v)/span)*plotH;
    const grid=[];
    for(let j=0;j<=4;j++){const v=min+span*j/4,y=py(v);grid.push(`<line x1="${L}" y1="${y}" x2="${W-R}" y2="${y}" stroke="#d7dce2" stroke-dasharray="3 5"/><text x="${L-10}" y="${y+4}" text-anchor="end" fill="#7b8491" font-size="10">${Math.round(v)}</text>`)}
    const points=usable.map((p,i)=>`${px(i)},${py(Number(p.x_power))}`).join(' ');
    const circles=usable.map((p,i)=>`<circle cx="${px(i)}" cy="${py(Number(p.x_power))}" r="4" fill="#475569" stroke="#ffffff" stroke-width="2"><title>${esc(seasonLabel(p.season))}: ${fmt(p.x_power)} XP · 順位 ${fmt(p.rank)}</title></circle>`).join('');
    const labels=usable.map((p,i)=>`<text x="${px(i)}" y="${H-13}" text-anchor="middle" fill="#667085" font-size="10">${esc(seasonLabel(p.season))}</text>`).join('');
    host.innerHTML=`<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Xパワーのシーズン別推移">${grid.join('')}<polyline points="${points}" fill="none" stroke="#475569" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>${circles}${labels}</svg>`;
  }
  // Delegate menu clicks at document level so nav controls still work if the
  // header is re-rendered or the menu is moved by a responsive layout.
  document.addEventListener('click', e => {
    const button = e.target instanceof Element ? e.target.closest('[data-page-nav]') : null;
    if (!button || button.disabled) return;
    e.preventDefault();
    setActivePage(button.dataset.pageNav);
  });
  $('player-subnav').addEventListener('click', e => {
    const button = e.target.closest('[data-player-subnav]');
    if (button) setPlayerSubpage(button.dataset.playerSubnav);
  });
  $('rule-tabs').addEventListener('click', e=>{const b=e.target.closest('[data-rule]');if(!b)return;state.selectedRule=b.dataset.rule;document.querySelectorAll('.rule-button').forEach(x=>x.classList.toggle('active',x===b));renderRanking();});
  $('season-select').addEventListener('change',async()=>{await loadSeasonRows(selectedSeason());updatePlayerCount();renderRanking();});
  $('search-input').addEventListener('input',()=>{renderRanking();clearTimeout(historicalNameSearchTimer);historicalNameSearchTimer=setTimeout(()=>renderHistoricalNameSearch(),220);});
  $('limit-select').addEventListener('change',renderRanking);
  $('clear-filters').addEventListener('click',async()=>{$('search-input').value='';$('name-search-results').classList.add('hidden');$('name-search-results').innerHTML='';$('limit-select').value='500';if(state.index)$('season-select').value=state.index.seasons[0]?.key||'';state.selectedRule='splatzones';document.querySelectorAll('.rule-button').forEach(x=>x.classList.toggle('active',x.dataset.rule==='splatzones'));await loadSeasonRows(selectedSeason());updatePlayerCount();renderRanking();});
  $('ranking-body').addEventListener('click',e=>{const b=e.target.closest('[data-player-key]');if(b)showPlayer(b.dataset.playerKey);});
  $('name-search-results').addEventListener('click',e=>{const b=e.target.closest('[data-historical-player-key]');if(b)showPlayer(b.dataset.historicalPlayerKey);});
  $('alltime-tabs').addEventListener('click',e=>{const b=e.target.closest('[data-alltime-view]');if(!b)return;state.selectedAllTime=b.dataset.alltimeView;renderAllTimeRanking();});
  $('alltime-ranking-body').addEventListener('click',e=>{const b=e.target.closest('[data-alltime-player-key]');if(b)showPlayer(b.dataset.alltimePlayerKey);});
  $('history-rule').addEventListener('change',()=>{renderPlayerHistory();renderHourlyHistory();renderArchiveHistory();});
  $('close-player').addEventListener('click',()=>setActivePage(state.previousPage || 'ranking'));
  $('reload-button').addEventListener('click',async()=>{state.dataCache.clear();state.loadedRules.clear();state.allRows=[];state.index=null;state.hourlyIndex=null;state.allTimeIndex=null;state.nameIndex=null;state.nameIndexPromise=null;await Promise.all([loadIndex(),loadAllTimeRanking(true)]);toast('ランキングと全期間最高Xパワーを読み直しました。');});
  setActivePage('ranking', false);
  setPlayerSubpage('overview', false);
  loadIndex();
  loadAllTimeRanking();
})();
