const fs = require("node:fs");
const path = require("node:path");
const {pathToFileURL} = require("node:url");
const {playwright, chromiumPath} = require("../docs/assets/validate.js");

async function main() {
  const root = path.resolve(__dirname, "..");
  const output = path.join(root, ".local", "vector-shots");
  fs.mkdirSync(output, {recursive: true});
  const svg = fs.readFileSync(path.join(root, "docs/assets/javorna/chair.svg"), "utf8");
  const reversed = fs.readFileSync(path.join(root, "docs/assets/javorna/chair-on-dark.svg"), "utf8");
  const box = svg.match(/viewBox="([^"]+)"/)[1].split(" ").map(Number);
  const source = pathToFileURL(path.join(root, "docs/assets/javorna/chair-original.png")).href;
  const browser = await playwright().chromium.launch({executablePath: chromiumPath()});
  try {
    const page = await browser.newPage({viewport: {width: 1400, height: 900}});
    const html = `<style>*{box-sizing:border-box}body{margin:0;font:20px Arial;background:#fff}
      .pair{display:grid;grid-template-columns:1fr 1fr;gap:20px;padding:20px}.tile{background:#FAF5EF}
      .crop{position:relative;aspect-ratio:${box[2]}/${box[3]};overflow:hidden}
      .crop img{position:absolute;width:${512 / box[2] * 100}%;max-width:none;
        left:${-box[0] / box[2] * 100}%;top:${-box[1] / box[3] * 100}%}
      svg{display:block;width:100%;height:auto}.sizes{display:flex;align-items:center;gap:35px;padding:25px}
      .sizes svg{height:auto}.dark{background:#332E27}.light{background:#EEEBDD}</style>
      <div class="pair"><div class="tile">Approved raster<div class="crop"><img src="${source}"></div></div>
      <div class="tile">SVG / ${Buffer.byteLength(svg)} bytes${svg}</div></div>
      <div class="sizes light">${[128,64,32].map(s=>svg.replace("<svg ",`<svg style="width:${s}px" `)).join("")}</div>
      <div class="sizes dark">${[128,64,32].map(s=>svg.replace("<svg ",`<svg style="width:${s}px" `)).join("")}
      <span style="color:#EEEBDD">Original colours</span></div>
      <div class="sizes dark">${[128,64,32].map(s=>reversed.replace("<svg ",`<svg style="width:${s}px" `)).join("")}
      <span style="color:#EEEBDD">Reversed materials / identical geometry</span></div>`;
    const comparison = path.join(output, "comparison.html");
    fs.writeFileSync(comparison, html);
    await page.goto(pathToFileURL(comparison).href);
    await page.locator("img").evaluate(img => img.decode());
    await page.screenshot({path: path.join(output, "comparison.png"), fullPage: true});
    await page.setContent(`<style>body{margin:0;background:transparent}svg{display:block;width:2048px;height:2048px}</style>${svg.replace(/viewBox="[^"]+"/, 'viewBox="0 0 512 512"')}`);
    await page.setViewportSize({width: 2048, height: 2048});
    await page.screenshot({path: path.join(output, "vector-native.png"), omitBackground: true});
  } finally {
    await browser.close();
  }
}
main().catch(error => {console.error(error); process.exitCode = 1;});
