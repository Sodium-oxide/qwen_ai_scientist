param(
    [string]$Image = "qwen-ai-scientist/power-experiment:latest",
    [switch]$RunTds,
    [switch]$RunCosim,
    [switch]$RunTds5s
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker CLI was not found. Install Docker Desktop and restart the terminal."
}

docker image inspect $Image | Out-Null

$argsList = @(
    "-B",
    "demo_module7_power_real_safe.py",
    "--backend",
    "docker",
    "--docker-image",
    $Image
)

if ($RunTds) { $argsList += "--tds" }
if ($RunCosim) { $argsList += "--cosim" }
if ($RunTds5s) { $argsList += "--tds5s" }

python @argsList
