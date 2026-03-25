<#
.SYNOPSIS
    Packages the PyInstaller output into a signed MSIX.

.DESCRIPTION
    1. Generates MSIX visual assets (PNGs) from the app icon.
    2. Patches AppxManifest.xml with the correct version and publisher.
    3. Copies PyInstaller dist + manifest + assets into a staging folder.
    4. Runs MakeAppx.exe to create the .msix.
    5. Signs the .msix with SignTool + Azure Trusted Signing (if configured).

.PARAMETER Version
    Semantic version (e.g. "0.5.0"). A ".0" build number is appended
    automatically to satisfy MSIX's four-part requirement.

.PARAMETER Publisher
    The certificate subject (CN=...) that matches your Azure Trusted
    Signing certificate profile.  Defaults to the placeholder in the
    manifest.

.PARAMETER SkipSign
    If set, skips the signing step (useful for local testing).

.EXAMPLE
    .\Build-Msix.ps1 -Version "0.5.0" -Publisher "CN=FollowCursor, O=FollowCursor, L=Redmond, S=Washington, C=US"
#>

param(
    [Parameter(Mandatory)]
    [string]$Version,

    [string]$Publisher = "",

    [switch]$SkipSign,

    # Azure Trusted Signing parameters (passed via CI secrets)
    [string]$AzureEndpoint = "",
    [string]$AzureCodeSigningAccountName = "",
    [string]$AzureCertificateProfileName = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot  # repo root / followcursor
$FollowCursorRoot = $PSScriptRoot                # followcursor/
$DistDir = Join-Path $FollowCursorRoot "dist\FollowCursor"
$MsixDir = Join-Path $FollowCursorRoot "msix"
$StagingDir = Join-Path $FollowCursorRoot "dist\msix_staging"
$OutputMsix = Join-Path $FollowCursorRoot "dist\FollowCursor-$Version.msix"

# ── Validate prerequisites ──────────────────────────────────────
if (-not (Test-Path $DistDir)) {
    Write-Error "PyInstaller output not found at $DistDir. Run build.bat first."
}
if (-not (Test-Path (Join-Path $MsixDir "AppxManifest.xml"))) {
    Write-Error "AppxManifest.xml not found in $MsixDir."
}

# ── 1. Generate MSIX visual assets ──────────────────────────────
Write-Host "Generating MSIX visual assets..." -ForegroundColor Cyan
$python = Join-Path $FollowCursorRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    $python = "python"
}
& $python (Join-Path $FollowCursorRoot "generate_msix_assets.py")
if ($LASTEXITCODE -ne 0) { Write-Error "Asset generation failed." }

# ── 2. Patch manifest with version + publisher ──────────────────
Write-Host "Patching AppxManifest.xml..." -ForegroundColor Cyan
$manifest = Get-Content (Join-Path $MsixDir "AppxManifest.xml") -Raw

# MSIX requires four-part version: Major.Minor.Patch.Build
$msixVersion = "$Version.0"
$manifest = $manifest -replace 'Version="[^"]*"', "Version=`"$msixVersion`""

if ($Publisher) {
    $manifest = $manifest -replace 'Publisher="[^"]*"', "Publisher=`"$Publisher`""
}

# Write patched manifest to staging (don't modify the template)
if (Test-Path $StagingDir) { Remove-Item $StagingDir -Recurse -Force }
New-Item -ItemType Directory -Path $StagingDir -Force | Out-Null

# ── 3. Stage files ──────────────────────────────────────────────
Write-Host "Staging MSIX content..." -ForegroundColor Cyan

# Copy PyInstaller output (flatten _internal into root for MSIX)
# MSIX expects the exe at the package root
Copy-Item "$DistDir\FollowCursor.exe" $StagingDir
if (Test-Path "$DistDir\_internal") {
    Copy-Item "$DistDir\_internal\*" $StagingDir -Recurse -Force
}

# Copy assets
$assetsStaging = Join-Path $StagingDir "Assets"
New-Item -ItemType Directory -Path $assetsStaging -Force | Out-Null
Copy-Item (Join-Path $MsixDir "Assets\*") $assetsStaging -Recurse -Force

# Write patched manifest
$manifest | Set-Content (Join-Path $StagingDir "AppxManifest.xml") -Encoding UTF8

# ── 4. Build MSIX ───────────────────────────────────────────────
Write-Host "Building MSIX package..." -ForegroundColor Cyan

# Find MakeAppx.exe from Windows SDK
$sdkBinPaths = @(
    "${env:ProgramFiles(x86)}\Windows Kits\10\bin\*\x64\MakeAppx.exe"
    "${env:ProgramFiles}\Windows Kits\10\bin\*\x64\MakeAppx.exe"
)
$makeAppx = $sdkBinPaths | ForEach-Object { Resolve-Path $_ -ErrorAction SilentlyContinue } |
    Sort-Object -Descending | Select-Object -First 1

if (-not $makeAppx) {
    Write-Error "MakeAppx.exe not found. Install the Windows SDK (Desktop C++ workload)."
}

& $makeAppx.Path pack /d $StagingDir /p $OutputMsix /o
if ($LASTEXITCODE -ne 0) { Write-Error "MakeAppx.exe failed." }

Write-Host "MSIX created: $OutputMsix" -ForegroundColor Green

# ── 5. Sign with Azure Trusted Signing ──────────────────────────
if ($SkipSign) {
    Write-Host "Signing skipped (-SkipSign)." -ForegroundColor Yellow
    return
}

if (-not $AzureEndpoint -or -not $AzureCodeSigningAccountName -or -not $AzureCertificateProfileName) {
    Write-Host "Azure Trusted Signing parameters not provided — skipping signing." -ForegroundColor Yellow
    Write-Host "To sign, provide -AzureEndpoint, -AzureCodeSigningAccountName, -AzureCertificateProfileName." -ForegroundColor Yellow
    return
}

Write-Host "Signing with Azure Trusted Signing..." -ForegroundColor Cyan

# Find SignTool.exe from Windows SDK
$signToolPaths = @(
    "${env:ProgramFiles(x86)}\Windows Kits\10\bin\*\x64\signtool.exe"
    "${env:ProgramFiles}\Windows Kits\10\bin\*\x64\signtool.exe"
)
$signTool = $signToolPaths | ForEach-Object { Resolve-Path $_ -ErrorAction SilentlyContinue } |
    Sort-Object -Descending | Select-Object -First 1

if (-not $signTool) {
    Write-Error "SignTool.exe not found. Install the Windows SDK."
}

# Azure Trusted Signing uses a dlib (dynamic library) for SignTool integration
# The dlib is installed via the Azure.CodeSigning NuGet package in CI
$dlibPath = Get-ChildItem -Path "$env:USERPROFILE\.nuget\packages\microsoft.trusted.signing.client" -Filter "Azure.CodeSigning.Dlib.dll" -Recurse -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -match "x64" } |
    Sort-Object FullName -Descending |
    Select-Object -First 1

if (-not $dlibPath) {
    Write-Error "Azure.CodeSigning.Dlib.dll not found. Install the Microsoft.Trusted.Signing.Client NuGet package."
}

# Build the metadata JSON for Azure Trusted Signing
$metadataJson = @{
    Endpoint               = $AzureEndpoint
    CodeSigningAccountName = $AzureCodeSigningAccountName
    CertificateProfileName = $AzureCertificateProfileName
} | ConvertTo-Json

$metadataPath = Join-Path $env:TEMP "trustedsigning-metadata.json"
$metadataJson | Set-Content $metadataPath -Encoding UTF8

& $signTool.Path sign /v /fd SHA256 /tr "http://timestamp.acs.microsoft.com" /td SHA256 /dlib $dlibPath.FullName /dmdf $metadataPath $OutputMsix

if ($LASTEXITCODE -ne 0) { Write-Error "Signing failed." }

Write-Host "MSIX signed successfully: $OutputMsix" -ForegroundColor Green

# Clean up
Remove-Item $metadataPath -ErrorAction SilentlyContinue
