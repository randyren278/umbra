// Playwright's side of bench.py: one persistent Node process per browser, its best case.
//   node bench/pw.js --browser chromium|webkit --base URL --runs N --warmup W --scale S --out FILE
// Writes {browser, version, playwright, metrics: {name: [ms, …]}, errors: {name: message}} to FILE.
const fs = require('fs');
const os = require('os');
const path = require('path');
const { performance } = require('perf_hooks');
const playwright = require(path.join(__dirname, '.node/node_modules/playwright'));

const opts = {};
for (let i = 2; i < process.argv.length; i += 2) opts[process.argv[i].replace(/^--/, '')] = process.argv[i + 1];
const base = opts.base, runs = Number(opts.runs || 20), warmup = Number(opts.warmup || 2), scale = Number(opts.scale || 1);
const type = playwright[opts.browser];
const contextOptions = { viewport: { width: 1280, height: 900 }, deviceScaleFactor: scale };
const shot = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'umbra-bench-pw-')), 'shot.png');
const result = { browser: opts.browser, playwright: require(path.join(__dirname, '.node/node_modules/playwright/package.json')).version, metrics: {}, errors: {} };

async function time(body) {
  const start = performance.now();
  await body();
  return performance.now() - start;
}

// Runs `step` warmup + runs times; `check` (untimed) proves each run did its job.
async function measure(name, step, check = async () => {}, skip = warmup) {
  try {
    const samples = [];
    for (let i = 0; i < skip + runs; i++) {
      const ms = await time(() => step(i));
      await check(i);
      if (i >= skip) samples.push(ms);
    }
    result.metrics[name] = samples;
  } catch (error) {
    result.errors[name] = String(error.message || error).split('\n')[0];
  }
}

function expect(ok, message) { if (!ok) throw new Error(message); }

(async () => {
  // Cold start, every run (no warm-up): launch, a fresh context (its own temporary profile), a page, the first load.
  let cold;
  await measure('cold', async () => {
    cold = await type.launch({ headless: true });
    await (await (await cold.newContext(contextOptions)).newPage()).goto(base + '/login');
  }, async () => {
    expect(await cold.contexts()[0].pages()[0].title() === 'Sign in · Northwind Ops', 'the login page did not load');
    await cold.close();
  }, 0);

  const browser = await type.launch({ headless: true });
  result.version = browser.version();
  const context = await browser.newContext(contextOptions);
  const page = await context.newPage();
  await page.goto(base + '/__login?next=/app');

  await measure('navigate', () => page.goto(base + '/app'),
    async () => expect(await page.title() === 'Dashboard · Northwind Ops', 'dashboard did not load'));

  await page.goto(base + '/app/orders');
  await page.waitForSelector('#rows tr');
  let tree = '';
  await measure('snapshot', async () => { tree = await page.locator('body').ariaSnapshot(); },
    async () => expect(tree.includes('#1040'), 'snapshot is missing the orders table'));

  await page.goto(base + '/app/settings');
  const toggle = page.locator('#notifications');
  let checked = await toggle.getAttribute('aria-checked');
  await measure('click', () => toggle.click(), async () => {
    const now = await toggle.getAttribute('aria-checked');
    expect(now !== checked, 'switch did not flip');
    checked = now;
  });

  const field = page.locator('#display-name');
  await measure('fill', i => field.fill('Jordan Lee ' + i),
    async i => expect(await field.inputValue() === 'Jordan Lee ' + i, 'field did not take the value'));

  await page.goto(base + '/app');
  await measure('screenshot', () => page.screenshot({ path: shot }), async () => {
    expect(fs.existsSync(shot) && fs.statSync(shot).size > 1000, 'no screenshot written');
    fs.unlinkSync(shot);
  });

  await measure('eval', async () => { result.lastEval = await page.evaluate('1 + 1'); },
    async () => expect(result.lastEval === 2, 'eval returned the wrong value'));
  delete result.lastEval;

  const loadedImages = () => page.evaluate(() => [...document.images].filter(i => i.complete && i.naturalWidth > 0).length);
  await measure('heavy', () => page.goto(base + '/heavy'),
    async () => expect(await loadedImages() === 200, 'not every image loaded'));

  await page.route('**/*.{png,jpg,jpeg,gif,webp,svg}', route => route.abort());
  await measure('heavy_blocked', () => page.goto(base + '/heavy'),
    async () => expect(await loadedImages() === 0, 'images were not blocked'));

  await browser.close();
  fs.rmSync(path.dirname(shot), { recursive: true, force: true });
  fs.writeFileSync(opts.out, JSON.stringify(result));
})().catch(error => { console.error(error); process.exit(1); });
