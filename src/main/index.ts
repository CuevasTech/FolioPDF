import { existsSync, mkdirSync, statSync } from 'node:fs'
import { readFile } from 'node:fs/promises'
import { isAbsolute, join, resolve } from 'node:path'
import { app, BrowserWindow, session } from 'electron'
import { AiModels } from './aimodels'
import { EngineClient, type EngineLaunch } from './engine'
import { Bridge, isOpenable } from './ipc'
import { initLang } from './i18n'
import { initLog, log } from './log'
import { PrintService } from './print'
import { hardenContents, hardenSession, registerAppScheme, serveApp } from './security'
import { Store } from './store'
import { Updates } from './updates'
import { aptosFile, findAptos, FONT_STYLES } from './fonts'

async function shareExtraFonts(): Promise<void> {
  const files = await findAptos()
  if (!files.regular || !engine) return
  let shared = 0
  for (const style of FONT_STYLES) {
    const file = files[style]
    if (!file) continue
    try {
      const data = await readFile(file)
      await engine.request('fonts.register', { family: 'Aptos', style }, { binary: new Uint8Array(data), priority: 9 })
      shared++
    } catch {
      log.warn(`fuentes: no se pudo entregar Aptos (${style})`)
    }
  }
  if (shared) bridge?.sendFontsChanged()
}
import { createWindow } from './window'

app.setAppUserModelId('com.cuevastech.folio')

// Todos los datos de la aplicación (ajustes, registro y copias de trabajo de los
// documentos) van a una carpeta local y privada del usuario, no a la itinerante.
if (!app.commandLine.hasSwitch('user-data-dir') && process.env.LOCALAPPDATA) {
  app.setPath('userData', join(process.env.LOCALAPPDATA, 'Folio'))
}

// Una sola instancia por carpeta de datos: los archivos que se abran desde el
// Explorador llegan a la ventana ya abierta, y nadie más usa la carpeta de trabajo
// (por eso es seguro limpiarla al arrancar).
if (!app.requestSingleInstanceLock()) {
  app.quit()
}

registerAppScheme()
hardenContents()

function localBase(): string {
  return app.getPath('userData')
}

/** Archivos pasados en la línea de órdenes (doble clic, «Abrir con»). */
function filesFromArgs(argv: string[], cwd: string): string[] {
  const out: string[] = []
  for (const arg of argv.slice(1)) {
    if (!arg || arg.startsWith('-') || !isOpenable(arg)) continue
    const path = isAbsolute(arg) ? arg : resolve(cwd, arg)
    try {
      if (existsSync(path) && statSync(path).isFile()) out.push(path)
    } catch {
      /* argumento que no es un archivo accesible */
    }
  }
  return out
}

let workDir = ''
/** Carpeta de los modelos de IA descargados (la lee el motor; ver aimodels.ts). */
let modelsRoot = ''
let engine: EngineClient | null = null
let bridge: Bridge | null = null
let updates: Updates | null = null
let mainWindow: BrowserWindow | null = null
let quitting = false

function prepareWorkDir(): string {
  // Carpeta estable: la usa el motor solo si Windows no le deja aislarse (si
  // puede, trabaja en la carpeta privada de su contenedor). Los restos de una
  // ejecución que terminó de golpe los aparta (si tenían cambios sin guardar,
  // para ofrecer recuperarlos) y los borra el propio motor al arrancar.
  const dir = join(localBase(), 'work')
  mkdirSync(dir, { recursive: true })
  return dir
}

function engineLaunch(): EngineLaunch {
  if (app.isPackaged) {
    const dir = join(process.resourcesPath, 'engine')
    return {
      command: join(dir, 'folio-engine.exe'),
      args: ['--workdir', workDir, '--resources', join(process.resourcesPath, 'assets'), '--models', modelsRoot],
      cwd: dir
    }
  }
  const root = app.getAppPath()
  return {
    command: join(root, 'engine', '.venv', 'Scripts', 'python.exe'),
    // -E y -s: el intérprete ignora las variables PYTHON* y los paquetes del
    // perfil del usuario, de modo que el entorno no puede inyectarle código.
    args: ['-E', '-s', '-m', 'folio_engine', '--workdir', workDir, '--resources', join(root, 'resources'), '--models', modelsRoot],
    cwd: join(root, 'engine')
  }
}

app.on('second-instance', (_event, argv, cwd) => {
  if (mainWindow) {
    if (mainWindow.isMinimized()) mainWindow.restore()
    mainWindow.focus()
  }
  void bridge?.openFromSystem(filesFromArgs(argv, cwd))
})

app.on('window-all-closed', () => app.quit())

app.on('before-quit', (event) => {
  if (quitting) return
  quitting = true
  event.preventDefault()
  // El motor borra sus copias de trabajo al cerrar cada documento y al salir.
  const finish = (): void => {
    // Una versión nueva que el usuario ya descargó se instala al salir, sin
    // volver a abrir Folio. El instalador cierra lo que quede; por si no
    // llegara a arrancar, se sale igualmente al poco.
    if (updates?.install(false)) setTimeout(() => app.exit(0), 5000)
    else app.exit(0)
  }
  if (engine) engine.stop().then(finish, finish)
  else finish()
})

void app.whenReady().then(async () => {
  initLog(join(localBase(), 'logs'))
  log.info(`Folio ${app.getVersion()} iniciado`)

  hardenSession(session.defaultSession)

  workDir = prepareWorkDir()
  const store = new Store(app.getPath('userData'))
  initLang(store.settings.language)
  const aiModels = new AiModels(app.getPath('userData'), (progress) => bridge?.sendAiProgress(progress))
  modelsRoot = aiModels.root
  await aiModels.load()
  engine = new EngineClient(engineLaunch)
  const printer = new PrintService(engine)
  serveApp(join(__dirname, '../renderer'), (url) => printer.respond(url), aptosFile)
  updates = new Updates((status) => bridge?.sendUpdate(status))
  bridge = new Bridge(engine, store, printer, updates, aiModels)
  // Cada vez que el motor arranca (o rearranca) recibe en memoria las fuentes
  // extra que haya en este equipo (Aptos); después la interfaz recarga la lista.
  engine.on('ready', () => void shareExtraFonts())
  // Y la configuración de la IA (qué modelos hay y si puede usar la gráfica).
  engine.on('ready', () => void bridge?.configureAi())
  engine.start().catch(() => log.error('el motor no arrancó'))

  mainWindow = createWindow(store, app.getVersion())
  bridge.attach(mainWindow)
  // Al poco de abrir, y solo si el usuario no lo ha desactivado: mirar si hay
  // versión nueva (solo se avisa; descargar e instalar lo decide él).
  setTimeout(() => {
    if (store.settings.checkUpdates) updates?.check(false)
  }, 6000)
  mainWindow.on('closed', () => {
    mainWindow = null
  })

  // Se abrirán cuando la interfaz avise de que ya está escuchando.
  void bridge.openFromSystem(filesFromArgs(process.argv, process.cwd()))
})
