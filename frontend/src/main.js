import { createApp } from 'vue';
import NetNexusUi, { initializeTheme } from 'netnexus-ui';
import 'netnexus-ui/style.css';
import './shared/styles.css';
import App from './app/App.vue';
import { router } from './app/router.js';

initializeTheme();
createApp(App).use(NetNexusUi).use(router).mount('#app');
