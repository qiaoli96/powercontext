# 阶跃星辰 · 浏览器本地知识库

浏览器用 Transformers.js 生成文档和查询向量。Node 服务只负责通过
seekdb-js 把原文、向量、文件名写入 seekdb，以及发出一次库内混合检索。
网页展示 seekdb 返回的顺序。浏览器加载固定版本 Transformers.js 4.3.2
及模型文件，首次运行需可访问 jsDelivr 和 Hugging Face。

## 启动

需要 Node.js 22 或以上及新版 Chrome 或 Edge。在 `stepfun/` 下执行：

```bash
npm ci --legacy-peer-deps
npm start
```

打开 `http://127.0.0.1:5173/`。默认尝试使用嵌入式 seekdb，文件目录是
用户目录下的 `.stepfun-seekdb/`。也可在 `.env.local` 中设置现有
seekdb Server 连接（`SEEKDB_HOST`、`SEEKDB_PORT`、`SEEKDB_DATABASE`、
`SEEKDB_USER`、`SEEKDB_PASSWORD`）。数据库由该机器的 Node 进程访问。

使用 `--legacy-peer-deps` 是为了绕过 seekdb 1.4.0 发布包的可选 peer
依赖解析问题。文档和查询向量由浏览器提供，因此不需要 SDK 的默认嵌入插件。

嵌入式 SDK 首次运行会下载原生组件。若下载完成后仍因上次初始化中断而
超时，停止旧服务，在 PowerShell 用一个全新的独立数据库目录启动：

```powershell
$env:SEEKDB_PATH = Join-Path ([Environment]::GetFolderPath('UserProfile')) '.stepfun-seekdb-clean'
npm.cmd start
```

## 最小验证

1. 保持知识库 `demo`，选择 INT8 和「WebGPU 优先」，点击「导入示例」。
2. 等待索引刷新完成，确认界面显示实际推理后端和 4 个段落。
3. 搜索 `battery charge limit`，预期前 5 条包含 `battery.md`，
   原文提及 80% 充电上限。再以 `sourdough fermentation` 搜索，预期包含 `baking.md`。
4. 导入一份新的 TXT 或 Markdown，查询其中的内容。改知识库名为
   `other` 并查询，未导入资料时应无命中。切回 `demo` 应恢复原结果。
5. 切换「强制 WASM」重跑，确认后端显示 WASM。还需要在禁用 WebGPU 的浏览器
   保持「WebGPU 优先」重跑，以验证自动降级路径。

示例语料为英语，模型选择为 [Xenova/all-MiniLM-L6-v2](https://huggingface.co/Xenova/all-MiniLM-L6-v2)，
ONNX 版本锁定到仓库修订 `751bff3`。FP32 选择原始 ONNX，INT8 选择同版本的
预量化权重（Transformers.js 的 `q8`）。两种精度分别存入独立集合。

## 性能和质量记录

页面可导出计时 JSON。也可以在服务保持运行时，从另一个终端在本目录执行
`node benchmark.mjs`。需要支持全局 WebSocket 的 Node.js（22 或以上）
和 Chrome，非默认安装位置可用 `CHROME_BIN` 指定浏览器。

脚本启动一个独立的无界面 Chrome，禁用 GPU，并保持页面选择「WebGPU 优先」。
它通过真实页面导入 8 段语料，在自动选择的 WASM 后端比较 FP32 和 INT8，
分别以批大小 1、4 运行 3 次，每种精度以 8 条标注查询运行 3 轮。
最后检查索引刷新后的新增文档与知识库隔离。结果含原始耗时、实际后端、
预期与实际排序以及缓存的模型文件大小，写入 `evidence/benchmark-results.json`。
首次执行需下载两套权重，耗时取决于网络。

已完成的实测对比、自动降级和知识库隔离结果见 [一页技术说明](TECHNICAL.md)。
