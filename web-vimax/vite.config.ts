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
  preview: {
    port: 4174,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8890',
        changeOrigin: true,
        configure(proxy) {
          proxy.on('proxyReq', (proxyRequest) => {
            // Keep the browser's Origin. Rewriting Host without the actual
            // public origin makes the accounts backend correctly reject
            // non-GET requests as cross-site.
            proxyRequest.removeHeader('origin');
          });
        },
      },
      '/uploads': {target: 'http://127.0.0.1:8890', changeOrigin: true},
    },
  },
  build: {
    target: 'es2022',
    rollupOptions: {
      output: {
        // 用函数而不是「包名数组」：数组形式下 react 会被 @xyflow/react 的依赖
        // 一起并进 flow-vendor，react-vendor 只剩一个 177 字节的 re-export 空壳
        // （实测产物 react-vendor = 177B），等于没拆。按真实模块路径归类才能把
        // react/react-dom/scheduler 稳定切出来，让它们单独长期缓存。
        manualChunks(id: string) {
          if (!id.includes('node_modules')) return undefined;
          if (id.includes('@xyflow') || id.includes('d3-')) return 'flow-vendor';
          if (id.includes('lucide-react')) return 'icon-vendor';
          if (
            id.includes('react-markdown')
            || id.includes('remark-')
            || id.includes('micromark')
            || id.includes('/yaml/')
          ) return 'markdown-vendor';
          if (
            id.includes('/react/')
            || id.includes('react-dom')
            || id.includes('/scheduler/')
            || id.includes('use-sync-external-store')
          ) return 'react-vendor';
          return undefined;
        },
      },
    },
  },
});
