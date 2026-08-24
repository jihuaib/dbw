import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';

export default defineConfig({
    plugins: [vue()],
    server: {
        host: true,          // 0.0.0.0：其它机器也能访问开发服务器
        port: 5178,
        proxy: { '/api': { target: 'http://127.0.0.1:8099', changeOrigin: true, ws: true } }
    }
});
