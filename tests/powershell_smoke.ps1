#requires -Version 7.3
$ErrorActionPreference = 'Stop'
function Assert($Condition, $Message) { if (!$Condition) { throw $Message } }
. (Join-Path $PSScriptRoot '../shell/tmem.ps1')
. (Join-Path $PSScriptRoot '../shell/tmem.ps1') # idempotent
Assert ($env:TMEM_SHELL -eq 'powershell') 'shell identity'

# Save and execute in the caller, not a function-local child scope.
tmem save state -- '$tmemTestState = "persistent"; function TmemTestFunction { "function persisted" }'
. tmem run state
Assert ($tmemTestState -eq 'persistent') 'variable must survive'
Assert ((TmemTestFunction) -eq 'function persisted') 'function must survive'

# The line-editor resolver evaluates arguments but never executes the selection.
tmem save literal -- '$tmemLiteral = {{value}}'
$value = "O'Brien €東京 `$(`"not executed`") ; & | ``"
$value += [string][char]0x2018 + [char]0x2019 + [char]0x201a + [char]0x201b
$expansion = _tmem_expand_line 'tmem run literal $value'
Assert ($null -ne $expansion.Plan) 'expansion returned a plan'
Assert (!(Get-Variable tmemLiteral -ErrorAction Ignore)) 'selection executed before acceptance'
. ([scriptblock]::Create($expansion.Plan.Script))
Assert ($tmemLiteral -ceq $value) 'literal quoting or native argument forwarding corrupted Unicode/quotes'

foreach ($line in @('tmem save should-not-run -- echo x', 'tmem run state; Write-Output bad',
                    'tmem run state | Write-Output', 'tmem run state > output.txt', 'tmem run state &',
                    '. tmem run state', 'param($x) tmem run state')) {
    Assert ($null -eq (_tmem_expand_line $line)) "unsafe eager expansion: $line"
}

# A group runs in the caller and stops on a native error, preserving its status.
tmem group grouped -- '$tmemBefore = "yes"' ::: '& $env:TMEM_TEST_PYTHON -c "raise SystemExit(7)"' ::: '$tmemAfter = "no"'
. tmem run grouped
$groupSucceeded = $?
$groupCode = $LASTEXITCODE
Assert (!$groupSucceeded -and $groupCode -eq 7) 'group lost native failure status'
Assert ($tmemBefore -eq 'yes') 'group lost caller scope'
Assert (!(Get-Variable tmemAfter -ErrorAction Ignore)) 'group continued after error'

# Explicit recording exercises UTF-8 stdin, metadata, pause and code preservation.
$global:LASTEXITCODE = 7
$global:_TmemPending = [pscustomobject]@{Command='Write-Output recorded';Cwd=(Get-Location).ProviderPath;Started=1;MemoryId=$null}
_tmem_record_pending -Succeeded $false -NativeCode 7
Assert ($LASTEXITCODE -eq 7) 'recording changed LASTEXITCODE'
tmem pause
$global:_TmemPending = [pscustomobject]@{Command='Write-Output private';Cwd=(Get-Location).ProviderPath;Started=1;MemoryId=$null}
_tmem_record_pending -Succeeded $true -NativeCode 0
tmem resume

$failed = $false
try { tmem run state } catch { $failed = $true }
Assert $failed 'non-dot-sourced execution silently used the wrong scope'
function global:_tmem_core { $global:_TmemCoreStatus = 0; 'execute`tinvalid' }
$failed = $false
try { _tmem_resolve @('shell-run','bad') } catch { $failed = $true }
Assert $failed 'malformed protocol accepted'
Write-Output POWERSHELL_OK
