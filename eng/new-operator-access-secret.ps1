[CmdletBinding()]
param(
  [Parameter(Mandatory)]
  [SecureString]$Passphrase,
  [ValidateRange(100000, 2000000)]
  [int]$Iterations = 210000
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Passphrase)
$plainText = $null
$rng = $null
$derive = $null
try {
  $plainText = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
  if ([string]::IsNullOrWhiteSpace($plainText) -or $plainText.Length -lt 12) {
    throw 'The operator passphrase must contain at least 12 characters.'
  }
  $salt = New-Object byte[] 16
  $rng = New-Object Security.Cryptography.RNGCryptoServiceProvider
  $rng.GetBytes($salt)
  $derive = [Security.Cryptography.Rfc2898DeriveBytes]::new(
    [Text.Encoding]::UTF8.GetBytes($plainText),
    $salt,
    $Iterations,
    [Security.Cryptography.HashAlgorithmName]::SHA256)
  $derived = $derive.GetBytes(32)
  "pbkdf2-sha256`$$Iterations`$$([Convert]::ToBase64String($salt))`$$([Convert]::ToBase64String($derived))"
}
finally {
  if ($null -ne $derive) { $derive.Dispose() }
  if ($null -ne $rng) { $rng.Dispose() }
  if ($null -ne $plainText) { $plainText = $null }
  [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
}
