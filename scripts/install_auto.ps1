param(
    [ValidateSet("install", "upgrade", "uninstall")]
    [string]$Action = "install",
    [ValidateSet("auto", "docker", "local")]
    [string]$Mode = "auto",
    [switch]$Purge,
    [switch]$NonInteractive
)

$ErrorActionPreference = "Stop"

$RepoUrl = if ($env:KEYGEN_REPO_URL) { $env:KEYGEN_REPO_URL } else { "https://github.com/1402771410/Codex-keygen.git" }
$RepoBranch = if ($env:KEYGEN_REPO_BRANCH) { $env:KEYGEN_REPO_BRANCH } else { "main" }
$InstallDir = if ($env:KEYGEN_INSTALL_DIR) { $env:KEYGEN_INSTALL_DIR } else { Join-Path $env:USERPROFILE ".codex-keygen" }

function Write-Info {
    param([string]$Message)
    Write-Host "[keygen-auto] $Message"
}

function Ask-YesNo {
    param(
        [string]$Prompt,
        [bool]$DefaultYes = $true
    )

    $suffix = if ($DefaultYes) { "Y/n" } else { "y/N" }
    $answer = Read-Host "$Prompt ($suffix)"
    if ([string]::IsNullOrWhiteSpace($answer)) {
        return $DefaultYes
    }

    $normalized = $answer.Trim().ToLowerInvariant()
    return @("y", "yes", "1", "true") -contains $normalized
}

function Ensure-Command {
    param(
        [string]$CommandName,
        [string]$WingetId
    )

    if (Get-Command $CommandName -ErrorAction SilentlyContinue) {
        return
    }

    Write-Info "缺少依赖命令：$CommandName"
    if (-not (Ask-YesNo "是否自动安装 $CommandName ?" $true)) {
        throw "用户取消安装依赖：$CommandName"
    }

    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw "未检测到 winget，无法自动安装 $CommandName"
    }

    & winget install --id $WingetId -e --accept-source-agreements --accept-package-agreements

    if (-not (Get-Command $CommandName -ErrorAction SilentlyContinue)) {
        throw "自动安装 $CommandName 失败，请手工安装后重试。"
    }
}

function Resolve-PythonCommand {
    if (Get-Command python -ErrorAction SilentlyContinue) {
        return @("python")
    }
    if (Get-Command py -ErrorAction SilentlyContinue) {
        return @("py", "-3")
    }

    Write-Info "未检测到 Python 3 运行环境"
    if (-not (Ask-YesNo "是否自动安装 Python 3 ?" $true)) {
        throw "用户取消安装 Python"
    }

    Ensure-Command -CommandName "python" -WingetId "Python.Python.3.12"
    return @("python")
}

function Ensure-Repository {
    New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null

    $gitDir = Join-Path $InstallDir ".git"
    if (-not (Test-Path $gitDir)) {
        Write-Info "首次安装：克隆仓库到 $InstallDir"
        & git clone --depth 1 --branch $RepoBranch $RepoUrl $InstallDir
        return
    }

    Write-Info "检测到已安装目录，执行升级同步"
    & git -C $InstallDir fetch --all --prune
    & git -C $InstallDir checkout $RepoBranch
    try {
        & git -C $InstallDir pull --ff-only
    } catch {
        Write-Info "git pull 失败，将继续使用本地代码执行命令"
    }
}

function Add-UserProfileToPath {
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if ([string]::IsNullOrWhiteSpace($userPath)) {
        $userPath = ""
    }

    $items = $userPath.Split(";", [System.StringSplitOptions]::RemoveEmptyEntries)
    foreach ($item in $items) {
        if ($item.Trim().ToLowerInvariant() -eq $env:USERPROFILE.Trim().ToLowerInvariant()) {
            return
        }
    }

    if (-not (Ask-YesNo "是否自动将 %USERPROFILE% 加入 PATH 以便在 CMD 直接运行 keygen ?" $true)) {
        return
    }

    $newPath = ($userPath.TrimEnd(';') + ";" + $env:USERPROFILE).Trim(';')
    [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
    Write-Info "PATH 已更新，请重新打开 CMD/PowerShell 后使用 keygen。"
}

function Install-KeygenLauncher {
    $launcherPath = Join-Path $env:USERPROFILE "keygen.bat"
    $normalizedInstallDir = $InstallDir -replace "/", "\\"
    $content = @"
@echo off
setlocal
set "KEYGEN_HOME=$normalizedInstallDir"
set "KEYGEN_LAUNCHER=keygen"
if exist "%KEYGEN_HOME%\.venv\Scripts\python.exe" (
    "%KEYGEN_HOME%\.venv\Scripts\python.exe" "%KEYGEN_HOME%\scripts\keygen.py" %*
) else (
    where py >nul 2>nul
    if %errorlevel%==0 (
        py -3 "%KEYGEN_HOME%\scripts\keygen.py" %*
    ) else (
        python "%KEYGEN_HOME%\scripts\keygen.py" %*
    )
)
"@

    Set-Content -Path $launcherPath -Value $content -Encoding ASCII
    Write-Info "已安装 Windows 管理命令：$launcherPath"
    Add-UserProfileToPath
}

function Invoke-Keygen {
    param(
        [string[]]$PythonCommand,
        [string[]]$Arguments
    )

    if ($PythonCommand.Count -eq 1) {
        & $PythonCommand[0] @Arguments
        return
    }

    & $PythonCommand[0] $PythonCommand[1] @Arguments
}

try {
    Ensure-Command -CommandName "git" -WingetId "Git.Git"
    $pythonCommand = Resolve-PythonCommand
    Ensure-Repository

    $commandAction = if ($Action -eq "upgrade") { "install" } else { $Action }

    $argList = @("scripts/keygen.py", $commandAction, "--mode", $Mode)
    if ($commandAction -eq "uninstall" -and $Purge.IsPresent) {
        $argList += "--purge"
    }
    if ($NonInteractive.IsPresent) {
        $argList += "--non-interactive"
    }

    Push-Location $InstallDir
    try {
        Write-Info "执行命令: $($pythonCommand -join ' ') $($argList -join ' ')"
        Invoke-Keygen -PythonCommand $pythonCommand -Arguments $argList
    } finally {
        Pop-Location
    }

    if ($commandAction -eq "install") {
        Install-KeygenLauncher
        Write-Info "完成：同一命令可重复执行，后续会自动走升级流程。"
        Write-Info "提示：可直接输入 keygen 打开管理面板。"
    } else {
        Write-Info "完成：卸载流程已执行。"
    }
} catch {
    Write-Error $_
    exit 1
}
