import {defineConfig} from 'vite';
import react from '@vitejs/plugin-react';

// P2 批2：路由/组件级拆包——把体积大头（react/react-dom、react-flow、图标/工具库）拆成独立 chunk，
// 减少首屏单包体积，长剧工作台按需加载。
export default defineConfig({
  plugins: [react()],
  server: {
    port: 8891,
    proxy: {
      '/api': {target: 'http://127.0.0.1:8890', changeOrigin: true},
      // 上传文件回读也必须代理：不配的话 /uploads/xxx 会落到 vite 的 SPA fallback，
      // 返回 index.html（实测 200 + text/html + 1988 字节），图片永远显示不出来。
      '/uploads': {target: 'http://127.0.0.1:8890', changeOrigin: true},
    },
  },
  build: {
    target: 'es2022',
    rollupOptions: {
      output: {
        manualChunks: {
          'react-vendor': ['react', 'react-dom', 'react-dom/client'],
          'flow-vendor': ['@xyflow/react'],
          'icon-vendor': ['lucide-react'],
          // P2-4：文档渲染与配置解析大库独立缓存；若后续视图懒加载还会自动从主包分离
          'markdown-vendor': ['react-markdown', 'remark-gfm', 'yaml'],
        },
      },
    },
  },
});
