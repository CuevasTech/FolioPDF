import { execFile } from 'node:child_process'
import { join } from 'node:path'

/**
 * Lee un valor de texto del registro de Windows con reg.exe: ruta absoluta del
 * sistema y argumentos fijos, sin intérprete de órdenes (nada de lo que llega
 * se interpreta como orden). Devuelve null si no existe o no se puede leer.
 */
export function regQuery(key: string, value: string | null): Promise<string | null> {
  const regExe = join(process.env.SystemRoot ?? 'C:\\Windows', 'System32', 'reg.exe')
  const args = ['query', key, ...(value === null ? ['/ve'] : ['/v', value])]
  return new Promise((resolve) => {
    execFile(regExe, args, { windowsHide: true, timeout: 5000 }, (error, stdout) => {
      if (error) return resolve(null)
      // «    ProgId    REG_SZ    Acrobat.Document.DC»
      const match = /REG_SZ\s+(.+?)\s*$/m.exec(stdout)
      resolve(match ? match[1].trim() : null)
    })
  })
}
