const BASE = '/api';

async function request(path, options = {}) {
    const res = await fetch(BASE + path, {
        headers: options.body instanceof FormData ? {} : { 'Content-Type': 'application/json' },
        ...options
    });
    const text = await res.text();
    let data = null;
    try {
        data = text ? JSON.parse(text) : null;
    } catch {
        data = text;
    }
    if (!res.ok) {
        throw new Error(data && data.detail ? data.detail : `请求失败（HTTP ${res.status}）`);
    }
    return data;
}

const send = (path, method, body) =>
    request(path, { method, body: body === undefined ? undefined : JSON.stringify(body) });

export const metaApi = {
    meta: () => request('/meta')
};

export const settingsApi = {
    read: () => request('/settings'),
    save: body => send('/settings', 'PUT', body),
    test: () => send('/settings/test', 'POST')
};

export const kbApi = {
    summary: () => request('/kb/summary'),
    docs: () => request('/kb/docs'),
    upload: form => request('/kb/docs', { method: 'POST', body: form }),
    uploadBatch: form => request('/kb/docs/batch', { method: 'POST', body: form }),
    importDir: (path, engine) => send('/kb/import-dir', 'POST', { path, engine }),
    job: id => request(`/kb/jobs/${id}`),
    deleteDoc: id => send(`/kb/docs/${id}`, 'DELETE'),
    samples: () => request('/kb/samples'),
    importSample: (filename, engine) => send('/kb/samples', 'POST', { filename, engine }),
    importSamples: (filenames, engine) =>
        send('/kb/samples/batch', 'POST', { filenames, engine }),
    commands: q => request(`/kb/commands${q ? `?q=${encodeURIComponent(q)}` : ''}`),
    toggle: (id, enabled) => send(`/kb/commands/${id}?enabled=${enabled}`, 'PATCH')
};

export const deviceApi = {
    list: () => request('/devices'),
    options: () => request('/devices/options'),
    create: body => send('/devices', 'POST', body),
    update: (id, body) => send(`/devices/${id}`, 'PUT', body),
    remove: id => send(`/devices/${id}`, 'DELETE'),
    test: (id, command) => send(`/devices/${id}/test`, 'POST', { command }),
    topology: () => request('/devices/topology'),
    topologyContext: () => request('/devices/topology/context'),
    discover: () => send('/devices/topology/discover', 'POST'),
    preview: (device_id, command) =>
        send('/devices/preview', 'POST', { device_id, command }),
    capabilities: device =>
        request(`/devices/capabilities${device ? `?device=${encodeURIComponent(device)}` : ''}`),
    probe: id => send(`/devices/${id}/probe`, 'POST'),
    pushReporting: id => send(`/devices/${id}/push-reporting`, 'POST')
};

export const eventsApi = {
    list: (kind = '', device = '') =>
        request(`/events?kind=${kind}&device=${encodeURIComponent(device)}`),
    clear: () => send('/events', 'DELETE'),
    receivers: () => request('/events/receivers'),
    startReceivers: (cfg = {}) => send('/events/receivers/start', 'POST', cfg),
    suggestHost: () => request('/events/suggest-host')
};

export const mibsApi = {
    sources: () => request('/mibs/sources'),
    upload: form => request('/mibs/sources', { method: 'POST', body: form }),
    deleteSource: file => send(`/mibs/sources/${encodeURIComponent(file)}`, 'DELETE'),
    compile: () => send('/mibs/compile', 'POST', {}),
    status: () => request('/mibs/status'),
    tree: (parent = '') => request(`/mibs/tree?parent=${encodeURIComponent(parent)}`),
    node: oid => request(`/mibs/node?oid=${encodeURIComponent(oid)}`),
    search: q => request(`/mibs/search?q=${encodeURIComponent(q)}`)
};

export const collectApi = {
    epoch: id => request(`/collect/epochs/${id}`),
    calibrate: payload => send('/collect/calibrate', 'POST', payload),
    profiles: device =>
        request(`/collect/profiles${device ? `?device=${encodeURIComponent(device)}` : ''}`),
    drift: (a, b) => request(`/collect/drift?a=${a}&b=${b}`)
};

export const diagnoseApi = {
    summary: () => request('/diagnose/summary'),
    sessions: () => request('/diagnose/sessions'),
    createSession: title => send('/diagnose/sessions', 'POST', { title }),
    deleteSession: id => send(`/diagnose/sessions/${id}`, 'DELETE'),
    turns: id => request(`/diagnose/sessions/${id}/turns`),
    ask: (id, payload) => send(`/diagnose/sessions/${id}/ask`, 'POST', payload),
    task: taskId => request(`/diagnose/tasks/${taskId}`),
    turnPrompt: id => request(`/diagnose/turns/${id}/prompt`),
    check: payload => send('/diagnose/consistency-check', 'POST', payload),
    frozen: () => request('/diagnose/frozen'),
    frozenDetail: fp => request(`/diagnose/frozen/${fp}`),
    verify: (fp, verified) => send(`/diagnose/frozen/${fp}`, 'PATCH', { verified }),
    unfreeze: fp => send(`/diagnose/frozen/${fp}`, 'DELETE')
};
