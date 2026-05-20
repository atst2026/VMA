// Render the live landing page via the running Flask app and screenshot it.
// Usage: node tool/dashboard_mockups/_screenshot.js <url> <out.png> [width] [height]
const { chromium } = require('/opt/node22/lib/node_modules/playwright/index.js');

(async () => {
  const url = process.argv[2];
  const out = process.argv[3];
  const w = parseInt(process.argv[4] || '1440', 10);
  const h = parseInt(process.argv[5] || '900', 10);
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: w, height: h } });
  const page = await ctx.newPage();
  await page.goto(url, { waitUntil: 'networkidle' });
  await page.waitForTimeout(900);
  await page.screenshot({ path: out, fullPage: false });
  await browser.close();
  console.log(`saved ${out} (${w}x${h})`);
})().catch(e => { console.error(e); process.exit(1); });
