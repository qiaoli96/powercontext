const MODEL = "Xenova/all-MiniLM-L6-v2";
const REVISION = "751bff3";
const $ = (id) => document.getElementById(id);
const samples = [
  ["battery.md", "Battery health: set the laptop's maximum charging limit to 80 percent when plugged in for long periods."],
  ["baking.md", "For sourdough bread, leave the dough to ferment overnight before shaping and baking."],
  ["database.md", "Hybrid search combines keyword matching with semantic similarity to find relevant text."],
  ["network.md", "A wireless router can improve coverage by switching to a less crowded channel."],
];
const metrics = [];
let model;
let busy = false;

function settings() {
  return { kb: $("kb").value.trim(), precision: $("precision").value, preference: $("device").value };
}

async function initialize({ precision, preference }) {
  const key = `${precision}:${preference}`;
  if (model?.key === key) return { ...model, loadMs: 0 };
  await model?.extractor.dispose();
  model = undefined;
  $("backend").textContent = "正在加载";
  const start = performance.now();
  const { pipeline } = await import("https://cdn.jsdelivr.net/npm/@huggingface/transformers@4.3.2");
  const devices = preference === "wasm" || !navigator.gpu ? ["wasm"] : ["webgpu", "wasm"];
  for (const device of devices) {
    let extractor;
    try {
      if (device === "webgpu" && !await navigator.gpu.requestAdapter()) throw new Error("无 WebGPU 适配器");
      extractor = await pipeline("feature-extraction", MODEL, {
        revision: REVISION, dtype: precision, device,
        progress_callback: (event) => {
          if (event.status === "progress") $("status").textContent = `加载 ${event.file} · ${Math.round(event.progress)}%`;
        },
      });
      await extractor("warmup", { pooling: "mean", normalize: true });
    } catch (error) {
      await extractor?.dispose();
      if (device === "wasm") {
        $("backend").textContent = "加载失败";
        throw error;
      }
      $("status").textContent = "WebGPU 初始化失败，正在切换到 WASM。";
      continue;
    }
    model = { key, extractor, device, loadMs: performance.now() - start };
    $("backend").textContent = device === "webgpu" ? "WebGPU" : "WASM";
    return model;
  }
}

async function call(action, body) {
  let response;
  try {
    response = await fetch(`/api/${action}`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      signal: AbortSignal.timeout(60_000),
    });
  } catch (error) {
    if (error.name === "TimeoutError") {
      throw new Error("等待 seekdb 超时。请查看服务终端，确认数据库或原生组件已就绪。");
    }
    throw error;
  }
  const result = await response.json();
  if (!response.ok) throw new Error(result.error);
  return result;
}

function chunks(text) {
  return text.split(/\r?\n\s*\r?\n/).flatMap((paragraph) =>
    Array.from(paragraph.trim().matchAll(/[\s\S]{1,480}/gu), (match) => match[0].trim()).filter(Boolean)
  );
}

function record(entry) {
  metrics.push({ ...entry, at: new Date().toISOString(), model: MODEL, revision: REVISION, browser: navigator.userAgent });
  $("timings").textContent = Object.entries(entry)
    .filter(([key]) => key.endsWith("Ms"))
    .map(([key, value]) => `${key}: ${value.toFixed(1)} ms`).join(" · ");
}

async function importDocuments(documents) {
  const config = settings();
  const batchSize = Number($("batch").value);
  const rows = documents.flatMap(([file, content]) => chunks(content).map((text) => ({ file, text })));
  if (!rows.length || rows.length > 64) throw new Error("每次请导入 1 至 64 个段落。");
  const { extractor, device, loadMs } = await initialize(config);
  const start = performance.now();
  for (let i = 0; i < rows.length; i += batchSize) {
    const batch = rows.slice(i, i + batchSize);
    const vectors = (await extractor(batch.map((row) => row.text), { pooling: "mean", normalize: true })).tolist();
    batch.forEach((row, index) => { row.vector = vectors[index]; });
    $("status").textContent = `已向量化 ${Math.min(i + batchSize, rows.length)} / ${rows.length}，后端 ${device}`;
  }
  const embeddingMs = performance.now() - start;
  $("status").textContent = "向量化完成，正在写入 seekdb 并刷新索引…";
  const result = await call("import", { ...config, rows });
  record({ action: "import", ...config, device, batchSize, rows: rows.length, loadMs, embeddingMs,
    writeMs: result.writeMs, refreshMs: result.refreshMs });
  $("status").textContent = `索引已刷新。当前知识库共 ${result.count} 个段落。`;
}

async function search() {
  const started = performance.now();
  const config = settings();
  const query = $("query").value.trim();
  if (!query) throw new Error("请输入查询。");
  $("results").replaceChildren();
  const { extractor, device, loadMs } = await initialize(config);
  const embeddingStarted = performance.now();
  const [vector] = (await extractor([query], { pooling: "mean", normalize: true })).tolist();
  const embeddingMs = performance.now() - embeddingStarted;
  const result = await call("search", { ...config, query, vector });
  for (const { file, text } of result.hits) {
    const card = document.createElement("article");
    const title = document.createElement("strong");
    const snippet = document.createElement("p");
    title.textContent = file;
    snippet.textContent = text;
    card.append(title, snippet);
    $("results").append(card);
  }
  $("status").textContent = `seekdb 返回 ${result.hits.length} 条混合检索结果。`;
  await new Promise((resolve) => requestAnimationFrame(() => resolve()));
  record({ action: "search", ...config, device, query, files: result.hits.map((hit) => hit.file), loadMs, embeddingMs,
    searchMs: result.searchMs, endToEndMs: performance.now() - started });
}

async function run(action) {
  if (busy) return;
  busy = true;
  const controls = [...document.querySelectorAll("button, input, select")];
  controls.forEach((control) => { control.disabled = true; });
  $("status").textContent = "处理中…";
  try {
    await action();
  } catch (error) {
    $("status").textContent = error.message;
  } finally {
    controls.forEach((control) => { control.disabled = false; });
    busy = false;
  }
}

$("import").onclick = () => run(async () => {
  const files = [...$("files").files];
  if (files.some((file) => file.size > 128_000)) throw new Error("单个文件不得超过 128 KB。");
  await importDocuments(await Promise.all(files.map(async (file) => [file.name, await file.text()])));
});
$("demo").onclick = () => run(() => importDocuments(samples));
$("search").onsubmit = (event) => { event.preventDefault(); run(search); };
for (const id of ["kb", "precision", "device"]) {
  $(id).onchange = () => {
    $("results").replaceChildren();
    $("status").textContent = "设置已更改，请重新查询。切换精度后需要为该精度导入资料。";
    if (id !== "kb") $("backend").textContent = "等待加载";
  };
}
$("download").onclick = () => {
  const url = URL.createObjectURL(new Blob([JSON.stringify(metrics, null, 2)], { type: "application/json" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = "stepfun-timings.json";
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
};
