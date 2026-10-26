import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath, URL } from "node:url";
import compression from "compression";

// Local development: the gateway runs on 8080 and the chat service on 8200.
// Everything under /api/chat belongs to the chat service; the rest of /api and
// /mcp belong to the gateway. This mirrors the Ingress routing in the cluster.
export default defineConfig({
  plugins: [
    react(),
    {
      // `vite preview` sends files uncompressed. Slow links (a tunnel) need gzip, but only
      // for static files: the chat stream under /api must reach the browser unbuffered.
      name: "gzip-static-preview",
      configurePreviewServer(server) {
        const skip = (url = "") => url.startsWith("/api") || url.startsWith("/mcp");
        server.middlewares.use(compression({ filter: (req) => !skip(req.url) }) as never);
      },
    },
  ],
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  css: { modules: { localsConvention: "camelCaseOnly" } },
  server: {
    // Lets the dev server answer through a Cloudflare quick tunnel (cloudflared tunnel --url).
    allowedHosts: [".trycloudflare.com", ".lhr.life", ".localhost.run", ".pinggy.link", ".pinggy.io", ".pinggy.net", ".pinggy-free.link", ".serveousercontent.com"],
    proxy: {
      "/api/chat": { target: "http://127.0.0.1:8200", changeOrigin: false },
      "/api": { target: "http://127.0.0.1:8080", changeOrigin: false },
      "/mcp": { target: "http://127.0.0.1:8080", changeOrigin: false },
    },
  },
  build: { outDir: "dist", sourcemap: false },
});
