const assert = require("node:assert/strict");
const path = require("node:path");
const fs = require("node:fs");
const {pathToFileURL} = require("node:url");
const {playwright, chromiumPath, frame} = require("../docs/assets/validate.js");

async function main() {
  const root = path.resolve(__dirname, "..");
  const file = path.join(root, "docs", "logo-review.html");
  const output = path.join(root, ".local", "logo-shots");
  fs.mkdirSync(output, {recursive: true});
  const browser = await playwright().chromium.launch({executablePath: chromiumPath()});
  try {
    for (const width of [390, 768, 1366, 1920]) {
      const context = await browser.newContext({viewport: {width, height: 1080}, offline: true});
      try {
        const page = await context.newPage();
        const errors = [];
        page.on("pageerror", error => errors.push(error.message));
        page.on("requestfailed", request => errors.push(request.url()));
        await page.goto(pathToFileURL(file).href, {waitUntil: "load"});
        await frame(page);
        await page.locator(".iteration-log summary").click();
        await page.evaluate(() => Promise.all([...document.images].map(img => {
          img.loading = "eager";
          return img.complete ? null : new Promise(done => { img.onload = img.onerror = done; });
        })));
        assert.equal(await page.locator(".logo-card").count(), 3);
        assert.equal(await page.locator(".study-frame").count(), 6);
        assert.equal(await page.locator(".widget").count(), 3);
        assert.equal(await page.locator(".iteration").count(), 16);
        assert.equal(await page.locator(".iteration.chosen").count(), 3);
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
        const integrity = await page.evaluate(() => [...document.images].every(img => img.naturalWidth === 2048 && img.naturalHeight === 2048));
        assert.equal(integrity, true, "every image is an unaltered native original");
        const studies = await page.locator(".study-frame").evaluateAll(items => items.map(item => {
          const r = item.getBoundingClientRect();
          return {ratio: r.width / r.height, overflow: item.scrollHeight > item.clientHeight + 1};
        }));
        for (const study of studies) {
          assert.ok(Math.abs(study.ratio - 16 / 9) < .015, "16:9 study");
          assert.equal(study.overflow, false, "study content fits");
        }
        const widgets = await page.locator(".widget").evaluateAll(items => items.map(item => {
          const r = item.getBoundingClientRect();
          const inside = [...item.querySelectorAll("*")].every(child => {
            const c = child.getBoundingClientRect();
            return c.width === 0 || (c.left >= r.left - 1 && c.right <= r.right + 1 && c.bottom <= r.bottom + 1);
          });
          return {kind: item.className, ratio: r.width / r.height, inside,
                  overflow: item.scrollHeight > item.clientHeight + 1 || item.scrollWidth > item.clientWidth + 1};
        }));
        for (const widget of widgets) {
          if (widget.kind.includes("widget-doc")) assert.ok(Math.abs(widget.ratio - 210 / 297) < .01, "A4 widget");
          if (widget.kind.includes("widget-square")) assert.ok(Math.abs(widget.ratio - 1) < .01, "square widget");
          assert.equal(widget.overflow, false, `${widget.kind} fits`);
          assert.equal(widget.inside, true, `${widget.kind} children stay inside`);
        }
        assert.deepEqual(errors, []);
        await page.locator(".logo-finalists").screenshot({path: path.join(output, `logos-${width}.png`)});
        await page.locator(".widget-gallery").screenshot({path: path.join(output, `widgets-${width}.png`)});
        if (width === 1366) {
          for (let index = 0; index < 6; index++) {
            await page.locator(".study-frame").nth(index).screenshot({path: path.join(output, `study-${index + 1}.png`)});
          }
          await page.locator(".iteration-log").screenshot({path: path.join(output, "iterations.png")});
        }
      } finally {
        await context.close();
      }
    }
    const context = await browser.newContext({javaScriptEnabled: false, offline: true});
    try {
      const page = await context.newPage();
      await page.goto(pathToFileURL(file).href);
      assert.equal(await page.locator(".logo-card:visible").count(), 3);
      assert.equal(await page.locator(".study-frame:visible").count(), 6);
      assert.equal(await page.locator(".widget:visible").count(), 3);
    } finally {
      await context.close();
    }
    console.log("Logo review passed: four widths, 6 studies, 3 widgets fit, 16 originals intact, offline and no-JS.");
  } finally {
    await browser.close();
  }
}

main().catch(error => {console.error(error); process.exitCode = 1;});
