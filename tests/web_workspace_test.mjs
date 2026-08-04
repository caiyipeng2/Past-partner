import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { webcrypto } from 'node:crypto';
import { test } from 'node:test';
import vm from 'node:vm';

const workspaceSource = await readFile(new URL('../web/workspace.js', import.meta.url), 'utf8');
const CHUNK_BYTES = 4 * 1024 * 1024;

function makeElement(id) {
    const classes = new Set();
    return {
        id,
        value: '',
        textContent: '',
        disabled: false,
        hidden: false,
        dataset: {},
        style: {},
        children: [],
        classList: {
            add: (...items) => items.forEach(item => classes.add(item)),
            remove: (...items) => items.forEach(item => classes.delete(item)),
            contains: item => classes.has(item),
        },
        addEventListener() {},
        append(...items) { this.children.push(...items); },
        appendChild(item) { this.children.push(item); },
        replaceChildren(...items) { this.children = items; },
        setAttribute(name, value) { this[name] = value; },
        querySelectorAll() { return []; },
        querySelector() { return null; },
        focus() {},
        remove() {},
        requestSubmit() {},
    };
}

function makeStorage() {
    const values = new Map();
    return {
        getItem(key) { return values.has(key) ? values.get(key) : null; },
        setItem(key, value) { values.set(key, String(value)); },
        removeItem(key) { values.delete(key); },
    };
}

function jsonResponse(payload, status = 200) {
    return {
        ok: status >= 200 && status < 300,
        status,
        headers: {get: () => 'application/json'},
        async json() { return payload; },
    };
}

function createBackend({pauseFirstUpload = false, initialChunks = []} = {}) {
    const fileSize = CHUNK_BYTES + 3;
    const chunks = new Map(initialChunks.map(index => [index, index === 0 ? CHUNK_BYTES : 3]));
    const job = {
        id: 'job-1',
        persona_id: 'persona-1',
        source_name: 'sample.bin',
        total_bytes: fileSize,
        received_bytes: [...chunks.values()].reduce((sum, value) => sum + value, 0),
        chunk_count: chunks.size,
        state: chunks.size ? 'uploading' : 'created',
    };
    const metrics = {
        importCreates: 0,
        missingQueries: [],
        chunkRequests: [],
        completions: 0,
        cancellations: [],
        pauseStarted: null,
    };

    async function fetchApi(url, options = {}) {
        const parsed = new URL(url, 'http://localhost');
        const path = parsed.pathname;
        if (path === '/api/v1/auth/session') return jsonResponse({access_token: 'test-token'});
        if (path === '/api/v1/health') return jsonResponse({status: 'healthy'});
        if (path === '/api/v1/providers') return jsonResponse({providers: []});
        if (path === '/api/v1/imports' && options.method === 'POST') {
            metrics.importCreates += 1;
            const payload = JSON.parse(options.body);
            Object.assign(job, {
                persona_id: payload.persona_id,
                source_name: payload.source_name,
                total_bytes: payload.total_bytes,
                state: 'created',
                received_bytes: 0,
                chunk_count: 0,
            });
            chunks.clear();
            return jsonResponse({...job});
        }
        if (path === `/api/v1/imports/${job.id}` && (!options.method || options.method === 'GET')) {
            return jsonResponse({...job});
        }
        if (path === `/api/v1/imports/${job.id}/missing-chunks`) {
            const expected = Number(parsed.searchParams.get('expected_chunks'));
            metrics.missingQueries.push(expected);
            const received = [...chunks.keys()].sort((a, b) => a - b);
            return jsonResponse({
                import_id: job.id,
                state: job.state,
                total_bytes: job.total_bytes,
                received_bytes: job.received_bytes,
                chunk_count: received.length,
                expected_chunk_count: expected,
                received_chunks: received,
                missing_chunks: Array.from({length: expected}, (_, index) => index)
                    .filter(index => !chunks.has(index)),
            });
        }
        if (path === `/api/v1/imports/${job.id}/cancel` && options.method === 'POST') {
            metrics.cancellations.push(job.id);
            job.state = 'cancelled';
            job.received_bytes = 0;
            job.chunk_count = 0;
            chunks.clear();
            return jsonResponse({...job});
        }
        const chunkMatch = path.match(new RegExp(`/api/v1/imports/${job.id}/chunks/(\\d+)$`));
        if (chunkMatch && options.method === 'PUT') {
            const index = Number(chunkMatch[1]);
            metrics.chunkRequests.push(index);
            if (pauseFirstUpload && !metrics.pauseStarted) {
                metrics.pauseStarted = new Promise((_, reject) => {
                    const abort = () => {
                        const error = new Error('aborted');
                        error.name = 'AbortError';
                        reject(error);
                    };
                    options.signal?.addEventListener('abort', abort, {once: true});
                    if (options.signal?.aborted) abort();
                });
                return metrics.pauseStarted;
            }
            const length = options.body.size;
            chunks.set(index, length);
            job.received_bytes = [...chunks.values()].reduce((sum, value) => sum + value, 0);
            job.chunk_count = chunks.size;
            job.state = 'uploading';
            return jsonResponse({import_id: job.id, index, received_bytes: job.received_bytes});
        }
        if (path === `/api/v1/imports/${job.id}/complete` && options.method === 'POST') {
            job.state = 'uploaded';
            metrics.completions += 1;
            return jsonResponse({...job});
        }
        throw new Error(`unexpected request: ${options.method || 'GET'} ${url}`);
    }

    return {fileSize, job, metrics, fetchApi};
}

function makeFile(size, name = 'sample.bin') {
    const blob = new Blob([new Uint8Array(size)]);
    return {
        name,
        type: 'application/octet-stream',
        size,
        lastModified: 123,
        webkitRelativePath: '',
        slice(start, end) { return blob.slice(start, end); },
    };
}

function createContext(backend, localStorage) {
    const ids = [
        'serviceState', 'serviceStateText', 'personaForm', 'displayName',
        'customRelationshipField', 'customRelationship', 'createPersonaButton',
        'personaStatus', 'chatFile', 'chatFolder', 'fileSummary', 'fileList',
        'clearFilesButton', 'uploadButton', 'pauseUploadButton', 'cancelUploadButton', 'uploadStatus', 'uploadProgress',
        'uploadProgressValue', 'providerSelect', 'modelSelect', 'modelCapability',
        'modelPricing', 'modelStatus', 'refreshProvidersButton', 'activePersonaName',
        'activeModelName', 'chatHistory', 'emptyChat', 'messageForm', 'messageInput',
        'sendButton', 'chatStatus',
    ];
    const elements = Object.fromEntries(ids.map(id => [id, makeElement(id)]));
    const document = {
        getElementById(id) { return elements[id] || (elements[id] = makeElement(id)); },
        querySelector() { return makeElement('step'); },
        createElement: makeElement,
    };
    const context = {
        AbortController,
        Blob,
        JSON,
        Promise,
        URL,
        console,
        crypto: webcrypto,
        document,
        fetch: backend.fetchApi,
        localStorage,
        sessionStorage: makeStorage(),
        setTimeout,
        clearTimeout,
    };
    context.window = context;
    vm.createContext(context);
    vm.runInContext(workspaceSource, context, {filename: 'workspace.js'});
    return {
        context,
        elements,
        state: vm.runInContext('state', context),
        upload: vm.runInContext('uploadSelectedFiles', context),
        togglePause: vm.runInContext('toggleUploadPause', context),
        cancel: vm.runInContext('cancelUpload', context),
    };
}

async function waitFor(predicate) {
    const deadline = Date.now() + 3000;
    while (Date.now() < deadline) {
        if (predicate()) return;
        await new Promise(resolve => setTimeout(resolve, 5));
    }
    throw new Error('timed out waiting for browser upload state');
}

test('pause aborts the active chunk and resume uploads only missing chunks', async () => {
    const backend = createBackend({pauseFirstUpload: true});
    const browser = createContext(backend, makeStorage());
    browser.state.personaId = 'persona-1';
    browser.state.selectedFiles = [makeFile(backend.fileSize)];

    const firstRun = browser.upload();
    await waitFor(() => Boolean(backend.metrics.pauseStarted));
    browser.togglePause();
    await firstRun;

    assert.equal(browser.state.upload.paused, true);
    assert.equal(browser.state.upload.running, false);
    assert.deepEqual(backend.metrics.chunkRequests, [0]);
    assert.equal(backend.metrics.completions, 0);

    browser.togglePause();
    await waitFor(() => browser.state.imported === true);

    assert.equal(browser.state.upload.paused, false);
    assert.deepEqual(backend.metrics.missingQueries, [2, 2]);
    assert.deepEqual(backend.metrics.chunkRequests, [0, 0, 1]);
    assert.equal(backend.metrics.completions, 1);
});

test('a new page context resumes a persisted job from its missing chunk list', async () => {
    const backend = createBackend({initialChunks: [0]});
    const storage = makeStorage();
    storage.setItem('past-partner:import:persona-1:sample.bin:' + backend.fileSize + ':123', backend.job.id);
    const browser = createContext(backend, storage);
    browser.state.personaId = 'persona-1';
    browser.state.selectedFiles = [makeFile(backend.fileSize)];

    await browser.upload();

    assert.equal(backend.metrics.importCreates, 0);
    assert.deepEqual(backend.metrics.missingQueries, [2]);
    assert.deepEqual(backend.metrics.chunkRequests, [1]);
    assert.equal(backend.metrics.completions, 1);
    assert.equal(browser.state.imported, true);
});

test('cancelling a paused upload closes the server job and clears local resume state', async () => {
    const backend = createBackend({pauseFirstUpload: true});
    const storage = makeStorage();
    const browser = createContext(backend, storage);
    browser.state.personaId = 'persona-1';
    browser.state.selectedFiles = [makeFile(backend.fileSize)];

    const firstRun = browser.upload();
    await waitFor(() => Boolean(backend.metrics.pauseStarted));
    browser.togglePause();
    await firstRun;
    await browser.cancel();

    assert.equal(browser.state.upload.paused, false);
    assert.deepEqual(backend.metrics.cancellations, ['job-1']);
    assert.equal(backend.metrics.completions, 0);
    assert.equal(storage.getItem('past-partner:import:persona-1:sample.bin:' + backend.fileSize + ':123'), null);
});
