param(
    [string]$ImageName = $(if ($env:IMAGE_NAME) { $env:IMAGE_NAME } else { "codex-register" }),
    [string]$ImageTag = $(if ($env:IMAGE_TAG) { $env:IMAGE_TAG } else { "latest" }),
    [string]$OutputDir = $(if ($env:OUTPUT_DIR) { $env:OUTPUT_DIR } else { "dist" }),
    [string]$ContainerCli = $(if ($env:CONTAINER_CLI) { $env:CONTAINER_CLI } else { "" }),
    [string]$OutputFile = ""
)

$ErrorActionPreference = "Stop"

$SafeImageName = $ImageName -replace '/', '_'
$SafeTag = $ImageTag -replace ':', '_'

if ([string]::IsNullOrWhiteSpace($OutputFile)) {
    $OutputFile = Join-Path $OutputDir "$SafeImageName-$SafeTag.tar"
}

if (-not (Test-Path $OutputDir)) {
    New-Item -ItemType Directory -Path $OutputDir | Out-Null
}

if ([string]::IsNullOrWhiteSpace($ContainerCli)) {
    if (Get-Command docker -ErrorAction SilentlyContinue) {
        $ContainerCli = "docker"
    } elseif (Get-Command podman -ErrorAction SilentlyContinue) {
        $ContainerCli = "podman"
    } else {
        throw "docker/podman command not found. Install a container runtime first."
    }
}

Write-Host "[1/2] Build image with ${ContainerCli}: $ImageName`:$ImageTag"
& $ContainerCli build -t "$ImageName`:$ImageTag" .
if ($LASTEXITCODE -ne 0) {
    throw "Image build failed with exit code $LASTEXITCODE"
}

Write-Host "[2/2] Export image tar: $OutputFile"
& $ContainerCli save -o "$OutputFile" "$ImageName`:$ImageTag"
if ($LASTEXITCODE -ne 0) {
    throw "Image export failed with exit code $LASTEXITCODE"
}

Write-Host "Done: $OutputFile"
