import { app, shell } from 'electron'
import type { DefaultAppStatus } from '@shared/types'
import { log } from './log'
import { regQuery } from './registry'

/**
 * Folio como programa predeterminado para abrir PDF.
 *
 * Windows no deja que un programa se ponga solo como predeterminado (para que
 * nadie se lo quite a otro a escondidas): lo decide el usuario en
 * Configuración. Folio solo mira si ya lo es y, si se lo pide, abre su página
 * en «Aplicaciones predeterminadas», donde basta un clic. El instalador
 * registra a Folio como aplicación (build/installer.nsh).
 */

/** Identificador del tipo de archivo de Folio (fileAssociations en electron-builder.yml). */
const PROG_ID = 'Folio.PDF'
/** Nombre con el que el instalador registra a Folio en RegisteredApplications. */
const REGISTERED_NAME = 'Folio'
const CHOICE = 'HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\FileExts\\.pdf'

/** Solo pruebas, sin empaquetar: simular el estado y no abrir Configuración de verdad. */
function testState(): DefaultAppStatus | null {
  if (app.isPackaged) return null
  const value = process.env.FOLIO_TEST_DEFAULT_APP
  return value === 'default' || value === 'other' ? value : null
}

async function readStatus(): Promise<DefaultAppStatus> {
  const simulated = testState()
  if (simulated) return simulated
  if (!app.isPackaged || process.platform !== 'win32') return 'unknown'
  // Windows 11 reciente guarda la elección en el valor ProgId de la subclave
  // UserChoiceLatest\ProgId; antes, en UserChoice.
  const chosen = (await regQuery(`${CHOICE}\\UserChoiceLatest\\ProgId`, 'ProgId')) ?? (await regQuery(`${CHOICE}\\UserChoice`, 'ProgId'))
  if (chosen === null) return 'unknown'
  return chosen.toLowerCase() === PROG_ID.toLowerCase() ? 'default' : 'other'
}

let lastStatus: DefaultAppStatus | null = null

/** ¿Abre Windows los PDF con Folio? 'unknown' si no se puede saber (sin empaquetar, por ejemplo). */
export async function pdfDefaultStatus(): Promise<DefaultAppStatus> {
  const status = await readStatus()
  // Al registro solo cuando cambia (se consulta cada vez que la ventana vuelve a primer plano).
  if (status !== lastStatus) log.info(`predeterminado para PDF: ${status}`)
  lastStatus = status
  return status
}

/** Abre la página de Folio en «Aplicaciones predeterminadas» (o la general, si Windows no la tiene). */
export async function openDefaultAppSettings(): Promise<boolean> {
  if (testState()) {
    log.info('predeterminado: se abriría Configuración (prueba)')
    return true
  }
  try {
    await shell.openExternal(`ms-settings:defaultapps?registeredAppUser=${encodeURIComponent(REGISTERED_NAME)}`)
    return true
  } catch {
    try {
      await shell.openExternal('ms-settings:defaultapps')
      return true
    } catch {
      log.warn('predeterminado: no se pudo abrir Configuración')
      return false
    }
  }
}
