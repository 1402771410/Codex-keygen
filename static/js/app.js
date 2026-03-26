/**
 * 注册页面 JavaScript
 * 使用 utils.js 中的工具库
 */

// 状态
let currentTask = null;
let currentBatch = null;
let logPollingInterval = null;
let batchPollingInterval = null;
let accountsPollingInterval = null;
let isBatchMode = false;
let isLoopMode = false;
let taskCompleted = false;  // 标记任务是否已完成
let batchCompleted = false;  // 标记批量任务是否已完成
let taskFinalStatus = null;  // 保存任务的最终状态
let batchFinalStatus = null;  // 保存批量任务的最终状态
let displayedLogs = new Set();  // 用于日志去重
let toastShown = false;  // 标记是否已显示过 toast
let availableServices = {
    tempmail: { available: true, services: [] }
};

// WebSocket 相关变量
let webSocket = null;
let batchWebSocket = null;  // 批量任务 WebSocket
let useWebSocket = true;  // 是否使用 WebSocket
let wsHeartbeatInterval = null;  // 心跳定时器
let batchWsHeartbeatInterval = null;  // 批量任务心跳定时器
let activeTaskUuid = null;   // 当前活跃的单任务 UUID（用于页面重新可见时重连）
let activeBatchId = null;    // 当前活跃的批量任务 ID（用于页面重新可见时重连）
const ACTIVE_TASK_STORAGE_KEY = 'activeTask';

// DOM 元素
const elements = {
    form: document.getElementById('registration-form'),
    emailService: document.getElementById('email-service'),
    regMode: document.getElementById('reg-mode'),
    regModeGroup: document.getElementById('reg-mode-group'),
    batchCountGroup: document.getElementById('batch-count-group'),
    batchCount: document.getElementById('batch-count'),
    loopWindowGroup: document.getElementById('loop-window-group'),
    loopWindowStart: document.getElementById('loop-window-start'),
    loopWindowEnd: document.getElementById('loop-window-end'),
    batchOptions: document.getElementById('batch-options'),
    intervalMin: document.getElementById('interval-min'),
    intervalMax: document.getElementById('interval-max'),
    startBtn: document.getElementById('start-btn'),
    cancelBtn: document.getElementById('cancel-btn'),
    taskStatusRow: document.getElementById('task-status-row'),
    batchProgressSection: document.getElementById('batch-progress-section'),
    consoleLog: document.getElementById('console-log'),
    clearLogBtn: document.getElementById('clear-log-btn'),
    // 任务状态
    taskId: document.getElementById('task-id'),
    taskEmail: document.getElementById('task-email'),
    taskStatus: document.getElementById('task-status'),
    taskService: document.getElementById('task-service'),
    taskStatusBadge: document.getElementById('task-status-badge'),
    // 批量状态
    batchProgressText: document.getElementById('batch-progress-text'),
    batchProgressPercent: document.getElementById('batch-progress-percent'),
    progressBar: document.getElementById('progress-bar'),
    batchSuccess: document.getElementById('batch-success'),
    batchFailed: document.getElementById('batch-failed'),
    batchRemaining: document.getElementById('batch-remaining'),
    // 已注册账号
    recentAccountsTable: document.getElementById('recent-accounts-table'),
    refreshAccountsBtn: document.getElementById('refresh-accounts-btn'),
    // 批量并发控件
    concurrencyMode: document.getElementById('concurrency-mode'),
    concurrencyCount: document.getElementById('concurrency-count'),
    concurrencyHint: document.getElementById('concurrency-hint'),
    intervalGroup: document.getElementById('interval-group'),
    // 注册后自动操作
    autoUploadCpa: document.getElementById('auto-upload-cpa'),
    cpaServiceSelectGroup: document.getElementById('cpa-service-select-group'),
    cpaServiceSelect: document.getElementById('cpa-service-select'),
    autoUploadSub2api: document.getElementById('auto-upload-sub2api'),
    sub2apiServiceSelectGroup: document.getElementById('sub2api-service-select-group'),
    sub2apiServiceSelect: document.getElementById('sub2api-service-select'),
    autoUploadTm: document.getElementById('auto-upload-tm'),
    tmServiceSelectGroup: document.getElementById('tm-service-select-group'),
    tmServiceSelect: document.getElementById('tm-service-select'),
};

function reportStorageWarning(action, error) {
    console.warn(`[状态恢复] ${action}失败`, error);
}

function persistActiveTaskState(state) {
    const serialized = JSON.stringify(state);
    try {
        sessionStorage.setItem(ACTIVE_TASK_STORAGE_KEY, serialized);
    } catch (error) {
        reportStorageWarning('写入 sessionStorage', error);
    }

    try {
        localStorage.setItem(ACTIVE_TASK_STORAGE_KEY, serialized);
    } catch (error) {
        reportStorageWarning('写入 localStorage', error);
    }
}

function readActiveTaskStateRaw() {
    try {
        const sessionValue = sessionStorage.getItem(ACTIVE_TASK_STORAGE_KEY);
        if (sessionValue) {
            return sessionValue;
        }
    } catch (error) {
        reportStorageWarning('读取 sessionStorage', error);
    }

    try {
        return localStorage.getItem(ACTIVE_TASK_STORAGE_KEY);
    } catch (error) {
        reportStorageWarning('读取 localStorage', error);
    }

    return null;
}

function clearActiveTaskState() {
    try {
        sessionStorage.removeItem(ACTIVE_TASK_STORAGE_KEY);
    } catch (error) {
        reportStorageWarning('清理 sessionStorage', error);
    }

    try {
        localStorage.removeItem(ACTIVE_TASK_STORAGE_KEY);
    } catch (error) {
        reportStorageWarning('清理 localStorage', error);
    }
}

// 初始化
document.addEventListener('DOMContentLoaded', async () => {
    initEventListeners();
    await loadAvailableServices();
    await initAutoUploadOptions();
    loadRecentAccounts();
    startAccountsPolling();
    initVisibilityReconnect();
    await restoreActiveTask();
});

// 初始化注册后自动操作选项（CPA / Sub2API / TM）
async function initAutoUploadOptions() {
    await Promise.all([
        loadServiceSelect('/cpa-services?enabled=true', elements.cpaServiceSelect, elements.autoUploadCpa, elements.cpaServiceSelectGroup),
        loadServiceSelect('/sub2api-services?enabled=true', elements.sub2apiServiceSelect, elements.autoUploadSub2api, elements.sub2apiServiceSelectGroup),
        loadServiceSelect('/tm-services?enabled=true', elements.tmServiceSelect, elements.autoUploadTm, elements.tmServiceSelectGroup),
    ]);
}

// 通用：构建自定义多选下拉组件并处理联动
async function loadServiceSelect(apiPath, container, checkbox, selectGroup) {
    if (!checkbox || !container) return;
    let services = [];
    try {
        services = await api.get(apiPath);
    } catch (error) {
        console.warn('加载上传服务列表失败:', apiPath, error);
    }

    if (!services || services.length === 0) {
        checkbox.disabled = true;
        checkbox.checked = false;
        checkbox.title = '请先在设置中添加对应服务';
        const label = checkbox.closest('label');
        if (label) label.style.opacity = '0.5';
        container.innerHTML = '<div class="msd-empty">暂无可用服务</div>';
        if (selectGroup) selectGroup.style.display = 'none';
    } else {
        checkbox.disabled = false;
        checkbox.checked = true;
        checkbox.title = '';
        const label = checkbox.closest('label');
        if (label) label.style.opacity = '1';
        const items = services.map(s =>
            `<label class="msd-item">
                <input type="checkbox" value="${s.id}" checked>
                <span>${escapeHtml(s.name)}</span>
            </label>`
        ).join('');
        container.innerHTML = `
            <div class="msd-dropdown" id="${container.id}-dd">
                <div class="msd-trigger" onclick="toggleMsd('${container.id}-dd')">
                    <span class="msd-label">全部 (${services.length})</span>
                    <span class="msd-arrow">▼</span>
                </div>
                <div class="msd-list">${items}</div>
            </div>`;
        // 监听 checkbox 变化，更新触发器文字
        container.querySelectorAll('.msd-item input').forEach(cb => {
            cb.addEventListener('change', () => updateMsdLabel(container.id + '-dd'));
        });
        updateMsdLabel(container.id + '-dd');
        // 点击外部关闭
        document.addEventListener('click', (e) => {
            const dd = document.getElementById(container.id + '-dd');
            if (dd && !dd.contains(e.target)) dd.classList.remove('open');
        }, true);
        if (selectGroup) selectGroup.style.display = checkbox.checked ? 'block' : 'none';
    }

    // 联动显示/隐藏服务选择区
    checkbox.addEventListener('change', () => {
        if (selectGroup) selectGroup.style.display = checkbox.checked ? 'block' : 'none';
    });
}

function toggleMsd(ddId) {
    const dd = document.getElementById(ddId);
    if (dd) dd.classList.toggle('open');
}

function updateMsdLabel(ddId) {
    const dd = document.getElementById(ddId);
    if (!dd) return;
    const all = dd.querySelectorAll('.msd-item input');
    const checked = dd.querySelectorAll('.msd-item input:checked');
    const label = dd.querySelector('.msd-label');
    if (!label) return;
    if (checked.length === 0) label.textContent = '未选择';
    else if (checked.length === all.length) label.textContent = `全部 (${all.length})`;
    else label.textContent = Array.from(checked).map(c => c.nextElementSibling.textContent).join(', ');
}

// 获取自定义多选下拉中选中的服务 ID 列表
function getSelectedServiceIds(container) {
    if (!container) return [];
    return Array.from(container.querySelectorAll('.msd-item input:checked')).map(cb => parseInt(cb.value));
}

// 事件监听
function initEventListeners() {
    // 注册表单提交
    elements.form.addEventListener('submit', handleStartRegistration);

    // 注册模式切换
    elements.regMode.addEventListener('change', handleModeChange);

    // 邮箱服务切换
    elements.emailService.addEventListener('change', handleServiceChange);

    // 取消按钮
    elements.cancelBtn.addEventListener('click', handleCancelTask);

    // 清空日志
    elements.clearLogBtn.addEventListener('click', () => {
        elements.consoleLog.innerHTML = '<div class="log-line info">[系统] 日志已清空</div>';
        displayedLogs.clear();  // 清空日志去重集合
    });

    // 刷新账号列表
    elements.refreshAccountsBtn.addEventListener('click', () => {
        loadRecentAccounts();
        toast.info('已刷新');
    });

    // 并发模式切换
    elements.concurrencyMode.addEventListener('change', () => {
        handleConcurrencyModeChange(elements.concurrencyMode, elements.concurrencyHint, elements.intervalGroup);
    });
}

// 加载可用的邮箱服务
async function loadAvailableServices() {
    try {
        const data = await api.get('/registration/available-services');
        availableServices = data;

        // 更新邮箱服务选择框
        updateEmailServiceOptions();

        addLog('info', '[系统] 邮箱服务列表已加载');
    } catch (error) {
        console.error('加载邮箱服务列表失败:', error);
        addLog('warning', '[警告] 加载邮箱服务列表失败');
    }
}

// 更新邮箱服务选择框
function updateEmailServiceOptions() {
    const select = elements.emailService;
    select.innerHTML = '';

    const tempmailServices = Array.isArray(availableServices?.tempmail?.services)
        ? availableServices.tempmail.services
        : [];

    if (availableServices?.tempmail?.available && tempmailServices.length > 0) {
        const optgroup = document.createElement('optgroup');
        optgroup.label = '🌐 临时邮箱池';

        const autoOption = document.createElement('option');
        autoOption.value = 'tempmail:default';
        autoOption.textContent = '自动选择（按邮箱设置模式）';
        autoOption.dataset.type = 'tempmail';
        optgroup.appendChild(autoOption);

        tempmailServices.forEach(service => {
            const option = document.createElement('option');
            option.value = `tempmail:${service.id || 'default'}`;
            option.textContent = service.description
                ? `${service.name} - ${service.description}`
                : service.name;
            option.dataset.type = 'tempmail';
            optgroup.appendChild(option);
        });

        select.appendChild(optgroup);
        select.value = 'tempmail:default';
        return;
    }

    const disabled = document.createElement('option');
    disabled.value = '';
    disabled.textContent = '暂无可用的临时邮箱规则，请先到邮箱服务页面添加';
    disabled.disabled = true;
    disabled.selected = true;
    select.appendChild(disabled);
}

// 处理邮箱服务切换
function handleServiceChange(e) {
    const value = e.target.value;
    if (!value) return;

    const [type, id] = value.split(':');

    if (type === 'tempmail' && id === 'default') {
        addLog('info', '[系统] 已选择自动模式，实际服务由“邮箱设置”决定（single/multi）');
        return;
    }

    // 显示服务信息
    const selectedServiceId = Number(id);
    
    if (type === 'tempmail') {
        const service = availableServices.tempmail.services.find(s => s.id === selectedServiceId);
        if (service) {
            addLog('info', `[系统] 已选择临时邮箱服务: ${service.name}`);
        }
    }
}

// 模式切换
function handleModeChange(e) {
    const mode = e.target.value;
    isBatchMode = mode === 'batch';
    isLoopMode = mode === 'loop';

    elements.batchCountGroup.style.display = isBatchMode ? 'block' : 'none';
    if (elements.loopWindowGroup) {
        elements.loopWindowGroup.style.display = isLoopMode ? 'block' : 'none';
    }
    elements.batchOptions.style.display = (isBatchMode || isLoopMode) ? 'block' : 'none';
}

function setEmailServiceSelection(settings) {
    if (!elements.emailService) return;

    const emailServiceType = settings?.email_service_type;
    if (!emailServiceType) return;

    const emailServiceId = settings?.email_service_id;
    const candidateValues = [];

    if (emailServiceId !== undefined && emailServiceId !== null && emailServiceId !== '') {
        candidateValues.push(`${emailServiceType}:${emailServiceId}`);
    }
    if (emailServiceType === 'tempmail') {
        candidateValues.push('tempmail:default');
    }
    candidateValues.push(`${emailServiceType}:default`);
    candidateValues.push(emailServiceType);

    const options = Array.from(elements.emailService.options);
    const matched = options.find((opt) => candidateValues.includes(opt.value));
    if (matched) {
        elements.emailService.value = matched.value;
        handleServiceChange({ target: elements.emailService });
    }
}

function setSelectedServiceIds(container, serviceIds) {
    if (!container) return;

    const checkboxes = Array.from(container.querySelectorAll('.msd-item input'));
    if (checkboxes.length === 0) return;

    const normalizedIds = Array.isArray(serviceIds)
        ? serviceIds.map((id) => String(id))
        : [];
    const selected = new Set(normalizedIds);

    if (selected.size === 0) {
        checkboxes.forEach((checkbox) => {
            checkbox.checked = true;
        });
    } else {
        let hasMatch = false;
        checkboxes.forEach((checkbox) => {
            const shouldCheck = selected.has(String(checkbox.value));
            checkbox.checked = shouldCheck;
            if (shouldCheck) {
                hasMatch = true;
            }
        });

        // 快照里的服务若已失效，回退到默认全选，避免恢复出“空列表”。
        if (!hasMatch) {
            checkboxes.forEach((checkbox) => {
                checkbox.checked = true;
            });
        }
    }

    const dropdown = container.querySelector('.msd-dropdown');
    if (dropdown && dropdown.id) {
        updateMsdLabel(dropdown.id);
    }
}

function applyAutoUploadRecoveredSettings(settings) {
    const restoreItems = [
        {
            enabledKey: 'auto_upload_cpa',
            idsKey: 'cpa_service_ids',
            checkbox: elements.autoUploadCpa,
            selectGroup: elements.cpaServiceSelectGroup,
            selectContainer: elements.cpaServiceSelect,
        },
        {
            enabledKey: 'auto_upload_sub2api',
            idsKey: 'sub2api_service_ids',
            checkbox: elements.autoUploadSub2api,
            selectGroup: elements.sub2apiServiceSelectGroup,
            selectContainer: elements.sub2apiServiceSelect,
        },
        {
            enabledKey: 'auto_upload_tm',
            idsKey: 'tm_service_ids',
            checkbox: elements.autoUploadTm,
            selectGroup: elements.tmServiceSelectGroup,
            selectContainer: elements.tmServiceSelect,
        },
    ];

    restoreItems.forEach((item) => {
        if (!item.checkbox) return;

        const enabled = Boolean(settings?.[item.enabledKey]);
        item.checkbox.checked = enabled;
        if (item.selectGroup) {
            item.selectGroup.style.display = enabled ? 'block' : 'none';
        }

        if (enabled) {
            setSelectedServiceIds(item.selectContainer, settings?.[item.idsKey]);
        }
    });
}

function applyRecoveredTaskSettings(taskMode, settings) {
    if (!settings) return;

    setEmailServiceSelection(settings);

    if (taskMode === 'batch' || taskMode === 'loop') {
        if (elements.regMode) {
            elements.regMode.value = taskMode;
            handleModeChange({ target: elements.regMode });
        }
    }

    if (settings.count !== undefined && settings.count !== null && elements.batchCount) {
        elements.batchCount.value = settings.count;
    }
    if (settings.mode && elements.concurrencyMode) {
        elements.concurrencyMode.value = settings.mode;
        handleConcurrencyModeChange(elements.concurrencyMode, elements.concurrencyHint, elements.intervalGroup);
    }
    if (settings.concurrency && elements.concurrencyCount) {
        elements.concurrencyCount.value = settings.concurrency;
    }
    if (settings.interval_min !== undefined && elements.intervalMin) {
        elements.intervalMin.value = settings.interval_min;
    }
    if (settings.interval_max !== undefined && elements.intervalMax) {
        elements.intervalMax.value = settings.interval_max;
    }

    if (taskMode === 'loop') {
        if (settings.window_start && elements.loopWindowStart) {
            elements.loopWindowStart.value = settings.window_start;
        }
        if (settings.window_end && elements.loopWindowEnd) {
            elements.loopWindowEnd.value = settings.window_end;
        }
    }

    applyAutoUploadRecoveredSettings(settings);
}

// 并发模式切换（批量）
function handleConcurrencyModeChange(selectEl, hintEl, intervalGroupEl) {
    const mode = selectEl.value;
    if (mode === 'parallel') {
        hintEl.textContent = '所有任务分成 N 个并发批次同时执行';
        intervalGroupEl.style.display = 'none';
    } else {
        hintEl.textContent = '同时最多运行 N 个任务，每隔 interval 秒启动新任务';
        intervalGroupEl.style.display = 'block';
    }
}

// 开始注册
async function handleStartRegistration(e) {
    e.preventDefault();

    const selectedValue = elements.emailService.value;
    if (!selectedValue) {
        toast.error('请选择一个邮箱服务');
        return;
    }

    const [emailServiceType, serviceId] = selectedValue.split(':');

    // 禁用开始按钮
    elements.startBtn.disabled = true;
    elements.cancelBtn.disabled = false;

    // 清空日志
    elements.consoleLog.innerHTML = '';

    // 构建请求数据（代理从设置中自动获取）
    const requestData = {
        email_service_type: emailServiceType,
        auto_upload_cpa: elements.autoUploadCpa ? elements.autoUploadCpa.checked : false,
        cpa_service_ids: elements.autoUploadCpa && elements.autoUploadCpa.checked ? getSelectedServiceIds(elements.cpaServiceSelect) : [],
        auto_upload_sub2api: elements.autoUploadSub2api ? elements.autoUploadSub2api.checked : false,
        sub2api_service_ids: elements.autoUploadSub2api && elements.autoUploadSub2api.checked ? getSelectedServiceIds(elements.sub2apiServiceSelect) : [],
        auto_upload_tm: elements.autoUploadTm ? elements.autoUploadTm.checked : false,
        tm_service_ids: elements.autoUploadTm && elements.autoUploadTm.checked ? getSelectedServiceIds(elements.tmServiceSelect) : [],
    };

    // 如果选择了数据库中的服务，传递 service_id
    if (serviceId && serviceId !== 'default') {
        requestData.email_service_id = parseInt(serviceId);
    }

    if (isLoopMode) {
        await handleLoopRegistration(requestData);
    } else if (isBatchMode) {
        await handleBatchRegistration(requestData);
    } else {
        await handleSingleRegistration(requestData);
    }
}

function getBatchExecutionOptions() {
    return {
        intervalMin: parseInt(elements.intervalMin.value) || 5,
        intervalMax: parseInt(elements.intervalMax.value) || 30,
        concurrency: parseInt(elements.concurrencyCount.value) || 3,
        mode: elements.concurrencyMode.value || 'pipeline'
    };
}

function validateIntervalOptions(intervalMin, intervalMax) {
    if (intervalMin < 0 || intervalMax < intervalMin) {
        throw new Error('间隔时间参数无效');
    }
}

function isValidTimeString(value) {
    if (typeof value !== 'string') return false;
    return /^([01]\d|2[0-3]):[0-5]\d$/.test(value.trim());
}

function formatDuration(seconds) {
    const total = Math.max(0, parseInt(seconds) || 0);
    const hours = Math.floor(total / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    const secs = total % 60;
    const parts = [];

    if (hours > 0) parts.push(`${hours}小时`);
    if (minutes > 0) parts.push(`${minutes}分钟`);
    if (hours === 0 && secs > 0) parts.push(`${secs}秒`);

    return parts.length > 0 ? parts.join('') : '0秒';
}

// 单次注册
async function handleSingleRegistration(requestData) {
    // 重置任务状态
    taskCompleted = false;
    taskFinalStatus = null;
    displayedLogs.clear();  // 清空日志去重集合
    toastShown = false;  // 重置 toast 标志

    addLog('info', '[系统] 正在启动注册任务...');

    try {
        const data = await api.post('/registration/start', requestData);

        currentTask = data;
        activeTaskUuid = data.task_uuid;  // 保存用于重连
        // 持久化任务状态，支持页面跳转与浏览器重开恢复。
        persistActiveTaskState({
            task_uuid: data.task_uuid,
            mode: 'single',
            settings: {
                registration_mode: 'single',
                email_service_type: requestData.email_service_type,
                email_service_id: requestData.email_service_id,
            }
        });
        addLog('info', `[系统] 任务已创建: ${data.task_uuid}`);
        showTaskStatus(data);
        updateTaskStatus('running');

        // 优先使用 WebSocket
        connectWebSocket(data.task_uuid);

    } catch (error) {
        addLog('error', `[错误] 启动失败: ${error.message}`);
        toast.error(error.message);
        resetButtons();
    }
}


// ============== WebSocket 功能 ==============

// 连接 WebSocket
function connectWebSocket(taskUuid) {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/api/ws/task/${taskUuid}`;

    try {
        webSocket = new WebSocket(wsUrl);

        webSocket.onopen = () => {
            console.log('WebSocket 连接成功');
            useWebSocket = true;
            // 停止轮询（如果有）
            stopLogPolling();
            // 开始心跳
            startWebSocketHeartbeat();
        };

        webSocket.onmessage = (event) => {
            const data = JSON.parse(event.data);

            if (data.type === 'log') {
                const logType = getLogType(data.message);
                addLog(logType, data.message);
            } else if (data.type === 'status') {
                updateTaskStatus(data.status);

                // 检查是否完成
                if (['completed', 'failed', 'cancelled', 'cancelling'].includes(data.status)) {
                    // 保存最终状态，用于 onclose 判断
                    taskFinalStatus = data.status;
                    taskCompleted = true;

                    // 断开 WebSocket（异步操作）
                    disconnectWebSocket();

                    // 任务完成后再重置按钮
                    resetButtons();

                    // 只显示一次 toast
                    if (!toastShown) {
                        toastShown = true;
                        if (data.status === 'completed') {
                            addLog('success', '[成功] 注册成功！');
                            toast.success('注册成功！');
                            // 刷新账号列表
                            loadRecentAccounts();
                        } else if (data.status === 'failed') {
                            addLog('error', '[错误] 注册失败');
                            toast.error('注册失败');
                        } else if (data.status === 'cancelled' || data.status === 'cancelling') {
                            addLog('warning', '[警告] 任务已取消');
                        }
                    }
                }
            } else if (data.type === 'pong') {
                // 心跳响应，忽略
            }
        };

        webSocket.onclose = (event) => {
            console.log('WebSocket 连接关闭:', event.code);
            stopWebSocketHeartbeat();

            // 只有在任务未完成且最终状态不是完成状态时才切换到轮询
            // 使用 taskFinalStatus 而不是 currentTask.status，因为 currentTask 可能已被重置
            const shouldPoll = !taskCompleted &&
                               taskFinalStatus === null;  // 如果 taskFinalStatus 有值，说明任务已完成

            if (shouldPoll && currentTask) {
                console.log('切换到轮询模式');
                useWebSocket = false;
                startLogPolling(currentTask.task_uuid);
            }
        };

        webSocket.onerror = (error) => {
            console.error('WebSocket 错误:', error);
            // 切换到轮询
            useWebSocket = false;
            stopWebSocketHeartbeat();
            startLogPolling(taskUuid);
        };

    } catch (error) {
        console.error('WebSocket 连接失败:', error);
        useWebSocket = false;
        startLogPolling(taskUuid);
    }
}

// 断开 WebSocket
function disconnectWebSocket() {
    stopWebSocketHeartbeat();
    if (webSocket) {
        webSocket.close();
        webSocket = null;
    }
}

// 开始心跳
function startWebSocketHeartbeat() {
    stopWebSocketHeartbeat();
    wsHeartbeatInterval = setInterval(() => {
        if (webSocket && webSocket.readyState === WebSocket.OPEN) {
            webSocket.send(JSON.stringify({ type: 'ping' }));
        }
    }, 25000);  // 每 25 秒发送一次心跳
}

// 停止心跳
function stopWebSocketHeartbeat() {
    if (wsHeartbeatInterval) {
        clearInterval(wsHeartbeatInterval);
        wsHeartbeatInterval = null;
    }
}

// 发送取消请求
function cancelViaWebSocket() {
    if (webSocket && webSocket.readyState === WebSocket.OPEN) {
        webSocket.send(JSON.stringify({ type: 'cancel' }));
    }
}

// 批量注册
async function handleBatchRegistration(requestData) {
    // 重置批量任务状态
    batchCompleted = false;
    batchFinalStatus = null;
    displayedLogs.clear();  // 清空日志去重集合
    toastShown = false;  // 重置 toast 标志

    const count = parseInt(elements.batchCount.value) || 5;
    const { intervalMin, intervalMax, concurrency, mode } = getBatchExecutionOptions();

    try {
        validateIntervalOptions(intervalMin, intervalMax);
    } catch (error) {
        const message = error instanceof Error ? error.message : '参数校验失败';
        addLog('error', `[错误] ${message}`);
        toast.error(message);
        resetButtons();
        return;
    }

    requestData.registration_mode = 'batch';
    requestData.count = count;
    requestData.interval_min = intervalMin;
    requestData.interval_max = intervalMax;
    requestData.concurrency = Math.min(50, Math.max(1, concurrency));
    requestData.mode = mode;

    addLog('info', `[系统] 正在启动批量注册任务 (目标成功数量: ${count})...`);

    try {
        const data = await api.post('/registration/batch', requestData);

        currentBatch = data;
        activeBatchId = data.batch_id;  // 保存用于重连
        // 持久化任务状态，支持页面跳转与浏览器重开恢复。
        persistActiveTaskState({
            batch_id: data.batch_id,
            mode: 'batch',
            total: data.count,
            settings: {
                registration_mode: 'batch',
                count,
                email_service_type: requestData.email_service_type,
                email_service_id: requestData.email_service_id,
                interval_min: intervalMin,
                interval_max: intervalMax,
                concurrency: requestData.concurrency,
                mode,
            }
        });
        addLog('info', `[系统] 批量任务已创建: ${data.batch_id}`);
        addLog('info', `[系统] 已创建批量任务，目标成功数量: ${data.count}`);
        showBatchStatus(data);

        // 优先使用 WebSocket
        connectBatchWebSocket(data.batch_id);

    } catch (error) {
        addLog('error', `[错误] 启动失败: ${error.message}`);
        toast.error(error.message);
        resetButtons();
    }
}

// 循环注册
async function handleLoopRegistration(requestData) {
    batchCompleted = false;
    batchFinalStatus = null;
    displayedLogs.clear();
    toastShown = false;

    const { intervalMin, intervalMax, concurrency, mode } = getBatchExecutionOptions();
    const windowStart = (elements.loopWindowStart?.value || '').trim();
    const windowEnd = (elements.loopWindowEnd?.value || '').trim();

    try {
        validateIntervalOptions(intervalMin, intervalMax);
        if (!isValidTimeString(windowStart) || !isValidTimeString(windowEnd)) {
            throw new Error('循环注册时间段格式无效，请使用 HH:MM');
        }
    } catch (error) {
        const message = error instanceof Error ? error.message : '参数校验失败';
        addLog('error', `[错误] ${message}`);
        toast.error(message);
        resetButtons();
        return;
    }

    requestData.registration_mode = 'loop';
    requestData.count = 0;
    requestData.window_start = windowStart;
    requestData.window_end = windowEnd;
    requestData.interval_min = intervalMin;
    requestData.interval_max = intervalMax;
    requestData.concurrency = Math.min(50, Math.max(1, concurrency));
    requestData.mode = mode;

    addLog('info', `[系统] 正在启动循环注册任务（时间段: ${windowStart}-${windowEnd}）...`);

    try {
        const data = await api.post('/registration/batch', requestData);

        currentBatch = data;
        activeBatchId = data.batch_id;
        persistActiveTaskState({
            batch_id: data.batch_id,
            mode: 'loop',
            total: data.count || 0,
            window_start: windowStart,
            window_end: windowEnd,
            settings: {
                registration_mode: 'loop',
                count: data.count || 0,
                email_service_type: requestData.email_service_type,
                email_service_id: requestData.email_service_id,
                interval_min: intervalMin,
                interval_max: intervalMax,
                concurrency: requestData.concurrency,
                mode,
                window_start: windowStart,
                window_end: windowEnd,
            }
        });

        addLog('info', `[系统] 循环任务已创建: ${data.batch_id}`);
        addLog('info', `[系统] 将在 ${windowStart}-${windowEnd} 时间段内持续注册，直到手动取消`);

        showBatchStatus({
            count: 0,
            registration_mode: 'loop',
            window_start: windowStart,
            window_end: windowEnd,
        });

        connectBatchWebSocket(data.batch_id);
    } catch (error) {
        addLog('error', `[错误] 启动失败: ${error.message}`);
        toast.error(error.message);
        resetButtons();
    }
}

// 取消任务
async function handleCancelTask() {
    // 禁用取消按钮，防止重复点击
    elements.cancelBtn.disabled = true;
    addLog('info', '[系统] 正在提交取消请求...');

    try {
        // 批量任务取消（普通批量模式和循环模式）
        if (currentBatch && (isBatchMode || isLoopMode)) {
            // 优先通过 WebSocket 取消
            if (batchWebSocket && batchWebSocket.readyState === WebSocket.OPEN) {
                batchWebSocket.send(JSON.stringify({ type: 'cancel' }));
                addLog('warning', '[警告] 批量任务取消请求已提交');
                toast.info('任务取消请求已提交');
            } else {
                // 降级到 REST API
                await api.post(`/registration/batch/${currentBatch.batch_id}/cancel`);
                addLog('warning', '[警告] 批量任务取消请求已提交');
                toast.info('任务取消请求已提交');
                stopBatchPolling();
                resetButtons();
            }
        }
        // 单次任务取消
        else if (currentTask) {
            // 优先通过 WebSocket 取消
            if (webSocket && webSocket.readyState === WebSocket.OPEN) {
                webSocket.send(JSON.stringify({ type: 'cancel' }));
                addLog('warning', '[警告] 任务取消请求已提交');
                toast.info('任务取消请求已提交');
            } else {
                // 降级到 REST API
                await api.post(`/registration/tasks/${currentTask.task_uuid}/cancel`);
                addLog('warning', '[警告] 任务已取消');
                toast.info('任务已取消');
                stopLogPolling();
                resetButtons();
            }
        }
        // 没有活动任务
        else {
            addLog('warning', '[警告] 没有活动的任务可以取消');
            toast.warning('没有活动的任务');
            resetButtons();
        }
    } catch (error) {
        addLog('error', `[错误] 取消失败: ${error.message}`);
        toast.error(error.message);
        // 恢复取消按钮，允许重试
        elements.cancelBtn.disabled = false;
    }
}

// 开始轮询日志
function startLogPolling(taskUuid) {
    let lastLogIndex = 0;

    logPollingInterval = setInterval(async () => {
        try {
            const data = await api.get(`/registration/tasks/${taskUuid}/logs`);

            // 更新任务状态
            updateTaskStatus(data.status);

            // 更新邮箱信息
            if (data.email) {
                elements.taskEmail.textContent = data.email;
            }
            if (data.email_service) {
                elements.taskService.textContent = getServiceTypeText(data.email_service);
            }

            // 添加新日志
            const logs = data.logs || [];
            for (let i = lastLogIndex; i < logs.length; i++) {
                const log = logs[i];
                const logType = getLogType(log);
                addLog(logType, log);
            }
            lastLogIndex = logs.length;

            // 检查任务是否完成
            if (['completed', 'failed', 'cancelled'].includes(data.status)) {
                stopLogPolling();
                resetButtons();

                // 只显示一次 toast
                if (!toastShown) {
                    toastShown = true;
                    if (data.status === 'completed') {
                        addLog('success', '[成功] 注册成功！');
                        toast.success('注册成功！');
                        // 刷新账号列表
                        loadRecentAccounts();
                    } else if (data.status === 'failed') {
                        addLog('error', '[错误] 注册失败');
                        toast.error('注册失败');
                    } else if (data.status === 'cancelled') {
                        addLog('warning', '[警告] 任务已取消');
                    }
                }
            }
        } catch (error) {
            console.error('轮询日志失败:', error);
        }
    }, 1000);
}

// 停止轮询日志
function stopLogPolling() {
    if (logPollingInterval) {
        clearInterval(logPollingInterval);
        logPollingInterval = null;
    }
}

// 开始轮询批量状态
function startBatchPolling(batchId) {
    batchPollingInterval = setInterval(async () => {
        try {
            const data = await api.get(`/registration/batch/${batchId}`);
            updateBatchProgress(data);

            // 检查是否完成
            if (data.finished) {
                stopBatchPolling();
                resetButtons();

                // 只显示一次 toast
                if (!toastShown) {
                    toastShown = true;
                    const isLoopTask = data.registration_mode === 'loop';
                    if (data.status === 'cancelled' || data.status === 'cancelling') {
                        if (isLoopTask) {
                            addLog('warning', `[取消] 循环注册已停止，累计成功: ${data.success}, 失败: ${data.failed}`);
                        } else {
                            addLog('warning', `[取消] 批量任务已取消，成功: ${data.success}, 失败: ${data.failed}`);
                        }
                        toast.info('任务取消请求已处理');
                    } else {
                        addLog('info', `[完成] 批量任务完成！成功: ${data.success}, 失败: ${data.failed}`);
                        if (data.success > 0) {
                            const successText = isLoopTask
                                ? `循环注册已结束，累计成功 ${data.success} 个`
                                : `批量注册完成，成功 ${data.success} 个`;
                            toast.success(successText);
                            loadRecentAccounts();
                        } else {
                            toast.warning('任务完成，但没有成功注册任何账号');
                        }
                    }
                }
            }
        } catch (error) {
            console.error('轮询批量状态失败:', error);
        }
    }, 2000);
}

// 停止轮询批量状态
function stopBatchPolling() {
    if (batchPollingInterval) {
        clearInterval(batchPollingInterval);
        batchPollingInterval = null;
    }
}

// 显示任务状态
function showTaskStatus(task) {
    elements.taskStatusRow.style.display = 'grid';
    elements.batchProgressSection.style.display = 'none';
    elements.taskStatusBadge.style.display = 'inline-flex';
    elements.taskId.textContent = task.task_uuid.substring(0, 8) + '...';
    elements.taskEmail.textContent = '-';
    elements.taskService.textContent = '-';
}

// 更新任务状态
function updateTaskStatus(status) {
    const statusInfo = {
        pending: { text: '等待中', class: 'pending' },
        running: { text: '运行中', class: 'running' },
        completed: { text: '已完成', class: 'completed' },
        failed: { text: '失败', class: 'failed' },
        cancelled: { text: '已取消', class: 'disabled' }
    };

    const info = statusInfo[status] || { text: status, class: '' };
    elements.taskStatusBadge.textContent = info.text;
    elements.taskStatusBadge.className = `status-badge ${info.class}`;
    elements.taskStatus.textContent = info.text;
}

// 显示批量状态
function showBatchStatus(batch) {
    const registrationMode = batch.registration_mode || (isLoopMode ? 'loop' : 'batch');
    const isLoopTask = registrationMode === 'loop';
    const totalCount = parseInt(batch.count || 0);

    elements.batchProgressSection.style.display = 'block';
    elements.taskStatusRow.style.display = 'none';
    elements.taskStatusBadge.style.display = 'none';

    if (isLoopTask) {
        const windowStart = batch.window_start || '--:--';
        const windowEnd = batch.window_end || '--:--';
        elements.batchProgressText.textContent = '循环任务初始化中';
        elements.batchProgressPercent.textContent = `${windowStart}-${windowEnd}`;
        elements.progressBar.style.width = '0%';
        elements.batchRemaining.textContent = '0';
    } else {
        elements.batchProgressText.textContent = `0/${totalCount}`;
        elements.batchProgressPercent.textContent = '0%';
        elements.progressBar.style.width = '0%';
        elements.batchRemaining.textContent = totalCount;
    }

    elements.batchSuccess.textContent = '0';
    elements.batchFailed.textContent = '0';

    // 重置计数器
    elements.batchSuccess.dataset.last = '0';
    elements.batchFailed.dataset.last = '0';
}

// 更新批量进度
function updateBatchProgress(data) {
    const registrationMode = data.registration_mode || (isLoopMode ? 'loop' : 'batch');
    if (registrationMode === 'loop') {
        const inWindow = Boolean(data.in_window);
        const nextWindowSeconds = parseInt(data.next_window_seconds || 0);
        const runningCount = parseInt(data.running || 0);
        const total = parseInt(data.total || 0);
        const completed = parseInt(data.completed || 0);
        const windowStart = data.window_start || '--:--';
        const windowEnd = data.window_end || '--:--';
        const statusText = inWindow
            ? `循环中（运行中 ${runningCount}）`
            : `窗口外等待（${formatDuration(nextWindowSeconds)} 后开始）`;

        elements.batchProgressText.textContent = `${statusText} | 已启动 ${total} | 已完成 ${completed}`;
        elements.batchProgressPercent.textContent = `${windowStart}-${windowEnd}`;
        elements.progressBar.style.width = inWindow ? '100%' : '15%';
        elements.batchSuccess.textContent = data.success || 0;
        elements.batchFailed.textContent = data.failed || 0;
        elements.batchRemaining.textContent = runningCount;

        const successNow = parseInt(data.success || 0);
        const failedNow = parseInt(data.failed || 0);
        const lastSuccess = parseInt(elements.batchSuccess.dataset.last || '0');
        const lastFailed = parseInt(elements.batchFailed.dataset.last || '0');

        if (successNow > lastSuccess) {
            addLog('success', `[成功] 循环注册累计成功 ${successNow} 个`);
        }
        if (failedNow > lastFailed) {
            addLog('error', `[失败] 循环注册累计失败 ${failedNow} 个`);
        }

        elements.batchSuccess.dataset.last = `${successNow}`;
        elements.batchFailed.dataset.last = `${failedNow}`;
        return;
    }

    const total = Math.max(0, parseInt(data.total || 0));
    const completed = Math.max(0, parseInt(data.completed || 0));
    const attempts = Math.max(0, parseInt(data.attempts || 0));
    const progress = total > 0 ? ((completed / total) * 100).toFixed(0) : '0';
    elements.batchProgressText.textContent = `${completed}/${total} (尝试: ${attempts})`;
    elements.batchProgressPercent.textContent = `${progress}%`;
    elements.progressBar.style.width = `${progress}%`;
    elements.batchSuccess.textContent = data.success;
    elements.batchFailed.textContent = data.failed;
    elements.batchRemaining.textContent = Math.max(0, total - completed);

    // 记录日志（避免重复）
    if (data.completed > 0) {
        const lastSuccess = parseInt(elements.batchSuccess.dataset.last || '0');
        const lastFailed = parseInt(elements.batchFailed.dataset.last || '0');

        if (data.success > lastSuccess) {
            addLog('success', `[成功] 第 ${data.success} 个账号注册成功`);
        }
        if (data.failed > lastFailed) {
            addLog('error', `[失败] 第 ${data.failed} 个账号注册失败`);
        }

        elements.batchSuccess.dataset.last = data.success;
        elements.batchFailed.dataset.last = data.failed;
    }
}

// 加载最近注册的账号
async function loadRecentAccounts() {
    try {
        const data = await api.get('/accounts?page=1&page_size=10');

        if (data.accounts.length === 0) {
            elements.recentAccountsTable.innerHTML = `
                <tr>
                    <td colspan="5">
                        <div class="empty-state" style="padding: var(--spacing-md);">
                            <div class="empty-state-icon">📭</div>
                            <div class="empty-state-title">暂无已注册账号</div>
                        </div>
                    </td>
                </tr>
            `;
            return;
        }

        elements.recentAccountsTable.innerHTML = data.accounts.map(account => `
            <tr data-id="${account.id}">
                <td>${account.id}</td>
                <td>
                    <span style="display:inline-flex;align-items:center;gap:4px;">
                        <span title="${escapeHtml(account.email)}">${escapeHtml(account.email)}</span>
                        <button class="btn-copy-icon copy-email-btn" data-email="${escapeHtml(account.email)}" title="复制邮箱">📋</button>
                    </span>
                </td>
                <td class="password-cell">
                    ${account.password
                        ? `<span style="display:inline-flex;align-items:center;gap:4px;">
                            <span class="password-hidden" title="点击查看">${escapeHtml(account.password.substring(0, 8))}...</span>
                            <button class="btn-copy-icon copy-pwd-btn" data-pwd="${escapeHtml(account.password)}" title="复制密码">📋</button>
                           </span>`
                        : '-'}
                </td>
                <td>
                    ${getStatusIcon(account.status)}
                </td>
            </tr>
        `).join('');

        // 绑定复制按钮事件
        elements.recentAccountsTable.querySelectorAll('.copy-email-btn').forEach(btn => {
            btn.addEventListener('click', (e) => { e.stopPropagation(); copyToClipboard(btn.dataset.email); });
        });
        elements.recentAccountsTable.querySelectorAll('.copy-pwd-btn').forEach(btn => {
            btn.addEventListener('click', (e) => { e.stopPropagation(); copyToClipboard(btn.dataset.pwd); });
        });

    } catch (error) {
        console.error('加载账号列表失败:', error);
    }
}

// 开始账号列表轮询
function startAccountsPolling() {
    // 每30秒刷新一次账号列表
    accountsPollingInterval = setInterval(() => {
        loadRecentAccounts();
    }, 30000);
}

// 添加日志
function addLog(type, message) {
    // 日志去重：使用消息内容的 hash 作为键
    const logKey = `${type}:${message}`;
    if (displayedLogs.has(logKey)) {
        return;  // 已经显示过，跳过
    }
    displayedLogs.add(logKey);

    // 限制去重集合大小，避免内存泄漏
    if (displayedLogs.size > 1000) {
        // 清空一半的记录
        const keys = Array.from(displayedLogs);
        keys.slice(0, 500).forEach((k) => {
            displayedLogs.delete(k);
        });
    }

    const line = document.createElement('div');
    line.className = `log-line ${type}`;

    // 添加时间戳
    const timestamp = new Date().toLocaleTimeString('zh-CN', {
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit'
    });

    line.innerHTML = `<span class="timestamp">[${timestamp}]</span>${escapeHtml(message)}`;
    elements.consoleLog.appendChild(line);

    // 自动滚动到底部
    elements.consoleLog.scrollTop = elements.consoleLog.scrollHeight;

    // 限制日志行数
    const lines = elements.consoleLog.querySelectorAll('.log-line');
    if (lines.length > 500) {
        lines[0].remove();
    }
}

// 获取日志类型
function getLogType(log) {
    if (typeof log !== 'string') return 'info';

    const lowerLog = log.toLowerCase();
    if (lowerLog.includes('error') || lowerLog.includes('失败') || lowerLog.includes('错误')) {
        return 'error';
    }
    if (lowerLog.includes('warning') || lowerLog.includes('警告')) {
        return 'warning';
    }
    if (lowerLog.includes('success') || lowerLog.includes('成功') || lowerLog.includes('完成')) {
        return 'success';
    }
    return 'info';
}

// 重置按钮状态
function resetButtons() {
    elements.startBtn.disabled = false;
    elements.cancelBtn.disabled = true;
    currentTask = null;
    currentBatch = null;
    if (elements.regMode) {
        isBatchMode = elements.regMode.value === 'batch';
        isLoopMode = elements.regMode.value === 'loop';
    } else {
        isBatchMode = false;
        isLoopMode = false;
    }
    // 重置完成标志
    taskCompleted = false;
    batchCompleted = false;
    // 重置最终状态标志
    taskFinalStatus = null;
    batchFinalStatus = null;
    // 清除活跃任务标识
    activeTaskUuid = null;
    activeBatchId = null;
    // 清除页面持久化状态
    clearActiveTaskState();
    // 断开 WebSocket
    disconnectWebSocket();
    disconnectBatchWebSocket();
}

// HTML 转义
function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}
// ============== 批量任务 WebSocket 功能 ==============

// 连接批量任务 WebSocket
function connectBatchWebSocket(batchId) {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/api/ws/batch/${batchId}`;

    try {
        batchWebSocket = new WebSocket(wsUrl);

        batchWebSocket.onopen = () => {
            console.log('批量任务 WebSocket 连接成功');
            // 停止轮询（如果有）
            stopBatchPolling();
            // 开始心跳
            startBatchWebSocketHeartbeat();
        };

        batchWebSocket.onmessage = (event) => {
            const data = JSON.parse(event.data);

            if (data.type === 'log') {
                const logType = getLogType(data.message);
                addLog(logType, data.message);
            } else if (data.type === 'status') {
                // 更新进度
                if (data.total !== undefined) {
                    updateBatchProgress(data);
                }

                // 检查是否完成
                if (['completed', 'failed', 'cancelled', 'cancelling'].includes(data.status)) {
                    // 保存最终状态，用于 onclose 判断
                    batchFinalStatus = data.status;
                    batchCompleted = true;

                    // 断开 WebSocket（异步操作）
                    disconnectBatchWebSocket();

                    // 任务完成后再重置按钮
                    resetButtons();

                    // 只显示一次 toast
                    if (!toastShown) {
                        toastShown = true;
                        const taskMode = data.registration_mode || (isLoopMode ? 'loop' : 'batch');
                        if (data.status === 'completed') {
                            if (taskMode === 'loop') {
                                addLog('success', `[完成] 循环注册任务已结束！成功: ${data.success}, 失败: ${data.failed}`);
                            } else {
                                addLog('success', `[完成] 批量任务完成！成功: ${data.success}, 失败: ${data.failed}`);
                            }
                            if (data.success > 0) {
                                const successText = taskMode === 'loop'
                                    ? `循环注册已结束，累计成功 ${data.success} 个`
                                    : `批量注册完成，成功 ${data.success} 个`;
                                toast.success(successText);
                                loadRecentAccounts();
                            } else {
                                toast.warning('任务完成，但没有成功注册任何账号');
                            }
                        } else if (data.status === 'failed') {
                            addLog('error', '[错误] 批量任务执行失败');
                            toast.error('批量任务执行失败');
                        } else if (data.status === 'cancelled' || data.status === 'cancelling') {
                            if (taskMode === 'loop') {
                                addLog('warning', `[警告] 循环注册已取消，累计成功: ${data.success || 0}，失败: ${data.failed || 0}`);
                            } else {
                                addLog('warning', '[警告] 批量任务已取消');
                            }
                        }
                    }
                }
            } else if (data.type === 'pong') {
                // 心跳响应，忽略
            }
        };

        batchWebSocket.onclose = (event) => {
            console.log('批量任务 WebSocket 连接关闭:', event.code);
            stopBatchWebSocketHeartbeat();

            // 只有在任务未完成且最终状态不是完成状态时才切换到轮询
            // 使用 batchFinalStatus 而不是 currentBatch.status，因为 currentBatch 可能已被重置
            const shouldPoll = !batchCompleted &&
                               batchFinalStatus === null;  // 如果 batchFinalStatus 有值，说明任务已完成

            if (shouldPoll && currentBatch) {
                console.log('切换到轮询模式');
                startBatchFallbackPolling(currentBatch.batch_id);
            }
        };

        batchWebSocket.onerror = (error) => {
            console.error('批量任务 WebSocket 错误:', error);
            stopBatchWebSocketHeartbeat();
            // 切换到轮询
            startBatchFallbackPolling(batchId);
        };

    } catch (error) {
        console.error('批量任务 WebSocket 连接失败:', error);
        startBatchFallbackPolling(batchId);
    }
}

// 断开批量任务 WebSocket
function disconnectBatchWebSocket() {
    stopBatchWebSocketHeartbeat();
    if (batchWebSocket) {
        batchWebSocket.close();
        batchWebSocket = null;
    }
}

// 开始批量任务心跳
function startBatchWebSocketHeartbeat() {
    stopBatchWebSocketHeartbeat();
    batchWsHeartbeatInterval = setInterval(() => {
        if (batchWebSocket && batchWebSocket.readyState === WebSocket.OPEN) {
            batchWebSocket.send(JSON.stringify({ type: 'ping' }));
        }
    }, 25000);  // 每 25 秒发送一次心跳
}

// 停止批量任务心跳
function stopBatchWebSocketHeartbeat() {
    if (batchWsHeartbeatInterval) {
        clearInterval(batchWsHeartbeatInterval);
        batchWsHeartbeatInterval = null;
    }
}

// 发送批量任务取消请求
function cancelBatchViaWebSocket() {
    if (batchWebSocket && batchWebSocket.readyState === WebSocket.OPEN) {
        batchWebSocket.send(JSON.stringify({ type: 'cancel' }));
    }
}

// 批量任务降级轮询
function startBatchFallbackPolling(batchId) {
    startBatchPolling(batchId);
}

// ============== 页面可见性重连机制 ==============

function initVisibilityReconnect() {
    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState !== 'visible') return;

        // 页面重新可见时，检查是否需要重连（针对同页面标签切换场景）
        const wsDisconnected = !webSocket || webSocket.readyState === WebSocket.CLOSED;
        const batchWsDisconnected = !batchWebSocket || batchWebSocket.readyState === WebSocket.CLOSED;

        // 单任务重连
        if (activeTaskUuid && !taskCompleted && wsDisconnected) {
            console.log('[重连] 页面重新可见，重连单任务 WebSocket:', activeTaskUuid);
            addLog('info', '[系统] 页面重新激活，正在重连任务监控...');
            connectWebSocket(activeTaskUuid);
        }

        // 批量任务重连
        if (activeBatchId && !batchCompleted && batchWsDisconnected) {
            console.log('[重连] 页面重新可见，重连批量任务 WebSocket:', activeBatchId);
            addLog('info', '[系统] 页面重新激活，正在重连批量任务监控...');
            connectBatchWebSocket(activeBatchId);
        }
    });
}

async function restoreSingleTaskById(taskUuid, settings = {}) {
    const data = await api.get(`/registration/tasks/${taskUuid}`);
    if (['completed', 'failed', 'cancelled'].includes(data.status)) {
        return false;
    }

    currentTask = data;
    activeTaskUuid = taskUuid;
    taskCompleted = false;
    taskFinalStatus = null;
    toastShown = false;
    displayedLogs.clear();
    elements.startBtn.disabled = true;
    elements.cancelBtn.disabled = false;

    isBatchMode = false;
    isLoopMode = false;

    const mergedSettings = {
        ...(settings || {}),
        ...(data.settings || {}),
    };
    applyRecoveredTaskSettings('single', mergedSettings);
    showTaskStatus(data);
    updateTaskStatus(data.status);
    addLog('info', `[系统] 检测到进行中的任务，正在重连监控... (${String(taskUuid).slice(0, 8)})`);
    connectWebSocket(taskUuid);
    return true;
}

async function restoreBatchTaskById(taskMode, batchId, fallback = {}) {
    const data = await api.get(`/registration/batch/${batchId}`);
    if (data.finished) {
        return false;
    }

    const settings = {
        ...(fallback.settings || {}),
        ...(data.config_snapshot || {}),
    };
    const normalizedMode = taskMode === 'loop' ? 'loop' : 'batch';

    currentBatch = { batch_id: batchId, ...data };
    activeBatchId = batchId;
    isBatchMode = (normalizedMode === 'batch');
    isLoopMode = (normalizedMode === 'loop');
    batchCompleted = false;
    batchFinalStatus = null;
    toastShown = false;
    displayedLogs.clear();
    elements.startBtn.disabled = true;
    elements.cancelBtn.disabled = false;

    if (elements.regModeGroup) {
        elements.regModeGroup.style.display = 'block';
    }
    if (elements.regMode) {
        elements.regMode.value = normalizedMode === 'loop' ? 'loop' : 'batch';
        handleModeChange({ target: elements.regMode });
    }

    applyRecoveredTaskSettings(normalizedMode, settings);

    showBatchStatus({
        count: fallback.total || data.target_success || data.total,
        registration_mode: normalizedMode,
        window_start: fallback.window_start || data.window_start || settings.window_start,
        window_end: fallback.window_end || data.window_end || settings.window_end,
    });
    updateBatchProgress(data);
    addLog('info', `[系统] 检测到进行中的批量任务，正在重连监控... (${String(batchId).slice(0, 8)})`);
    connectBatchWebSocket(batchId);
    return true;
}

async function restoreFromServerActive() {
    const activeResponse = await api.get('/registration/active');
    const batchCandidates = Array.isArray(activeResponse.batch_tasks) ? activeResponse.batch_tasks : [];
    const singleCandidates = Array.isArray(activeResponse.single_tasks) ? activeResponse.single_tasks : [];
    const active = activeResponse.active || batchCandidates[0] || singleCandidates[0];

    if (!active) {
        return false;
    }

    if (!activeResponse.active && Number(activeResponse.active_count || 0) > 1) {
        addLog('warning', '[系统] 检测到多个进行中的任务，已自动恢复最近活跃任务的监控');
    }

    if (active.mode === 'single' && active.task_uuid) {
        const ok = await restoreSingleTaskById(active.task_uuid, active.settings || {});
        if (ok) {
            persistActiveTaskState({
                task_uuid: active.task_uuid,
                mode: 'single',
                settings: active.settings || {},
            });
        }
        return ok;
    }

    if (active.batch_id) {
        const mode = active.mode || active.registration_mode || 'batch';
        const ok = await restoreBatchTaskById(mode, active.batch_id, {
            total: active.target_success || active.total,
            window_start: active.window_start,
            window_end: active.window_end,
            settings: active.config_snapshot || {},
        });
        if (ok) {
            persistActiveTaskState({
                batch_id: active.batch_id,
                mode,
                total: active.target_success || active.total,
                window_start: active.window_start,
                window_end: active.window_end,
                settings: active.config_snapshot || {},
            });
        }
        return ok;
    }

    return false;
}

// 页面加载时恢复进行中的任务（浏览器重开也可恢复）
async function restoreActiveTask() {
    let restored = false;
    const saved = readActiveTaskStateRaw();

    if (saved) {
        try {
            const state = JSON.parse(saved);
            const { mode, task_uuid, batch_id, total, window_start, window_end } = state;

            if (mode === 'single' && task_uuid) {
                restored = await restoreSingleTaskById(task_uuid, state.settings || {});
            } else if ((mode === 'batch' || mode === 'loop') && batch_id) {
                restored = await restoreBatchTaskById(mode, batch_id, {
                    total,
                    window_start,
                    window_end,
                    settings: state.settings || {},
                });
            }
        } catch {
            clearActiveTaskState();
        }
    }

    if (!restored) {
        try {
            restored = await restoreFromServerActive();
        } catch (error) {
            console.warn('[状态恢复] 服务端活动任务恢复失败:', error);
        }
    }
}
