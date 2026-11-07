import { createHash } from "node:crypto";
import { mkdir } from "node:fs/promises";
import { homedir } from "node:os";
import { join } from "node:path";
import { performance } from "node:perf_hooks";
import { FulltextIndexConfig, Schema, SeekdbClient, VectorIndexConfig } from "seekdb";

const DIMENSION = 384;
const digest = (text) => createHash("sha256").update(text).digest("hex");

async function readJson(req) {
  const chunks = [];
  let size = 0;
  for await (const chunk of req) {
    size += chunk.length;
    if (size > 4_000_000) throw new Error("请求超过 4 MB。");
    chunks.push(chunk);
  }
  return JSON.parse(Buffer.concat(chunks).toString("utf8"));
}

function validVector(vector) {
  return Array.isArray(vector) && vector.length === DIMENSION && vector.every(Number.isFinite);
}

export function knowledgeBaseApi(config) {
  let client;
  const collections = new Map();
  const pending = new Map();

  async function getCollection(name) {
    if (!client) {
      if (config.SEEKDB_HOST) {
        client = new SeekdbClient({
          host: config.SEEKDB_HOST,
          port: Number(config.SEEKDB_PORT || 2881),
          database: config.SEEKDB_DATABASE || "test",
          user: config.SEEKDB_USER || "root",
          password: config.SEEKDB_PASSWORD || "",
        });
      } else {
        const path = config.SEEKDB_PATH || join(homedir(), ".stepfun-seekdb");
        await mkdir(path, { recursive: true });
        client ??= new SeekdbClient({ path, database: "test" });
      }
    }
    if (!collections.has(name)) {
      const schema = new Schema()
        .createIndex(new VectorIndexConfig({
          hnsw: { dimension: DIMENSION, distance: "cosine" },
          embeddingFunction: null,
        }))
        .createIndex(new FulltextIndexConfig());
      const created = client.getOrCreateCollection({ name, schema }).catch((error) => {
        collections.delete(name);
        throw new Error(`打开 seekdb 集合失败：${error.message}`, { cause: error });
      });
      collections.set(name, created);
    }
    return collections.get(name);
  }

  async function handle(route, body) {
    const { kb, precision } = body;
    if (typeof kb !== "string" || !kb.trim() || kb.length > 64 || !["q8", "fp32"].includes(precision)) {
      throw new Error("知识库名或模型精度无效。");
    }
    const name = `stepfun_${digest(kb.trim()).slice(0, 20)}_${precision}`;
    const collection = await getCollection(name);

    if (route === "/api/import") {
      const { rows } = body;
      if (!Array.isArray(rows) || !rows.length || rows.length > 64 || rows.some((row) =>
        !row || typeof row.file !== "string" || !row.file || row.file.length > 255 ||
        typeof row.text !== "string" || !row.text.trim() || row.text.length > 2000 || !validVector(row.vector)
      )) throw new Error("导入记录无效。");

      const write = (pending.get(name) ?? Promise.resolve()).catch(() => {}).then(async () => {
        const unique = new Map(rows.map((row) => [digest(JSON.stringify([row.file, row.text])), row]));
        const records = [...unique.values()];
        const started = performance.now();
        await collection.upsert({
          ids: [...unique.keys()],
          documents: records.map((row) => row.text),
          embeddings: records.map((row) => row.vector),
          metadatas: records.map((row) => ({ file: row.file })),
        });
        const writeMs = performance.now() - started;
        const refreshing = performance.now();
        try {
          await collection.refresh_index();
        } catch (error) {
          throw new Error(`seekdb 已写入，但刷新索引失败：${error.message}`, { cause: error });
        }
        const refreshMs = performance.now() - refreshing;
        return { indexed: records.length, count: await collection.count(), writeMs, refreshMs };
      });
      pending.set(name, write);
      try {
        return await write;
      } finally {
        if (pending.get(name) === write) pending.delete(name);
      }
    }

    if (route === "/api/search") {
      const { query, vector } = body;
      if (typeof query !== "string" || !query.trim() || query.length > 512 || !validVector(vector)) {
        throw new Error("查询文本或向量无效。");
      }
      await pending.get(name);
      const started = performance.now();
      const result = await collection.hybridSearch({
        query: { whereDocument: { $contains: query }, nResults: 20 },
        knn: { queryEmbeddings: [vector], nResults: 20 },
        nResults: 5,
        include: ["documents", "metadatas"],
      });
      const searchMs = performance.now() - started;
      return {
        searchMs,
        hits: (result.ids[0] ?? []).slice(0, 5).map((id, i) => ({
          id, text: result.documents[0][i], file: result.metadatas[0][i]?.file,
        })),
      };
    }
    throw new Error("未知操作。");
  }

  return {
    name: "stepfun-seekdb-api",
    configureServer(server) {
      server.middlewares.use(async (req, res, next) => {
        if (!["/api/import", "/api/search"].includes(req.url)) return next();
        res.setHeader("Content-Type", "application/json; charset=utf-8");
        if (req.method !== "POST" || !req.headers["content-type"]?.startsWith("application/json")) {
          res.statusCode = 405;
          return res.end(JSON.stringify({ error: "请使用 JSON POST。" }));
        }
        try {
          const result = await handle(req.url, await readJson(req));
          res.end(JSON.stringify(result));
        } catch (error) {
          server.config.logger.error(`[seekdb] ${req.url}: ${error.message}`);
          res.statusCode = 400;
          res.end(JSON.stringify({ error: error.message }));
        }
      });
    },
  };
}
