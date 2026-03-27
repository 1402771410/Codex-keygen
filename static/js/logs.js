/**
 * 日志中心页面脚本
 */

const logElements = {
    runLogCount: document.getElementById('run-log-count'),
    operationLogCount: document.getElementById('operation-log-count'),
    lastRefreshTime: document.getElementById('last-refresh-time'),
    runLogFile: document.getElementById('run-log-file'),

    runLogLevel: document.getElementById('run-log-level'),
    runLogLines: document.getElementById('run-log-lines'),
    runLogKeyword: document.getElementById('run-log-keyword'),
    operationLogKeyword: document.getElementById('operation-log-keyword'),
    operationLogCategory: document.getElementById('operation-log-category'),
    autoRefreshLogs: document.getElementById('auto-refresh-logs'),
    refreshLogsBtn: document.getElementById('refresh-logs-btn'),

    runLogsTable: document.getElementById('run-logs-table'),
    operationLogsTable: document.getElementById('operation-logs-table'),
};

let logsAutoTimer = null;

document.addEventListener('DOMContentLoaded', () => {
    bindLogEvents();
    refreshAllLogs();
    startLogAutoRefresh();
});

function bindLogEvents() {
    if (logElements.refreshLogsBtn) {
        logElements.refreshLogsBtn.addEventListener('click', () => refreshAllLogs(true));
    }

    const runInputs = [logElements.runLogLevel, logElements.runLogLines];
    runInputs.forEach((input) => {
        if (!input) return;
        input.addEventListener('change', () => refreshRunLogs());
    });

    if (logElements.runLogKeyword) {
        logElements.runLogKeyword.addEventListener('input', debounce(() => refreshRunLogs(), 300));
    }

    if (logElements.operationLogKeyword) {
        logElements.operationLogKeyword.addEventListener('input', debounce(() => refreshOperationLogs(), 300));
    }

    if (logElements.operationLogCategory) {
        logElements.operationLogCategory.addEventListener('change', () => refreshOperationLogs());
    }

    if (logElements.autoRefreshLogs) {
        logElements.autoRefreshLogs.addEventListener('change', () => {
            if (logElements.autoRefreshLogs.checked) {
                startLogAutoRefresh();
            } else {
                stopLogAutoRefresh();
            }
        });
    }

    document.addEventListener('visibilitychange', () => {
        if (document.hidden) {
            stopLogAutoRefresh();
            return;
        }
        if (logElements.autoRefreshLogs?.checked) {
            startLogAutoRefresh();
            refreshAllLogs();
        }
    });
}

function startLogAutoRefresh() {
    stopLogAutoRefresh();
    if (!logElements.autoRefreshLogs?.checked) {
        return;
    }
    logsAutoTimer = window.setInterval(() => {
        if (!document.hidden) {
            refreshAllLogs();
        }
    }, 8000);
}

function stopLogAutoRefresh() {
    if (!logsAutoTimer) {
        return;
    }
    window.clearInterval(logsAutoTimer);
    logsAutoTimer = null;
}

async function refreshAllLogs(showToast = false) {
    await Promise.all([refreshRunLogs(), refreshOperationLogs()]);
    if (logElements.lastRefreshTime) {
        logElements.lastRefreshTime.textContent = format.date(new Date().toISOString());
    }
    if (showToast) {
        toast.success('日志已刷新');
    }
}

async function refreshRunLogs() {
    const lines = Number(logElements.runLogLines?.value || 200);
    const level = (logElements.runLogLevel?.value || '').trim();
    const keyword = (logElements.runLogKeyword?.value || '').trim();

    const params = new URLSearchParams({ lines: String(lines) });
    if (level) {
        params.append('level', level);
    }
    if (keyword) {
        params.append('keyword', keyword);
    }

    try {
        const data = await api.get(`/logs/run?${params.toString()}`);
        const entries = Array.isArray(data.entries) ? data.entries : [];
        renderRunLogTable(entries);
        if (logElements.runLogCount) {
            logElements.runLogCount.textContent = String(entries.length);
        }
        if (logElements.runLogFile) {
            logElements.runLogFile.textContent = `日志文件：${data.file || '--'}`;
        }
    } catch (error) {
        renderRunLogError(error.message);
    }
}

async function refreshOperationLogs() {
    const keyword = (logElements.operationLogKeyword?.value || '').trim();
    const category = (logElements.operationLogCategory?.value || '').trim();

    const params = new URLSearchParams({ limit: '300' });
    if (keyword) {
        params.append('keyword', keyword);
    }
    if (category) {
        params.append('category', category);
    }

    try {
        const data = await api.get(`/logs/operations?${params.toString()}`);
        const entries = Array.isArray(data.entries) ? data.entries : [];
        renderOperationLogTable(entries);
        if (logElements.operationLogCount) {
            logElements.operationLogCount.textContent = String(entries.length);
        }
    } catch (error) {
        renderOperationLogError(error.message);
    }
}

function renderRunLogTable(entries) {
    if (!logElements.runLogsTable) {
        return;
    }

    if (entries.length === 0) {
        logElements.runLogsTable.innerHTML = `
            <tr>
                <td colspan="4">
                    <div class="empty-state">
                        <div class="empty-state-title">暂无运行日志</div>
                    </div>
                </td>
            </tr>
        `;
        return;
    }

    logElements.runLogsTable.innerHTML = entries.map((entry) => {
        const level = String(entry.level || 'UNKNOWN').toUpperCase();
        const levelClass = mapLogLevelClass(level);
        const source = escapeHtml(entry.source || '-');
        const message = escapeHtml(entry.message || entry.raw || '-');
        const raw = escapeHtml(entry.raw || message);
        return `
            <tr>
                <td>${format.date(entry.timestamp)}</td>
                <td><span class="status-badge ${levelClass}">${level}</span></td>
                <td>${source}</td>
                <td title="${raw}">${message}</td>
            </tr>
        `;
    }).join('');
}

function renderOperationLogTable(entries) {
    if (!logElements.operationLogsTable) {
        return;
    }

    if (entries.length === 0) {
        logElements.operationLogsTable.innerHTML = `
            <tr>
                <td colspan="5">
                    <div class="empty-state">
                        <div class="empty-state-title">暂无操作日志</div>
                    </div>
                </td>
            </tr>
        `;
        return;
    }

    logElements.operationLogsTable.innerHTML = entries.map((entry) => {
        const level = String(entry.level || 'INFO').toUpperCase();
        const levelClass = mapLogLevelClass(level);
        const category = escapeHtml(entry.category || '-');
        const action = escapeHtml(entry.action || '-');
        const title = escapeHtml(entry.title || '-');
        const message = escapeHtml(entry.message || '-');
        return `
            <tr>
                <td>${format.date(entry.timestamp)}</td>
                <td><span class="status-badge ${levelClass}">${category}</span></td>
                <td>${action}</td>
                <td>${title}</td>
                <td>${message}</td>
            </tr>
        `;
    }).join('');
}

function renderRunLogError(message) {
    if (!logElements.runLogsTable) {
        return;
    }
    logElements.runLogsTable.innerHTML = `
        <tr>
            <td colspan="4">
                <div class="empty-state">
                    <div class="empty-state-title">加载运行日志失败</div>
                    <div class="empty-state-description">${escapeHtml(message || '未知错误')}</div>
                </div>
            </td>
        </tr>
    `;
}

function renderOperationLogError(message) {
    if (!logElements.operationLogsTable) {
        return;
    }
    logElements.operationLogsTable.innerHTML = `
        <tr>
            <td colspan="5">
                <div class="empty-state">
                    <div class="empty-state-title">加载操作日志失败</div>
                    <div class="empty-state-description">${escapeHtml(message || '未知错误')}</div>
                </div>
            </td>
        </tr>
    `;
}

function mapLogLevelClass(level) {
    if (level === 'ERROR' || level === 'CRITICAL') {
        return 'error';
    }
    if (level === 'WARNING') {
        return 'warning';
    }
    if (level === 'DEBUG') {
        return 'pending';
    }
    return 'running';
}

function escapeHtml(text) {
    if (text === null || text === undefined) {
        return '';
    }
    const div = document.createElement('div');
    div.textContent = String(text);
    return div.innerHTML;
}
