// Headless screenshots of the local app through the Chrome DevTools protocol (Node >= 22: built-in WebSocket).
//
//   node app/screenshot.mjs --url "http://127.0.0.1:18051/#/review/showcase-1" --out shot.png \
//        [--width 1280] [--height 900] [--full] [--theme light|dark] [--gate accept] [--wait 1500] [--click "#sel"]
//
// --gate accept ticks the 18+ checkbox and presses Continue in the page, the way a person would.
// Starts its own Chrome with a throwaway profile on a debug port in 18050-18059 and stops only that process.
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const args = Object.fromEntries(process.argv.slice(2).reduce((acc, a, i, all) => {
  if (a.startsWith("--")) acc.push([a.slice(2), all[i + 1] && !all[i + 1].startsWith("--") ? all[i + 1] : true]);
  return acc;
}, []));
const CHROME = process.env.CHROME || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const PORT = Number(args.port || 18058);
if (PORT === 18000 || PORT === 18002) throw new Error("reserved port");
const W = Number(args.width || 1280), H = Number(args.height || 900);
const profile = mkdtempSync(join(tmpdir(), "hr-shot-"));
const chrome = spawn(CHROME, ["--headless=new", `--remote-debugging-port=${PORT}`, `--user-data-dir=${profile}`,
  "--no-first-run", "--no-default-browser-check", "--hide-scrollbars", `--window-size=${W},${H}`, "about:blank"],
  { stdio: "ignore" });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function target() {
  for (let i = 0; i < 50; i++) {
    try {
      const list = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
      const page = list.find((t) => t.type === "page");
      if (page) return page.webSocketDebuggerUrl;
    } catch { /* not up yet */ }
    await sleep(200);
  }
  throw new Error("Chrome did not start");
}

async function main() {
  const ws = new WebSocket(await target());
  await new Promise((r) => ws.addEventListener("open", r, { once: true }));
  let id = 0;
  const pending = new Map();
  ws.addEventListener("message", (ev) => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
  });
  const send = (method, params = {}) => new Promise((resolve, reject) => {
    const mid = ++id;
    pending.set(mid, (m) => (m.error ? reject(new Error(method + ": " + m.error.message)) : resolve(m.result)));
    ws.send(JSON.stringify({ id: mid, method, params }));
  });
  const evaluate = async (expr) => (await send("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true })).result.value;

  await send("Page.enable");
  await send("Runtime.enable");
  await send("Emulation.setDeviceMetricsOverride", { width: W, height: H, deviceScaleFactor: Number(args.scale || 2), mobile: W < 600 });
  if (args.theme) await send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-color-scheme", value: args.theme }] });
  await send("Page.navigate", { url: args.url });
  await sleep(Number(args.wait || 1500));
  if (args.gate === "accept") {
    await evaluate(`(() => { const d = document.getElementById('gate'); if (!d || !d.open) return 'no gate';
      const c = document.getElementById('gate-age'); c.click(); document.getElementById('gate-ok').click(); return 'accepted'; })()`);
    await sleep(400);
  }
  if (args.click) {
    for (const sel of String(args.click).split("|")) {
      await evaluate(`(() => { const e = document.querySelector(${JSON.stringify(sel)}); if (e) { e.click(); return 1 } return 0 })()`);
      await sleep(400);
    }
  }
  if (args.eval) await evaluate(String(args.eval));
  await evaluate("window.scrollTo(0,0); document.activeElement && document.activeElement.blur && document.activeElement.blur(); 1");
  await sleep(300);
  let clip;
  if (args.full) {
    const m = await send("Page.getLayoutMetrics");
    const ch = Math.ceil(m.cssContentSize.height);
    clip = { x: 0, y: 0, width: W, height: ch, scale: 1 };
  }
  const shot = await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: !!args.full, ...(clip ? { clip } : {}) });
  writeFileSync(args.out, Buffer.from(shot.data, "base64"));
  console.log("saved", args.out);
  ws.close();
}

main().catch((e) => { console.error(e.message); process.exitCode = 1; }).finally(() => {
  chrome.kill("SIGTERM");
  setTimeout(() => { try { rmSync(profile, { recursive: true, force: true }); } catch { /* ignore */ } }, 500);
});
