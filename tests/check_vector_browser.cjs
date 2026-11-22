const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const {pathToFileURL} = require("node:url");
const {playwright, chromiumPath, frame} = require("../docs/assets/validate.js");

async function imageCheck(page) {
  await page.evaluate(() => Promise.all([...document.images].map(img => {
    img.loading = "eager";
    return img.decode();
  })));
  assert.equal(await page.evaluate(() => [...document.images].every(img => img.naturalWidth > 0)), true);
}

async function widgetCheck(page) {
  const boxes = await page.locator(".widget").evaluateAll(items => items.map(item => {
    const r = item.getBoundingClientRect();
    const outside = [...item.querySelectorAll("*")].filter(child => {
      const c = child.getBoundingClientRect();
      return c.width > 0 && (c.left < r.left - 1 || c.right > r.right + 1 || c.bottom > r.bottom + 1);
    }).map(el => el.className);
    return {kind: item.className, ratio: r.width / r.height, outside,
      overflow: item.scrollHeight > item.clientHeight + 1 || item.scrollWidth > item.clientWidth + 1};
  }));
  for (const box of boxes) {
    assert.equal(box.overflow, false, box.kind);
    assert.deepEqual(box.outside, [], box.kind);
    if (box.kind.includes("widget-doc")) assert.ok(Math.abs(box.ratio - 210 / 297) < .01);
    if (box.kind.includes("widget-square")) assert.ok(Math.abs(box.ratio - 1) < .01);
  }
}

async function main() {
  const root = path.resolve(__dirname, "..");
  const shots = path.join(root, ".local/vector-shots");
  fs.mkdirSync(shots, {recursive: true});
  const browser = await playwright().chromium.launch({executablePath: chromiumPath()});
  try {
    for (const width of [390, 768, 1366, 1920]) {
      const context = await browser.newContext({viewport: {width, height: 1080}, offline: true});
      try {
        const page = await context.newPage();
        const errors = [];
        page.on("pageerror", error => errors.push(error.message));
        page.on("requestfailed", request => errors.push(request.url()));
        await page.goto(pathToFileURL(path.join(root, "docs/vector-review.html")).href);
        await frame(page);
        await imageCheck(page);
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `gallery width ${width}`);
        await widgetCheck(page);
        const marks = await page.locator(".sample-mark,.w-mark").evaluateAll(images => images.map(image => ({
          src: image.getAttribute("src"), background: getComputedStyle(image).backgroundColor,
          border: getComputedStyle(image).borderWidth,
        })));
        for (const mark of marks) {
          assert.ok(mark.src.endsWith(".svg"));
          assert.equal(mark.background, "rgba(0, 0, 0, 0)", "no logo tile");
          assert.equal(mark.border, "0px");
        }
        await page.locator(".vector-pair").screenshot({path: path.join(shots, `lockups-${width}.png`)});
        if (width === 1366) {
          await page.locator(".study-frame").nth(0).screenshot({path: path.join(shots, "title.png")});
          await page.locator(".study-frame").nth(1).screenshot({path: path.join(shots, "content.png")});
          await page.locator(".widget-gallery").screenshot({path: path.join(shots, "widgets.png")});
        }
        assert.deepEqual(errors, []);
      } finally { await context.close(); }
    }

    for (const width of [1366, 1920]) {
      const context = await browser.newContext({viewport: {width, height: 1080}, offline: true});
      try {
        const page = await context.newPage();
        const errors = [];
        page.on("pageerror", error => errors.push(error.message));
        page.on("requestfailed", request => errors.push(request.url()));
        for (const theme of ["light", "dark"]) for (const accent of ["blue", "red", "green", "yellow"]) {
          const url = pathToFileURL(path.join(root, "docs/presentation.html")).href;
          await page.goto(`${url}?theme=${theme}&accent=${accent}`);
          await frame(page);
          await imageCheck(page);
          const ids = await page.locator(".slide").evaluateAll(slides => slides.map(s => s.id));
          assert.equal(ids.length, 32);
          for (const id of ids) {
            await page.evaluate(id => {location.hash = id;}, id);
            await frame(page);
            const fit = await page.locator(`#${id}`).evaluate(slide => {
              const r = slide.getBoundingClientRect();
              const outside = [...slide.querySelectorAll("*")].filter(child => {
                const c = child.getBoundingClientRect();
                return c.width > 0 && (c.left < r.left - 1 || c.right > r.right + 1 || c.bottom > r.bottom + 1);
              }).map(child => child.className);
              const body = slide.querySelector(".slide-body");
              return {outside, fit: body ? Number(getComputedStyle(body).getPropertyValue("--fit") || 1) : 1};
            });
            assert.deepEqual(fit.outside, [], `${id} ${theme}/${accent}`);
            assert.ok(fit.fit >= .99, `${id} should not rely on shrinking`);
            if (["s-title", "s-cast", "s-week-three", "s-end"].includes(id)) {
              await page.locator(`#${id}`).screenshot({path: path.join(shots, `${id}-${theme}-${accent}-${width}.png`)});
            }
          }
        }
        assert.deepEqual(errors, []);
      } finally { await context.close(); }
    }

    const context = await browser.newContext({offline: true, javaScriptEnabled: false});
    try {
      const page = await context.newPage();
      await page.goto(pathToFileURL(path.join(root, "docs/vector-review.html")).href);
      assert.equal(await page.locator(".study-frame:visible").count(), 2);
      assert.equal(await page.locator(".widget:visible").count(), 3);
      for (const name of ["decision", "status", "kickoff"]) {
        for (const width of [320, 680, 1920]) {
          await page.setViewportSize({width, height: 1100});
          await page.goto(pathToFileURL(path.join(root, `docs/widgets/${name}.html`)).href);
          await imageCheck(page);
          await widgetCheck(page);
          assert.equal(await page.evaluate(() => [...document.images].every(i => i.src.startsWith("data:image/svg+xml;base64,"))), true);
        }
      }
    } finally { await context.close(); }
    console.log("Vector identity passed: frameless responsive review, 32 slides in 8 appearances at 2 widths, SVG-only offline widgets.");
  } finally { await browser.close(); }
}
main().catch(error => {console.error(error); process.exitCode = 1;});
