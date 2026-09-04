import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';

const portText = (process.env.DETOPS_PORT || '8099').trim();
const backendPort = /^\d+$/.test(portText) && Number(portText) >= 1 && Number(portText) <= 65535
    ? String(Number(portText))
    : '8099';
const listenHost = (process.env.DETOPS_HOST || '127.0.0.1').trim();
const proxyHost = listenHost === '0.0.0.0'
    ? '127.0.0.1'
    : (listenHost === '::' || listenHost === '0:0:0:0:0:0:0:0'
        ? '[::1]'
        : (listenHost.includes(':') && !listenHost.startsWith('[') ? `[${listenHost}]` : listenHost));
const apiTarget = process.env.DETOPS_API_TARGET || `http://${proxyHost}:${backendPort}`;

export default defineConfig({
    plugins: [vue()],
    server: {
        host: true,          // 0.0.0.0：其它机器也能访问开发服务器
        port: 5178,
        proxy: { '/api': { target: apiTarget, changeOrigin: true, ws: true } }
    }
});
