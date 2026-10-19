import { FolderOpen } from 'lucide-react'
import { useEffect } from 'react'
import { DialogHost } from './components/Dialogs'
import { checkRecovery, resetRecoveryCheck } from './components/Recovery'
// Registra los cuadros de imprimir (se abre con printDoc) y de comentarios.
import './components/PrintDialog'
import './components/FeedbackDialog'
import { StartScreen } from './components/StartScreen'
import { TitleBar } from './components/TitleBar'
import { BusyOverlay, Toasts } from './components/Toasts'
import { UpdateCard } from './components/UpdateCard'
import { Workspace } from './components/Workspace'
import { L } from './i18n'
import { cn } from './lib/util'
import { initApp } from './state/actions'
import { useActiveDoc, useApp } from './state/app'
import { useFileDrop, useGlobalShortcuts } from './state/shortcuts'
import { followDefaultApp } from './state/defaultApp'
import { followAi } from './state/ai'
import { followUpdates } from './state/updates'

export function App() {
  const ready = useApp((s) => s.ready)
  const dragging = useApp((s) => s.dragging)
  const isolated = useApp((s) => s.isolated)
  const active = useActiveDoc()

  useEffect(() => {
    void initApp()
    followUpdates()
    followAi()
    followDefaultApp()
  }, [])

  // Cada vez que el motor arranca (o rearranca tras un fallo) se mira si quedó algo por recuperar.
  useEffect(() => {
    if (!ready) return
    if (isolated === null) resetRecoveryCheck()
    else void checkRecovery()
  }, [ready, isolated])
  useGlobalShortcuts()
  useFileDrop()

  // El título de la ventana (barra de tareas, Alt+Tab) sigue al documento activo.
  const title = active ? `${active.state.dirty ? '• ' : ''}${active.state.name} — Folio` : 'Folio'
  useEffect(() => {
    document.title = title
  }, [title])

  return (
    <div className={cn('app', dragging && 'dragging')}>
      <TitleBar />
      {!ready ? <div className="start" /> : active ? <Workspace key={active.id} doc={active} /> : <StartScreen />}
      {dragging && active && (
        <div className="drop-veil">
          <div>
            <FolderOpen size={18} />
            {L.shell.app.dropToOpen}
          </div>
        </div>
      )}
      <DialogHost />
      <BusyOverlay />
      <UpdateCard />
      <Toasts />
    </div>
  )
}
