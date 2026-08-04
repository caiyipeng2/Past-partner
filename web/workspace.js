'use strict';

const API_BASE = '/api/v1';
const MAX_IMPORT_BYTES = 3 * 1024 * 1024 * 1024;
const CHUNK_BYTES = 4 * 1024 * 1024;
const MAX_CHUNK_ATTEMPTS = 3;

function readSessionToken() {
    try {
        return sessionStorage.getItem('pastPartnerSession');
    } catch (_error) {
        return null;
    }
}

const state = {
    authToken: readSessionToken(),
    personaId: null,
    selectedFiles: [],
    providers: [],
    providerId: null,
    modelId: null,
    messages: [],
    imported: false,
    upload: {
        running: false,
        paused: false,
        controller: null,
        uploadedBytes: 0,
        totalBytes: 0,
    },
};

const elementIds = [
    'serviceState', 'serviceStateText', 'personaForm', 'displayName',
    'customRelationshipField', 'customRelationship', 'createPersonaButton',
    'personaStatus', 'chatFile', 'chatFolder', 'fileSummary', 'fileList',
    'clearFilesButton', 'uploadButton', 'pauseUploadButton', 'uploadStatus', 'uploadProgress',
    'uploadProgressValue', 'providerSelect', 'modelSelect', 'modelCapability',
    'modelPricing', 'modelStatus', 'refreshProvidersButton', 'activePersonaName',
    'activeModelName', 'chatHistory', 'emptyChat', 'messageForm', 'messageInput',
    'sendButton', 'chatStatus',
];
const elements = Object.fromEntries(elementIds.map(id => [id, document.getElementById(id)]));

async function api(path, options = {}) {
    const request = {...options, headers: {...(options.headers || {})}};
    if (state.authToken) request.headers.Authorization = `Bearer ${state.authToken}`;
    const response = await fetch(`${API_BASE}${path}`, request);
    const contentType = response.headers.get('Content-Type') || '';
    const payload = contentType.includes('application/json') ? await response.json() : null;
    if (!response.ok) {
        const error = new Error(payload?.error?.message || `请求失败 (${response.status})`);
        error.code = payload?.error?.code || 'request_failed';
        throw error;
    }
    return payload;
}

async function bootstrapSession() {
    if (state.authToken) return;
    const response = await fetch(`${API_BASE}/auth/session`, {method: 'POST'});
    const contentType = response.headers.get('Content-Type') || '';
    const payload = contentType.includes('application/json') ? await response.json() : null;
    if (!response.ok) {
        const error = new Error(payload?.error?.message || `会话初始化失败 (${response.status})`);
        error.code = payload?.error?.code || 'session_bootstrap_failed';
        throw error;
    }
    state.authToken = payload.access_token;
    try {
        sessionStorage.setItem('pastPartnerSession', state.authToken);
    } catch (_error) {
        // The current page can continue using the in-memory bearer token.
    }
}

function postJson(path, value) {
    return api(path, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(value),
    });
}

function setStatus(element, message, kind = '') {
    element.textContent = message;
    element.dataset.kind = kind;
}

function completeStep(step, nextStep) {
    const current = document.querySelector(`[data-step="${step}"]`);
    const next = document.querySelector(`[data-step="${nextStep}"]`);
    current?.classList.remove('is-current');
    current?.classList.add('is-complete');
    next?.classList.add('is-current');
}

function relationshipValue() {
    return elements.personaForm.querySelector('input[name="relationship_type"]:checked')?.value || '';
}

async function createPersona(event) {
    event.preventDefault();
    elements.createPersonaButton.disabled = true;
    setStatus(elements.personaStatus, '保存中');
    const relationshipType = relationshipValue();
    const payload = {
        display_name: elements.displayName.value.trim(),
        relationship_type: relationshipType,
    };
    if (relationshipType === 'custom') payload.custom_label = elements.customRelationship.value.trim();

    try {
        const persona = await postJson('/personas', payload);
        state.personaId = persona.id;
        elements.activePersonaName.textContent = persona.display_name;
        elements.chatFile.disabled = false;
        elements.chatFolder.disabled = false;
        setStatus(elements.personaStatus, '人物已保存', 'success');
        completeStep('persona', 'import');
        updateControls();
    } catch (error) {
        setStatus(elements.personaStatus, error.message, 'error');
    } finally {
        elements.createPersonaButton.disabled = false;
    }
}

function onRelationshipChange() {
    const isCustom = relationshipValue() === 'custom';
    elements.customRelationshipField.hidden = !isCustom;
    elements.customRelationship.required = isCustom;
    if (isCustom) elements.customRelationship.focus();
}

function selectFiles(files, otherInput) {
    if (state.upload.running) return;
    state.selectedFiles = Array.from(files);
    state.upload.paused = false;
    otherInput.value = '';
    renderSelectedFiles();
}

function renderSelectedFiles() {
    elements.fileList.replaceChildren();
    const totalBytes = selectedBytes();
    if (!state.selectedFiles.length) {
        elements.fileSummary.textContent = '未选择资料';
        elements.clearFilesButton.hidden = true;
        setStatus(elements.uploadStatus, '');
    } else {
        elements.fileSummary.textContent = `${state.selectedFiles.length} 个文件 · ${formatBytes(totalBytes)}`;
        elements.clearFilesButton.hidden = false;
        state.selectedFiles.slice(0, 5).forEach(file => {
            const item = document.createElement('li');
            const name = document.createElement('span');
            const size = document.createElement('span');
            name.textContent = file.webkitRelativePath || file.name;
            size.textContent = formatBytes(file.size);
            item.append(name, size);
            elements.fileList.appendChild(item);
        });
        if (state.selectedFiles.length > 5) {
            const remaining = document.createElement('li');
            remaining.textContent = `另有 ${state.selectedFiles.length - 5} 个文件`;
            elements.fileList.appendChild(remaining);
        }
        setStatus(elements.uploadStatus, totalBytes > MAX_IMPORT_BYTES ? '所选资料总量超过 3 GiB' : '', totalBytes > MAX_IMPORT_BYTES ? 'error' : '');
    }
    updateControls();
}

function clearFiles() {
    if (state.upload.running) return;
    state.selectedFiles = [];
    state.upload.paused = false;
    elements.chatFile.value = '';
    elements.chatFolder.value = '';
    renderSelectedFiles();
}

function importResumeKey(file) {
    const sourceName = file.webkitRelativePath || file.name;
    return `past-partner:import:${state.personaId}:${sourceName}:${file.size}:${file.lastModified}`;
}

async function resolveImportJob(file) {
    const key = importResumeKey(file);
    const sourceName = file.webkitRelativePath || file.name;
    let storedId = null;
    try {
        storedId = localStorage.getItem(key);
    } catch (_error) {
        // Uploads still work when browser policy disables persistent storage.
    }

    if (storedId) {
        try {
            const existing = await api(`/imports/${storedId}`);
            const sameSource = existing.persona_id === state.personaId
                && existing.source_name === sourceName
                && existing.total_bytes === file.size;
            if (sameSource && ['created', 'uploading'].includes(existing.state)) {
                return {job: existing, key, completed: false};
            }
            if (sameSource && existing.state === 'uploaded') {
                localStorage.removeItem(key);
                return {job: existing, key, completed: true};
            }
        } catch (_error) {
            // A stale or inaccessible task is replaced below.
        }
        try {
            localStorage.removeItem(key);
        } catch (_error) {
            // Persistent storage is an optimization, not an upload dependency.
        }
    }

    const job = await postJson('/imports', {
        persona_id: state.personaId,
        source_name: sourceName,
        total_bytes: file.size,
        media_type: file.type || 'application/octet-stream',
    });
    try {
        localStorage.setItem(key, job.id);
    } catch (_error) {
        // Continue without cross-refresh resume if storage is unavailable.
    }
    return {job, key, completed: false};
}

async function missingChunksForFile(job, file) {
    const expectedChunks = Math.ceil(file.size / CHUNK_BYTES);
    return api(`/imports/${encodeURIComponent(job.id)}/missing-chunks?expected_chunks=${expectedChunks}`);
}

async function uploadSelectedFiles() {
    const totalBytes = selectedBytes();
    if (state.upload.running || !state.personaId || !state.selectedFiles.length || totalBytes > MAX_IMPORT_BYTES) return;
    state.upload.running = true;
    state.upload.paused = false;
    state.upload.controller = new AbortController();
    state.upload.uploadedBytes = 0;
    state.upload.totalBytes = totalBytes;
    state.imported = false;
    elements.uploadProgress.hidden = false;
    let uploadedBytes = 0;

    try {
        for (const file of state.selectedFiles) {
            if (state.upload.paused) return;
            setStatus(elements.uploadStatus, `正在导入 ${file.name}`);
            const {job, key, completed} = await resolveImportJob(file);
            if (completed) {
                uploadedBytes += file.size;
                state.upload.uploadedBytes = uploadedBytes;
                updateProgress(totalBytes ? uploadedBytes / totalBytes : 1);
                continue;
            }

            const uploadStatus = await missingChunksForFile(job, file);
            if (state.upload.paused) return;
            uploadedBytes += uploadStatus.received_bytes || 0;
            state.upload.uploadedBytes = uploadedBytes;
            updateProgress(totalBytes ? uploadedBytes / totalBytes : 1);

            for (const index of uploadStatus.missing_chunks || []) {
                if (state.upload.paused) return;
                const offset = index * CHUNK_BYTES;
                if (offset >= file.size) continue;
                const chunk = file.slice(offset, Math.min(offset + CHUNK_BYTES, file.size));
                const digest = await sha256Hex(await chunk.arrayBuffer());
                await uploadChunkWithRetry(job.id, index, chunk, digest, state.upload.controller.signal);
                uploadedBytes += chunk.size;
                state.upload.uploadedBytes = uploadedBytes;
                updateProgress(totalBytes ? uploadedBytes / totalBytes : 1);
            }
            if (state.upload.paused) return;
            await postJson(`/imports/${job.id}/complete`, {});
            try {
                localStorage.removeItem(key);
            } catch (_error) {
                // A completed upload does not depend on local cleanup succeeding.
            }
        }
        state.imported = true;
        updateProgress(1);
        setStatus(elements.uploadStatus, `已导入 ${state.selectedFiles.length} 个文件`, 'success');
        completeStep('import', 'model');
    } catch (error) {
        if (state.upload.paused || error?.name === 'AbortError') {
            state.upload.paused = true;
            setStatus(elements.uploadStatus, '上传已暂停，可继续导入');
        } else {
            setStatus(elements.uploadStatus, error.message, 'error');
        }
    } finally {
        state.upload.running = false;
        state.upload.controller = null;
        updateControls();
    }
}

async function uploadChunkWithRetry(importId, index, chunk, digest, signal) {
    let lastError;
    for (let attempt = 1; attempt <= MAX_CHUNK_ATTEMPTS; attempt += 1) {
        try {
            return await api(`/imports/${importId}/chunks/${index}`, {
                method: 'PUT',
                headers: {
                    'Content-Type': 'application/octet-stream',
                    'X-Chunk-Sha256': digest,
                },
                body: chunk,
                signal,
            });
        } catch (error) {
            if (error?.name === 'AbortError' || state.upload.paused) throw error;
            lastError = error;
            if (attempt < MAX_CHUNK_ATTEMPTS) {
                await new Promise(resolve => window.setTimeout(resolve, 300 * attempt));
            }
        }
    }
    throw lastError;
}

function toggleUploadPause() {
    if (state.upload.running) {
        state.upload.paused = true;
        state.upload.controller?.abort();
        setStatus(elements.uploadStatus, '上传已暂停，可继续导入');
        updateControls();
        return;
    }
    if (state.upload.paused) {
        state.upload.paused = false;
        setStatus(elements.uploadStatus, '正在恢复上传');
        void uploadSelectedFiles();
    }
}

async function sha256Hex(buffer) {
    const digest = await crypto.subtle.digest('SHA-256', buffer);
    return Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('');
}

function updateProgress(ratio) {
    const percent = Math.max(0, Math.min(100, Math.round(ratio * 100)));
    elements.uploadProgressValue.style.width = `${percent}%`;
    elements.uploadProgress.setAttribute('aria-label', `导入进度 ${percent}%`);
}

async function loadProviders() {
    elements.refreshProvidersButton.disabled = true;
    setStatus(elements.modelStatus, '读取中');
    try {
        const payload = await api('/providers');
        state.providers = payload.providers;
        renderProviders();
        setStatus(elements.modelStatus, '');
    } catch (error) {
        setStatus(elements.modelStatus, error.message, 'error');
    } finally {
        elements.refreshProvidersButton.disabled = false;
    }
}

function renderProviders() {
    elements.providerSelect.replaceChildren();
    const placeholder = document.createElement('option');
    placeholder.value = '';
    placeholder.textContent = '选择已配置的供应商';
    placeholder.selected = true;
    elements.providerSelect.appendChild(placeholder);
    state.providers.forEach(provider => {
        const option = document.createElement('option');
        option.value = provider.id;
        option.textContent = provider.configured ? provider.display_name : `${provider.display_name} / 未配置`;
        option.disabled = !provider.configured;
        elements.providerSelect.appendChild(option);
    });
    elements.providerSelect.disabled = !state.providers.some(provider => provider.configured);
    renderModels();
}

function renderModels() {
    const provider = state.providers.find(item => item.id === elements.providerSelect.value);
    elements.modelSelect.replaceChildren();
    (provider?.models || []).forEach(model => {
        const option = document.createElement('option');
        option.value = model.id;
        option.textContent = model.display_name;
        elements.modelSelect.appendChild(option);
    });
    elements.modelSelect.disabled = !provider?.models?.length;
    state.providerId = provider?.id || null;
    renderModelMetadata();
}

function renderModelMetadata() {
    const provider = state.providers.find(item => item.id === state.providerId);
    const model = provider?.models?.find(item => item.id === elements.modelSelect.value);
    state.modelId = model?.id || null;
    elements.modelCapability.textContent = model?.capabilities?.join(' · ') || '需在服务端配置模型';
    elements.modelPricing.textContent = provider?.pricing_source === 'local' ? '本地算力' : '价格以供应商为准';
    elements.activeModelName.textContent = model ? `${provider.display_name} / ${model.display_name}` : '未选择';
    updateControls();
}

async function sendMessage(event) {
    event.preventDefault();
    const content = elements.messageInput.value.trim();
    if (!content || !state.providerId || !state.modelId) return;
    const userMessage = {role: 'user', content};
    state.messages.push(userMessage);
    appendMessage(userMessage);
    elements.messageInput.value = '';
    elements.sendButton.disabled = true;
    setStatus(elements.chatStatus, '回复中');

    try {
        const response = await postJson('/chat', {
            provider_id: state.providerId,
            model_id: state.modelId,
            messages: state.messages,
        });
        const assistantMessage = {role: 'assistant', content: response.content};
        state.messages.push(assistantMessage);
        appendMessage(assistantMessage);
        setStatus(elements.chatStatus, '');
        completeStep('model', 'chat');
    } catch (error) {
        state.messages.pop();
        setStatus(elements.chatStatus, error.message, 'error');
    } finally {
        updateControls();
        elements.messageInput.focus();
    }
}

function appendMessage(message) {
    elements.emptyChat?.remove();
    const article = document.createElement('article');
    article.className = `message ${message.role === 'user' ? 'user-message' : 'assistant-message'}`;
    const label = document.createElement('span');
    label.className = 'message-role';
    label.textContent = message.role === 'user' ? '你' : elements.activePersonaName.textContent;
    const content = document.createElement('p');
    content.textContent = message.content;
    article.append(label, content);
    elements.chatHistory.appendChild(article);
    elements.chatHistory.scrollTop = elements.chatHistory.scrollHeight;
}

function updateControls() {
    const totalBytes = selectedBytes();
    const uploadActive = state.upload.running;
    elements.chatFile.disabled = !state.personaId || uploadActive;
    elements.chatFolder.disabled = !state.personaId || uploadActive;
    elements.clearFilesButton.disabled = uploadActive;
    elements.uploadButton.disabled = uploadActive || state.upload.paused || !state.personaId || !state.selectedFiles.length || totalBytes > MAX_IMPORT_BYTES;
    elements.pauseUploadButton.hidden = !uploadActive && !state.upload.paused;
    elements.pauseUploadButton.disabled = uploadActive ? state.upload.paused : !state.upload.paused;
    elements.pauseUploadButton.textContent = state.upload.paused ? '继续导入' : '暂停导入';
    const canChat = Boolean(state.personaId && state.providerId && state.modelId);
    elements.messageInput.disabled = !canChat;
    elements.sendButton.disabled = !canChat || !elements.messageInput.value.trim();
}

function selectedBytes() {
    return state.selectedFiles.reduce((sum, file) => sum + file.size, 0);
}

function formatBytes(value) {
    if (value < 1024) return `${value} B`;
    if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KiB`;
    if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(1)} MiB`;
    return `${(value / 1024 ** 3).toFixed(2)} GiB`;
}

async function checkHealth() {
    try {
        await api('/health');
        elements.serviceState.classList.add('is-online');
        elements.serviceStateText.textContent = '服务在线';
    } catch (_error) {
        elements.serviceState.classList.remove('is-online');
        elements.serviceStateText.textContent = '服务离线';
    }
}

elements.personaForm.addEventListener('submit', createPersona);
elements.personaForm.querySelectorAll('input[name="relationship_type"]').forEach(input => input.addEventListener('change', onRelationshipChange));
elements.chatFile.addEventListener('change', event => selectFiles(event.target.files, elements.chatFolder));
elements.chatFolder.addEventListener('change', event => selectFiles(event.target.files, elements.chatFile));
elements.clearFilesButton.addEventListener('click', clearFiles);
elements.uploadButton.addEventListener('click', uploadSelectedFiles);
elements.pauseUploadButton.addEventListener('click', toggleUploadPause);
elements.providerSelect.addEventListener('change', renderModels);
elements.modelSelect.addEventListener('change', renderModelMetadata);
elements.refreshProvidersButton.addEventListener('click', loadProviders);
elements.messageForm.addEventListener('submit', sendMessage);
elements.messageInput.addEventListener('input', updateControls);
elements.messageInput.addEventListener('keydown', event => {
    if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        elements.messageForm.requestSubmit();
    }
});

bootstrapSession()
    .then(() => Promise.all([checkHealth(), loadProviders()]))
    .catch(() => checkHealth());
updateControls();
