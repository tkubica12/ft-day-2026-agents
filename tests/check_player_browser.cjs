const assert = require("node:assert/strict");
const path = require("node:path");
const fs = require("node:fs");
const {pathToFileURL} = require("node:url");
const {playwright, chromiumPath} = require("../docs/assets/validate.js");

const root = path.resolve(__dirname, "..");
const files = process.argv.slice(2).length ? process.argv.slice(2).map(f => path.resolve(f))
  : [path.join(root, "docs", "player.html")];
const output = path.join(root, ".local", "player-shots");
const viewports = [[1920, 1080], [1280, 720], [1024, 768]];

async function check(browser, file, [width, height]) {
  const context = await browser.newContext({viewport: {width, height}, offline: true});
  try {
    const page = await context.newPage();
    const errors = [], external = [];
    const self = pathToFileURL(file).href;
    page.on("pageerror", error => errors.push(error.message));
    page.on("request", request => {
      const url = request.url();
      if (!url.startsWith("data:") && !url.startsWith("blob:") && url.split("#")[0] !== self) external.push(url);
    });
    page.on("requestfailed", request => { if (!/\/video\/\d\d\.mp4$/.test(request.url())) errors.push(request.url()); });
    await page.goto(self + "#1", {waitUntil: "load"});
    const total = await page.locator(".slide").count();
    assert.equal(total, 34);
    assert.equal(await page.locator(".slide.video").count(), 14);
    const name = path.basename(file, ".html");
    for (let i = 0; i < total; i++) {
      assert.equal(await page.evaluate(() => [...document.querySelectorAll(".slide")].findIndex(s => s.classList.contains("active"))), i);
      assert.equal(await page.locator("#count").textContent(), `${i + 1} / ${total}`);
      if (await page.locator(".slide.active.video").count()) {
        await page.waitForFunction(() => {
          const s = document.querySelector(".slide.active.video");
          const v = s && s.querySelector("video");
          return s && (s.classList.contains("missing") || (v && v.readyState >= 1));
        }, {timeout: 8000});
      }
      await page.evaluate(() => document.fonts.ready);
      const layout = await page.evaluate(() => {
        const slide = document.querySelector(".slide.active");
        const outside = [...slide.querySelectorAll("*")].filter(el => {
          if (el.closest("video") || el.classList.contains("title-photo") || el.classList.contains("ended-hint")) return false;
          const r = el.getBoundingClientRect();
          if (!r.width || !r.height || getComputedStyle(el).display === "none") return false;
          return r.left < -1 || r.top < -1 || r.right > innerWidth + 1 || r.bottom > innerHeight + 1;
        }).map(el => el.className || el.tagName);
        const brand = slide.querySelector(":scope > .brand"), card = slide.querySelector(".card");
        let overlap = false;
        if (brand && card) {
          const b = brand.getBoundingClientRect(), c = [...card.children].map(x => x.getBoundingClientRect());
          overlap = c.some(r => r.top < b.bottom && r.bottom > b.top && r.left < b.right && r.right > b.left);
        }
        const clipped = [...slide.querySelectorAll(".card, .card *")].some(el => el.scrollWidth > el.clientWidth + 1 && getComputedStyle(el).overflow !== "visible");
        return {title: slide.dataset.title, outside, overlap, clipped};
      });
      assert.deepEqual(layout.outside, [], `${layout.title} @${width}x${height} stays on screen`);
      assert.equal(layout.overlap, false, `${layout.title} @${width}x${height} brand clears content`);
      assert.equal(layout.clipped, false, `${layout.title} @${width}x${height} no clipped text`);
      fs.mkdirSync(output, {recursive: true});
      await page.screenshot({path: path.join(output, `${name}-${width}-${String(i + 1).padStart(2, "0")}.png`)});
      if (i < total - 1) await page.keyboard.press(i % 2 ? "ArrowRight" : "PageDown");
    }
    await page.keyboard.press("Home");
    assert.equal(await page.evaluate(() => location.hash), "#1");
    await page.keyboard.press("End");
    assert.equal(await page.evaluate(() => location.hash), `#${total}`);
    await page.goto(self + "#5");
    await page.reload();
    assert.equal(await page.evaluate(() => document.querySelector(".slide.active").dataset.title), "01 video");
    const isMissing = await page.evaluate(() => document.querySelector(".slide.active").classList.contains("missing"));
    if (isMissing) {
      await page.keyboard.press(" ");
      assert.equal(await page.evaluate(() => location.hash), "#6", "Space skips a pending clip");
    }
    assert.deepEqual(errors, []);
    if (file.includes(`${path.sep}dist${path.sep}`)) assert.deepEqual(external, [], "single file needs no other files");
  } finally {
    await context.close();
  }
}

async function main() {
  const browser = await playwright().chromium.launch({executablePath: chromiumPath()});
  try {
    for (const file of files) for (const viewport of viewports) await check(browser, file, viewport);
    console.log(`Player passed: ${files.length} file(s), ${viewports.length} viewports, 34 slides each, offline.`);
  } finally {
    await browser.close();
  }
}

main().catch(error => {console.error(error); process.exitCode = 1;});
