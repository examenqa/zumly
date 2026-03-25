<#
.SYNOPSIS
    Provisions Azure Trusted Signing resources and configures GitHub
    repo secrets for MSIX signing in CI.

.DESCRIPTION
    This script is idempotent — safe to re-run if a step fails partway.

    Steps:
    1. Register the Microsoft.CodeSigning resource provider
    2. Create a resource group
    3. Create a Trusted Signing account
    4. Create a certificate profile (PrivateTrust)
    5. Create an Azure AD app registration for GitHub Actions OIDC
    6. Create federated credentials for the GitHub repo
    7. Assign "Artifact Signing Certificate Profile Signer" role
    8. Set GitHub repo secrets via `gh`

.PARAMETER Location
    Azure region for the Trusted Signing account. Must be a region
    that supports Trusted Signing (e.g. eastus, westus, westeurope).

.PARAMETER ResourceGroupName
    Name of the resource group to create or use.

.PARAMETER AccountName
    Name of the Trusted Signing account.

.PARAMETER CertificateProfileName
    Name of the certificate profile.

.PARAMETER GitHubRepo
    GitHub repo in "owner/repo" format.

.EXAMPLE
    .\Setup-AzureSigning.ps1
#>

param(
    [Parameter(Mandatory)]
    [string]$Location,

    [Parameter(Mandatory)]
    [string]$ResourceGroupName,

    [Parameter(Mandatory)]
    [string]$AccountName,

    [Parameter(Mandatory)]
    [string]$CertificateProfileName,

    [Parameter(Mandatory)]
    [string]$GitHubRepo
)

$ErrorActionPreference = "Stop"

# ── Helpers ─────────────────────────────────────────────────────
function Write-Step { param([string]$msg) Write-Host "`n> $msg" -ForegroundColor Cyan }
function Write-OK   { param([string]$msg) Write-Host "  OK: $msg" -ForegroundColor Green }
function Write-Skip { param([string]$msg) Write-Host "  SKIP: $msg (already exists)" -ForegroundColor Yellow }

# ── Prerequisites ───────────────────────────────────────────────
Write-Step "Checking prerequisites"
$sub = az account show --query "{id:id, tenantId:tenantId}" -o json | ConvertFrom-Json
if (-not $sub) { throw "Not logged in to Azure CLI. Run 'az login' first." }
$subscriptionId = $sub.id
$tenantId = $sub.tenantId
Write-OK "Subscription: $subscriptionId"
Write-OK "Tenant: $tenantId"

gh auth status 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Not logged in to GitHub CLI. Run 'gh auth login' first." }
Write-OK "GitHub CLI authenticated"

# ── 1. Register resource provider ──────────────────────────────
Write-Step "Registering Microsoft.CodeSigning resource provider"
$providerState = az provider show --namespace Microsoft.CodeSigning --query "registrationState" -o tsv 2>$null
if ($providerState -eq "Registered") {
    Write-Skip "Microsoft.CodeSigning already registered"
} else {
    az provider register --namespace Microsoft.CodeSigning --wait
    Write-OK "Registered Microsoft.CodeSigning"
}

# ── 2. Create resource group ───────────────────────────────────
Write-Step "Creating resource group: $ResourceGroupName"
$rgExists = az group exists --name $ResourceGroupName -o tsv
if ($rgExists -eq "true") {
    Write-Skip "Resource group $ResourceGroupName"
} else {
    az group create --name $ResourceGroupName --location $Location -o none
    Write-OK "Created $ResourceGroupName in $Location"
}

# ── 3. Create Trusted Signing account ──────────────────────────
Write-Step "Creating Trusted Signing account: $AccountName"
$acctExists = $null
try {
    $acctExists = az resource show --resource-group $ResourceGroupName `
        --resource-type "Microsoft.CodeSigning/codeSigningAccounts" `
        --name $AccountName --query "id" -o tsv 2>&1
    if ($LASTEXITCODE -ne 0) { $acctExists = $null }
} catch { $acctExists = $null }
if ($acctExists) {
    Write-Skip "Trusted Signing account $AccountName"
} else {
    az resource create `
        --resource-group $ResourceGroupName `
        --resource-type "Microsoft.CodeSigning/codeSigningAccounts" `
        --name $AccountName `
        --location $Location `
        --properties '{"sku":{"name":"Basic"}}' `
        -o none
    Write-OK "Created Trusted Signing account $AccountName"
}

# Get the account endpoint
$acctEndpoint = $null
try {
    $acctEndpoint = az resource show --resource-group $ResourceGroupName `
        --resource-type "Microsoft.CodeSigning/codeSigningAccounts" `
        --name $AccountName --query "properties.accountUri" -o tsv 2>&1
    if ($LASTEXITCODE -ne 0) { $acctEndpoint = $null }
} catch { $acctEndpoint = $null }
if (-not $acctEndpoint) {
    # Construct the endpoint from the location
    $acctEndpoint = "https://$Location.codesigning.azure.net"
}
Write-OK "Endpoint: $acctEndpoint"

# ── 4. Create certificate profile ──────────────────────────────
Write-Step "Creating certificate profile: $CertificateProfileName"
$profileExists = $null
try {
    $profileExists = az resource show --resource-group $ResourceGroupName `
        --resource-type "Microsoft.CodeSigning/codeSigningAccounts/certificateProfiles" `
        --name "$AccountName/$CertificateProfileName" --query "id" -o tsv 2>&1
    if ($LASTEXITCODE -ne 0) { $profileExists = $null }
} catch { $profileExists = $null }
if ($profileExists) {
    Write-Skip "Certificate profile $CertificateProfileName"
} else {
    az resource create `
        --resource-group $ResourceGroupName `
        --resource-type "Microsoft.CodeSigning/codeSigningAccounts/certificateProfiles" `
        --name "$AccountName/$CertificateProfileName" `
        --properties (@{
            profileType = "PrivateTrust"
            includeCity = $false
            includeState = $false
            includePostalCode = $false
            includeStreetAddress = $false
        } | ConvertTo-Json) `
        -o none
    Write-OK "Created certificate profile $CertificateProfileName"
}

# Get the publisher (subject name) from the certificate profile
$publisher = $null
try {
    $publisher = az resource show --resource-group $ResourceGroupName `
        --resource-type "Microsoft.CodeSigning/codeSigningAccounts/certificateProfiles" `
        --name "$AccountName/$CertificateProfileName" `
        --query "properties.certificates[0].subjectName" -o tsv 2>&1
    if ($LASTEXITCODE -ne 0) { $publisher = $null }
} catch { $publisher = $null }
if (-not $publisher) {
    # PrivateTrust profiles may need time to generate the cert;
    # use a placeholder that the user can update later
    $publisher = "CN=$AccountName, O=FollowCursor"
    Write-Host "  WARNING: Certificate not yet generated. Using placeholder publisher: $publisher" -ForegroundColor Yellow
    Write-Host "    Update MSIX_PUBLISHER secret once the certificate is ready." -ForegroundColor Yellow
}
Write-OK "Publisher: $publisher"

# ── 5. Create Azure AD app registration ────────────────────────
Write-Step "Creating app registration: followcursor-github-actions"
$appName = "followcursor-github-actions"
$existingApp = $null
try {
    $existingApp = az ad app list --display-name $appName --query "[0].appId" -o tsv 2>&1
    if ($LASTEXITCODE -ne 0) { $existingApp = $null }
} catch { $existingApp = $null }
if ($existingApp) {
    $clientId = $existingApp
    Write-Skip "App registration $appName (clientId=$clientId)"
} else {
    $clientId = az ad app create --display-name $appName --query "appId" -o tsv
    Write-OK "Created app registration (clientId=$clientId)"
}

# Create service principal if needed
$spExists = $null
try {
    $spExists = az ad sp show --id $clientId --query "id" -o tsv 2>&1
    if ($LASTEXITCODE -ne 0) { $spExists = $null }
} catch { $spExists = $null }
if ($spExists) {
    $spObjectId = $spExists
    Write-Skip "Service principal"
} else {
    $spObjectId = az ad sp create --id $clientId --query "id" -o tsv
    Write-OK "Created service principal"
}

# ── 6. Add federated credentials for GitHub OIDC ───────────────
Write-Step "Configuring federated credentials for $GitHubRepo"

# Credential for tagged releases (refs/tags/v*)
$tagCredName = "github-tag-releases"
$tagCredExists = $null
try {
    $tagCredExists = az ad app federated-credential show --id $clientId --federated-credential-id $tagCredName 2>&1
    if ($LASTEXITCODE -ne 0) { $tagCredExists = $null }
} catch { $tagCredExists = $null }
if ($tagCredExists) {
    Write-Skip "Federated credential: $tagCredName"
} else {
    $tagCredBody = @{
        name        = $tagCredName
        issuer      = "https://token.actions.githubusercontent.com"
        subject     = "repo:${GitHubRepo}:ref:refs/heads/main"
        audiences   = @("api://AzureADTokenExchange")
        description = "GitHub Actions OIDC for FollowCursor main branch"
    } | ConvertTo-Json
    $tagCredBody | az ad app federated-credential create --id $clientId --parameters "@-"
    Write-OK "Created federated credential: $tagCredName"
}

# Credential for main branch pushes
$mainCredName = "github-main-branch"
$mainCredExists = $null
try {
    $mainCredExists = az ad app federated-credential show --id $clientId --federated-credential-id $mainCredName 2>&1
    if ($LASTEXITCODE -ne 0) { $mainCredExists = $null }
} catch { $mainCredExists = $null }
if ($mainCredExists) {
    Write-Skip "Federated credential: $mainCredName"
} else {
    $mainCredBody = @{
        name        = $mainCredName
        issuer      = "https://token.actions.githubusercontent.com"
        subject     = "repo:${GitHubRepo}:ref:refs/tags/*"
        audiences   = @("api://AzureADTokenExchange")
        description = "GitHub Actions OIDC for FollowCursor tag releases"
    } | ConvertTo-Json
    # Pipe JSON to stdin to avoid escaping issues
    $mainCredBody | az ad app federated-credential create --id $clientId --parameters "@-"
    Write-OK "Created federated credential: $mainCredName"
}

# ── 7. Assign Artifact Signing Certificate Profile Signer role ──
Write-Step "Assigning 'Artifact Signing Certificate Profile Signer' role"
$signingAccountId = az resource show --resource-group $ResourceGroupName `
    --resource-type "Microsoft.CodeSigning/codeSigningAccounts" `
    --name $AccountName --query "id" -o tsv

$roleAssigned = $null
try {
    $roleAssigned = az role assignment list --assignee $clientId --scope $signingAccountId `
        --role "Artifact Signing Certificate Profile Signer" --query "[0].id" -o tsv 2>&1
    if ($LASTEXITCODE -ne 0) { $roleAssigned = $null }
} catch { $roleAssigned = $null }
if ($roleAssigned) {
    Write-Skip "Role already assigned"
} else {
    az role assignment create `
        --assignee $clientId `
        --role "Artifact Signing Certificate Profile Signer" `
        --scope $signingAccountId `
        -o none
    Write-OK "Role assigned"
}

# ── 8. Set GitHub repo secrets ──────────────────────────────────
Write-Step "Setting GitHub repo secrets on $GitHubRepo"

$secrets = @{
    AZURE_CLIENT_ID              = $clientId
    AZURE_TENANT_ID              = $tenantId
    AZURE_SUBSCRIPTION_ID        = $subscriptionId
    MSIX_PUBLISHER               = $publisher
    AZURE_CODE_SIGNING_ENDPOINT  = $acctEndpoint
    AZURE_CODE_SIGNING_ACCOUNT   = $AccountName
    AZURE_CERTIFICATE_PROFILE    = $CertificateProfileName
}

foreach ($kv in $secrets.GetEnumerator()) {
    gh secret set $kv.Key --repo $GitHubRepo --body $kv.Value
    Write-OK "$($kv.Key) = $($kv.Value)"
}

# ── Done ────────────────────────────────────────────────────────
Write-Host "`n================================================" -ForegroundColor Green
Write-Host "  Azure Trusted Signing setup complete!" -ForegroundColor Green
Write-Host "================================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Subscription:       $subscriptionId"
Write-Host "  Resource Group:     $ResourceGroupName"
Write-Host "  Signing Account:    $AccountName"
Write-Host "  Certificate Profile:$CertificateProfileName"
Write-Host "  Endpoint:           $acctEndpoint"
Write-Host "  App Registration:   $appName ($clientId)"
Write-Host "  GitHub Repo:        $GitHubRepo"
Write-Host ""
Write-Host "  Next: push a tag (git tag v0.5.0 && git push --tags)" -ForegroundColor Yellow
Write-Host "  to trigger a signed MSIX build." -ForegroundColor Yellow
