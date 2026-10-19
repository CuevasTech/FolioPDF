import { join } from 'node:path'
import { app, BrowserWindow, ipcMain, nativeTheme, screen } from 'electron'
import { lang, locale } from './i18n'
import { APP_ORIGIN, isTrustedSender } from './security'
import type { Store } from './store'

/** Fecha de compilación (AAAA-MM-DD), la pone electron-vite. */
declare const __FOLIO_BUILT__: string

const TITLEBAR_HEIGHT = 40

const CHROME = {
  light: { background: '#ffffff', symbol: '#1f2937' },
  dark: { background: '#1c1d20', symbol: '#e5e7eb' }
}

function isDark(store: Store): boolean {
  const choice = store.settings.theme
  return choice === 'dark' || (choice === 'system' && nativeTheme.shouldUseDarkColors)
}

export function createWindow(store: Store, version: string): BrowserWindow {
  const saved = store.windowBounds
  const area = screen.getPrimaryDisplay().workAreaSize
  const width = Math.min(saved?.width ?? 1360, area.width)
  const height = Math.min(saved?.height ?? 880, area.height)
  const dark = isDark(store)
  const chrome = dark ? CHROME.dark : CHROME.light

  const window = new BrowserWindow({
    width,
    height,
    x: saved?.x,
    y: saved?.y,
    minWidth: 900,
    minHeight: 580,
    show: false,
    backgroundColor: chrome.background,
    title: 'Folio',
    // En la app instalada el icono va en el ejecutable; esto lo pone al ejecutar desde el código.
    icon: app.isPackaged ? undefined : join(app.getAppPath(), 'build', 'icon.ico'),
    titleBarStyle: 'hidden',
    titleBarOverlay: { color: chrome.background, symbolColor: chrome.symbol, height: TITLEBAR_HEIGHT },
    autoHideMenuBar: true,
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      // Aislamiento completo de la interfaz: sin Node, en sandbox y con su
      // propio contexto de JavaScript separado del preload (A02:2025).
      sandbox: true,
      contextIsolation: true,
      nodeIntegration: false,
      webSecurity: true,
      allowRunningInsecureContent: false,
      webviewTag: false,
      spellcheck: false,
      navigateOnDragDrop: false,
      devTools: !app.isPackaged,
      additionalArguments: [
        `--folio-version=${version}`,
        `--folio-built=${__FOLIO_BUILT__}`,
        `--folio-lang=${lang}`,
        `--folio-locale=${locale}`,
        ...(app.isPackaged ? [] : ['--folio-dev'])
      ]
    }
  })

  window.removeMenu()
  if (saved?.maximized) window.maximize()
  window.once('ready-to-show', () => window.show())

  let saveTimer: NodeJS.Timeout | null = null
  const remember = (): void => {
    if (saveTimer) clearTimeout(saveTimer)
    saveTimer = setTimeout(() => {
      if (window.isDestroyed() || window.isMinimized()) return
      const bounds = window.getNormalBounds()
      store.setWindowBounds({ ...bounds, maximized: window.isMaximized() })
    }, 600)
  }
  window.on('resize', remember)
  window.on('move', remember)
  window.on('maximize', remember)
  window.on('unmaximize', remember)

  const applyChrome = (useDark: boolean): void => {
    if (window.isDestroyed()) return
    const colors = useDark ? CHROME.dark : CHROME.light
    window.setTitleBarOverlay({ color: colors.background, symbolColor: colors.symbol, height: TITLEBAR_HEIGHT })
    window.setBackgroundColor(colors.background)
  }
  const onSetDark = (event: Electron.IpcMainEvent, value: unknown): void => {
    if (!isTrustedSender(event, window.isDestroyed() ? null : window.webContents)) return
    applyChrome(value === true)
  }
  ipcMain.on('window:setDark', onSetDark)
  window.on('closed', () => ipcMain.removeListener('window:setDark', onSetDark))

  const devUrl = app.isPackaged ? undefined : process.env.ELECTRON_RENDERER_URL
  void window.loadURL(devUrl ?? `${APP_ORIGIN}/index.html`)
  return window
}
