import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  base: "/",
  build: {
    outDir: "../../skills/wysiwyg-grilling/scripts/web",
    emptyOutDir: true,
    chunkSizeWarningLimit: 4000,
  },
  define: {
    "process.env.IS_PREACT": JSON.stringify("false"),
  },
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8801",
    },
  },
});
