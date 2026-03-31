# FlowDesk launcher: activates .venv if present, then runs main.py
Set-Location $PSScriptRoot
if (Test-Path ".venv\Scripts\Activate.ps1") {
    & ".venv\Scripts\Activate.ps1"
}
python main.py @args
