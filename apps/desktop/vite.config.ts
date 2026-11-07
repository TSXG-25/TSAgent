import { defineConfig } from "vite";

export default defineConfig({
  server: {
    port: 4173,
  },
  build: {
    rollupOptions: {
      output: {
        manualChunks: {
          three: ["three"],
          react: ["react", "react-dom", "react-router-dom"],
        },
      },
    },
  },
});
