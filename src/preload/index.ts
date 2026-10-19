import { contextBridge, ipcRenderer, webUtils } from 'electron'
import type { AiDownloadError, AiDownloadProgress } from '@shared/ai'
import type { EngineStatus, FolioApi } from '@shared/api'
import { LOCALE_RE, type Lang } from '@shared/i18n'
import type { ProgressInfo, UpdateStatus } from '@shared/types'

/**
 * Único punto de contacto entre la interfaz y el resto de la aplicación.
 *
 * No se expone `ipcRenderer` ni ningún módulo de Node: solo funciones concretas
 * con canales fijos, de modo que la interfaz no puede enviar mensajes
 * arbitrarios al proceso principal (OWASP A01:2025).
 */

function subscribe<T>(channel: string, listener: (payload: T) => void): () => void {
  const wrapped = (_event: Electron.IpcRendererEvent, payload: T): void => listener(payload)
  ipcRenderer.on(channel, wrapped)
  return () => ipcRenderer.removeListener(channel, wrapped)
}

const argValue = (name: string): string => process.argv.find((arg) => arg.startsWith(`--${name}=`))?.split('=')[1] ?? ''
const version = argValue('folio-version')
const builtArg = argValue('folio-built')
const built = /^\d{4}-\d{2}-\d{2}$/.test(builtArg) ? builtArg : ''
// Idioma y región: solo valores con forma conocida; si no, español de España.
const lang: Lang = argValue('folio-lang') === 'en' ? 'en' : 'es'
const localeArg = argValue('folio-locale')
const locale = LOCALE_RE.test(localeArg) ? localeArg : lang === 'en' ? 'en-US' : 'es-ES'

const api: FolioApi = {
  env: { version, built, dev: process.argv.includes('--folio-dev'), lang, locale },

  engine: (method, params, options) =>
    ipcRenderer.invoke('engine:call', {
      method,
      params,
      binary: options?.binary,
      callId: options?.callId,
      priority: options?.priority
    }),
  cancel: (callId) => ipcRenderer.send('engine:cancel', callId),
  onProgress: (listener) => subscribe<ProgressInfo>('engine:progress', listener),
  onEngineDown: (listener) => subscribe<void>('engine:down', () => listener()),
  engineStatus: () => ipcRenderer.invoke('engine:status'),
  onEngineStatus: (listener) => subscribe<EngineStatus>('engine:status', listener),
  onFontsChanged: (listener) => subscribe<void>('fonts:changed', () => listener()),

  files: {
    openDialog: () => ipcRenderer.invoke('files:openDialog'),
    // La ruta de cada archivo soltado la da el sistema a partir del objeto File
    // real; un script de la página no puede fabricar una ruta distinta.
    openDropped: (files) =>
      ipcRenderer.invoke(
        'files:openDropped',
        files.map((file) => webUtils.getPathForFile(file)).filter((path) => path.length > 0)
      ),
    openRecent: (id) => ipcRenderer.invoke('files:openRecent', id),
    retryOpen: (token, password) => ipcRenderer.invoke('files:retryOpen', { token, password }),
    onOpened: (listener) => subscribe('files:opened', listener),
    ready: () => ipcRenderer.send('app:ready'),
    save: (doc, force) => ipcRenderer.invoke('files:save', { doc, force }),
    saveAs: (doc, suggestedName) => ipcRenderer.invoke('files:saveAs', { doc, suggestedName }),
    close: (doc) => ipcRenderer.invoke('files:close', doc),
    showInFolder: (doc) => ipcRenderer.invoke('files:showInFolder', doc),
    pickImage: () => ipcRenderer.invoke('files:pickImage'),
    recover: (id, password) => ipcRenderer.invoke('files:recover', { id, password }),
    newBlank: () => ipcRenderer.invoke('files:newBlank'),
    merge: () => ipcRenderer.invoke('files:merge'),
    fromImages: () => ipcRenderer.invoke('files:fromImages'),
    insertPages: (doc, before) => ipcRenderer.invoke('files:insertPages', { doc, before }),
    extractPages: (doc, pages) => ipcRenderer.invoke('files:extractPages', { doc, pages }),
    splitPages: (doc, options, callId) => ipcRenderer.invoke('files:splitPages', { doc, options, callId }),
    exportAs: (doc, kind, options, callId) => ipcRenderer.invoke('files:exportAs', { doc, kind, options, callId })
  },

  sign: {
    pickPfx: () => ipcRenderer.invoke('sign:pickPfx'),
    pfxInfo: (token, password) => ipcRenderer.invoke('sign:pfxInfo', { token, password }),
    sign: (doc, request, image) => ipcRenderer.invoke('sign:sign', { doc, request, image })
  },

  signatures: {
    list: () => ipcRenderer.invoke('signatures:list'),
    add: (signature) => ipcRenderer.invoke('signatures:add', signature),
    remove: (id) => ipcRenderer.invoke('signatures:remove', id)
  },

  print: {
    printers: () => ipcRenderer.invoke('print:printers'),
    start: (doc, pages, request, callId) => ipcRenderer.invoke('print:start', { doc, pages, request, callId })
  },

  scan: {
    devices: () => ipcRenderer.invoke('scan:devices'),
    fromImages: (job, callId) => ipcRenderer.invoke('scan:fromImages', { job, callId }),
    fromScanner: (job, options, callId) => ipcRenderer.invoke('scan:fromScanner', { job, ...options, callId }),
    build: (job, pages, style, ocr, callId, unwarp) => ipcRenderer.invoke('scan:build', { job, pages, style, ocr, callId, unwarp: unwarp === true })
  },

  settings: {
    get: () => ipcRenderer.invoke('settings:get'),
    set: (patch) => ipcRenderer.invoke('settings:set', patch)
  },
  recent: {
    list: () => ipcRenderer.invoke('recent:list'),
    clear: () => ipcRenderer.invoke('recent:clear'),
    remove: (id) => ipcRenderer.invoke('recent:remove', id)
  },
  shell: {
    openExternal: (url) => ipcRenderer.invoke('shell:openExternal', url)
  },
  updates: {
    status: () => ipcRenderer.invoke('updates:status'),
    check: () => ipcRenderer.send('updates:check'),
    download: () => ipcRenderer.send('updates:download'),
    install: () => ipcRenderer.send('updates:install'),
    onStatus: (listener) => subscribe<UpdateStatus>('updates:status', listener)
  },
  about: {
    open: (what) => ipcRenderer.invoke('about:open', what)
  },
  ai: {
    status: () => ipcRenderer.invoke('ai:status'),
    install: () => ipcRenderer.invoke('ai:install'),
    cancel: () => ipcRenderer.invoke('ai:cancel'),
    remove: () => ipcRenderer.invoke('ai:remove'),
    onProgress: (listener) => subscribe<AiDownloadProgress | null>('ai:progress', listener),
    onChanged: (listener) => subscribe<void>('ai:changed', () => listener()),
    onError: (listener) => subscribe<AiDownloadError>('ai:error', listener)
  },
  feedback: {
    available: () => ipcRenderer.invoke('feedback:available'),
    send: (kind, message) => ipcRenderer.invoke('feedback:send', { kind, message })
  },
  defaultApp: {
    status: () => ipcRenderer.invoke('defaultApp:status'),
    open: () => ipcRenderer.invoke('defaultApp:open')
  },
  window: {
    setDark: (dark) => ipcRenderer.send('window:setDark', dark),
    onCloseRequest: (listener) => subscribe<void>('window:close-request', () => listener()),
    closeNow: () => ipcRenderer.send('window:closeNow')
  }
}

contextBridge.exposeInMainWorld('folio', api)
