// QC harness for the radar_v2 mockups.
// For each mockup: load from file://, capture the resting state, then click
// the first [data-qa="lead-trigger"] and capture the expanded call-file
// state. Console errors and missing QA hooks are reported per file.
// Usage: node mockups/radar_v2/_shared/qc_screenshot.js [file.html ...]
const { chromium } = require('/opt/node22/lib/node_modules/playwright/index.js');
const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const OUT = path.join(ROOT, 'previews');

(async () => {
  const files = process.argv.length > 2
    ? process.argv.slice(2)
    : fs.readdirSync(ROOT).filter(f => f.endsWith('.html') && f !== 'index.html').sort();
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch();
  const report = [];
  for (const f of files) {
    const name = path.basename(f, '.html');
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, ignoreHTTPSErrors: true });
    const page = await ctx.newPage();
    const errors = [];
    page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
    page.on('pageerror', e => errors.push(String(e)));
    try {
      await page.goto('file://' + path.join(ROOT, path.basename(f)), { waitUntil: 'load' });
      await page.waitForTimeout(5200); // let boot sequences/typewriters/entrances settle
      await page.screenshot({ path: path.join(OUT, name + '.png') });
      const trig = page.locator('[data-qa="lead-trigger"]').first();
      const trigCount = await page.locator('[data-qa="lead-trigger"]').count();
      let portfolioVisible = false;
      if (trigCount > 0) {
        await trig.click({ force: true });
        await page.waitForTimeout(2200);
        // Some concepts need a second step (e.g. intro card -> first lead).
        const port = page.locator('[data-qa="portfolio"]');
        portfolioVisible = (await port.count()) > 0 && await port.first().isVisible().catch(() => false);
        if (!portfolioVisible && trigCount > 1) {
          await page.locator('[data-qa="lead-trigger"]').nth(1).click({ force: true }).catch(() => {});
          await page.waitForTimeout(2000);
          portfolioVisible = await port.first().isVisible().catch(() => false);
        }
        await page.screenshot({ path: path.join(OUT, name + '_open.png') });
      }
      report.push({ file: f, triggers: trigCount, portfolioVisible, errors });
    } catch (e) {
      report.push({ file: f, fatal: String(e), errors });
    }
    await ctx.close();
  }
  await browser.close();
  for (const r of report) {
    const status = r.fatal ? 'FATAL' : (r.errors.length ? 'ERRORS' : 'ok');
    console.log(`[${status}] ${r.file} triggers=${r.triggers ?? '-'} portfolio=${r.portfolioVisible ?? '-'}`);
    if (r.fatal) console.log('   fatal: ' + r.fatal);
    for (const e of (r.errors || []).slice(0, 4)) console.log('   err: ' + e);
  }
})();
