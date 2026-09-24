# OpenNote Installer - PowerShell (Windows)
# Fetches OpenNote from GitHub (no PyPI, no extra hosting).
# Usage: iwr -useb https://raw.githubusercontent.com/natarmr/OpenNote/main/install.ps1 | iex
# Alternative: curl.exe -fsSL https://raw.githubusercontent.com/natarmr/OpenNote/main/install | bash  (Git Bash)
#
# NOTE: never `pip install opennote` — that PyPI name belongs to an unrelated
# video-API SDK. OpenNote installs only from the GitHub tarball below.

$ErrorActionPreference = "Stop"

$TarballUrl = "https://github.com/natarmr/OpenNote/archive/refs/heads/main.tar.gz"

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

# 2. Install from the GitHub tarball (PEP 508 direct reference).
Write-Host "Installing OpenNote..."
& $pythonBin -m pip install "opennote @ $TarballUrl"
if ($LASTEXITCODE -ne 0) { Write-Error "pip install failed"; exit 1 }

# 3. Verify (bare console script first, python -m fallback)
if (Get-Command opennote -ErrorAction SilentlyContinue) { try { opennote --help | Out-Null; Write-Host "OpenNote installed successfully! Run: opennote --help"; exit 0 } catch {} }
try { & $pythonBin -m opennote.cli --help | Out-Null; Write-Host "OpenNote installed successfully! (console script not on PATH yet) Run: $pythonBin -m opennote.cli --help"; exit 0 } catch {}
Write-Error "Installation may need PATH adjustment. Try: $pythonBin -m opennote.cli --help"
exit 1
