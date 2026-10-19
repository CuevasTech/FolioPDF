import { CircleAlert, CircleCheck, Info, TriangleAlert, X } from 'lucide-react'
import { L } from '@/i18n'
import { useApp } from '@/state/app'
import { Dialog } from './ui'

const ICONS = {
  info: <Info size={16} />,
  success: <CircleCheck size={16} />,
  warning: <TriangleAlert size={16} />,
  error: <CircleAlert size={16} />
}

export function Toasts() {
  const toasts = useApp((s) => s.toasts)
  const dismiss = useApp((s) => s.dismissToast)
  if (toasts.length === 0) return null
  return (
    <div className="toasts" role="status" aria-live="polite">
      {toasts.map((toast) => (
        <div key={toast.id} className={`toast ${toast.kind}`}>
          {ICONS[toast.kind]}
          <span>{toast.text}</span>
          {toast.action && (
            <button
              onClick={() => {
                toast.action?.run()
                dismiss(toast.id)
              }}
            >
              {toast.action.label}
            </button>
          )}
          <button aria-label={L.shell.dismiss} onClick={() => dismiss(toast.id)}>
            <X size={14} />
          </button>
        </div>
      ))}
    </div>
  )
}

/** Ventana de progreso de las operaciones largas (OCR, exportar…). */
export function BusyOverlay() {
  const busy = useApp((s) => s.busy)
  if (!busy) return null
  const percent = busy.progress === null ? null : Math.round(Math.min(1, Math.max(0, busy.progress)) * 100)
  return (
    <Dialog
      title={busy.title}
      onClose={() => undefined}
      footer={
        busy.cancel ? (
          <button className="btn" onClick={busy.cancel}>
            {L.common.cancel}
          </button>
        ) : undefined
      }
    >
      <p>{busy.message || L.shell.busy.working}</p>
      <div className={percent === null ? 'progress indeterminate' : 'progress'}>
        <div style={percent === null ? undefined : { width: `${percent}%` }} />
      </div>
    </Dialog>
  )
}
