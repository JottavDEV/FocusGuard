param(
    [string]$Version = ""
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $scriptDir

Set-Location $projectRoot

if ([string]::IsNullOrWhiteSpace($Version)) {
    $versionFile = Join-Path $projectRoot "VERSION"
    if (-not (Test-Path $versionFile)) {
        throw "Arquivo VERSION nao encontrado em: $versionFile"
    }
    $Version = (Get-Content $versionFile -Raw).Trim()
}

if (-not ($Version -match '^\d+\.\d+\.\d+$')) {
    throw "Versao invalida '$Version'. Use formato semver simples, ex.: 1.0.0"
}

Write-Host "== FocusGuard Release Build =="
Write-Host "Versao: $Version"

$releaseDir = Join-Path $projectRoot "release"
if (-not (Test-Path $releaseDir)) {
    New-Item -ItemType Directory -Path $releaseDir | Out-Null
}

Write-Host "1) Instalando/atualizando dependencias..."
python -m pip install -r requirements.txt

Write-Host "2) Gerando executavel do aplicativo..."
$primaryName = "FocusGuard"
$fallbackName = "FocusGuard_build"
$appExe = Join-Path $projectRoot "dist\FocusGuard.exe"
$appExeForInstaller = $appExe

python -m PyInstaller --noconfirm --clean --onefile --windowed --name $primaryName --hidden-import customtkinter --collect-all customtkinter --collect-all pystray --collect-all PIL main.py
if ($LASTEXITCODE -ne 0) {
    Write-Host "Build primario falhou. Tentando fallback com nome alternativo..."
    python -m PyInstaller --noconfirm --clean --onefile --windowed --name $fallbackName --hidden-import customtkinter --collect-all customtkinter --collect-all pystray --collect-all PIL main.py
    if ($LASTEXITCODE -ne 0) {
        throw "Falha ao gerar executavel do aplicativo."
    }
    $appExeForInstaller = Join-Path $projectRoot ("dist\{0}.exe" -f $fallbackName)
}

if (-not (Test-Path $appExeForInstaller)) {
    throw "Executavel do app nao encontrado em: $appExeForInstaller"
}

Write-Host "3) Localizando compilador do Inno Setup..."
$isccCommand = Get-Command "ISCC.exe" -ErrorAction SilentlyContinue
if (-not $isccCommand) {
    $candidatePaths = @(
        "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        "C:\Program Files\Inno Setup 6\ISCC.exe",
        (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe")
    )

    $isccPath = $null
    foreach ($candidate in $candidatePaths) {
        if (Test-Path $candidate) {
            $isccPath = $candidate
            break
        }
    }

    if (-not $isccPath) {
        throw "ISCC.exe nao encontrado. Instale Inno Setup 6 e execute novamente."
    }
} else {
    $isccPath = $isccCommand.Source
}

Write-Host "4) Compilando instalador..."
$issPath = Join-Path $scriptDir "FocusGuard.iss"
& $isccPath "/DMyAppVersion=$Version" "/DMyAppExeSource=$appExeForInstaller" $issPath

$installerExe = Join-Path $releaseDir ("FocusGuard-Setup-v{0}.exe" -f $Version)
if (-not (Test-Path $installerExe)) {
    throw "Instalador nao encontrado em: $installerExe"
}

Write-Host "5) Gerando hashes..."
$effectiveAppPath = $appExe
if (Test-Path $appExeForInstaller) {
    $effectiveAppPath = $appExeForInstaller
}
$appHash = Get-FileHash -Algorithm SHA256 -Path $effectiveAppPath
$installerHash = Get-FileHash -Algorithm SHA256 -Path $installerExe

$hashReport = @"
FocusGuard v$Version

App EXE:
Path: $effectiveAppPath
SHA256: $($appHash.Hash)

Installer EXE:
Path: $installerExe
SHA256: $($installerHash.Hash)
"@

$hashFile = Join-Path $releaseDir ("SHA256-v{0}.txt" -f $Version)
Set-Content -Path $hashFile -Value $hashReport -Encoding UTF8

Write-Host ""
Write-Host "Build concluido com sucesso."
Write-Host "App: $effectiveAppPath"
Write-Host "Installer: $installerExe"
Write-Host "Hashes: $hashFile"
