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
    ruleHttpConfig: document.getElementById('rule-http-config'),
    ruleBaseUrl: document.getElementById('rule-base-url'),
    ruleAddressPrefix: document.getElementById('rule-address-prefix'),
    rulePreferredDomain: document.getElementById('rule-preferred-domain'),
    rulePop3Config: document.getElementById('rule-pop3-config'),
    ruleBaseEmail: document.getElementById('rule-base-email'),
    ruleAliasLength: document.getElementById('rule-alias-length'),
    ruleAliasCharset: document.getElementById('rule-alias-charset'),
    rulePop3Host: document.getElementById('rule-pop3-host'),
    rulePop3Port: document.getElementById('rule-pop3-port'),
    rulePop3Username: document.getElementById('rule-pop3-username'),
    rulePop3Password: document.getElementById('rule-pop3-password'),
    rulePop3UseSsl: document.getElementById('rule-pop3-use-ssl'),
    rulePop3PollInterval: document.getElementById('rule-pop3-poll-interval'),
    ruleSubjectKeyword: document.getElementById('rule-subject-keyword'),
    ruleSenderKeyword: document.getElementById('rule-sender-keyword'),
    ruleTimeout: document.getElementById('rule-timeout'),
    ruleMaxRetriesGroup: document.getElementById('rule-max-retries-group'),
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
    { value: 'pop3_alias', label: '普通邮箱 POP3+Alias', description: '主邮箱+随机别名，POP3 收验证码' },
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

function isPop3AliasProvider(provider) {
    return String(provider || '').trim().toLowerCase() === 'pop3_alias';
}

function syncProviderSpecificFields(provider) {
    const isPop3Alias = isPop3AliasProvider(provider);

    if (elements.ruleHttpConfig) {
        elements.ruleHttpConfig.style.display = isPop3Alias ? 'none' : 'block';
    }
    if (elements.ruleMaxRetriesGroup) {
        elements.ruleMaxRetriesGroup.style.display = isPop3Alias ? 'none' : 'block';
    }
    if (elements.rulePop3Config) {
        elements.rulePop3Config.style.display = isPop3Alias ? 'block' : 'none';
    }

    if (elements.ruleBaseUrl) {
        elements.ruleBaseUrl.disabled = isPop3Alias;
        if (isPop3Alias) {
            elements.ruleBaseUrl.value = '';
        }
    }
    if (elements.ruleAddressPrefix) {
        elements.ruleAddressPrefix.disabled = isPop3Alias;
        if (isPop3Alias) {
            elements.ruleAddressPrefix.value = '';
        }
    }
    if (elements.rulePreferredDomain) {
        elements.rulePreferredDomain.disabled = isPop3Alias;
        if (isPop3Alias) {
            elements.rulePreferredDomain.value = '';
        }
    }
    if (elements.ruleMaxRetries) {
        elements.ruleMaxRetries.disabled = isPop3Alias;
    }
}

function syncPop3SslPort() {
    if (!elements.rulePop3UseSsl || !elements.rulePop3Port) {
        return;
    }
    const port = Number(elements.rulePop3Port.value || 0);
    if (!port || port === 995 || port === 110) {
        elements.rulePop3Port.value = elements.rulePop3UseSsl.checked ? '995' : '110';
    }
}

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
    if (elements.ruleProvider) {
        elements.ruleProvider.addEventListener('change', () => {
            syncProviderSpecificFields(elements.ruleProvider.value);
        });
    }
    if (elements.rulePop3UseSsl) {
        elements.rulePop3UseSsl.addEventListener('change', syncPop3SslPort);
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

    if (!elements.ruleProvider.value && providerOptions.length > 0) {
        elements.ruleProvider.value = providerOptions[0].value;
    }
    syncProviderSpecificFields(elements.ruleProvider.value);
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
        if (item.provider !== 'pop3_alias') {
            if (config.base_url) {
                summaryParts.push(`API: ${escapeHtml(config.base_url)}`);
            }
            if (config.address_prefix) {
                summaryParts.push(`前缀: ${escapeHtml(config.address_prefix)}`);
            }
            if (config.preferred_domain) {
                summaryParts.push(`域名: ${escapeHtml(config.preferred_domain)}`);
            }
        }
        if (item.provider === 'pop3_alias') {
            if (config.base_email) {
                summaryParts.push(`主邮箱: ${escapeHtml(config.base_email)}`);
            }
            if (config.pop3_host) {
                const pop3Port = Number(config.pop3_port || 995);
                summaryParts.push(`POP3: ${escapeHtml(config.pop3_host)}:${pop3Port}`);
            }
            if (config.alias_length) {
                summaryParts.push(`别名长度: ${Number(config.alias_length)}`);
            }
            if (config.alias_charset) {
                const charsetTextMap = {
                    digits: '仅数字',
                    lower: '仅小写字母',
                    loweralnum: '小写+数字',
                    mixedalnum: '大小写+数字',
                };
                const charsetLabel = charsetTextMap[String(config.alias_charset)] || String(config.alias_charset);
                summaryParts.push(`字符集: ${escapeHtml(charsetLabel)}`);
            }
            summaryParts.push(`SSL: ${config.use_ssl === false ? '关闭' : '开启'}`);
            summaryParts.push(`轮询: ${Number(config.poll_interval || 5)}s`);
        }
        summaryParts.push(`超时: ${Number(config.timeout || 30)}s`);
        if (item.provider !== 'pop3_alias') {
            summaryParts.push(`重试: ${Number(config.max_retries || 3)}`);
        }

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

    const isPop3Alias = isPop3AliasProvider(provider);
    elements.ruleBaseUrl.value = isPop3Alias ? '' : (config.base_url || '');
    elements.ruleAddressPrefix.value = isPop3Alias ? '' : (config.address_prefix || '');
    elements.rulePreferredDomain.value = isPop3Alias ? '' : (config.preferred_domain || '');
    if (elements.ruleBaseEmail) {
        elements.ruleBaseEmail.value = config.base_email || '';
    }
    if (elements.ruleAliasLength) {
        elements.ruleAliasLength.value = String(config.alias_length || 8);
    }
    if (elements.ruleAliasCharset) {
        elements.ruleAliasCharset.value = config.alias_charset || 'loweralnum';
    }
    if (elements.rulePop3Host) {
        elements.rulePop3Host.value = config.pop3_host || '';
    }
    if (elements.rulePop3Port) {
        elements.rulePop3Port.value = String(config.pop3_port || 995);
    }
    if (elements.rulePop3Username) {
        elements.rulePop3Username.value = config.pop3_username || '';
    }
    if (elements.rulePop3Password) {
        elements.rulePop3Password.value = config.pop3_password || '';
    }
    if (elements.rulePop3UseSsl) {
        const useSslRaw = config.use_ssl;
        elements.rulePop3UseSsl.checked = typeof useSslRaw === 'boolean'
            ? useSslRaw
            : String(useSslRaw || 'true').toLowerCase() !== 'false';
    }
    if (elements.rulePop3PollInterval) {
        elements.rulePop3PollInterval.value = String(config.poll_interval || 5);
    }
    if (elements.ruleSubjectKeyword) {
        elements.ruleSubjectKeyword.value = config.subject_keyword || '';
    }
    if (elements.ruleSenderKeyword) {
        elements.ruleSenderKeyword.value = config.sender_keyword || '';
    }
    elements.ruleTimeout.value = String(config.timeout || 30);
    elements.ruleMaxRetries.value = String(config.max_retries || 3);

    syncProviderSpecificFields(provider);

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
    syncProviderSpecificFields(elements.ruleProvider?.value || 'tempmail_lol');
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
    const isPop3Alias = isPop3AliasProvider(provider);

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
        timeout: Number.isFinite(timeout) ? Math.max(5, timeout) : 30,
    };

    if (isPop3Alias) {
        const baseEmail = (elements.ruleBaseEmail?.value || '').trim();
        const pop3Host = (elements.rulePop3Host?.value || '').trim();
        const pop3Port = Number(elements.rulePop3Port?.value || 995);
        const pop3Username = (elements.rulePop3Username?.value || '').trim();
        const pop3Password = elements.rulePop3Password?.value || '';
        const aliasLength = Number(elements.ruleAliasLength?.value || 8);
        const aliasCharset = (elements.ruleAliasCharset?.value || 'loweralnum').trim();
        const pollInterval = Number(elements.rulePop3PollInterval?.value || 5);
        const useSsl = Boolean(elements.rulePop3UseSsl?.checked);
        const subjectKeyword = (elements.ruleSubjectKeyword?.value || '').trim();
        const senderKeyword = (elements.ruleSenderKeyword?.value || '').trim();

        if (!baseEmail || !pop3Host || !pop3Username || !pop3Password) {
            toast.error('POP3 无限邮箱配置不完整，请填写主邮箱、服务器、用户名和密码');
            return;
        }
        if (!Number.isInteger(pop3Port) || pop3Port < 1 || pop3Port > 65535) {
            toast.error('POP3 端口无效，请填写 1-65535 之间的整数');
            return;
        }

        config.base_email = baseEmail;
        config.pop3_host = pop3Host;
        config.pop3_port = pop3Port;
        config.pop3_username = pop3Username;
        config.pop3_password = pop3Password;
        config.use_ssl = useSsl;
        config.alias_length = Number.isInteger(aliasLength) ? Math.max(4, aliasLength) : 8;
        config.alias_charset = aliasCharset || 'loweralnum';
        config.poll_interval = Number.isInteger(pollInterval) ? Math.max(2, pollInterval) : 5;
        if (subjectKeyword) {
            config.subject_keyword = subjectKeyword;
        }
        if (senderKeyword) {
            config.sender_keyword = senderKeyword;
        }
    } else {
        const normalizedMaxRetries = Number.isFinite(maxRetries) ? Math.max(1, maxRetries) : 3;
        config.max_retries = normalizedMaxRetries;

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
