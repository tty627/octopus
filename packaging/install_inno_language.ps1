$ErrorActionPreference = "Stop"

$SourceUrl = "https://raw.githubusercontent.com/jrsoftware/issrc/cfdf48923178df4b4f040e038b423aa555a61ffc/Files/Languages/Unofficial/ChineseSimplified.isl"
$ExpectedSha256 = "7d544b9bb1d142cfa11f2e5d3cc8abe2e55f8e066c5124e3772675aa236e1278"
$LanguageDirectory = "${env:ProgramFiles(x86)}\Inno Setup 6\Languages"
$Destination = Join-Path $LanguageDirectory "ChineseSimplified.isl"

New-Item -ItemType Directory -Force -Path $LanguageDirectory | Out-Null
Invoke-WebRequest -Uri $SourceUrl -OutFile $Destination
$ActualSha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $Destination).Hash.ToLowerInvariant()
if ($ActualSha256 -ne $ExpectedSha256) {
    Remove-Item -LiteralPath $Destination -Force
    throw "Inno Setup Simplified Chinese language hash mismatch"
}
