/**
 * 临时邮箱池管理页面
 */

const elements = {
    rulesTotal: document.getElementById('rules-total'),
    rulesEnabled: document.getElementById('rules-enabled'),
    rulesAvailable: document.getElementById('rules-available'),

    addRuleBtn: document.getElementById('add-rule-btn'),
    rulesTable: document.getElementById('tempmail-rules-table'),

    ruleModal: document.getElementById('rule-modal'),
    ruleModalTitle: document.getElementById('rule-modal-title'),
    closeRuleModal: document.getElementById('close-rule-modal'),
    cancelRuleBtn: document.getElementById('cancel-rule-btn'),
    ruleForm: document.getElementById('rule-form'),
    ruleId: document.getElementById('rule-id'),
    ruleName: document.getElementById('rule-name'),
    ruleProvider: document.getElementById('rule-provider'),
    rulePriority: document.getElementById('rule-priority'),
    ruleBaseUrl: document.getElementById('rule-base-url'),
    ruleAddressPrefix: document.getElementById('rule-address-prefix'),
    rulePreferredDomain: document.getElementById('rule-preferred-domain'),
    ruleTimeout: document.getElementById('rule-timeout'),
    ruleMaxRetries: document.getElementById('rule-max-retries'),
    ruleEnabled: document.getElementById('rule-enabled'),
    ruleEnabledHint: document.getElementById('rule-enabled-hint'),

    testProgressModal: document.getElementById('test-progress-modal'),
    testProgressTitle: document.getElementById('test-progress-title'),
    testProgressDetail: document.getElementById('test-progress-detail'),
    testStageBoard: document.getElementById('test-stage-board'),
};

const fallbackProviders = [
    { value: 'tempmail_lol', label: 'Tempmail.lol', description: 'Token inbox 接口' },
];

let tempmailRules = [];
let providerOptions = [...fallbackProviders];
const activeTests = new Set();

const TEST_STAGE_ORDER = ['prepare', 'create_email', 'request_openai', 'wait_otp', 'otp_received'];
const TEST_STAGE_ALIAS = {
    probe_exception: 'prepare',
    bootstrap_failed: 'prepare',
    create_email_failed: 'create_email',
    request_otp_failed: 'request_openai',
    wait_otp_timeout: 'wait_otp',
    wait_otp_failed: 'wait_otp',
    otp_received: 'otp_received',
};
const TEST_STAGE_HINTS = {
    prepare: '初始化测试资源并检查服务配置...',
    create_email: '正在创建临时邮箱地址...',
    request_openai: '正在触发 OpenAI OTP 邮件发送...',
    wait_otp: '正在轮询收件箱等待验证码邮件...',
    otp_received: '已确认收到 OTP，正在保存结果...',
};

let testStageAutoTimer = null;
let testStageAutoIndex = 0;

document.addEventListener('DOMContentLoaded', async () => {
    bindEvents();
    await Promise.all([loadProviderOptions(), loadTempmailRules()]);
});

function bindEvents() {
    if (elements.addRuleBtn) {
        elements.addRuleBtn.addEventListener('click', () => openRuleModal());
    }
    if (elements.closeRuleModal) {
        elements.closeRuleModal.addEventListener('click', closeRuleModalHandler);
    }
    if (elements.cancelRuleBtn) {
        elements.cancelRuleBtn.addEventListener('click', closeRuleModalHandler);
    }
    if (elements.ruleModal) {
        elements.ruleModal.addEventListener('click', (event) => {
            if (event.target === elements.ruleModal) {
                closeRuleModalHandler();
            }
        });
    }
    if (elements.ruleForm) {
        elements.ruleForm.addEventListener('submit', handleSaveRule);
    }
}

async function loadProviderOptions() {
    try {
        const data = await api.get('/email-services/types');
        const tempmailType = Array.isArray(data?.types)
            ? data.types.find((item) => item.value === 'tempmail')
            : null;
        const providers = Array.isArray(tempmailType?.providers) ? tempmailType.providers : [];
        if (providers.length > 0) {
            providerOptions = providers;
        }
    } catch (error) {
        console.warn('加载供应商列表失败，使用默认列表:', error.message);
    }
    renderProviderSelect();
}

function renderProviderSelect() {
    if (!elements.ruleProvider) {
        return;
    }
    elements.ruleProvider.innerHTML = providerOptions.map((provider) => {
        const label = provider.label || provider.value;
        const desc = provider.description ? ` - ${provider.description}` : '';
        return `<option value="${escapeHtml(provider.value)}">${escapeHtml(label + desc)}</option>`;
    }).join('');
}

function isRuleAvailable(rule) {
    if (typeof rule?.available === 'boolean') {
        return rule.available;
    }
    const testStatus = String(rule?.last_test_status || '').trim().toLowerCase();
    const testMessage = String(rule?.last_test_message || '').trim().toLowerCase();
    return testStatus === 'success' && testMessage.includes('[otp_received]');
}

function getTestResultMeta(rule) {
    const raw = String(rule?.last_test_message || '').trim();
    const status = String(rule?.last_test_status || '').trim().toLowerCase();

    if (!raw) {
        return {
            fullText: '未测试',
            shortText: '未测试',
            className: 'muted',
        };
    }

    const shortText = raw.length > 42 ? `${raw.slice(0, 42)}...` : raw;
    return {
        fullText: raw,
        shortText,
        className: status === 'success' ? 'success' : 'error',
    };
}

function updateRuleEnabledHint(text) {
    if (!elements.ruleEnabledHint) {
        return;
    }
    elements.ruleEnabledHint.textContent = text;
}

function syncRuleEnabledState(rule = null) {
    if (!elements.ruleEnabled) {
        return;
    }

    if (!rule) {
        elements.ruleEnabled.checked = false;
        elements.ruleEnabled.disabled = true;
        updateRuleEnabledHint('新增规则默认不可启用，请先测试通过后再启用。');
        return;
    }

    const available = isRuleAvailable(rule);
    const enabled = Boolean(rule.enabled);
    elements.ruleEnabled.checked = enabled;
    elements.ruleEnabled.disabled = !enabled && !available;

    if (enabled && available) {
        updateRuleEnabledHint('该规则已通过测试，可继续保持启用。');
        return;
    }

    if (enabled && !available) {
        updateRuleEnabledHint('该规则当前为启用状态，但尚未通过最新 OTP 测试，建议重新测试。');
        return;
    }

    if (available) {
        updateRuleEnabledHint('该规则已通过测试，可在保存时启用。');
        return;
    }

    updateRuleEnabledHint('该规则尚未通过 OTP 测试，暂不可启用。');
}

function showTestProgressModal(ruleName) {
    if (!elements.testProgressModal) {
        return;
    }
    const displayName = ruleName || '当前规则';
    if (elements.testProgressTitle) {
        elements.testProgressTitle.textContent = '测试中，请等待...';
    }
    if (elements.testProgressDetail) {
        elements.testProgressDetail.textContent = `正在验证「${displayName}」并等待真实 OTP 验证，请勿关闭页面。`;
    }
    resetTestStageBoard();
    updateTestStageBoard('prepare', 'running');
    startTestStageAutoProgress();
    elements.testProgressModal.classList.add('active');
}

function hideTestProgressModal() {
    if (!elements.testProgressModal) {
        return;
    }
    stopTestStageAutoProgress();
    elements.testProgressModal.classList.remove('active');
}

function resetTestStageBoard() {
    if (!elements.testStageBoard) {
        return;
    }
    const items = elements.testStageBoard.querySelectorAll('.test-stage-item');
    items.forEach((item) => {
        item.classList.remove('is-pending', 'is-running', 'is-success', 'is-error');
        item.classList.add('is-pending');
    });
    testStageAutoIndex = 0;
}

function normalizeTestStage(stage) {
    const normalized = String(stage || '').trim().toLowerCase();
    if (!normalized) {
        return 'wait_otp';
    }
    if (TEST_STAGE_ORDER.includes(normalized)) {
        return normalized;
    }
    if (TEST_STAGE_ALIAS[normalized]) {
        return TEST_STAGE_ALIAS[normalized];
    }
    if (normalized.includes('otp')) {
        return normalized.includes('receive') ? 'otp_received' : 'wait_otp';
    }
    if (normalized.includes('create')) {
        return 'create_email';
    }
    if (normalized.includes('openai') || normalized.includes('request')) {
        return 'request_openai';
    }
    return 'prepare';
}

function updateTestProgressDetail(text) {
    if (!elements.testProgressDetail) {
        return;
    }
    elements.testProgressDetail.textContent = text;
}

function updateTestStageBoard(stage, status) {
    if (!elements.testStageBoard) {
        return;
    }

    const normalizedStage = normalizeTestStage(stage);
    const stageIndex = TEST_STAGE_ORDER.indexOf(normalizedStage);
    const items = elements.testStageBoard.querySelectorAll('.test-stage-item');
    items.forEach((item) => {
        const itemStage = String(item.dataset.stage || '');
        const itemIndex = TEST_STAGE_ORDER.indexOf(itemStage);

        item.classList.remove('is-pending', 'is-running', 'is-success', 'is-error');

        if (itemIndex < 0 || stageIndex < 0) {
            item.classList.add('is-pending');
            return;
        }

        if (itemIndex < stageIndex) {
            item.classList.add('is-success');
            return;
        }

        if (itemIndex > stageIndex) {
            item.classList.add('is-pending');
            return;
        }

        if (status === 'success') {
            item.classList.add('is-success');
        } else if (status === 'error') {
            item.classList.add('is-error');
        } else {
            item.classList.add('is-running');
        }
    });
}

function startTestStageAutoProgress() {
    stopTestStageAutoProgress();
    testStageAutoIndex = 0;
    testStageAutoTimer = window.setInterval(() => {
        const safeIndex = Math.min(testStageAutoIndex, TEST_STAGE_ORDER.length - 1);
        const stage = TEST_STAGE_ORDER[safeIndex];
        updateTestStageBoard(stage, 'running');
        updateTestProgressDetail(TEST_STAGE_HINTS[stage] || '测试进行中，请稍候...');

        if (testStageAutoIndex < TEST_STAGE_ORDER.length - 2) {
            testStageAutoIndex += 1;
        }
    }, 1600);
}

function stopTestStageAutoProgress() {
    if (!testStageAutoTimer) {
        return;
    }
    window.clearInterval(testStageAutoTimer);
    testStageAutoTimer = null;
}

function extractStageFromMessage(message) {
    const text = String(message || '');
    const match = text.match(/^\[(?<stage>[a-zA-Z0-9_]+)]/);
    if (!match?.groups?.stage) {
        return null;
    }
    return normalizeTestStage(match.groups.stage);
}

function sleep(ms) {
    return new Promise((resolve) => {
        window.setTimeout(resolve, ms);
    });
}

async function loadTempmailRules() {
    try {
        const result = await api.get('/email-services?service_type=tempmail');
        tempmailRules = Array.isArray(result.services) ? result.services : [];
        renderTempmailRulesTable();
        updateStats();
    } catch (error) {
        elements.rulesTable.innerHTML = `
            <tr>
                <td colspan="9">
                    <div class="empty-state">
                        <div class="empty-state-icon">✕</div>
                        <div class="empty-state-title">加载失败</div>
                        <div class="empty-state-description">${escapeHtml(error.message)}</div>
                    </div>
                </td>
            </tr>
        `;
    }
}

function updateStats() {
    const enabledCount = tempmailRules.filter((item) => item.enabled).length;
    const availableCount = tempmailRules.filter((item) => isRuleAvailable(item)).length;
    if (elements.rulesTotal) {
        elements.rulesTotal.textContent = String(tempmailRules.length);
    }
    if (elements.rulesEnabled) {
        elements.rulesEnabled.textContent = String(enabledCount);
    }
    if (elements.rulesAvailable) {
        elements.rulesAvailable.textContent = String(availableCount);
    }
}

function renderTempmailRulesTable() {
    if (!elements.rulesTable) {
        return;
    }

    if (tempmailRules.length === 0) {
        elements.rulesTable.innerHTML = `
            <tr>
                <td colspan="9">
                    <div class="empty-state">
                        <div class="empty-state-icon">□</div>
                        <div class="empty-state-title">暂无临时邮箱规则</div>
                        <div class="empty-state-description">点击“添加规则”配置邮箱调用方式和参数。</div>
                    </div>
                </td>
            </tr>
        `;
        return;
    }

    elements.rulesTable.innerHTML = tempmailRules.map((item) => {
        const config = item.config || {};
        const providerLabel = item.provider_label || item.provider || 'Tempmail';
        const callStyle = item.provider_runtime_meta?.call_style || '';
        const summaryParts = [];
        if (config.base_url) {
            summaryParts.push(`API: ${escapeHtml(config.base_url)}`);
        }
        if (config.address_prefix) {
            summaryParts.push(`前缀: ${escapeHtml(config.address_prefix)}`);
        }
        if (config.preferred_domain) {
            summaryParts.push(`域名: ${escapeHtml(config.preferred_domain)}`);
        }
        summaryParts.push(`超时: ${Number(config.timeout || 30)}s`);
        summaryParts.push(`重试: ${Number(config.max_retries || 3)}`);

        const fixedTag = item.is_immutable ? '<span class="mailhub-fixed-tag">固定</span>' : '';
        const available = isRuleAvailable(item);
        const testMeta = getTestResultMeta(item);
        const testingNow = activeTests.has(item.id);

        const editButton = item.is_immutable
            ? ''
            : `<button type="button" class="semi-action-btn mini neutral" onclick="editTempmailRule(${item.id})">编辑</button>`;
        const deleteButton = item.is_immutable
            ? ''
            : `<button type="button" class="semi-action-btn mini danger" onclick="deleteTempmailRule(${item.id}, '${escapeHtml(item.name || '')}')">删除</button>`;
        const testButton = `<button type="button" class="semi-action-btn mini neutral" ${testingNow ? 'disabled' : ''} onclick="testTempmailRule(${item.id})">${testingNow ? '测试中' : '测试'}</button>`;

        const toggleToEnable = !item.enabled;
        const toggleDisabled = toggleToEnable && !available;
        const toggleHint = toggleDisabled ? '需先通过测试才可启用' : '';

        return `
            <tr>
                <td>${item.id}</td>
                <td>
                    <div class="rule-name-cell">
                        <span>${escapeHtml(item.name || '-')}</span>
                        ${fixedTag}
                    </div>
                </td>
                <td>
                    <div class="provider-text">${escapeHtml(providerLabel)}</div>
                    ${callStyle ? `<div class="provider-meta">${escapeHtml(callStyle)}</div>` : ''}
                </td>
                <td style="font-size: 0.8125rem; color: var(--text-secondary);">${summaryParts.join(' / ')}</td>
                <td>${item.priority ?? 0}</td>
                <td>${item.enabled ? '<span class="status-badge active">启用</span>' : '<span class="status-badge disabled">禁用</span>'}</td>
                <td>${available ? '<span class="status-badge running">可用</span>' : '<span class="status-badge error">不可用</span>'}</td>
                <td>
                    <span class="test-result-text ${escapeHtml(testMeta.className)}" title="${escapeHtml(testMeta.fullText)}">
                        ${escapeHtml(testMeta.shortText)}
                    </span>
                </td>
                <td>
                    <div class="rule-action-group">
                        ${editButton}
                        ${testButton}
                        <button type="button" class="semi-action-btn mini ${toggleToEnable ? 'primary' : 'neutral'}" ${toggleDisabled ? 'disabled' : ''} title="${escapeHtml(toggleHint)}" onclick="toggleTempmailRule(${item.id}, ${toggleToEnable})">${toggleToEnable ? '启用' : '停用'}</button>
                        ${deleteButton}
                    </div>
                </td>
            </tr>
        `;
    }).join('');
}

function openRuleModal(rule = null) {
    const isEdit = Boolean(rule);
    if (elements.ruleModalTitle) {
        elements.ruleModalTitle.textContent = isEdit ? '编辑临时邮箱规则' : '添加临时邮箱规则';
    }

    elements.ruleId.value = isEdit ? String(rule.id) : '';
    elements.ruleName.value = isEdit ? (rule.name || '') : '';
    elements.rulePriority.value = isEdit ? String(rule.priority ?? 0) : '0';

    const config = isEdit ? (rule.config || {}) : {};
    const provider = (isEdit ? rule.provider : null) || config.provider || 'tempmail_lol';

    renderProviderSelect();
    if (elements.ruleProvider) {
        elements.ruleProvider.value = provider;
        elements.ruleProvider.disabled = isEdit;
    }

    elements.ruleBaseUrl.value = config.base_url || '';
    elements.ruleAddressPrefix.value = config.address_prefix || '';
    elements.rulePreferredDomain.value = config.preferred_domain || '';
    elements.ruleTimeout.value = String(config.timeout || 30);
    elements.ruleMaxRetries.value = String(config.max_retries || 3);

    syncRuleEnabledState(rule);
    elements.ruleModal.classList.add('active');
}

function closeRuleModalHandler() {
    elements.ruleModal.classList.remove('active');
    elements.ruleForm.reset();
    elements.ruleId.value = '';
    if (elements.ruleProvider) {
        elements.ruleProvider.disabled = false;
    }
    syncRuleEnabledState(null);
}

async function handleSaveRule(event) {
    event.preventDefault();

    const ruleId = elements.ruleId.value.trim();
    const name = elements.ruleName.value.trim();
    const provider = elements.ruleProvider?.value || 'tempmail_lol';
    const priority = Number(elements.rulePriority.value || 0);
    const timeout = Number(elements.ruleTimeout.value || 30);
    const maxRetries = Number(elements.ruleMaxRetries.value || 3);
    const enableRequested = Boolean(elements.ruleEnabled.checked);

    if (!name) {
        toast.error('规则名称不能为空');
        return;
    }

    if (enableRequested) {
        if (!ruleId) {
            toast.warning('新增规则需要先测试通过后才能启用');
            return;
        }
        const currentRule = tempmailRules.find((item) => String(item.id) === String(ruleId));
        if (!currentRule || !isRuleAvailable(currentRule)) {
            toast.warning('该规则尚未通过真实 OTP 测试，暂不可启用');
            return;
        }
    }

    const config = {
        provider,
        timeout,
        max_retries: maxRetries,
    };
    const baseUrl = elements.ruleBaseUrl.value.trim();
    const addressPrefix = elements.ruleAddressPrefix.value.trim();
    const preferredDomain = elements.rulePreferredDomain.value.trim();

    if (baseUrl) {
        config.base_url = baseUrl;
    }
    if (addressPrefix) {
        config.address_prefix = addressPrefix;
    }
    if (preferredDomain) {
        config.preferred_domain = preferredDomain;
    }

    try {
        if (ruleId) {
            await api.patch(`/email-services/${ruleId}`, {
                name,
                priority,
                enabled: enableRequested,
                config,
            });
            toast.success('规则已更新');
        } else {
            await api.post('/email-services', {
                service_type: 'tempmail',
                provider,
                name,
                priority,
                enabled: false,
                config,
            });
            toast.success('规则已创建，请先测试通过后再启用');
        }

        closeRuleModalHandler();
        await loadTempmailRules();
    } catch (error) {
        toast.error(`保存失败: ${error.message}`);
    }
}

async function editTempmailRule(ruleId) {
    const rule = tempmailRules.find((item) => item.id === ruleId);
    if (!rule) {
        toast.error('未找到对应规则');
        return;
    }
    if (rule.is_immutable) {
        toast.warning('固定内置规则不可编辑');
        return;
    }
    openRuleModal(rule);
}

async function testTempmailRule(ruleId) {
    if (activeTests.has(ruleId)) {
        return;
    }

    const rule = tempmailRules.find((item) => item.id === ruleId);
    activeTests.add(ruleId);
    renderTempmailRulesTable();
    showTestProgressModal(rule?.name || `规则 ${ruleId}`);

    try {
        const result = await api.post(`/email-services/${ruleId}/test`);
        const persistedMessage = result?.details?.persisted_message || result?.message || '';
        const resultStage = normalizeTestStage(result?.stage || extractStageFromMessage(persistedMessage));
        if (result.success) {
            stopTestStageAutoProgress();
            updateTestStageBoard(resultStage, 'success');
            if (resultStage !== 'otp_received') {
                updateTestStageBoard('otp_received', 'success');
            }
            updateTestProgressDetail(persistedMessage || TEST_STAGE_HINTS.otp_received);
            toast.success(persistedMessage || '规则测试通过，已可用于启用');
        } else {
            stopTestStageAutoProgress();
            updateTestStageBoard(resultStage, 'error');
            updateTestProgressDetail(persistedMessage || result.message || '规则测试失败');
            toast.error(persistedMessage || result.message || '规则测试失败');
        }
    } catch (error) {
        stopTestStageAutoProgress();
        updateTestStageBoard('wait_otp', 'error');
        updateTestProgressDetail(`测试异常：${error.message}`);
        toast.error(`测试失败: ${error.message}`);
    } finally {
        activeTests.delete(ruleId);
        await sleep(900);
        hideTestProgressModal();
        await loadTempmailRules();
    }
}

async function toggleTempmailRule(ruleId, enabled) {
    if (activeTests.has(ruleId)) {
        toast.warning('规则正在测试中，请稍后再操作');
        return;
    }

    if (enabled) {
        const target = tempmailRules.find((item) => item.id === ruleId);
        if (!target || !isRuleAvailable(target)) {
            toast.warning('规则未通过真实 OTP 测试，暂不可启用');
            return;
        }
    }

    const endpoint = enabled ? 'enable' : 'disable';
    try {
        await api.post(`/email-services/${ruleId}/${endpoint}`);
        toast.success(enabled ? '规则已启用' : '规则已禁用');
        await loadTempmailRules();
    } catch (error) {
        toast.error(`操作失败: ${error.message}`);
    }
}

async function deleteTempmailRule(ruleId, name) {
    const rule = tempmailRules.find((item) => item.id === ruleId);
    if (rule?.is_immutable) {
        toast.warning('固定内置规则不可删除');
        return;
    }

    const confirmed = await confirm(`确认删除规则「${name || ruleId}」吗？`);
    if (!confirmed) {
        return;
    }

    try {
        await api.delete(`/email-services/${ruleId}`);
        toast.success('规则已删除');
        await loadTempmailRules();
    } catch (error) {
        toast.error(`删除失败: ${error.message}`);
    }
}

function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

window.editTempmailRule = editTempmailRule;
window.testTempmailRule = testTempmailRule;
window.toggleTempmailRule = toggleTempmailRule;
window.deleteTempmailRule = deleteTempmailRule;
