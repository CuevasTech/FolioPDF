import { spawn } from 'node:child_process'
import { copyFileSync, existsSync, readdirSync } from 'node:fs'
import { extname, join, resolve, sep } from 'node:path'
import { app } from 'electron'
import { EngineError } from './engine'
import { L } from './i18n'
import { log } from './log'

/**
 * Escáneres (USB, de red y los de las impresoras multifunción) a través de WIA,
 * la interfaz de Windows para escáneres: sirve con los controladores que ya
 * instala Windows o el fabricante, sin instalar nada más.
 *
 * WIA se usa desde PowerShell de Windows (viene en todos los Windows 10 y 11)
 * con guiones fijos que van dentro de Folio. Nada se interpola en ellos: el
 * escáner, el origen del papel, el color, la resolución y la carpeta les llegan
 * por variables de entorno ya validadas (A05:2025, inyección de órdenes). Se
 * lanza por su ruta completa, nunca por la que diga el PATH (A08:2025).
 */

export interface ScannerInfo {
  id: string
  name: string
}
export type ScanSource = 'flatbed' | 'feeder' | 'duplex'
export type ScanColor = 'color' | 'gray' | 'bw'
export interface ScanRequest {
  device: string
  source: ScanSource
  color: ScanColor
  dpi: 150 | 200 | 300
}

const POWERSHELL = join(process.env.SystemRoot ?? 'C:\\Windows', 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe')
const LIST_TIMEOUT_MS = 30_000
const SCAN_TIMEOUT_MS = 15 * 60_000
const MAX_PAGES = 300
const MAX_OUTPUT = 256 * 1024
/** Identificador de WIA: texto imprimible y sin tabuladores (es lo que separa los campos). */
const DEVICE_ID = /^[\x20-\x7e]{1,300}$/

// Solo comillas simples en los guiones: así no hay nada que escapar al pasarlos a PowerShell.
const PRELUDE = `
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
function Say($kind, $text) { [Console]::Out.WriteLine($kind + [char]9 + $text) }
function Code($err) { $e = $err.Exception; while ($e.InnerException) { $e = $e.InnerException }; return ('{0:X8}' -f $e.HResult) }
`

const LIST_SCRIPT = `${PRELUDE}
try {
  $manager = New-Object -ComObject WIA.DeviceManager
  foreach ($info in $manager.DeviceInfos) {
    if ($info.Type -ne 1) { continue }
    $name = ''
    foreach ($p in $info.Properties) { if ($p.Name -eq 'Name') { $name = [string]$p.Value } }
    Say 'DEV' ($info.DeviceID + [char]9 + $name)
  }
} catch { Say 'ERR' (Code $_); exit 4 }
`

const SCAN_SCRIPT = `${PRELUDE}
function Put($props, $id, $value) {
  foreach ($p in $props) { if ($p.PropertyID -eq $id) { try { $p.Value = $value; return $true } catch { return $false } } }
  return $false
}
function PropValue($props, $id) { foreach ($p in $props) { if ($p.PropertyID -eq $id) { return $p.Value } }; return $null }
$png = '{B96B3CAF-0728-11D3-9D7B-0000F81EF32E}'
$source = $env:FOLIO_SCAN_SOURCE
$dpi = [int]$env:FOLIO_SCAN_DPI
try {
  $manager = New-Object -ComObject WIA.DeviceManager
  $info = $null
  foreach ($d in $manager.DeviceInfos) { if ($d.DeviceID -eq $env:FOLIO_SCAN_DEVICE) { $info = $d } }
  if ($null -eq $info) { Say 'ERR' 'notfound'; exit 3 }
  $device = $info.Connect()
  # Origen del papel (3088): 1 alimentador, 2 cristal, 5 alimentador a doble cara. Una pagina por vez (3096).
  [void](Put $device.Properties 3088 (@{ flatbed = 2; feeder = 1; duplex = 5 }[$source]))
  [void](Put $device.Properties 3096 1)
  $item = $device.Items.Item(1)
  # Intencion (6146): 1 color, 2 gris, 4 texto. Antes que la resolucion, porque la cambia.
  [void](Put $item.Properties 6146 (@{ color = 1; gray = 2; bw = 4 }[$env:FOLIO_SCAN_COLOR]))
  [void](Put $item.Properties 6147 $dpi)
  [void](Put $item.Properties 6148 $dpi)
  # Toda la superficie: el cristal o el alimentador entero (milesimas de pulgada).
  $wide = if ($source -eq 'flatbed') { PropValue $device.Properties 3074 } else { PropValue $device.Properties 3076 }
  $tall = if ($source -eq 'flatbed') { PropValue $device.Properties 3075 } else { PropValue $device.Properties 3077 }
  if ($wide -and $tall) {
    [void](Put $item.Properties 6149 0)
    [void](Put $item.Properties 6150 0)
    [void](Put $item.Properties 6151 ([int]([double]$wide * $dpi / 1000)))
    [void](Put $item.Properties 6152 ([int]([double]$tall * $dpi / 1000)))
  }
  $n = 0
  while ($true) {
    try { $image = $item.Transfer() } catch {
      $code = Code $_
      if ($n -gt 0 -and $code -eq '80210003') { break }
      Say 'ERR' $code; exit 4
    }
    if ($image.FormatID -ne $png) {
      try {
        $process = New-Object -ComObject WIA.ImageProcess
        $process.Filters.Add($process.FilterInfos.Item('Convert').FilterID)
        $process.Filters.Item(1).Properties.Item('FormatID').Value = $png
        $image = $process.Apply($image)
      } catch { }
    }
    $n++
    $path = Join-Path $env:FOLIO_SCAN_OUT ('pagina-{0:D3}.{1}' -f $n, $image.FileExtension)
    $image.SaveFile($path)
    Say 'PAGE' $path
    if ($source -eq 'flatbed' -or $n -ge ${MAX_PAGES}) { break }
  }
} catch { Say 'ERR' (Code $_); exit 4 }
`

type ScannerText = keyof typeof L.main.scanner

/** Lo que le pasa al escáner (código de WIA → texto para el usuario, en L.main.scanner). */
const PROBLEMS: Record<string, ScannerText> = {
  notfound: 'notFound',
  '80210002': 'paperJam',
  '80210003': 'noPaper',
  '80210004': 'paperProblem',
  '80210005': 'notResponding',
  '80210006': 'busy',
  '80210007': 'warmingUp',
  '80210008': 'userAction',
  '8021000A': 'noConnection',
  '8021000D': 'locked',
  '80210015': 'notResponding',
  '80210016': 'coverOpen',
  '80210017': 'lampOff'
}

const failure = (code: string): EngineError => {
  const text: ScannerText = Object.prototype.hasOwnProperty.call(PROBLEMS, code) ? PROBLEMS[code] : 'failed'
  return new EngineError({ code: 'scan_failed', message: L.main.scanner[text] })
}

/** Solo pruebas, sin empaquetar: una carpeta de imágenes hace de escáner. */
function testScanner(): string | null {
  return !app.isPackaged && process.env.FOLIO_TEST_SCANNER ? process.env.FOLIO_TEST_SCANNER : null
}

interface Run {
  lines: string[]
  code: number | null
}

function runScript(script: string, env: Record<string, string>, timeoutMs: number, signal?: AbortSignal): Promise<Run> {
  return new Promise((done, fail) => {
    const child = spawn(POWERSHELL, ['-NoLogo', '-NoProfile', '-NonInteractive', '-Command', script], {
      env: { ...process.env, ...env },
      windowsHide: true,
      stdio: ['ignore', 'pipe', 'pipe']
    })
    let output = ''
    let finished = false
    const stop = (): void => {
      if (!child.killed) child.kill()
    }
    const timer = setTimeout(stop, timeoutMs)
    const onAbort = (): void => stop()
    signal?.addEventListener('abort', onAbort, { once: true })
    child.stdout.setEncoding('utf8')
    child.stdout.on('data', (chunk: string) => {
      // Tope de lo que se guarda: nada que diga el proceso puede llenar la memoria.
      if (output.length < MAX_OUTPUT) output += chunk
    })
    // Los mensajes de error de PowerShell pueden citar rutas: no se registran (A09:2025).
    child.stderr.resume()
    child.on('error', (error) => {
      if (finished) return
      finished = true
      clearTimeout(timer)
      signal?.removeEventListener('abort', onAbort)
      fail(error)
    })
    child.on('close', (code) => {
      if (finished) return
      finished = true
      clearTimeout(timer)
      signal?.removeEventListener('abort', onAbort)
      done({ lines: output.split(/\r?\n/).filter(Boolean), code })
    })
  })
}

const cancelled = (): EngineError => new EngineError({ code: 'cancelled', message: L.main.scanner.cancelled })

/** Los escáneres que Windows tiene instalados. */
export async function listScanners(): Promise<ScannerInfo[]> {
  if (testScanner()) return [{ id: 'prueba', name: L.main.scanner.testDevice }]
  let run: Run
  try {
    run = await runScript(LIST_SCRIPT, {}, LIST_TIMEOUT_MS)
  } catch {
    log.warn('escáneres: no se pudo preguntar a Windows')
    return []
  }
  const found: ScannerInfo[] = []
  for (const line of run.lines) {
    const [kind, id, ...rest] = line.split('\t')
    const name = rest.join(' ').trim().slice(0, 120)
    if (kind === 'DEV' && DEVICE_ID.test(id ?? '')) found.push({ id, name: name || L.main.scanner.unnamed })
  }
  if (run.code !== 0) log.warn(`escáneres: WIA respondió con el código ${run.code}`)
  return found.slice(0, 50)
}

/**
 * Escanea en ``folder`` (carpeta propia, vacía) y devuelve las imágenes, en
 * orden: una del cristal o todas las del alimentador.
 */
export async function scanPages(request: ScanRequest, folder: string, signal?: AbortSignal): Promise<string[]> {
  if (!DEVICE_ID.test(request.device)) throw failure('notfound')
  const own = resolve(folder) + sep
  const test = testScanner()
  if (test) {
    if (signal?.aborted) throw cancelled()
    const images = readdirSync(test).filter((name) => /\.(png|jpe?g)$/i.test(name)).sort()
    const chosen = request.source === 'flatbed' ? images.slice(0, 1) : images
    return chosen.map((name, i) => {
      const target = join(own, `pagina-${String(i + 1).padStart(3, '0')}${extname(name).toLowerCase()}`)
      copyFileSync(join(test, name), target)
      return target
    })
  }
  const run = await runScript(
    SCAN_SCRIPT,
    {
      FOLIO_SCAN_DEVICE: request.device,
      FOLIO_SCAN_SOURCE: request.source,
      FOLIO_SCAN_COLOR: request.color,
      FOLIO_SCAN_DPI: String(request.dpi),
      FOLIO_SCAN_OUT: folder
    },
    SCAN_TIMEOUT_MS,
    signal
  )
  if (signal?.aborted) throw cancelled()
  const pages: string[] = []
  let problem: string | null = null
  for (const line of run.lines) {
    const [kind, value = ''] = line.split('\t')
    // Solo imágenes dentro de la carpeta de este escaneo.
    if (kind === 'PAGE' && resolve(value).startsWith(own) && existsSync(value)) pages.push(resolve(value))
    else if (kind === 'ERR') problem = value.trim()
  }
  if (problem !== null && pages.length === 0) {
    log.warn(`escáner: no se pudo escanear (${/^[0-9A-F]{8}$|^notfound$/.test(problem) ? problem : 'otro'})`)
    throw failure(problem)
  }
  if (pages.length === 0) throw failure('')
  return pages.slice(0, MAX_PAGES)
}
