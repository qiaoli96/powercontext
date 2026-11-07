// Drive the real UI in an isolated Chromium profile with WebGPU disabled.
import { spawn } from "node:child_process";
import { mkdir, mkdtemp, readFile, writeFile } from "node:fs/promises";
import { cpus, release, tmpdir, totalmem } from "node:os";
import { join } from "node:path";

const chrome = process.env.CHROME_BIN || (process.platform === "win32"
  ? "C:/Program Files/Google/Chrome/Application/chrome.exe"
  : "chromium");
const origin = process.env.STEPFUN_URL || "http://127.0.0.1:5173";
const profile = await mkdtemp(join(tmpdir(), "stepfun-bench-"));
const browser = spawn(chrome, [
  "--headless=new", "--disable-gpu", "--disable-features=WebGPU",
  "--no-first-run", "--no-default-browser-check",
  "--remote-debugging-port=0", `--user-data-dir=${profile}`, origin,
], { windowsHide: true, stdio: "ignore" });
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
let socket;

try {
  let port;
  for (let i = 0; !port && i < 100; i++) {
    try {
      port = (await readFile(join(profile, "DevToolsActivePort"), "utf8")).split(/\r?\n/)[0];
    } catch {
      await delay(200);
    }
  }
  if (!port) throw new Error("Chromium did not expose a debugging port.");
  const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
  const page = targets.find((target) => target.type === "page" && target.url.startsWith(origin));
  if (!page) throw new Error(`Could not find the app tab: ${targets.map((target) => target.url).join(", ")}`);
  socket = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {
    socket.addEventListener("open", resolve, { once: true });
    socket.addEventListener("error", reject, { once: true });
  });
  const pending = new Map();
  let sequence = 0;
  socket.addEventListener("message", ({ data }) => {
    const message = JSON.parse(data);
    if (!pending.has(message.id)) return;
    const { resolve, reject } = pending.get(message.id);
    pending.delete(message.id);
    if (message.error) reject(new Error(message.error.message));
    else resolve(message.result);
  });
  const send = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++sequence;
    pending.set(id, { resolve, reject });
    socket.send(JSON.stringify({ id, method, params }));
  });
  async function evaluate(fn, ...args) {
    const result = await send("Runtime.evaluate", {
      expression: `(${fn})(...${JSON.stringify(args)})`,
      awaitPromise: true, returnByValue: true,
    });
    if (result.exceptionDetails) throw new Error(result.exceptionDetails.exception?.description || result.exceptionDetails.text);
    return result.result.value;
  }
  let pageReady;
  for (let i = 0; !pageReady?.ready && i < 75; i++) {
    try {
      pageReady = await evaluate(() => ({
        ready: Boolean(document.getElementById("kb")) && document.readyState === "complete",
        url: location.href, title: document.title,
      }));
    } catch (error) {
      if (!/context/i.test(error.message)) throw error;
    }
    if (!pageReady?.ready) await delay(200);
  }
  if (!pageReady?.ready) throw new Error(`Unexpected application page: ${JSON.stringify(pageReady)}`);
  const gpu = await evaluate(async () => ({
    exposed: Boolean(navigator.gpu), adapter: Boolean(await navigator.gpu?.requestAdapter()),
    userAgent: navigator.userAgent,
  }));
  if (gpu.adapter) throw new Error("This browser still has a WebGPU adapter.");
  await evaluate(() => {
    const create = URL.createObjectURL.bind(URL);
    URL.createObjectURL = (blob) => {
      if (blob.type === "application/json") window.stepfunMetrics = blob.text();
      return create(blob);
    };
    document.addEventListener("click", (e) => {
      if (e.target instanceof HTMLAnchorElement && e.target.download === "stepfun-timings.json") e.preventDefault();
    }, true);
  });
  const corpus = [
    ["battery.md", "Battery health improves when a laptop limits charging to 80 percent."],
    ["bread.md", "Sourdough bread ferments overnight before shaping and baking."],
    ["database.md", "Hybrid search combines keyword matching with vector similarity."],
    ["network.md", "A wireless router can improve coverage by choosing a less crowded channel."],
    ["telescope.md", "An optical telescope collects faint light from distant galaxies."],
    ["plants.md", "Plants use sunlight and chlorophyll for photosynthesis."],
    ["cycling.md", "A bicycle chain needs lubrication to reduce friction and wear."],
    ["music.md", "A metronome helps musicians maintain a steady tempo when practicing."],
  ];
  const queries = [
    ["battery charge limit", "battery.md"], ["overnight sourdough fermentation", "bread.md"],
    ["keyword and semantic retrieval", "database.md"], ["wireless router channel", "network.md"],
    ["observe distant galaxies", "telescope.md"], ["chlorophyll sunlight", "plants.md"],
    ["lubricate bicycle chain", "cycling.md"], ["practice steady tempo", "music.md"],
  ];
  const kb = `bench-${Date.now()}`;
  const set = (config) => evaluate((values) => {
    for (const [id, value] of Object.entries(values)) {
      const input = document.getElementById(id);
      input.value = value;
      input.dispatchEvent(new Event("change"));
    }
  }, config);
  const wait = (button) => evaluate(async (id) => {
    const control = document.getElementById(id);
    const deadline = Date.now() + 900_000;
    while (control.disabled && Date.now() < deadline) await new Promise((resolve) => setTimeout(resolve, 200));
    if (control.disabled) throw new Error("UI operation timed out: " + document.getElementById("status").textContent);
    return document.getElementById("status").textContent;
  }, button);
  async function importFiles(files) {
    await evaluate((rows) => {
      const data = new DataTransfer();
      for (const [name, text] of rows) data.items.add(new File([text], name, { type: "text/plain" }));
      document.getElementById("files").files = data.files;
      document.getElementById("import").click();
    }, files);
    const status = await wait("import");
    if (!status.startsWith("索引已刷新")) throw new Error(status);
    return status;
  }
  async function search(query) {
    await evaluate((value) => {
      document.getElementById("query").value = value;
      document.getElementById("search").requestSubmit();
    }, query);
    const status = await wait("import");
    if (!status.startsWith("seekdb 返回")) throw new Error(status);
    return evaluate(() => [...document.querySelectorAll("#results article strong")].map((node) => node.textContent));
  }
  const quality = {};
  for (const precision of ["q8", "fp32"]) {
    await set({ kb, precision, device: "auto", batch: "4" });
    console.log(precision, "first import", await importFiles(corpus));
    for (const batch of ["1", "4", "4", "1", "1", "4"]) {
      await set({ batch });
      await importFiles(corpus);
    }
    quality[precision] = [];
    for (let round = 1; round <= 3; round++) {
      for (const [query, expected] of queries) {
        quality[precision].push({ round, query, expected, files: await search(query) });
      }
    }
    console.log(precision, "quality queries", quality[precision].length);
  }
  await set({ precision: "q8", kb });
  const increment = ["meteorite.md", "Meteorite analysis uses a nickel iron alloy signature to identify fragments from space."];
  const incrementalStatus = await importFiles([increment]);
  const needle = "meteorite nickel iron";
  const afterImport = await search(needle);
  await set({ kb: `${kb}-isolated` });
  await importFiles([["other.md", "This isolated knowledge base stores only a recipe for pumpkin soup."]]);
  const isolated = await search(needle);
  await set({ kb });
  const afterSwitchBack = await search(needle);
  if (!afterImport.includes(increment[0]) || !afterSwitchBack.includes(increment[0]) ||
      isolated.length !== 1 || isolated[0] !== "other.md") {
    throw new Error("Incremental indexing or knowledge-base isolation failed.");
  }
  const timings = await evaluate(async () => {
    document.getElementById("download").click();
    return JSON.parse(await window.stepfunMetrics);
  });
  if (timings.some((row) => row.preference !== "auto" || row.device !== "wasm")) {
    throw new Error("Expected automatic selection of WASM.");
  }
  const modelFiles = await evaluate(async () => {
    const files = new Map();
    for (const name of await caches.keys()) {
      const cache = await caches.open(name);
      for (const request of await cache.keys()) {
        if (!request.url.includes("/Xenova/all-MiniLM-L6-v2/resolve/")) continue;
        const response = await cache.match(request);
        files.set(request.url, (await response.blob()).size);
      }
    }
    return [...files].map(([url, bytes]) => ({ url, bytes }));
  });
  const hardware = { cpu: cpus()[0]?.model, threads: cpus().length, memoryBytes: totalmem(), os: release() };
  const result = {
    origin, gpu, hardware, modelFiles, corpus, queries, quality,
    incrementalStatus, afterImport, isolated, afterSwitchBack, timings,
  };
  await mkdir("evidence", { recursive: true });
  await writeFile("evidence/benchmark-results.json", JSON.stringify(result, null, 2) + "\n");
  console.log("Saved evidence/benchmark-results.json");
} finally {
  socket?.close();
  browser.kill();
}
