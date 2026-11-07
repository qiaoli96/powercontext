import { defineConfig, loadEnv } from "vite";
import { knowledgeBaseApi } from "./server.js";

export default defineConfig(({ mode }) => ({
  plugins: [knowledgeBaseApi(loadEnv(mode, process.cwd(), "SEEKDB_"))],
  server: { host: "127.0.0.1", port: 5173, strictPort: true },
}));
