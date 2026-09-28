Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$script:Version = '1.0.0'
$script:Retention = @{tmp=7; cache=7; logs=30; reports=90; inputs=0; outputs=0}

function Assert-NoLink([string]$Path) {
    $full = [IO.Path]::GetFullPath($Path)
    for ($p=$full; $p; $p=Split-Path -Parent $p) {
        if (Test-Path -LiteralPath $p) {
            if ((Get-Item -LiteralPath $p -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "LINK_FORBIDDEN: $p" }
        }
        if ($p -eq [IO.Path]::GetPathRoot($p)) { break }
    }
    return $full.TrimEnd('\','/')
}
function Test-Within([string]$Child,[string]$Parent) {
    return $Child.Equals($Parent,[StringComparison]::OrdinalIgnoreCase) -or $Child.StartsWith($Parent.TrimEnd('\','/')+[IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)
}
function Get-ArtifactRoots([string]$RepoRoot,[string]$Slug,[string]$WorkbenchRoot='', [string]$RuntimeRoot='') {
    if ($Slug -cnotmatch '^[a-z0-9]+(-[a-z0-9]+)*$') { throw 'INVALID_PROJECT_ID' }
    $prefix=$Slug.Replace('-','_').ToUpperInvariant()
    $work=if($WorkbenchRoot){$WorkbenchRoot}elseif([Environment]::GetEnvironmentVariable($prefix+'_WORKBENCH_ROOT')){[Environment]::GetEnvironmentVariable($prefix+'_WORKBENCH_ROOT')}else{'D:/CodexProjects/codex_workbench'}
    $runtime=if($RuntimeRoot){$RuntimeRoot}elseif([Environment]::GetEnvironmentVariable($prefix+'_RUNTIME_ROOT')){[Environment]::GetEnvironmentVariable($prefix+'_RUNTIME_ROOT')}else{'D:/CodexProjects/codex_runtime'}
    $work=Assert-NoLink $work; $runtime=Assert-NoLink $runtime; $source=Assert-NoLink $RepoRoot
    if ((Test-Within $work $runtime) -or (Test-Within $runtime $work)) { throw 'ROOTS_OVERLAP' }
    # A packaged helper may live inside a runtime environment. It is not a
    # checkout; its files remain protected, but its containing runtime is valid.
    $packaged=(Split-Path -Leaf $source) -eq 'governance' -and (Test-Path -LiteralPath (Join-Path (Split-Path -Parent $source) 'artifact_lifecycle.py'))
    $protected=@((Join-Path $env:USERPROFILE '.codex'),(Join-Path $env:USERPROFILE '.agents'))
    foreach($root in @($work,$runtime)) {
        if((Test-Within $root $source) -or ((Test-Within $source $root) -and !($packaged -and $root -eq $runtime))){throw 'PROTECTED_ROOT_OVERLAP'}
        if($root -eq [IO.Path]::GetPathRoot($root).TrimEnd('\') -or $root -eq $env:USERPROFILE) { throw 'BROAD_ROOT_FORBIDDEN' }
        foreach($p in $protected) { if((Test-Within $root $p) -or (Test-Within $p $root)) { throw 'PROTECTED_ROOT_OVERLAP' } }
        for($p=$root; $p; $p=Split-Path -Parent $p) {
            if(Test-Path -LiteralPath (Join-Path $p '.git')) { throw 'SOURCE_ROOT_FORBIDDEN' }
            if($p -eq [IO.Path]::GetPathRoot($p)){break}
        }
    }
    return @{workbench=$work; runtime=$runtime; project=(Join-Path $work "projects/$Slug"); slug=$Slug; source=$source}
}
function Write-ArtifactJson([string]$Path,$Value) {
    $null=Assert-NoLink $Path
    $tmp=$Path+'.'+[guid]::NewGuid().ToString('N')+'.tmp'
    try {
        [IO.File]::WriteAllText($tmp,($Value | ConvertTo-Json -Depth 25),[Text.UTF8Encoding]::new($false))
        [IO.File]::Move($tmp,$Path,$true)
    } finally { if(Test-Path -LiteralPath $tmp){Remove-Item -LiteralPath $tmp -Force} }
}
function Get-ArtifactInventory([string]$Root) {
    $null=Assert-NoLink $Root
    $stack=[Collections.Generic.Stack[string]]::new(); $stack.Push($Root)
    $files=[Collections.Generic.List[object]]::new(); $dirs=[Collections.Generic.List[string]]::new()
    while($stack.Count) {
        $dir=$stack.Pop(); $null=Assert-NoLink $dir; $dirs.Add($dir)
        foreach($f in Get-ChildItem -LiteralPath $dir -Force) {
            $null=Assert-NoLink $f.FullName
            if($f.Name -eq '.git'){throw 'NESTED_REPOSITORY_FORBIDDEN'}
            if($f.PSIsContainer){$stack.Push($f.FullName);continue}
            $size=$f.Length; $ticks=$f.LastWriteTimeUtc.Ticks
            $hash=(Get-FileHash -LiteralPath $f.FullName -Algorithm SHA256).Hash
            $f.Refresh()
            if($f.Length -ne $size -or $f.LastWriteTimeUtc.Ticks -ne $ticks){throw 'ARTIFACT_CHANGED'}
            $files.Add([ordered]@{path=[IO.Path]::GetRelativePath($Root,$f.FullName);bytes=$size;mtime_ticks=$ticks;sha256=$hash})
        }
    }
    $sorted=@($files | Sort-Object {$_['path']})
    $json=ConvertTo-Json -InputObject $sorted -Depth 5 -Compress
    $hash=[Convert]::ToHexString([Security.Cryptography.SHA256]::HashData([Text.Encoding]::UTF8.GetBytes($json)))
    return @{files=$sorted;directories=@($dirs | Sort-Object Length -Descending);sha256=$hash}
}
function Remove-ArtifactTree([string]$Root,$Expected) {
    $current=Get-ArtifactInventory $Root
    if($current.sha256 -ne $Expected.sha256){throw 'ARTIFACT_CHANGED'}
    foreach($file in $current.files) {
        $path=Join-Path $Root $file.path; $null=Assert-NoLink $path
        if((Get-FileHash -LiteralPath $path).Hash -ne $file.sha256){throw 'ARTIFACT_CHANGED'}
        Remove-Item -LiteralPath $path -Force
    }
    foreach($dir in $current.directories) {
        $null=Assert-NoLink $dir
        if(@(Get-ChildItem -LiteralPath $dir -Force).Count){throw 'DIRECTORY_CHANGED'}
        Remove-Item -LiteralPath $dir -Force
    }
}
function Invoke-ArtifactAction {
    [CmdletBinding()]
    param([Parameter(Mandatory)][hashtable]$Roots,
        [ValidateSet('New','Complete','Activate','Pin','Unpin','Cleanup','Restore')][string]$Action,
        [ValidateSet('tmp','cache','logs','reports','inputs','outputs')][string]$Kind='tmp',
        [string]$Id='', [switch]$Apply, [switch]$Opportunistic,
        [datetimeoffset]$Now=[datetimeoffset]::UtcNow)
    if($Id -and $Id -cnotmatch '^[a-z0-9][a-z0-9_-]{0,95}$'){throw 'INVALID_ARTIFACT_ID'}
    if($Action -notin @('New','Cleanup') -and !$Id){throw 'ARTIFACT_ID_REQUIRED'}
    $project=Assert-NoLink $Roots.project
    if(!(Test-Within $project $Roots.workbench) -or $project -eq $Roots.workbench){throw 'INVALID_PROJECT_BOUNDARY'}
    $meta=Join-Path $project '.registry'
    $mutating=($Action -notin @('Cleanup','Restore')) -or $Apply
    if(!$mutating -and !(Test-Path -LiteralPath $meta)){return @{status='preview';actions=@();version=$script:Version}}
    if($mutating){New-Item -ItemType Directory -Path $meta -Force | Out-Null}
    $null=Assert-NoLink $meta
    $lock=$null
    try {
        # Read-only previews do not create locks or directories.
        if($mutating){$lockPath=Assert-NoLink (Join-Path $meta 'project.lock');$lock=[IO.File]::Open($lockPath,'OpenOrCreate','ReadWrite','None')}
        $events=[Collections.Generic.List[object]]::new()
        $clockPath=Join-Path $meta 'cleanup_clock.json'
        $null=Assert-NoLink $clockPath
        if($Action -eq 'Cleanup' -and $Opportunistic -and (Test-Path -LiteralPath $clockPath)) {
            $clock=Get-Content -LiteralPath $clockPath -Raw | ConvertFrom-Json
            if(($Now-[datetimeoffset]$clock.last_attempt).TotalHours -lt 24){return @{status='throttled';actions=@()}}
        }
        if($Action -eq 'Cleanup' -and $Opportunistic -and $Apply){Write-ArtifactJson $clockPath @{last_attempt=$Now.ToString('o')}}
        if($Action -eq 'New') {
            if(!$Id){$Id=[guid]::NewGuid().ToString('N')}
            $recordPath=Join-Path $meta ($Id+'.json')
            $target=Join-Path $project "$Kind/$Id"
            if((Test-Path -LiteralPath $recordPath) -or (Test-Path -LiteralPath $target)){throw 'ARTIFACT_EXISTS'}
            $null=Assert-NoLink $target; New-Item -ItemType Directory -Path $target -Force | Out-Null
            $record=@{schema=1;id=$Id;repo=$Roots.slug;kind=$Kind;state='active';pinned=$false;created_at=$Now.ToString('o');last_used_at=$Now.ToString('o');completed_at=$null;quarantined_at=$null;inventory=$null;version=$script:Version}
            Write-ArtifactJson $recordPath $record
            return @{status='active';id=$Id;path=$target;version=$script:Version}
        }
        $recordFiles=if($Action -eq 'Cleanup'){@(Get-ChildItem -LiteralPath $meta -File -Filter '*.json' | Where-Object Name -ne 'cleanup_clock.json')}else{@(Get-Item -LiteralPath (Join-Path $meta ($Id+'.json')))}
        foreach($rf in $recordFiles) {
            $null=Assert-NoLink $rf.FullName
            try {
                $r=Get-Content -LiteralPath $rf.FullName -Raw | ConvertFrom-Json -AsHashtable
                if($r.schema -ne 1 -or $r.version -ne $script:Version -or $r.repo -ne $Roots.slug -or $r.id -cnotmatch '^[a-z0-9][a-z0-9_-]{0,95}$' -or $rf.BaseName -cne $r.id -or !$script:Retention.ContainsKey($r.kind)){throw 'INVALID_REGISTRATION'}
                $original=Join-Path $project "$($r.kind)/$($r.id)"
                $quarantine=Join-Path $project ".quarantine/$($r.id)"
                $null=Assert-NoLink $original; $null=Assert-NoLink $quarantine
                if($Action -in @('Complete','Activate','Pin','Unpin')) {
                    if($r.state -notin @('active','completed') -or !(Test-Path -LiteralPath $original)){throw 'ARTIFACT_NOT_AVAILABLE'}
                    if($Action -eq 'Complete'){$r.state='completed';$r.completed_at=$Now.ToString('o')}
                    if($Action -eq 'Activate'){$r.state='active';$r.completed_at=$null}
                    if($Action -eq 'Pin'){$r.pinned=$true}
                    if($Action -eq 'Unpin'){$r.pinned=$false}
                    $r.last_used_at=$Now.ToString('o')
                    Write-ArtifactJson $rf.FullName $r
                    $events.Add(@{id=$r.id;action=$Action;path=$original});continue
                }
                if($Action -eq 'Restore') {
                    if($r.state -ne 'quarantined' -or !(Test-Path -LiteralPath $quarantine)){throw 'NOT_QUARANTINED'}
                    if(Test-Path -LiteralPath $original){throw 'RESTORE_COLLISION'}
                    if((Get-ArtifactInventory $quarantine).sha256 -ne $r.inventory.sha256){throw 'QUARANTINE_CHANGED'}
                    if($Apply){
                        New-Item -ItemType Directory -Path (Split-Path -Parent $original) -Force | Out-Null
                        Move-Item -LiteralPath $quarantine -Destination $original
                        $r.state='completed';$r.pinned=$true;$r.last_used_at=$Now.ToString('o');$r.quarantined_at=$null
                        Write-ArtifactJson $rf.FullName $r
                    }
                    $events.Add(@{id=$r.id;action='restore';applied=[bool]$Apply});continue
                }
                if($r.pinned -or $r.kind -in @('inputs','outputs') -or $r.state -in @('active','deleted')){continue}
                if($r.state -eq 'completed'){
                    $completed=[datetimeoffset]$r.completed_at;$used=[datetimeoffset]$r.last_used_at
                    if($completed -gt $Now -or $used -gt $Now){throw 'FUTURE_TIMESTAMP'}
                    $reference=if($completed -gt $used){$completed}else{$used}
                    if(($Now-$reference).TotalDays -lt $script:Retention[$r.kind]){continue}
                    $inventory=Get-ArtifactInventory $original
                    if($Apply){
                        if(Test-Path -LiteralPath $quarantine){throw 'QUARANTINE_COLLISION'}
                        New-Item -ItemType Directory -Path (Split-Path -Parent $quarantine) -Force | Out-Null
                        # Record intent before move. An interrupted operation is review-only, never swept.
                        $r.inventory=$inventory;$r.state='quarantining'
                        Write-ArtifactJson $rf.FullName $r
                        if((Get-ArtifactInventory $original).sha256 -ne $inventory.sha256){throw 'ARTIFACT_CHANGED'}
                        Move-Item -LiteralPath $original -Destination $quarantine
                        if((Get-ArtifactInventory $quarantine).sha256 -ne $inventory.sha256){throw 'QUARANTINE_CHANGED'}
                        $r.state='quarantined';$r.quarantined_at=$Now.ToString('o')
                        Write-ArtifactJson $rf.FullName $r
                    }
                    $events.Add(@{id=$r.id;action='quarantine';sha256=$inventory.sha256;applied=[bool]$Apply})
                } elseif($r.state -eq 'quarantined'){
                    if(($Now-[datetimeoffset]$r.quarantined_at).TotalDays -lt 7){continue}
                    if((Get-ArtifactInventory $quarantine).sha256 -ne $r.inventory.sha256){throw 'QUARANTINE_CHANGED'}
                    if($Apply){
                        $r.state='deleting';Write-ArtifactJson $rf.FullName $r
                        Remove-ArtifactTree $quarantine $r.inventory
                        $r.state='deleted';$r.deleted_at=$Now.ToString('o');$r.inventory=@{sha256=$r.inventory.sha256}
                        Write-ArtifactJson $rf.FullName $r
                    }
                    $events.Add(@{id=$r.id;action='delete';applied=[bool]$Apply})
                } else {throw 'INCOMPLETE_TRANSACTION_REQUIRES_REVIEW'}
            } catch {
                if($Action -ne 'Cleanup'){throw}
                $events.Add(@{id=$rf.BaseName;action='preserve';error=$_.Exception.Message})
            }
        }
        if($mutating -and $events.Count){
            $logPath=Join-Path $meta 'events.jsonl'
            $null=Assert-NoLink $logPath
            foreach($event in $events){[IO.File]::AppendAllText($logPath,(@{at=$Now.ToString('o');event=$event}|ConvertTo-Json -Depth 8 -Compress)+[Environment]::NewLine)}
        }
        return @{status=if($mutating){'applied'}else{'preview'};actions=@($events);version=$script:Version}
    } finally {
        if($lock){$lock.Dispose()}
        if($Action -in @('New','Complete')){
            try {
                $sweep=Invoke-ArtifactAction -Roots $Roots -Action Cleanup -Apply -Opportunistic -Now $Now
                foreach($entry in $sweep.actions){if($entry.action -eq 'preserve'){[Console]::Error.WriteLine('Cleanup preserved '+$entry.id+': '+$entry.error)}}
            }catch{[Console]::Error.WriteLine('Cleanup skipped: '+$_.Exception.Message)}
        }
    }
}
Export-ModuleMember -Function Get-ArtifactRoots,Invoke-ArtifactAction,Get-ArtifactInventory,Assert-NoLink,Test-Within
