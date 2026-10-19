import { CircleAlert, Download, RefreshCw, Sparkles, X } from 'lucide-react'
import { L, fmt } from '@/i18n'
import { requestQuit } from '@/state/actions'
import { postponeUpdate, useUpdates } from '@/state/updates'

/**
 * Aviso de versión nueva de Folio, con lo justo: título y botones. Nada se
 * descarga hasta pulsar «Actualizar» ni se instala sin permiso: con «Reiniciar
 * y actualizar» (preguntando antes qué guardar) o, ya descargada, al cerrar Folio.
 */
export function UpdateCard() {
  const status = useUpdates((s) => s.status)
  const postponed = useUpdates((s) => s.postponed)
  const version = status.version && status.version !== '?' ? status.version : ''

  if (status.state === 'available' && postponed !== null && postponed === (status.version ?? null)) return null
  if (status.state === 'ready' && postponed === 'ready') return null
  if (status.state === 'error' && postponed === 'error') return null

  if (status.state === 'available') {
    return (
      <div className="update-card" role="status">
        <Sparkles size={18} className="update-icon" />
        <div className="grow">
          <b>{version ? fmt(L.shell.update.availableVersion, { version }) : L.shell.update.available}</b>
          <div className="update-actions">
            <button className="btn sm primary" onClick={() => window.folio.updates.download()}>
              <Download size={14} />
              {L.shell.update.update}
            </button>
            <button className="btn sm ghost" onClick={postponeUpdate}>
              {L.shell.update.notNow}
            </button>
          </div>
        </div>
      </div>
    )
  }

  if (status.state === 'downloading') {
    const percent = status.percent ?? 0
    return (
      <div className="update-card" role="status">
        <Download size={18} className="update-icon" />
        <div className="grow">
          <b>{version ? fmt(L.shell.update.downloadingVersion, { version }) : L.shell.update.downloading}</b>
          <div className="update-bar" aria-label={fmt(L.shell.percent, { n: percent })}>
            <div style={{ width: `${percent}%` }} />
          </div>
        </div>
      </div>
    )
  }

  if (status.state === 'ready') {
    return (
      <div className="update-card" role="status">
        <RefreshCw size={18} className="update-icon" />
        <div className="grow">
          <b>{version ? fmt(L.shell.update.readyVersion, { version }) : L.shell.update.ready}</b>
          <div className="update-actions">
            <button className="btn sm primary" onClick={() => void requestQuit({ install: true })}>
              <RefreshCw size={14} />
              {L.shell.update.restart}
            </button>
            <button className="btn sm ghost" data-tip={L.shell.update.laterTip} onClick={() => useUpdates.setState({ postponed: 'ready' })}>
              {L.shell.update.later}
            </button>
          </div>
        </div>
      </div>
    )
  }

  if (status.state === 'error') {
    return (
      <div className="update-card error" role="alert">
        <CircleAlert size={18} className="update-icon" />
        <div className="grow">
          <b>{L.shell.update.failed}</b>
          <span>{status.message ?? L.shell.update.tryLater}</span>
          <div className="update-actions">
            <button
              className="btn sm"
              onClick={() => (status.version ? window.folio.updates.download() : window.folio.updates.check())}
            >
              {L.common.retry}
            </button>
          </div>
        </div>
        <button className="icon-btn sm" aria-label={L.shell.dismiss} onClick={() => useUpdates.setState({ postponed: 'error' })}>
          <X size={14} />
        </button>
      </div>
    )
  }

  return null
}
