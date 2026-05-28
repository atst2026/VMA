// Render the four standalone AI-layout mockups to PNG previews.
// Usage: PLAYWRIGHT_BROWSERS_PATH=~/.cache/ms-playwright node _render.js
const { chromium } = require('/opt/node22/lib/node_modules/playwright/index.js');
const path = require('path');

const DIR = __dirname;
const files = [
  '1-command-console',
  '2-app-workspace',
  '3-daily-briefing',
  '4-bento-intelligence',
];

(async () => {
  const browser = await chromium.launch();
  for (const name of files) {
    const ctx = await browser.newContext({
      viewport: { width: 1440, height: 900 },
      deviceScaleFactor: 2,
    });
    const page = await ctx.newPage();
    await page.goto('file://' + path.join(DIR, name + '.html'), { waitUntil: 'networkidle' });
    // let webfonts settle
    await page.evaluate(() => document.fonts && document.fonts.ready);
    await page.waitForTimeout(600);
    const out = path.join(DIR, 'previews', name + '.png');
    await page.screenshot({ path: out, fullPage: true });
    console.log('saved', out);
    await ctx.close();
  }
  await browser.close();
})().catch(e => { console.error(e); process.exit(1); });
