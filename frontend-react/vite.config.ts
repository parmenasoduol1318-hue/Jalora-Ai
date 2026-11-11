import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  const viteEnv = loadEnv(mode, ".", "VITE_");
  const runtimeEnv = (
    globalThis as typeof globalThis & {
      process?: { env?: Record<string, string | undefined> };
    }
  ).process?.env ?? {};
  const backendTarget = runtimeEnv.VITE_API_PROXY_TARGET
    || viteEnv.VITE_API_PROXY_TARGET
    || "http://127.0.0.1:8000";
  const devPort = Number(runtimeEnv.VITE_DEV_PORT || viteEnv.VITE_DEV_PORT || 5173);
  return {
    plugins: [react()],
    server: {
      port: devPort,
      strictPort: true,
      proxy: {
        "/api": { target: backendTarget, changeOrigin: true, ws: true },
      },
    },
    build: {
      outDir: "dist",
      emptyOutDir: true,
    },
  };
});
