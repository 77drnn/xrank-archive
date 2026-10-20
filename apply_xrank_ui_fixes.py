#!/usr/bin/env python3
"""Apply the requested X Rank Archive UI edits to a local repository checkout.

Run from the root of the xrank-archive repository:
    python apply_xrank_ui_fixes.py
The script writes .bak backups before editing and is safe to run more than once.
"""
from pathlib import Path
import re
import sys

ROOT = Path.cwd()
FILES = {name: ROOT / name for name in ("index.html", "app.js", "style.css", "sw.js")}
missing = [str(p.name) for p in FILES.values() if not p.is_file()]
if missing:
    print("必要なファイルが見つかりません: " + ", ".join(missing), file=sys.stderr)
    print("xrank-archive のルートフォルダーで実行してください。", file=sys.stderr)
    sys.exit(2)

for p in FILES.values():
    original = p.read_text(encoding="utf-8")
    changed = original

    if p.name == "index.html":
        # Replace the decorative slogan with a plain functional heading.
        changed = changed.replace(
            '<h1>ランキングの軌跡を、<em>ひとつに。</em></h1>',
            '<h1>ランキング</h1>'
        )
        changed = changed.replace(
            '<p class="hero-desc">過去シーズンを横断して、順位・Xパワー・使用ブキの変化を探せる非公式アーカイブ。</p>',
            '<p class="hero-desc">シーズン別の順位・Xパワーを検索できます。</p>'
        )
        changed = changed.replace(
            '<p class="eyebrow"><span class="eyebrow-dot"></span> X MATCH / SEASON DATABASE</p>',
            '<p class="eyebrow"><span class="eyebrow-dot"></span> Xマッチランキング</p>'
        )
        # Remove only the standalone past-names page, not player-level name history/search.
        changed = re.sub(
            r'<section class="past-names-section page-view page-hidden" data-page="names".*?</section>\s*(?=<section id="player-panel")',
            '', changed, count=1, flags=re.S
        )
        changed = re.sub(
            r'[ \t]*<button type="button" class="nav-button" data-page-nav="names">過去の名前</button>\s*',
            '', changed, count=1
        )
        changed = changed.replace(
            '<option value="100">上位100</option><option value="300" selected>上位300</option><option value="1000">全件</option>',
            '<option value="100">上位100</option><option value="500" selected>上位500</option><option value="1000">全件</option>'
        )
        # Bust the page's static-asset cache after updating styles and logic.
        changed = changed.replace("style.css?v=8", "style.css?v=9")
        changed = changed.replace("app.js?v=8", "app.js?v=9")

    elif p.name == "app.js":
        changed = changed.replace("Number($('limit-select').value || 300)", "Number($('limit-select').value || 500)")
        changed = changed.replace("$('limit-select').value='300'", "$('limit-select').value='500'")
        # These state fields and functions served only the removed standalone page.
        changed = changed.replace(
            "archiveCache: new Map(), pastNameRows: [], pastNamesVisible: 0, pastNamesLoaded: false, allTimeIndex:",
            "archiveCache: new Map(), allTimeIndex:"
        )
        changed = changed.replace(
            "    if (page === 'names') {\n      const details = $('past-names-details');\n      if (details && !details.open) details.open = true;\n    }\n", ""
        )
        changed = re.sub(
            r'  async function renderPastNames\(reset = false\) \{.*?\n  \}\n(?=  function renderArchiveProgress\(index\) \{)',
            '', changed, count=1, flags=re.S
        )
        # Remove event listeners tied to DOM nodes removed with that page.
        changed = re.sub(r"^\s*\$\('past-names-details'\)\.addEventListener\('toggle'.*?\);\s*\n", "", changed, flags=re.M)
        changed = re.sub(r"^\s*\$\('past-names-body'\)\.addEventListener\('click'.*?\);\s*\n", "", changed, flags=re.M)
        changed = re.sub(r"^\s*\$\('past-names-more'\)\.addEventListener\('click'.*?\);\s*\n", "", changed, flags=re.M)

    elif p.name == "style.css":
        marker = "/* Readability pass v9 */"
        if marker not in changed:
            changed += r'''

/* Readability pass v9 */
:root {
  color-scheme: dark;
  --bg: #10151d;
  --panel: #19222d;
  --panel2: #222e3b;
  --line: #465466;
  --muted: #c0cad7;
  --text: #f8fafc;
  --cyan: #75e2d1;
  --lime: #d7ed9a;
  --pink: #ffabc2;
  --blue: #9fc5ff;
}
body {
  background: #10151d !important;
  color: #f8fafc !important;
}
.hero-art { display: none !important; }
.hero { min-height: 0; }
.hero-copy { max-width: 100%; padding-top: 28px; padding-bottom: 26px; }
.hero-desc { color: #e0e7ef !important; }
.hero-note, .field-hint, .brand small, .stat-foot,
.results-subtitle, .table-footer, .alltime-definition,
.player-meta, .name-period, .status-pill {
  color: #c0cad7 !important;
}
.filter-panel, .results-panel, .stat-card, .alltime-card,
.player-panel, .chart-card, .source-card, .past-names-details {
  background-color: #19222d !important;
  color: #f8fafc !important;
  border-color: #465466 !important;
}
input, select, textarea, .field-control {
  color: #f8fafc !important;
  background-color: #222e3b !important;
  border-color: #58677a !important;
}
input::placeholder { color: #c0cad7 !important; opacity: 1; }
button, .nav-button, .rule-button, .alltime-tab,
.player-subnav-button, .button-outline, .button-quiet {
  color: #f8fafc;
}
.nav-button.active, .rule-button.active, .alltime-tab.active,
.player-subnav-button.active {
  color: #10151d !important;
  background: #d7ed9a !important;
  border-color: #d7ed9a !important;
}
.rank-table th, .history-table th { color: #e7edf5 !important; }
.rank-table td, .history-table td { color: #f1f5f9; }
.rank-table tbody tr:hover, .history-table tbody tr:hover {
  background-color: #263343 !important;
}
a { color: #9fc5ff; }
.name-search-item { color: #f8fafc !important; background: #222e3b !important; border-color: #465466 !important; }
.name-search-item small { color: #c0cad7 !important; }
.empty-state, .chart-empty { color: #d4dce6 !important; }
@media (max-width: 760px) {
  .app-shell { padding-left: 14px; padding-right: 14px; }
  .topbar { height: auto; min-height: 72px; flex-wrap: wrap; gap: 10px; padding: 12px 0; }
  .top-actions { margin-left: auto; }
  .hero-copy { padding: 22px 0; }
}
'''

    elif p.name == "sw.js":
        changed = changed.replace("xrank-shell-v8", "xrank-shell-v9")
        changed = changed.replace("style.css?v=8", "style.css?v=9")
        changed = changed.replace("app.js?v=8", "app.js?v=9")

    if changed != original:
        backup = p.with_suffix(p.suffix + ".bak")
        if not backup.exists():
            backup.write_text(original, encoding="utf-8")
        p.write_text(changed, encoding="utf-8")
        print(f"更新: {p.name} (バックアップ: {backup.name})")
    else:
        print(f"変更なし: {p.name}")

# Guard against a partial or unexpected source version.
html = FILES["index.html"].read_text(encoding="utf-8")
js = FILES["app.js"].read_text(encoding="utf-8")
checks = {
    "standalone past names page removed": 'data-page="names"' not in html,
    "player-level name history retained": 'data-player-subnav="names"' in html and 'id="player-name-history"' in html,
    "historical-name search retained": 'id="name-search-results"' in html and 'renderHistoricalNameSearch' in js,
    "TOP 500 selected by default": '<option value="500" selected>上位500</option>' in html,
    "filter reset selects TOP 500": "$ ('limit-select').value='500'" not in js and "$('limit-select').value='500'" in js,
    "v9 cache active": "xrank-shell-v9" in FILES["sw.js"].read_text(encoding="utf-8"),
}
for description, passed in checks.items():
    print(("OK" if passed else "NG") + ": " + description)
if not all(checks.values()):
    print("一部チェックに失敗しました。変更前の .bak ファイルから復元して確認してください。", file=sys.stderr)
    sys.exit(3)
print("完了。GitHub Desktopで変更を確認して commit / push してください。")
