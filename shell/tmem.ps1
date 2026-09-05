#requires -Version 7.3
# tmem PowerShell integration. Dot-source from $PROFILE.
if (Get-Variable _TmemLoaded -Scope Global -ErrorAction Ignore) { return }
$global:_TmemLoaded = $true
$env:TMEM_SHELL = 'powershell'
$env:TMEM_SHELL_INTEGRATION = '1'
$env:TMEM_SESSION_ID = "${env:COMPUTERNAME}:${PID}:$([guid]::NewGuid())"
$global:_TmemPending = $null
$global:_TmemMemoryId = $null
$global:_TmemCoreStatus = 0

function global:_tmem_core {
    param([string[]] $Arguments, [AllowNull()][string] $InputText = $null)
    $core = if ($env:TMEM_CORE) { $env:TMEM_CORE } else { 'tmem-core' }
    $OutputEncoding = [Text.UTF8Encoding]::new($false)
    $previousEncoding = $env:PYTHONUTF8
    $previousCode = $global:LASTEXITCODE
    try {
        $env:PYTHONUTF8 = '1'
        if ($PSBoundParameters.ContainsKey('InputText')) { $InputText | & $core @Arguments }
        else { & $core @Arguments }
        $global:_TmemCoreStatus = $LASTEXITCODE
    } finally {
        $env:PYTHONUTF8 = $previousEncoding
        $global:LASTEXITCODE = $previousCode
    }
}

function global:_tmem_resolve {
    param([string[]] $Arguments)
    $lines = @(_tmem_core -Arguments $Arguments)
    if ($global:_TmemCoreStatus -ne 0) {
        throw "tmem-core exited with status $global:_TmemCoreStatus"
    }
    if ($lines.Count -eq 0) { return $null }
    if ($lines.Count -ne 1) { throw 'tmem: invalid execution response' }
    $fields = $lines[0].Split("`t")
    if ($fields.Count -ne 4 -or $fields[0] -ne 'execute' -or
        !$fields[1] -or !$fields[2] -or $fields[3] -notmatch '^\d*$') {
        throw 'tmem: invalid execution response'
    }
    $utf8 = [Text.UTF8Encoding]::new($false, $true)
    $script = $utf8.GetString([Convert]::FromBase64String($fields[1]))
    $display = $utf8.GetString([Convert]::FromBase64String($fields[2]))
    if (!$script -or !$display -or $script.Contains([char]0)) { throw 'tmem: invalid script' }
    [pscustomobject]@{ Script = $script; Display = $display; MemoryId = $fields[3] }
}

function global:_tmem_prepare {
    # Only invoked after the line editor verifies a standalone tmem command.
    if ($args.Count -eq 0) { return _tmem_resolve -Arguments @('shell-ui') }
    if ($args[0] -eq 'run') {
        if ($args.Count -lt 2) { throw 'Usage: tmem run <memory> [parameter ...]' }
        return _tmem_resolve -Arguments (@('shell-run') + @($args | Select-Object -Skip 1))
    }
    return _tmem_resolve -Arguments (@('shell-run') + $args)
}

function global:tmem {
    # At the interactive prompt Enter replaces tmem with the actual script.
    # In scripts, `. tmem run name` explicitly opts into the caller's scope.
    $tmemArguments = @($args)
    if ($tmemArguments.Count -gt 0) {
        switch ($tmemArguments[0]) {
            'pause' { $env:TMEM_PAUSED = '1'; Write-Host 'tmem recording paused for this shell.'; return }
            'resume' { Remove-Item Env:TMEM_PAUSED -ErrorAction Ignore; Write-Host 'tmem recording resumed for this shell.'; return }
            'status' {
                if ($env:TMEM_PAUSED -eq '1') { Write-Host 'tmem recording is paused for this shell.' }
                else { Write-Host 'tmem recording is active for this shell.' }
                return
            }
            'help' { _tmem_core -Arguments @('--help'); return }
        }
        if ($tmemArguments[0] -in @('search','failed','today','cwd','list','show','edit','rm','remove','save','group','stats','import-history','doctor','init','--help','-h','--version')) {
            _tmem_core -Arguments $tmemArguments
            return
        }
    }
    if ($MyInvocation.InvocationName -ne '.') {
        throw 'Use Enter at the interactive prompt, or dot-source in scripts: . tmem run <memory>'
    }
    $tmemExecution = _tmem_prepare @tmemArguments
    if ($null -eq $tmemExecution) { return }
    Write-Host $tmemExecution.Display
    if ($tmemExecution.MemoryId) {
        _tmem_core -Arguments @('note-run', $tmemExecution.MemoryId) | Out-Null
    }
    . ([scriptblock]::Create($tmemExecution.Script))
}

function global:_tmem_expand_line {
    param([string] $Line)
    $tokens = $null; $parseErrors = $null
    $ast = [Management.Automation.Language.Parser]::ParseInput($Line, [ref]$tokens, [ref]$parseErrors)
    if ($parseErrors.Count -or $ast.BeginBlock -or $ast.ProcessBlock -or $ast.ParamBlock -or
        $ast.EndBlock.Statements.Count -ne 1) { return $null }
    $pipeline = $ast.EndBlock.Statements[0]
    if ($pipeline -isnot [Management.Automation.Language.PipelineAst] -or $pipeline.PipelineElements.Count -ne 1 -or $pipeline.Background) { return $null }
    $command = $pipeline.PipelineElements[0]
    if ($command -isnot [Management.Automation.Language.CommandAst] -or
        $command.GetCommandName() -ne 'tmem' -or $command.Redirections.Count -or
        $command.InvocationOperator -ne [Management.Automation.Language.TokenKind]::Unknown) { return $null }
    if ($command.CommandElements.Count -gt 1) {
        # Management commands execute normally, not while the line is edited.
        $verb = $command.CommandElements[1].Extent.Text.Trim("'", '"')
        if ($verb -in @('pause','resume','status','help','search','failed','today','cwd','list','show','edit','rm','remove','save','group','stats','import-history','doctor','init','--help','-h','--version')) { return $null }
    }
    # ParseInput restricted this to one command (no pipelines/chains/redirection).
    # Only its arguments are evaluated, once, exactly as the user entered them.
    $first = $command.CommandElements[0].Extent
    $resolve = $Line.Substring(0, $first.StartOffset) + '_tmem_prepare' + $Line.Substring($first.EndOffset)
    $plan = & ([scriptblock]::Create($resolve))
    [pscustomobject]@{ Handled = $true; Plan = $plan }
}

function global:_tmem_record_pending {
    param([bool] $Succeeded, [int] $NativeCode)
    $pending = $global:_TmemPending
    $global:_TmemPending = $null
    if ($null -eq $pending) { return }
    try {
        if ($env:TMEM_PAUSED -ne '1') {
            $exitCode = if ($Succeeded) { 0 } elseif ($NativeCode -ne 0) { $NativeCode } else { 1 }
            # Cmdlet/script failures are not the exit code of a stale native command.
            $history = Get-History -Count 1
            if (!$Succeeded -and $history -and $history.ExecutionStatus -eq 'Failed') { $exitCode = 1 }
            _tmem_core -Arguments @('record', '--cwd', $pending.Cwd, '--exit-code', "$exitCode",
                '--started-at-ms', "$($pending.Started)", '--shell', 'powershell',
                '--session', $env:TMEM_SESSION_ID) -InputText $pending.Command | Out-Null
        }
        if ($pending.MemoryId) { _tmem_core -Arguments @('note-run', "$($pending.MemoryId)") | Out-Null }
    } catch { Write-Warning "tmem could not record this command: $_" }
}

# Install only in an interactive host. No execution-policy changes are made.
if ([Environment]::UserInteractive -and ![Console]::IsInputRedirected -and
    !([Environment]::GetCommandLineArgs() -contains '-NonInteractive')) {
    Import-Module PSReadLine -ErrorAction Stop
    $env:TMEM_HISTORY_FILE = (Get-PSReadLineOption).HistorySavePath
    $global:_TmemOriginalReadLine = (Get-Item Function:PSConsoleHostReadLine).ScriptBlock
    $global:_TmemOriginalPrompt = (Get-Item Function:prompt).ScriptBlock
    $global:_TmemOriginalEnter = Get-PSReadLineKeyHandler -Chord Enter
    function global:PSConsoleHostReadLine {
        $line = & $global:_TmemOriginalReadLine
        if (![string]::IsNullOrWhiteSpace($line)) {
            $global:_TmemPending = [pscustomobject]@{
                Command = $line; Cwd = (Get-Location).ProviderPath
                Started = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
                MemoryId = $global:_TmemMemoryId
            }
        }
        $global:_TmemMemoryId = $null
        return $line
    }
    function global:prompt {
        $succeeded = $?; $nativeCode = $global:LASTEXITCODE
        _tmem_record_pending -Succeeded $succeeded -NativeCode $nativeCode
        & $global:_TmemOriginalPrompt
    }
    # Public Get-PSReadLineKeyHandler results do not expose custom delegates.
    # Never silently replace a user's custom Enter binding with AcceptLine.
    $global:_TmemEnterMethod = [Microsoft.PowerShell.PSConsoleReadLine].GetMethod(
        $global:_TmemOriginalEnter.Function, [type[]]@([Nullable[ConsoleKeyInfo]], [object]))
    if ($global:_TmemOriginalEnter.Group -eq 'Custom' -or !$global:_TmemEnterMethod) {
        Write-Warning 'tmem preserved your custom Enter binding. Use . tmem run <memory>, or load tmem after binding Enter to AcceptLine.'
        $env:TMEM_CAPTURE_MODE = 'psreadline-explicit-execution'
        return
    }
    Set-PSReadLineKeyHandler -Chord Enter -BriefDescription TmemAcceptLine -ScriptBlock {
        param($key, $arg)
        $line = ''; $cursor = 0
        [Microsoft.PowerShell.PSConsoleReadLine]::GetBufferState([ref]$line, [ref]$cursor)
        try {
            $result = _tmem_expand_line -Line $line
            if ($null -ne $result) {
                $replacement = if ($result.Plan) { $result.Plan.Script } else { '' }
                $global:_TmemMemoryId = if ($result.Plan) { $result.Plan.MemoryId } else { $null }
                [Microsoft.PowerShell.PSConsoleReadLine]::Replace(0, $line.Length, $replacement)
            }
        } catch {
            Write-Host "`ntmem: $_" -ForegroundColor Red
            [Microsoft.PowerShell.PSConsoleReadLine]::InvokePrompt()
            return
        }
        [void] $global:_TmemEnterMethod.Invoke($null, @($key, $arg))
    }
    $env:TMEM_CAPTURE_MODE = 'psreadline'
}
