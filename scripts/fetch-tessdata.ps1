<#
.SYNOPSIS
    Descarga los datos de idioma del OCR (Tesseract) a resources/tessdata.

.DESCRIPTION
    PyMuPDF trae Tesseract integrado; solo faltan los datos de idioma
    (*.traineddata). Este script los baja del repositorio oficial
    tesseract-ocr/tessdata_fast y comprueba su integridad.

    Es idempotente: un archivo que ya existe y cuyo hash es correcto no se
    vuelve a descargar. Compatible con Windows PowerShell 5.1 y PowerShell 7.

    Uso:  powershell -NoProfile -ExecutionPolicy Bypass -File scripts/fetch-tessdata.ps1
#>
[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# --- Cadena de suministro (OWASP A03:2025, fallos en la cadena de suministro) ---
# La URL se fija a un commit concreto y no a una rama: el contenido de "main"
# puede cambiar (o ser sustituido) sin que nadie lo revise. Este commit es el
# que senala la etiqueta de version 4.1.0 del repositorio oficial.
$Commit = '65727574dfcd264acbb0c3e07860e4e9e9b22185'
# Solo HTTPS y con el servidor escrito aqui: nada de esto llega por parametros,
# asi que no hay forma de redirigir la descarga desde fuera del script.
$BaseUrl = "https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/$Commit"

# SHA-256 esperado de cada archivo (OWASP A08:2025, integridad de datos). El
# commit fijado dice DE DONDE se baja; el hash garantiza QUE es lo que se baja,
# aunque el servidor o la conexion estuvieran comprometidos. El tamano exacto
# sirve ademas de tope para cortar una descarga que no sea la esperada.
# Los hashes se calcularon en la primera descarga desde ese commit (confianza
# en la primera descarga) y coinciden con los blobs del arbol git del commit.
$Files = @(
    @{ Name = 'spa.traineddata'; Size = 2294433;  Sha256 = '6f2e04d02774a18f01bed44b1111f2cd7f3ba7ac9dc4373cd3f898a40ea6b464' },
    @{ Name = 'eng.traineddata'; Size = 4113088;  Sha256 = '7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2' },
    @{ Name = 'osd.traineddata'; Size = 10562727; Sha256 = '9cf5d576fcc47564f11265841e5ca839001e7e6f38ff7f7aacf46d15a96b00ff' }
)

# El destino se calcula a partir de la ubicacion del script, nunca del
# directorio actual ni de un parametro: no se puede escribir en otra carpeta.
$Root = Split-Path -Parent $PSScriptRoot
$Dest = Join-Path $Root 'resources\tessdata'

function Get-Sha256([string]$Path) {
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Receive-File([System.Net.Http.HttpClient]$Client, [string]$Url, [string]$Path, [long]$MaxBytes) {
    $response = $Client.GetAsync($Url, [System.Net.Http.HttpCompletionOption]::ResponseHeadersRead).GetAwaiter().GetResult()
    try {
        # Las redirecciones estan desactivadas, asi que cualquier respuesta
        # distinta de 200 (incluido un 3xx hacia otro servidor) es un fallo.
        if ([int]$response.StatusCode -ne 200) {
            throw "respuesta HTTP $([int]$response.StatusCode) al descargar $Url"
        }
        $stream = $response.Content.ReadAsStreamAsync().GetAwaiter().GetResult()
        try {
            $file = [System.IO.File]::Open($Path, [System.IO.FileMode]::Create, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
            try {
                $buffer = New-Object byte[] 81920
                [long]$total = 0
                while ($true) {
                    $read = $stream.Read($buffer, 0, $buffer.Length)
                    if ($read -le 0) { break }
                    $total += $read
                    # Tope de tamano: una respuesta mayor de lo esperado se corta
                    # en vez de llenar el disco (consumo de recursos sin limite).
                    if ($total -gt $MaxBytes) {
                        throw "la descarga supera el tamano esperado ($MaxBytes bytes)"
                    }
                    $file.Write($buffer, 0, $read)
                }
            }
            finally { $file.Dispose() }
        }
        finally { $stream.Dispose() }
    }
    finally { $response.Dispose() }
}

function Save-Verified([System.Net.Http.HttpClient]$Client, [hashtable]$Item) {
    $name = $Item.Name
    $target = Join-Path $Dest $name
    if (Test-Path -LiteralPath $target -PathType Leaf) {
        if ((Get-Sha256 $target) -eq $Item.Sha256) {
            Write-Host "ok          $name (ya estaba, hash correcto)"
            return
        }
        # Un archivo que no coincide con su hash no se conserva: podria estar
        # danado o manipulado, y el motor lo cargaria tal cual.
        Remove-Item -LiteralPath $target -Force
        Write-Host "descartado  $name (hash incorrecto); se descarga de nuevo"
    }

    # Se descarga con otro nombre y solo se le da el definitivo despues de
    # comprobar el hash: bajo el nombre *.traineddata nunca hay datos sin validar.
    $partial = "$target.part"
    try {
        Receive-File $Client "$BaseUrl/$name" $partial ([long]$Item.Size)
        $actual = Get-Sha256 $partial
        if ($actual -ne $Item.Sha256) {
            throw "el SHA-256 de $name no coincide (esperado $($Item.Sha256), obtenido $actual)"
        }
        Move-Item -LiteralPath $partial -Destination $target -Force
        Write-Host "descargado  $name ($($Item.Size) bytes, hash correcto)"
    }
    finally {
        # Pase lo que pase no queda un archivo a medias (ni uno con hash malo).
        if (Test-Path -LiteralPath $partial) { Remove-Item -LiteralPath $partial -Force }
    }
}

try {
    # TLS 1.2 como minimo (OWASP A04:2025, fallos criptograficos). Windows
    # PowerShell 5.1 puede negociar por defecto versiones antiguas; en
    # PowerShell 7 este ajuste no tiene efecto y rige el del sistema (1.2 o 1.3).
    [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.SecurityProtocolType]::Tls12
    Add-Type -AssemblyName System.Net.Http

    if (-not (Test-Path -LiteralPath $Dest -PathType Container)) {
        New-Item -ItemType Directory -Path $Dest -Force | Out-Null
    }

    $handler = New-Object System.Net.Http.HttpClientHandler
    # Sin redirecciones automaticas: la descarga solo puede venir del servidor
    # fijado arriba, nunca de otro al que este la desvie.
    $handler.AllowAutoRedirect = $false
    $client = New-Object System.Net.Http.HttpClient($handler)
    $client.Timeout = [TimeSpan]::FromMinutes(5)
    try {
        foreach ($item in $Files) { Save-Verified $client $item }
    }
    finally {
        $client.Dispose()
        $handler.Dispose()
    }
    Write-Host "Datos de idioma listos en $Dest"
    exit 0
}
catch {
    Write-Host "ERROR: $($_.Exception.Message)"
    exit 1
}
