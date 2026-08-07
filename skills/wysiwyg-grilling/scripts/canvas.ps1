# WYSIWYG Grilling — Windows entrypoint (never .sh — spec §10).
# Usage: .\canvas.ps1 start   (any canvas.py subcommand + args pass through)
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$canvas = Join-Path $here "canvas.py"

function Find-Python {
    # uv is the canonical runner (provisions Python itself; ADR 0001)
    $uv = Get-Command uv -ErrorAction SilentlyContinue
    if ($uv) { return @($uv.Source, "run", "--python", ">=3.9", "python") }
    # python detection immune to the Microsoft Store stub: the stub prints a
    # store message and exits non-zero on `--version`
    foreach ($name in @("python3", "python")) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd) {
            & $cmd.Source --version *> $null
            if ($LASTEXITCODE -eq 0) { return @($cmd.Source) }
        }
    }
    Write-Error ("No usable Python found. Install uv (https://docs.astral.sh/uv/) " +
                 "or Python 3.9+ and re-run.")
}

$py = Find-Python
$exe = $py[0]
$rest = @()
if ($py.Length -gt 1) { $rest = $py[1..($py.Length - 1)] }
& $exe @rest $canvas @args
exit $LASTEXITCODE
