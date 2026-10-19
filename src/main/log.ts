import { appendFileSync, existsSync, mkdirSync, renameSync, statSync } from 'node:fs'
import { join } from 'node:path'

/**
 * Registro mínimo a archivo. Regla del proyecto (OWASP A09:2025): aquí nunca se
 * escriben contraseñas, contenido de documentos ni datos de certificados; solo
 * nombres de operación, códigos de error y tiempos.
 */

const MAX_BYTES = 1_000_000
let logFile: string | null = null

export function initLog(dir: string): void {
  try {
    mkdirSync(dir, { recursive: true })
    logFile = join(dir, 'folio.log')
  } catch {
    logFile = null
  }
}

function rotate(): void {
  if (!logFile) return
  try {
    if (existsSync(logFile) && statSync(logFile).size > MAX_BYTES) {
      renameSync(logFile, logFile.replace(/\.log$/, '.1.log'))
    }
  } catch {
    /* si no se puede rotar se sigue escribiendo en el mismo archivo */
  }
}

/** Quita saltos de línea y caracteres de control para que una entrada no pueda falsificar otras. */
function clean(text: string): string {
  // eslint-disable-next-line no-control-regex
  return text.replace(/[\u0000-\u001f\u007f]+/g, ' ').slice(0, 2000)
}

function write(level: string, message: string): void {
  const line = `${new Date().toISOString()} ${level} ${clean(message)}\n`
  if (process.env.NODE_ENV !== 'production') process.stderr.write(line)
  if (!logFile) return
  try {
    rotate()
    appendFileSync(logFile, line, { encoding: 'utf8' })
  } catch {
    /* el registro nunca debe tumbar la aplicación */
  }
}

export const log = {
  info: (message: string): void => write('INFO ', message),
  warn: (message: string): void => write('WARN ', message),
  error: (message: string): void => write('ERROR', message)
}
