param(
    [string]$Image = "qwen-ai-scientist/power-experiment:latest"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker CLI was not found. Install Docker Desktop and restart the terminal."
}

docker build `
    --file docker/module7-power.Dockerfile `
    --tag $Image `
    .

docker image inspect $Image | Out-Null
Write-Host "Built Docker image: $Image"
