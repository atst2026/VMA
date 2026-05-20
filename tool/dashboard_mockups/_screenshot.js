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
  // Wait for any <img> elements to finish decoding so we don't screenshot
  // a half-loaded page (the previous 900ms timeout was not enough for the
  // index page with five embedded mockup previews).
  await page.evaluate(async () => {
    await Promise.all(
      Array.from(document.images).map(img =>
        img.complete ? Promise.resolve() : new Promise(res => {
          img.addEventListener('load', res, { once: true });
          img.addEventListener('error', res, { once: true });
        })
      )
    );
  });
  await page.waitForTimeout(400);
  await page.screenshot({ path: out, fullPage: false });
  await browser.close();
  console.log(`saved ${out} (${w}x${h})`);
})().catch(e => { console.error(e); process.exit(1); });
