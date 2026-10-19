import { readdir, readFile, stat } from 'node:fs/promises'
import { join } from 'node:path'
import { log } from './log'

/**
 * Aptos, la fuente de Office, para escribir en los PDF.
 *
 * No viene con Windows: está instalada (en Windows o solo para el usuario) o,
 * más a menudo, solo en la caché de fuentes de Office, con nombres numéricos.
 * Ni el motor aislado ni la interfaz pueden leer esa caché, así que el proceso
 * principal localiza aquí sus cuatro variantes y las entrega en memoria: al
 * motor (fonts.register) y a la interfaz (folio://app/fonts/…). No se instala
 * ni se copia nada en el sistema.
 */

export type FontStyle = 'regular' | 'bold' | 'italic' | 'boldItalic'
export const FONT_STYLES: FontStyle[] = ['regular', 'bold', 'italic', 'boldItalic']

/** Nombre completo (nameID 4) de cada variante de Aptos que se usa. */
const STYLE_BY_NAME: Record<string, FontStyle> = {
  aptos: 'regular',
  'aptos regular': 'regular',
  'aptos bold': 'bold',
  'aptos italic': 'italic',
  'aptos bold italic': 'boldItalic'
}
const MAX_FONT_BYTES = 8 * 1024 * 1024
const MAX_FILES = 60

/** Nombre completo de una fuente TrueType/OpenType (tabla 'name'), o null si no se puede leer. */
export function fontFullName(data: Buffer): string | null {
  try {
    const tables = Math.min(data.readUInt16BE(4), 64)
    for (let i = 0; i < tables; i++) {
      const record = 12 + i * 16
      if (data.toString('latin1', record, record + 4) !== 'name') continue
      const table = data.readUInt32BE(record + 8)
      const count = Math.min(data.readUInt16BE(table + 2), 500)
      const strings = table + data.readUInt16BE(table + 4)
      let mac: string | null = null
      for (let j = 0; j < count; j++) {
        const entry = table + 6 + j * 12
        const platform = data.readUInt16BE(entry)
        const encoding = data.readUInt16BE(entry + 2)
        const nameId = data.readUInt16BE(entry + 6)
        const length = data.readUInt16BE(entry + 8)
        const start = strings + data.readUInt16BE(entry + 10)
        if (nameId !== 4 || start + length > data.length) continue
        if (platform === 3 && (encoding === 0 || encoding === 1)) {
          // UTF-16 big-endian
          const chars: string[] = []
          for (let k = start; k + 1 < start + length; k += 2) chars.push(String.fromCharCode(data.readUInt16BE(k)))
          return chars.join('')
        }
        if (platform === 1 && encoding === 0) mac = data.toString('latin1', start, start + length)
      }
      return mac
    }
  } catch {
    /* archivo que no es una fuente: se ignora */
  }
  return null
}

let cached: Promise<Partial<Record<FontStyle, string>>> | null = null

/** Archivos de las cuatro variantes de Aptos (vacío si no está la normal). Se busca una vez. */
export function findAptos(): Promise<Partial<Record<FontStyle, string>>> {
  cached ??= search().catch(() => ({}))
  return cached
}

async function search(): Promise<Partial<Record<FontStyle, string>>> {
  const local = process.env.LOCALAPPDATA ?? ''
  const windows = process.env.WINDIR ?? process.env.SystemRoot ?? 'C:\\Windows'
  // Primero lo instalado (en Windows o para el usuario) y después la caché de Office.
  const folders: [string, boolean][] = [
    [join(windows, 'Fonts'), true],
    ...(local ? ([[join(local, 'Microsoft', 'Windows', 'Fonts'), true], [join(local, 'Microsoft', 'FontCache', '4', 'CloudFonts', 'Aptos'), false]] as [string, boolean][]) : [])
  ]
  const found: Partial<Record<FontStyle, string>> = {}
  for (const [folder, byName] of folders) {
    let names: string[]
    try {
      names = await readdir(folder)
    } catch {
      continue
    }
    // En las carpetas de Windows, solo los archivos de Aptos; en la de Office, todos (nombres numéricos).
    const candidates = names.filter((name) => /\.(ttf|otf)$/i.test(name) && (!byName || /^aptos/i.test(name))).slice(0, MAX_FILES)
    for (const name of candidates) {
      const file = join(folder, name)
      try {
        const info = await stat(file)
        if (!info.isFile() || info.size > MAX_FONT_BYTES) continue
        const full = fontFullName(await readFile(file))
        const style = full ? STYLE_BY_NAME[full.trim().toLowerCase()] : undefined
        if (style && !found[style]) found[style] = file
      } catch {
        /* archivo ilegible: se ignora */
      }
    }
  }
  if (!found.regular) return {}
  log.info(`fuentes: Aptos disponible (${FONT_STYLES.filter((style) => found[style]).length} variantes)`)
  return found
}

/** Contenido de una variante para servirla a la interfaz (null si no está). */
export async function aptosFile(style: string): Promise<Buffer | null> {
  if (!FONT_STYLES.includes(style as FontStyle)) return null
  const file = (await findAptos())[style as FontStyle]
  return file ? readFile(file) : null
}
