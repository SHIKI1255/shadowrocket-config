param(
    [ValidateSet('New','Complete','Activate','Pin','Unpin','Cleanup','Restore')][string]$Action='Cleanup',
    [ValidateSet('tmp','cache','logs','reports','inputs','outputs')][string]$Kind='tmp',
    [string]$Id='', [string]$WorkbenchRoot='', [string]$RuntimeRoot='',
    [switch]$Apply, [switch]$Opportunistic
)
$ErrorActionPreference='Stop'
Import-Module (Join-Path $PSScriptRoot 'tools/governance/artifacts.psm1') -DisableNameChecking
try {
    $slug=(Get-Content -LiteralPath (Join-Path $PSScriptRoot 'artifact_config.json') -Raw|ConvertFrom-Json).repo_slug
    $roots=Get-ArtifactRoots -RepoRoot $PSScriptRoot -Slug $slug -WorkbenchRoot $WorkbenchRoot -RuntimeRoot $RuntimeRoot
    Invoke-ArtifactAction -Roots $roots -Action $Action -Kind $Kind -Id $Id -Apply:$Apply -Opportunistic:$Opportunistic | ConvertTo-Json -Depth 25
}catch{
    @{status='failed';error=$_.Exception.Message}|ConvertTo-Json -Compress
    exit 1
}
