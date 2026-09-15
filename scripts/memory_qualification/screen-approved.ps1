param(
    [Parameter(Mandatory=$true)][string]$Python,
    [Parameter(Mandatory=$true)][string]$Ollama,
    [Parameter(Mandatory=$true)][string]$RunId
)
# Explicit development comparison only. Each process exits/unloads before the
# next candidate starts. This script is never called by Novi's application.
$ErrorActionPreference = 'Stop'
if ($RunId -notmatch '^[a-zA-Z0-9-]+$') { throw 'RunId must be an alphanumeric label' }
$manifest = Get-Content scripts/memory_qualification/artifacts.json -Raw | ConvertFrom-Json
$candidates = @(
    @{file='LFM2.5-350M-Q4_K_M.gguf'; alias='novi-qual-lfm350'},
    @{file='granite-4.0-350m-Q4_K_M.gguf'; alias='novi-qual-granite350'},
    @{file='LFM2.5-1.2B-Instruct-Q4_K_M.gguf'; alias='novi-qual-lfm1200'},
    @{file='Qwen_Qwen3.5-0.8B-Q4_K_M.gguf'; alias='novi-qual-qwen800'}
)
foreach ($candidate in $candidates) {
    if (Test-Path model_cache/qualification/STOP) { throw 'Operator stop requested' }
    $item = $manifest.files | Where-Object file -eq $candidate.file
    if (!$item) { throw 'Candidate is not in approved manifest' }
    $artifact = Join-Path $manifest.storage $item.file
    $resultPath = "model_cache/qualification/$($candidate.alias)-$RunId.jsonl"
    if (Test-Path -LiteralPath $resultPath) { throw "Preserve existing run: $resultPath" }
    Write-Output "Importing $($candidate.alias)"
    & $Python -m scripts.memory_qualification.prepare --file $item.file --alias $candidate.alias --ollama-exe $Ollama
    if ($LASTEXITCODE -ne 0) { throw "Import failed: $($candidate.alias)" }
    Write-Output "Screening $($candidate.alias)"
    & $Python -m scripts.memory_qualification.run --model "$($candidate.alias):latest" --artifact $artifact --sha256 $item.sha256 --model-store model_cache/qualification/ollama-store --ollama-exe $Ollama --supplied-spans --output $resultPath
    if ($LASTEXITCODE -ne 0) { throw "Runner failed: $($candidate.alias)" }
    $lastCase = Get-Content -LiteralPath $resultPath -Tail 1 | ConvertFrom-Json
    if ($lastCase.kind -eq 'case' -and !$lastCase.telemetry.failure -and $lastCase.unloaded -and $lastCase.result.status -ne 'runtime_failure') {
        Write-Output "Verifier challenges: $($candidate.alias)"
        & $Python -m scripts.memory_qualification.run --model "$($candidate.alias):latest" --artifact $artifact --sha256 $item.sha256 --model-store model_cache/qualification/ollama-store --ollama-exe $Ollama --supplied-spans --verification-only --dataset tests/fixtures/memory_curation/verifier_challenges.json --limit 4 --output "model_cache/qualification/$($candidate.alias)-$RunId-verifier.jsonl"
        if ($LASTEXITCODE -ne 0) { throw "Verifier runner failed: $($candidate.alias)" }
    }
}
