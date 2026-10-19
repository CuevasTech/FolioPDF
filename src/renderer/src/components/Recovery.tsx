import { FileClock, KeyRound } from 'lucide-react'
import { useState } from 'react'
import { L } from '@/i18n'
import { errorMessage, query, unwrap } from '@/lib/api'
import { formatWhen } from '@/lib/util'
import { handleOutcomes } from '@/state/actions'
import { useApp } from '@/state/app'
import { type CustomDialogProps, registerDialog } from './Dialogs'
import { Dialog } from './ui'

export interface RecoverableDoc {
  id: string
  name: string
  path: string | null
  time: string
  encrypted: boolean
}

let asked = false

/**
 * Tras un cierre inesperado (de la aplicación o del motor), ofrece recuperar
 * los documentos que tenían cambios sin guardar. Se pregunta una vez por
 * arranque del motor; lo que no se decide se vuelve a ofrecer la próxima vez,
 * hasta que el motor lo borra a los siete días.
 */
export async function checkRecovery(): Promise<void> {
  if (asked || useApp.getState().dialog) return
  asked = true
  try {
    const { documents } = await query<{ documents: RecoverableDoc[] }>('recovery.list')
    if (documents.length > 0) useApp.getState().openDialog({ type: 'custom', id: 'recovery', data: { documents } })
  } catch {
    /* sin motor no hay nada que recuperar ahora; se intentará al volver */
  }
}

/** El motor se ha reiniciado: puede haber algo nuevo que recuperar. */
export function resetRecoveryCheck(): void {
  asked = false
}

function RecoveryDialog({ data }: CustomDialogProps) {
  const close = useApp((s) => s.closeDialog)
  const [items, setItems] = useState<RecoverableDoc[]>((data?.documents as RecoverableDoc[] | undefined) ?? [])
  const [working, setWorking] = useState<string | null>(null)
  const [passwords, setPasswords] = useState<Record<string, string>>({})
  const [errors, setErrors] = useState<Record<string, string>>({})

  const done = (id: string): void => {
    const left = items.filter((item) => item.id !== id)
    setItems(left)
    if (left.length === 0) close()
  }

  const recover = async (item: RecoverableDoc): Promise<void> => {
    setWorking(item.id)
    try {
      const outcome = unwrap(await window.folio.files.recover(item.id, item.encrypted ? passwords[item.id] ?? '' : undefined))
      if (outcome.ok) {
        handleOutcomes([outcome])
        done(item.id)
      } else {
        setErrors((current) => ({ ...current, [item.id]: outcome.error.message }))
      }
    } catch (error) {
      setErrors((current) => ({ ...current, [item.id]: errorMessage(error) }))
    } finally {
      setWorking(null)
    }
  }

  const discard = async (ids: string[]): Promise<void> => {
    for (const id of ids) await window.folio.engine('recovery.discard', { id })
    const left = items.filter((item) => !ids.includes(item.id))
    setItems(left)
    if (left.length === 0) close()
  }

  return (
    <Dialog
      title={L.shell.recovery.title}
      onClose={close}
      footer={
        <>
          <button className="btn ghost" disabled={working !== null} onClick={() => void discard(items.map((item) => item.id))}>
            {L.shell.recovery.discardAll}
          </button>
          <div className="spacer" />
          <button className="btn" disabled={working !== null} onClick={close}>
            {L.shell.recovery.later}
          </button>
        </>
      }
    >
      <p>{L.shell.recovery.text}</p>
      <div className="recovery-list">
        {items.map((item) => (
          <div key={item.id} className="recovery-item">
            <FileClock size={18} />
            <div className="grow">
              <div className="recovery-name">{item.name}</div>
              <div className="recovery-meta">
                {formatWhen(Date.parse(item.time))}
                {item.path ? ` · ${item.path}` : ''}
              </div>
              {item.encrypted && (
                <label className="recovery-password">
                  <KeyRound size={14} />
                  <input
                    className="input"
                    type="password"
                    placeholder={L.shell.recovery.password}
                    autoComplete="off"
                    maxLength={1024}
                    value={passwords[item.id] ?? ''}
                    onChange={(event) => setPasswords((current) => ({ ...current, [item.id]: event.target.value }))}
                  />
                </label>
              )}
              {errors[item.id] && <div className="error-text">{errors[item.id]}</div>}
            </div>
            <button className="btn ghost sm" disabled={working !== null} onClick={() => void discard([item.id])}>
              {L.shell.recovery.discard}
            </button>
            <button className="btn primary sm" disabled={working !== null} onClick={() => void recover(item)}>
              {L.shell.recovery.recover}
            </button>
          </div>
        ))}
      </div>
    </Dialog>
  )
}

registerDialog('recovery', RecoveryDialog)
