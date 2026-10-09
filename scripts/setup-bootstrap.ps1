# Only this process uses ExecutionPolicy Bypass; no system setting is changed.
$ErrorActionPreference = 'Stop'
$env:PYTHON_MANAGER_AUTOMATIC_INSTALL = 'false'
Remove-Item Env:PYLAUNCHER_ALLOW_INSTALL -ErrorAction SilentlyContinue

function Test-SetupPython {
    param([string]$Executable, [string]$Prefix = '')
    $probe = New-Object System.Diagnostics.Process
    $probe.StartInfo.FileName = $Executable
    $probe.StartInfo.Arguments = $Prefix + ' -I -c "import struct, sys; sys.exit(0 if sys.version_info >= (3, 10) and struct.calcsize(''P'') == 8 else 1)"'
    $probe.StartInfo.UseShellExecute = $false
    $probe.StartInfo.CreateNoWindow = $true
    $probe.StartInfo.RedirectStandardOutput = $true
    $probe.StartInfo.RedirectStandardError = $true
    try {
        [void]$probe.Start()
        $stdout = $probe.StandardOutput.ReadToEndAsync()
        $stderr = $probe.StandardError.ReadToEndAsync()
        if (-not $probe.WaitForExit(10000)) {
            $probe.Kill()
            return $false
        }
        return $probe.ExitCode -eq 0
    } catch {
        return $false
    } finally {
        $probe.Dispose()
    }
}

try {
    $project = Split-Path -Parent $PSScriptRoot
    $helper = Join-Path $project 'setup_comfyshelf.py'
    if (-not (Test-Path -LiteralPath $helper -PathType Leaf)) {
        throw 'Setup files are missing. Extract the entire ComfyShelf download first.'
    }
    $candidates = @()
    if ($env:COMFYSHELF_SETUP_PYTHON) {
        # Explicit diagnostic override: never silently fall back to another runtime.
        $candidates += @{ Exe = $env:COMFYSHELF_SETUP_PYTHON; Prefix = '' }
    } else {
        $localPython = Join-Path $project '.venv\Scripts\python.exe'
        if (Test-Path -LiteralPath $localPython -PathType Leaf) {
            $candidates += @{ Exe = $localPython; Prefix = '' }
        }
        $launcher = Get-Command py.exe -CommandType Application -ErrorAction SilentlyContinue
        if ($launcher) { $candidates += @{ Exe = $launcher.Source; Prefix = '-3' } }
        foreach ($command in @(Get-Command python.exe -All -CommandType Application -ErrorAction SilentlyContinue)) {
            # Skip Store aliases, which may open a store instead of running Python.
            if ($command -and $command.Source -notmatch '\\WindowsApps\\') {
                $candidates += @{ Exe = $command.Source; Prefix = '' }
            }
        }
    }
    foreach ($candidate in $candidates) {
        if (Test-SetupPython -Executable $candidate.Exe -Prefix $candidate.Prefix) {
            $arguments = @('-I', $helper)
            if ($candidate.Prefix) { $arguments = @($candidate.Prefix) + $arguments }
            & $candidate.Exe @arguments
            $exitCode = $LASTEXITCODE
            if ($exitCode -eq 0) {
                try {
                    $wsh = New-Object -ComObject WScript.Shell
                    $shortcutPath = Join-Path $project "ComfyShelf.lnk"
                    $shortcut = $wsh.CreateShortcut($shortcutPath)
                    $shortcut.TargetPath = Join-Path $project "Start_ComfyShelf.cmd"
                    $shortcut.WorkingDirectory = $project
                    $icon = Join-Path $project "assets\comfyshelf.ico"
                    if (Test-Path $icon) { $shortcut.IconLocation = "$icon,0" }
                    $shortcut.WindowStyle = 7
                    $shortcut.Description = "ComfyShelf - Workflow Switcher"
                    $shortcut.Save()
                } catch {}
            }
            exit $exitCode
        }
    }
    Write-Host 'ComfyShelf needs an installed 64-bit Python 3.10 or newer.'
    Write-Host 'Install Python from https://www.python.org/downloads/windows/, then run Setup_ComfyShelf.cmd again.'
    Write-Host 'If you installed only the Python Install Manager, open Python once to finish installing its runtime.'
    Write-Host 'Help: docs\guide.en.md. Nothing has been installed by this setup.'
    Write-Host ''
    Write-Host 'ComfyShelf braucht ein installiertes 64-Bit-Python ab Version 3.10.'
    Write-Host 'Python von https://www.python.org/downloads/windows/ installieren, danach Setup_ComfyShelf.cmd erneut starten.'
    Write-Host 'Nur den Python Install Manager installiert? Python einmal oeffnen, um dessen Einrichtung abzuschliessen.'
    Write-Host 'Hilfe: docs\guide.de.md. Diese Einrichtung hat nichts installiert.'
    exit 2
} catch {
    Write-Host ('Setup could not continue / Einrichtung nicht moeglich: ' + $_.Exception.Message)
    Write-Host 'Help / Hilfe: docs\guide.en.md / docs\guide.de.md'
    exit 2
}
