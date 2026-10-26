import { execFile } from 'node:child_process'
import { join } from 'node:path'

/** Saca de la salida de «reg query» el texto del valor con ese nombre (null si no está). */
export function parseRegValue(stdout: string, value: string): string | null {
  // «    ProgId    REG_SZ    Acrobat.Document.DC». Se busca el valor por su
  // nombre: el predeterminado de una clave sin él sale como «(valor no
  // establecido)», traducido, y se confundía con un dato de verdad.
  const name = value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const match = new RegExp(`^\\s*${name}\\s+REG_SZ\\s+(.+?)\\s*$`, 'im').exec(stdout)
  return match ? match[1].trim() : null
}

/**
 * Lee un valor de texto del registro de Windows con reg.exe: ruta absoluta del
 * sistema y argumentos fijos, sin intérprete de órdenes (nada de lo que llega
 * se interpreta como orden). Devuelve null si no existe o no se puede leer.
 */
export function regQuery(key: string, value: string): Promise<string | null> {
  const regExe = join(process.env.SystemRoot ?? 'C:\\Windows', 'System32', 'reg.exe')
  return new Promise((resolve) => {
    execFile(regExe, ['query', key, '/v', value], { windowsHide: true, timeout: 5000 }, (error, stdout) => {
      resolve(error ? null : parseRegValue(stdout, value))
    })
  })
}
