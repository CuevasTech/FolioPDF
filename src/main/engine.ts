import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process'
import { EventEmitter } from 'node:events'
import type { EngineErrorInfo } from '@shared/types'
import { L, lang } from './i18n'
import { log } from './log'

/**
 * Cliente del motor de documentos (proceso Python aparte).
 *
 * La comunicación es exclusivamente por la entrada/salida estándar del proceso
 * hijo: no hay puertos de red, así que ningún otro programa del equipo ni
 * ninguna página web pueden hablar con el motor (OWASP A01:2025).
 */

// Mismos límites que aplica el motor: una longitud declarada disparatada no
// puede hacer que este proceso reserve memoria sin control.
const MAX_JSON_BYTES = 16 * 1024 * 1024
const MAX_BINARY_BYTES = 768 * 1024 * 1024
/** Texto parcial de la IA que viaja con cada aviso de progreso (como mucho). */
const MAX_PROGRESS_TEXT = 32_000

/** Tiempo máximo para operaciones que deberían ser inmediatas (dibujar, leer texto…). */
const WATCHDOG_MS = 60_000
const WATCHED = /^(page\.|doc\.(describe|outline|undo|redo)|obj\.|annot\.|form\.(?!autoDetect)|fonts\.)/

export class EngineError extends Error {
  readonly code: string
  readonly detail?: Record<string, unknown>

  constructor(info: EngineErrorInfo) {
    super(info.message)
    this.name = 'EngineError'
    this.code = info.code
    this.detail = info.detail
  }

  toInfo(): EngineErrorInfo {
    return { code: this.code, message: this.message, detail: this.detail }
  }
}

export interface EngineLaunch {
  command: string
  args: string[]
  cwd: string
}

export interface EngineResponse<T = unknown> {
  result: T
  binary?: Buffer
}

export interface RequestOptions {
  binary?: Uint8Array
  priority?: number
  onProgress?: (data: { done: number; total: number; message: string; text?: string }) => void
  /** Se llama con el identificador interno en cuanto la petición queda registrada. */
  onId?: (id: number) => void
}

interface Job {
  id: number
  method: string
  frame: Buffer
  binary?: Uint8Array
  priority: number
  resolve: (value: EngineResponse) => void
  reject: (reason: Error) => void
  onProgress?: RequestOptions['onProgress']
  timer?: NodeJS.Timeout
}

// Solo se heredan las variables de entorno imprescindibles. Así el entorno del
// usuario no puede alterar al motor (p. ej. OPENSSL_CONF o SSLKEYLOGFILE, que
// afectarían a las conexiones del sellado de tiempo) (A02:2025 / A08:2025).
const ENV_ALLOWLIST = [
  'SystemRoot',
  'SystemDrive',
  'windir',
  'TEMP',
  'TMP',
  'USERPROFILE',
  'APPDATA',
  'LOCALAPPDATA',
  'ProgramData',
  'ProgramFiles',
  'ProgramFiles(x86)',
  'CommonProgramFiles',
  'PATH',
  'PATHEXT',
  'USERNAME',
  'USERDOMAIN',
  'COMPUTERNAME',
  'HOMEDRIVE',
  'HOMEPATH',
  'NUMBER_OF_PROCESSORS',
  'PROCESSOR_ARCHITECTURE',
  'HTTP_PROXY',
  'HTTPS_PROXY',
  'NO_PROXY'
]

function childEnv(): NodeJS.ProcessEnv {
  const env: NodeJS.ProcessEnv = {}
  const lower = new Map(Object.keys(process.env).map((key) => [key.toLowerCase(), key]))
  for (const name of ENV_ALLOWLIST) {
    const actual = lower.get(name.toLowerCase())
    if (actual && process.env[actual] !== undefined) env[name] = process.env[actual]
  }
  env.PYTHONUTF8 = '1'
  // Idioma de los mensajes del motor: solo uno de los valores conocidos.
  env.FOLIO_LANG = lang
  env.PYTHONDONTWRITEBYTECODE = '1'
  return env
}

export class EngineClient extends EventEmitter {
  private child: ChildProcessWithoutNullStreams | null = null
  private chunks: Buffer[] = []
  private buffered = 0
  private need: { json: number; bin: number } | null = null
  private nextId = 1
  private seq = 0
  private queue: (Job & { seq: number })[] = []
  private inflight: Job | null = null
  private ready = false
  private stopping = false
  private startWaiters: { resolve: () => void; reject: (e: Error) => void }[] = []
  private restarts: number[] = []
  private isolatedNow: boolean | null = null

  constructor(private readonly launch: () => EngineLaunch) {
    super()
  }

  get isReady(): boolean {
    return this.ready
  }

  /** Si el proceso que lee los PDF corre aislado, según su saludo (null si aún no ha saludado). */
  get isolated(): boolean | null {
    return this.ready ? this.isolatedNow : null
  }

  start(): Promise<void> {
    if (this.ready) return Promise.resolve()
    const waiter = new Promise<void>((resolve, reject) => this.startWaiters.push({ resolve, reject }))
    if (!this.child) this.spawnChild()
    return waiter
  }

  private spawnChild(): void {
    const spec = this.launch()
    // spawn con lista de argumentos y sin shell: nada de lo que contengan las
    // rutas puede interpretarse como órdenes (A05:2025).
    const child = spawn(spec.command, spec.args, {
      cwd: spec.cwd,
      env: childEnv(),
      stdio: ['pipe', 'pipe', 'pipe'],
      windowsHide: true,
      shell: false
    })
    this.child = child
    this.chunks = []
    this.buffered = 0
    this.need = null

    child.stdout.on('data', (chunk: Buffer) => this.onData(chunk))
    let stderrTail = ''
    child.stderr.setEncoding('utf8')
    child.stderr.on('data', (text: string) => {
      stderrTail = (stderrTail + text).slice(-8000)
      for (const line of text.split(/\r?\n/)) if (line.trim()) log.info(`[motor] ${line}`)
    })
    child.on('error', (error) => {
      log.error(`no se pudo iniciar el motor: ${error.message}`)
      this.onExit(child, -1)
    })
    child.on('exit', (code) => this.onExit(child, code ?? -1))
    child.stdin.on('error', () => {
      /* el cierre del proceso ya se gestiona en 'exit' */
    })
  }

  private onExit(child: ChildProcessWithoutNullStreams, code: number): void {
    if (this.child !== child) return
    this.child = null
    const wasReady = this.ready
    this.ready = false
    const down = new EngineError({ code: 'engine_down', message: L.main.engine.stopped })
    const jobs = [...(this.inflight ? [this.inflight] : []), ...this.queue]
    this.inflight = null
    this.queue = []
    for (const job of jobs) {
      if (job.timer) clearTimeout(job.timer)
      job.reject(down)
    }
    for (const waiter of this.startWaiters.splice(0)) waiter.reject(down)
    if (this.stopping) return
    log.error(`el motor terminó de forma inesperada (código ${code})`)
    if (wasReady) this.emit('down')
    // Reinicio automático, con tope para no entrar en bucle si falla siempre.
    const now = Date.now()
    this.restarts = this.restarts.filter((t) => now - t < 60_000)
    if (this.restarts.length < 3) {
      this.restarts.push(now)
      setTimeout(() => {
        if (!this.stopping && !this.child) {
          this.start().catch(() => undefined)
        }
      }, 400)
    }
  }

  // ------------------------------------------------------------------ tramas

  private take(n: number): Buffer {
    const first = this.chunks[0]
    if (first.length === n) {
      this.chunks.shift()
      this.buffered -= n
      return first
    }
    if (first.length > n) {
      this.chunks[0] = first.subarray(n)
      this.buffered -= n
      return first.subarray(0, n)
    }
    const out = Buffer.allocUnsafe(n)
    let offset = 0
    while (offset < n) {
      const head = this.chunks[0]
      const want = Math.min(head.length, n - offset)
      head.copy(out, offset, 0, want)
      offset += want
      if (want === head.length) this.chunks.shift()
      else this.chunks[0] = head.subarray(want)
    }
    this.buffered -= n
    return out
  }

  private onData(chunk: Buffer): void {
    this.chunks.push(chunk)
    this.buffered += chunk.length
    for (;;) {
      if (!this.need) {
        if (this.buffered < 8) return
        const head = this.take(8)
        const json = head.readUInt32BE(0)
        const bin = head.readUInt32BE(4)
        if (json === 0 || json > MAX_JSON_BYTES || bin > MAX_BINARY_BYTES) {
          this.protocolFailure('trama fuera de límites')
          return
        }
        this.need = { json, bin }
      }
      if (this.buffered < this.need.json + this.need.bin) return
      const { json, bin } = this.need
      this.need = null
      const header = this.take(json)
      const binary = bin ? this.take(bin) : undefined
      let message: unknown
      try {
        message = JSON.parse(header.toString('utf8'))
      } catch {
        this.protocolFailure('cabecera no válida')
        return
      }
      this.dispatch(message, binary)
    }
  }

  private protocolFailure(reason: string): void {
    // Con el flujo desincronizado no se puede confiar en nada de lo que llegue
    // después: se termina el proceso y se arranca uno nuevo (fallo seguro, A10:2025).
    log.error(`protocolo del motor: ${reason}`)
    this.child?.kill()
  }

  private dispatch(message: unknown, binary?: Buffer): void {
    if (typeof message !== 'object' || message === null) return
    const msg = message as Record<string, unknown>
    if (typeof msg.event === 'string') {
      if (msg.event === 'ready') {
        const data = (msg.data ?? {}) as { sandbox?: unknown }
        this.isolatedNow = data.sandbox === true
        this.ready = true
        if (!this.isolatedNow) log.error('el motor de documentos funciona sin aislamiento')
        this.emit('ready', this.isolatedNow)
        for (const waiter of this.startWaiters.splice(0)) waiter.resolve()
        this.pump()
      } else if (msg.event === 'progress' && this.inflight && msg.id === this.inflight.id) {
        const data = msg.data as { done?: number; total?: number; message?: string; text?: unknown } | undefined
        this.inflight.onProgress?.({
          done: Number(data?.done ?? 0),
          total: Number(data?.total ?? 1),
          message: String(data?.message ?? ''),
          // Texto que la IA va generando (respuestas que aparecen poco a poco), acotado.
          ...(typeof data?.text === 'string' ? { text: data.text.slice(0, MAX_PROGRESS_TEXT) } : {})
        })
      }
      return
    }
    const job = this.inflight
    if (!job || msg.id !== job.id) return
    this.inflight = null
    if (job.timer) clearTimeout(job.timer)
    if (msg.ok === true) {
      job.resolve({ result: msg.result ?? {}, binary })
    } else {
      const error = (msg.error ?? {}) as Partial<EngineErrorInfo>
      job.reject(
        new EngineError({
          code: typeof error.code === 'string' ? error.code : 'internal',
          message: typeof error.message === 'string' ? error.message : L.main.engine.failed,
          detail: error.detail
        })
      )
    }
    this.pump()
  }

  // ------------------------------------------------------------------- cola

  /**
   * Se envía una sola petición cada vez: el motor es secuencial y así la cola,
   * que vive aquí, puede reordenarse por prioridad y descartar lo que ya no hace
   * falta (p. ej. páginas que el usuario ha dejado atrás al desplazarse).
   */
  private pump(): void {
    if (!this.ready || this.inflight || !this.child || this.queue.length === 0) return
    let best = 0
    for (let i = 1; i < this.queue.length; i++) {
      const a = this.queue[i]
      const b = this.queue[best]
      if (a.priority > b.priority || (a.priority === b.priority && a.seq < b.seq)) best = i
    }
    const [job] = this.queue.splice(best, 1)
    this.inflight = job
    this.child.stdin.write(job.frame)
    if (job.binary?.length) this.child.stdin.write(job.binary)
    if (WATCHED.test(job.method)) {
      job.timer = setTimeout(() => {
        // Un PDF malformado puede dejar colgado al intérprete: mejor reiniciar
        // el motor que dejar la aplicación bloqueada para siempre.
        log.error(`sin respuesta a ${job.method}; se reinicia el motor`)
        this.child?.kill()
      }, WATCHDOG_MS)
    }
  }

  request<T = unknown>(method: string, params: Record<string, unknown> = {}, options: RequestOptions = {}): Promise<EngineResponse<T>> {
    return new Promise<EngineResponse<T>>((resolve, reject) => {
      const id = this.nextId++
      if (this.nextId > 2_000_000_000) this.nextId = 1
      const body = Buffer.from(JSON.stringify({ id, method, params }), 'utf8')
      const size = options.binary?.length ?? 0
      if (body.length > MAX_JSON_BYTES || size > MAX_BINARY_BYTES) {
        reject(new EngineError({ code: 'invalid_params', message: L.main.engine.tooLarge }))
        return
      }
      const head = Buffer.allocUnsafe(8)
      head.writeUInt32BE(body.length, 0)
      head.writeUInt32BE(size, 4)
      this.queue.push({
        id,
        method,
        frame: Buffer.concat([head, body]),
        binary: options.binary,
        priority: options.priority ?? 5,
        seq: this.seq++,
        resolve: resolve as (value: EngineResponse) => void,
        reject,
        onProgress: options.onProgress
      })
      options.onId?.(id)
      if (!this.child && !this.stopping) this.spawnChild()
      this.pump()
    })
  }

  cancel(id: number): void {
    const index = this.queue.findIndex((job) => job.id === id)
    if (index >= 0) {
      const [job] = this.queue.splice(index, 1)
      job.reject(new EngineError({ code: 'cancelled', message: L.main.engine.cancelled }))
      return
    }
    if (this.inflight?.id === id && this.child) {
      const body = Buffer.from(JSON.stringify({ method: '$cancel', params: { id } }), 'utf8')
      const head = Buffer.allocUnsafe(8)
      head.writeUInt32BE(body.length, 0)
      head.writeUInt32BE(0, 4)
      this.child.stdin.write(Buffer.concat([head, body]))
    }
  }

  async stop(): Promise<void> {
    this.stopping = true
    const child = this.child
    if (!child) return
    await new Promise<void>((resolve) => {
      const force = setTimeout(() => {
        child.kill()
        resolve()
      }, 2500)
      child.once('exit', () => {
        clearTimeout(force)
        resolve()
      })
      // Al cerrar su entrada el motor termina por sí mismo y borra sus archivos de trabajo.
      child.stdin.end()
    })
  }
}
