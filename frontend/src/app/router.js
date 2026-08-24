import { createRouter, createWebHashHistory } from 'vue-router';

import DiagnoseView from '../modules/diagnose/DiagnoseView.vue';
import KbView from '../modules/kb/KbView.vue';
import DevicesView from '../modules/devices/DevicesView.vue';
import ConsistencyView from '../modules/consistency/ConsistencyView.vue';
import SyslogView from '../modules/syslog/SyslogView.vue';
import SnmpView from '../modules/snmp/SnmpView.vue';

export const router = createRouter({
    history: createWebHashHistory(),
    routes: [
        { path: '/', redirect: '/diagnose' },
        { path: '/diagnose', name: 'diagnose', component: DiagnoseView },
        { path: '/kb', name: 'kb', component: KbView },
        { path: '/devices', name: 'devices', component: DevicesView },
        { path: '/syslog', name: 'syslog', component: SyslogView },
        { path: '/snmp', name: 'snmp', component: SnmpView },
        { path: '/consistency', name: 'consistency', component: ConsistencyView }
    ]
});
