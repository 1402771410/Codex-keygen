/**
 * 临时邮箱池管理页面
 */

const elements = {
    rulesTotal: document.getElementById('rules-total'),
    rulesEnabled: document.getElementById('rules-enabled'),
    globalEnabledText: document.getElementById('global-enabled-text'),
    globalBaseUrlText: document.getElementById('global-base-url-text'),

    globalForm: document.getElementById('global-tempmail-form'),
    globalBaseUrl: document.getElementById('global-base-url'),
    globalEnabled: document.getElementById('global-enabled'),
    globalSelectionMode: document.getElementById('global-selection-mode'),
    globalSingleServiceGroup: document.getElementById('global-single-service-group'),
    globalSingleServiceId: document.getElementById('global-single-service-id'),
    testGlobalTempmailBtn: document.getElementById('test-global-tempmail-btn'),

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
};

const fallbackProviders = [
    { value: 'tempmail_lol', label: 'Tempmail.lol', description: 'Token inbox 接口' },
    { value: 'mail_tm', label: 'Mail.tm', description: 'JWT REST 接口' },
    { value: 'mail_gw', label: 'Mail.gw', description: 'JWT REST 接口' },
    { value: 'onesecmail', label: '1secmail', description: 'Query Action 接口' },
    { value: 'guerrillamail', label: 'GuerrillaMail', description: 'Session Query 接口' },
];

let tempmailRules = [];
let providerOptions = [...fallbackProviders];
let globalSelectionMode = 'single';
let globalSingleServiceId = null;

document.addEventListener('DOMContentLoaded', async () => {
    bindEvents();
    await Promise.all([loadProviderOptions(), loadGlobalTempmailSettings(), loadTempmailRules()]);
});

function bindEvents() {
    if (elements.globalForm) {
        elements.globalForm.addEventListener('submit', handleSaveGlobalTempmailSettings);
    }
    if (elements.globalSelectionMode) {
        elements.globalSelectionMode.addEventListener('change', () => {
            globalSelectionMode = elements.globalSelectionMode.value || 'single';
            updateSingleServiceVisibility();
        });
    }
    if (elements.testGlobalTempmailBtn) {
        elements.testGlobalTempmailBtn.addEventListener('click', handleTestGlobalTempmail);
    }
    if (elements.addRuleBtn) {
        elements.addRuleBtn.addEventListener('click', () => openRuleModal());
    }
    if (elements.closeRuleModal) {
        elements.closeRuleModal.addEventListener('click', closeRuleModal);
    }
    if (elements.cancelRuleBtn) {
        elements.cancelRuleBtn.addEventListener('click', closeRuleModal);
    }
    if (elements.ruleModal) {
        elements.ruleModal.addEventListener('click', (event) => {
            if (event.target === elements.ruleModal) {
                closeRuleModal();
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

async function loadGlobalTempmailSettings() {
    try {
        const settings = await api.get('/settings/tempmail');
        const baseUrl = settings.base_url || settings.api_url || '';
        const enabled = settings.enabled !== false;

        globalSelectionMode = settings.selection_mode || 'single';
        globalSingleServiceId = settings.single_service_id ?? null;

        if (elements.globalBaseUrl) {
            elements.globalBaseUrl.value = baseUrl;
        }
        if (elements.globalEnabled) {
            elements.globalEnabled.checked = enabled;
        }
        if (elements.globalSelectionMode) {
            elements.globalSelectionMode.value = globalSelectionMode;
        }

        if (elements.globalBaseUrlText) {
            elements.globalBaseUrlText.textContent = baseUrl || '-';
        }
        if (elements.globalEnabledText) {
            elements.globalEnabledText.textContent = enabled ? '启用' : '关闭';
        }

        renderSingleServiceOptions();
        updateSingleServiceVisibility();
    } catch (error) {
        toast.error(`加载全局配置失败: ${error.message}`);
    }
}

function updateSingleServiceVisibility() {
    if (!elements.globalSingleServiceGroup) {
        return;
    }
    elements.globalSingleServiceGroup.style.display = globalSelectionMode === 'single' ? 'block' : 'none';
}

function renderSingleServiceOptions() {
    if (!elements.globalSingleServiceId) {
        return;
    }

    const selectableRules = tempmailRules.filter((service) => service.enabled);

    const options = [
        '<option value="">自动选择首个启用规则</option>',
        ...selectableRules.map((service) => {
            const providerLabel = service.provider_label || service.provider || 'Tempmail';
            return `<option value="${service.id}">${escapeHtml(`${service.name} / ${providerLabel}`)}</option>`;
        }),
    ];
    elements.globalSingleServiceId.innerHTML = options.join('');

    if (globalSingleServiceId && selectableRules.some((service) => service.id === globalSingleServiceId)) {
        elements.globalSingleServiceId.value = String(globalSingleServiceId);
    } else {
        elements.globalSingleServiceId.value = '';
    }
}

async function handleSaveGlobalTempmailSettings(event) {
    event.preventDefault();

    const baseUrl = elements.globalBaseUrl?.value?.trim() || '';
    const enabled = Boolean(elements.globalEnabled?.checked);
    const selectionMode = elements.globalSelectionMode?.value || 'single';
    const rawSingleServiceId = elements.globalSingleServiceId?.value || '';
    const singleServiceId = rawSingleServiceId ? Number(rawSingleServiceId) : null;

    if (!baseUrl) {
        toast.error('默认 API 地址不能为空');
        return;
    }

    try {
        await api.post('/settings/tempmail', {
            base_url: baseUrl,
            enabled,
            selection_mode: selectionMode,
            single_service_id: selectionMode === 'single' ? singleServiceId : null,
        });
        toast.success('全局临时邮箱配置已保存');
        await Promise.all([loadGlobalTempmailSettings(), loadTempmailRules()]);
    } catch (error) {
        toast.error(`保存失败: ${error.message}`);
    }
}

async function handleTestGlobalTempmail() {
    const baseUrl = elements.globalBaseUrl?.value?.trim() || '';
    if (!baseUrl) {
        toast.error('请先填写默认 API 地址');
        return;
    }

    const button = elements.testGlobalTempmailBtn;
    if (button) {
        button.disabled = true;
        button.textContent = '测试中...';
    }

    try {
        const result = await api.post('/email-services/test-tempmail', { base_url: baseUrl, provider: 'tempmail_lol' });
        if (result.success) {
            toast.success(result.message || '连接正常');
        } else {
            toast.error(result.message || '连接失败');
        }
    } catch (error) {
        toast.error(`测试失败: ${error.message}`);
    } finally {
        if (button) {
            button.disabled = false;
            button.textContent = '🔌 测试全局连接';
        }
    }
}

async function loadTempmailRules() {
    try {
        const result = await api.get('/email-services?service_type=tempmail');
        tempmailRules = Array.isArray(result.services) ? result.services : [];
        renderTempmailRulesTable();
        updateStats();
        renderSingleServiceOptions();
    } catch (error) {
        elements.rulesTable.innerHTML = `
            <tr>
                <td colspan="8">
                    <div class="empty-state">
                        <div class="empty-state-icon">❌</div>
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
    if (elements.rulesTotal) {
        elements.rulesTotal.textContent = String(tempmailRules.length);
    }
    if (elements.rulesEnabled) {
        elements.rulesEnabled.textContent = String(enabledCount);
    }
}

function renderTempmailRulesTable() {
    if (!elements.rulesTable) {
        return;
    }

    if (tempmailRules.length === 0) {
        elements.rulesTable.innerHTML = `
            <tr>
                <td colspan="8">
                    <div class="empty-state">
                        <div class="empty-state-icon">📭</div>
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

        const fixedTag = item.is_immutable
            ? '<span style="display:inline-block;padding:2px 8px;border-radius:10px;background:rgba(255,183,3,.14);color:#ad6800;font-size:12px;">固定</span>'
            : '';

        const editButton = item.is_immutable
            ? ''
            : `<button type="button" class="btn btn-secondary btn-sm" onclick="editTempmailRule(${item.id})">编辑</button>`;
        const deleteButton = item.is_immutable
            ? ''
            : `<button type="button" class="btn btn-danger btn-sm" onclick="deleteTempmailRule(${item.id}, '${escapeHtml(item.name || '')}')">删除</button>`;
        const testButton = item.is_immutable
            ? ''
            : `<button type="button" class="btn btn-secondary btn-sm" onclick="testTempmailRule(${item.id})">测试</button>`;

        return `
            <tr>
                <td>${item.id}</td>
                <td>${escapeHtml(item.name || '-')} ${fixedTag}</td>
                <td>${escapeHtml(providerLabel)}</td>
                <td style="font-size: 0.8125rem; color: var(--text-secondary);">${summaryParts.join(' / ')}</td>
                <td>${item.priority ?? 0}</td>
                <td>${item.enabled ? '<span class="status-badge active">启用</span>' : '<span class="status-badge disabled">禁用</span>'}</td>
                <td>${format.date(item.last_used)}</td>
                <td>
                    <div style="display:flex;gap:4px;align-items:center;white-space:nowrap;flex-wrap:wrap;">
                        ${editButton}
                        ${testButton}
                        <button type="button" class="btn btn-secondary btn-sm" onclick="toggleTempmailRule(${item.id}, ${!item.enabled})">${item.enabled ? '禁用' : '启用'}</button>
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
    elements.ruleEnabled.checked = isEdit ? Boolean(rule.enabled) : true;

    elements.ruleModal.classList.add('active');
}

function closeRuleModal() {
    elements.ruleModal.classList.remove('active');
    elements.ruleForm.reset();
    elements.ruleId.value = '';
    if (elements.ruleProvider) {
        elements.ruleProvider.disabled = false;
    }
}

async function handleSaveRule(event) {
    event.preventDefault();

    const ruleId = elements.ruleId.value.trim();
    const name = elements.ruleName.value.trim();
    const provider = elements.ruleProvider?.value || 'tempmail_lol';
    const priority = Number(elements.rulePriority.value || 0);
    const timeout = Number(elements.ruleTimeout.value || 30);
    const maxRetries = Number(elements.ruleMaxRetries.value || 3);

    if (!name) {
        toast.error('规则名称不能为空');
        return;
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
                enabled: Boolean(elements.ruleEnabled.checked),
                config,
            });
            toast.success('规则已更新');
        } else {
            await api.post('/email-services', {
                service_type: 'tempmail',
                provider,
                name,
                priority,
                enabled: Boolean(elements.ruleEnabled.checked),
                config,
            });
            toast.success('规则已创建');
        }

        closeRuleModal();
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
    try {
        const result = await api.post(`/email-services/${ruleId}/test`);
        if (result.success) {
            toast.success(result.message || '规则连接正常');
        } else {
            toast.error(result.message || '规则连接失败');
        }
    } catch (error) {
        toast.error(`测试失败: ${error.message}`);
    }
}

async function toggleTempmailRule(ruleId, enabled) {
    const endpoint = enabled ? 'enable' : 'disable';
    try {
        await api.post(`/email-services/${ruleId}/${endpoint}`);
        toast.success(enabled ? '规则已启用' : '规则已禁用');
        await Promise.all([loadGlobalTempmailSettings(), loadTempmailRules()]);
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
