param(
    [string]$SourceDirectory = (Join-Path ${env:ProgramFiles(x86)} 'Line6\HX Edit')
)

$ErrorActionPreference = 'Stop'

$source = Get-Item -LiteralPath $SourceDirectory
$exeName = 'HX Edit.exe'
$sourceExe = Join-Path $source.FullName $exeName
$expectedHash = 'F7FAAC4996164F9039C9AE3CA8D85CD135C5BCEAE8A511C8921A1244046CAB66'
if ((Get-FileHash -LiteralPath $sourceExe).Hash -ne $expectedHash) {
    throw 'Expected the HX Edit 3.82 Windows executable used as the baseline for this project.'
}

$build = Join-Path $PSScriptRoot 'build'
$destination = Join-Path $build 'HX Edit'
if (Test-Path -LiteralPath $destination) {
    throw "The local copy already exists at '$destination'. Keep or move it before preparing a new one."
}

$buildDirectory = New-Item -ItemType Directory -Path $build -Force
if ($buildDirectory.Attributes -band [IO.FileAttributes]::ReparsePoint) {
    throw "The build directory must be a local directory: '$build'."
}

$staging = New-Item -ItemType Directory -Path (Join-Path $build ([guid]::NewGuid().ToString()))
try {
    # The installed uninstaller can act on the system installation even from another directory.
    Get-ChildItem -LiteralPath $source.FullName -Force |
        Where-Object Name -ne 'HX Edit Uninstaller.exe' |
        Copy-Item -Destination $staging.FullName -Recurse -Force

    $originalFiles = @(Get-ChildItem -LiteralPath $source.FullName -Recurse -File -Force |
        Where-Object FullName -ne (Join-Path $source.FullName 'HX Edit Uninstaller.exe'))
    $copiedFiles = @(Get-ChildItem -LiteralPath $staging.FullName -Recurse -File -Force)
    if ($originalFiles.Count -ne $copiedFiles.Count) {
        throw 'The file count of the local copy differs from the installation.'
    }
    foreach ($file in $originalFiles) {
        $relativePath = $file.FullName.Substring($source.FullName.Length + 1)
        $copiedFile = Join-Path $staging.FullName $relativePath
        if ((Get-FileHash -LiteralPath $file.FullName).Hash -ne (Get-FileHash -LiteralPath $copiedFile).Hash) {
            throw "File verification failed: '$relativePath'."
        }
    }

    [IO.Directory]::Move($staging.FullName, $destination)
    Write-Output "Verified $($originalFiles.Count) files. Launch '$destination\$exeName'."
} finally {
    if ((Split-Path -Parent $staging.FullName) -eq $buildDirectory.FullName -and
        (Test-Path -LiteralPath $staging.FullName)) {
        Remove-Item -LiteralPath $staging.FullName -Recurse -Force
    }
}
