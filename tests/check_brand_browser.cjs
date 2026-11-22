const assert = require("node:assert/strict");
const path = require("node:path");
const fs = require("node:fs");
const {pathToFileURL} = require("node:url");
const {playwright, chromiumPath, frame} = require("../docs/assets/validate.js");

async function main() {
  const root = path.resolve(__dirname, "..");
  const file = path.join(root, "docs", "furniture-review.html");
  const output = path.join(root, ".local", "furniture-shots");
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
        assert.equal(await page.locator(".furniture-option").count(), 3);
        assert.equal(await page.locator(".study-frame").count(), 6);
        assert.equal(await page.locator('a[href^="furniture-slides.html#"]').count(), 6);
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
        const checks = await page.locator(".study-frame").evaluateAll(studies => studies.map(study => {
          const r = study.getBoundingClientRect();
          const photo = study.querySelector(".title-photo");
          const heading = study.querySelector(".title-heading");
          const headingRect = heading?.getBoundingClientRect();
          const subRect = study.querySelector(".title-sub")?.getBoundingClientRect();
          const bottomRect = study.querySelector(".title-bottom")?.getBoundingClientRect();
          return {
            ratio: r.width / r.height,
            overflow: study.scrollWidth > study.clientWidth + 1 || study.scrollHeight > study.clientHeight + 1,
            copySafe: !photo || headingRect.right <= photo.getBoundingClientRect().left,
            footerSafe: !bottomRect || subRect.bottom + 5 < bottomRect.top,
            imageSizes: [...study.querySelectorAll("img")].every(img => img.naturalWidth === 2048 && img.naturalHeight === 2048)
          };
        }));
        for (const check of checks) {
          assert.ok(Math.abs(check.ratio - 16 / 9) < .015, "16:9 composition");
          assert.equal(check.overflow, false, "sample container fits");
          assert.equal(check.copySafe, true, "title remains off photo");
          assert.equal(check.footerSafe, true, "title copy clears presenter");
          assert.equal(check.imageSizes, true, "unaltered native originals");
        }
        assert.deepEqual(errors, []);
        await page.locator(".furniture-options").screenshot({path: path.join(output, `logos-${width}.png`)});
        if (width === 1366) {
          for (let index = 0; index < 6; index++) {
            await page.locator(".study-frame").nth(index).screenshot({path: path.join(output, `sample-${index + 1}.png`)});
          }
        }
      } finally {
        await context.close();
      }
    }
    const context = await browser.newContext({javaScriptEnabled: false, offline: true});
    try {
      const page = await context.newPage();
      await page.goto(pathToFileURL(file).href);
      assert.equal(await page.locator(".study-frame:visible").count(), 6);
      assert.equal(await page.locator(".furniture-option:visible").count(), 3);
    } finally {
      await context.close();
    }
    console.log("Brand gallery passed: four widths, six 16:9 studies, image integrity, copy clearance, offline and no-JS.");
  } finally {
    await browser.close();
  }
}

main().catch(error => {console.error(error); process.exitCode = 1;});
