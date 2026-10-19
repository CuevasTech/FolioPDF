# Empaqueta el motor Python como ejecutable independiente (dist-engine\folio-engine).
# Uso: powershell -File scripts\build-engine.ps1
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root 'engine\.venv\Scripts\python.exe'
if (-not (Test-Path $python)) { throw 'No existe el entorno virtual del motor (engine\.venv).' }

Push-Location (Join-Path $root 'engine')
try {
    # --console: el motor habla con la aplicacion por su entrada/salida estandar.
    # La aplicacion lo lanza con la ventana oculta, asi que no se ve ninguna consola.
    # IA local: las bibliotecas de llama.cpp y ONNX Runtime van con el motor (la
    # Store no permite descargar codigo despues); los modelos se descargan aparte.
    # Fuera el servidor HTTP de llama-cpp-python y el cliente de descargas de
    # huggingface_hub: el motor no tiene red y no los usa.
    & $python -m PyInstaller --noconfirm --clean `
        --name folio-engine `
        --console `
        --distpath (Join-Path $root 'dist-engine') `
        --workpath (Join-Path $root 'build-engine\work') `
        --specpath (Join-Path $root 'build-engine') `
        --collect-submodules folio_engine `
        --collect-submodules pyhanko `
        --collect-submodules pyhanko_certvalidator `
        --collect-data pyhanko `
        --collect-data pyhanko_certvalidator `
        --collect-data tzdata `
        --collect-data certifi `
        --collect-submodules pdf2docx `
        --collect-data docx `
        --collect-binaries llama_cpp `
        --collect-binaries onnxruntime `
        --exclude-module llama_cpp.server `
        --exclude-module huggingface_hub `
        --exclude-module tkinter `
        --exclude-module pytest `
        run_engine.py
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller termino con codigo $LASTEXITCODE" }
}
finally {
    Pop-Location
}
# OpenCV (lo usa la conversion a Word) trae un decodificador de video que aqui
# no hace falta: menos tamano y menos codigo de terceros dentro de la aplicacion.
Get-ChildItem (Join-Path $root 'dist-engine\folio-engine\_internal\cv2') -Filter 'opencv_videoio_ffmpeg*.dll' -ErrorAction SilentlyContinue |
    Remove-Item -Force
Write-Host "Motor empaquetado en dist-engine\folio-engine"
