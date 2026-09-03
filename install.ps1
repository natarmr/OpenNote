# OpenNote Installer - PowerShell (Windows)
# Usage: iwr -useb https://ramratan.in/install.ps1 | iex
# Alternative: curl.exe -fsSL https://ramratan.in/install | bash  (Git Bash)

$ErrorActionPreference = "Stop"

# 1. Resolve Python
$pythonBin = $null
foreach ($cmd in @("python3","python","py")) {
    if (Get-Command $cmd -ErrorAction SilentlyContinue) { $pythonBin = $cmd; break }
}
if (-not $pythonBin) {
    Write-Error "Python 3.10+ is required (python3/python/py not found)"
    exit 1
}

# Verify version >= 3.10
try {
    & $pythonBin -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)"
    if ($LASTEXITCODE -ne 0) { throw "version check failed" }
} catch {
    Write-Error "Python 3.10+ is required (found $(& $pythonBin --version 2>&1))"
    exit 1
}

# 2. Install
Write-Host "Installing OpenNote..."
$installed = $false
try {
    & $pythonBin -m pip install --quiet "opennote" 2>$null
    if ($LASTEXITCODE -eq 0) { $installed = $true; Write-Host "Installed from PyPI." }
} catch {}
if (-not $installed) {
    Write-Host "PyPI not available, installing from GitHub..."
    & $pythonBin -m pip install "opennote @ https://github.com/natarmr/OpenNote/archive/refs/heads/main.tar.gz"
    if ($LASTEXITCODE -ne 0) { Write-Error "pip install failed"; exit 1 }
}

# 3. Verify
try { & $pythonBin -m opennote --help | Out-Null; Write-Host "OpenNote installed successfully! Run: opennote --help (or: $pythonBin -m opennote --help)"; exit 0 } catch {}
if (Get-Command opennote -ErrorAction SilentlyContinue) { Write-Host "OpenNote installed successfully! Run: opennote --help"; exit 0 }
Write-Error "Installation may need PATH adjustment. Try: $pythonBin -m opennote --help"
exit 1
