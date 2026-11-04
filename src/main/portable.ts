import { accessSync, constants, existsSync, mkdirSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { app } from 'electron'

/**
 * Versión portable (el zip, sin instalar).
 *
 * Se reconoce por el archivo portable.txt junto a Folio.exe (lo pone
 * scripts/make-portable.mjs). Entonces todo lo suyo (ajustes, registro, copias
 * de trabajo y modelos de la IA) va a la carpeta FolioData de al lado, Folio no
 * se registra en Windows y las versiones nuevas se descargan desde el
 * navegador (el instalador no sirve para una copia sin instalar).
 *
 * portable.txt solo cambia dónde se guardan los datos; su contenido no se lee.
 * Si FolioData no se puede escribir (por ejemplo, descomprimida en Archivos de
 * programa), se usa la carpeta normal del usuario, como la versión instalada.
 */
let dataDir: string | null | undefined

/** Carpeta de datos de la versión portable, o null si esta copia no lo es. */
export function portableDataDir(): string | null {
  if (dataDir !== undefined) return dataDir
  dataDir = null
  if (!app.isPackaged) return dataDir
  const base = dirname(process.execPath)
  if (!existsSync(join(base, 'portable.txt'))) return dataDir
  const data = join(base, 'FolioData')
  try {
    mkdirSync(data, { recursive: true })
    accessSync(data, constants.W_OK)
    dataDir = data
  } catch {
    // Sin permiso de escritura: la carpeta normal del usuario.
  }
  return dataDir
}

/**
 * ¿Es la versión portable? En pruebas (sin empaquetar) se simula con
 * FOLIO_TEST_PORTABLE=1, sin cambiar la carpeta de datos del arnés.
 */
export function isPortable(): boolean {
  if (!app.isPackaged) return process.env.FOLIO_TEST_PORTABLE === '1'
  return portableDataDir() !== null
}
