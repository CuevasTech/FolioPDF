import { Download, ExternalLink, Trash2, X } from 'lucide-react'
import { useState } from 'react'
import type { AiTier } from '@shared/ai'
import { Checkbox, Field, Segmented } from '@/components/ui'
import { fmt, L, locale } from '@/i18n'
import { updateSettings } from '@/state/actions'
import { refreshAi, sizeText, useAi } from '@/state/ai'
import { useApp } from '@/state/app'

/**
 * Ajustes → IA: elegir el nivel, descargar (o actualizar) los modelos, usar
 * la gráfica y borrarlo todo. Nada se descarga hasta que el usuario lo pide.
 */
export function AiSettings() {
  const settings = useApp((s) => s.settings)
  const status = useAi((s) => s.status)
  const progress = useAi((s) => s.progress)
  const error = useAi((s) => s.error)
  const [confirmRemove, setConfirmRemove] = useState(false)
  const T = L.ai.settings
  const size = (bytes: number): string => sizeText(bytes, locale)

  const tier = settings.aiTier
  // Hasta que el proceso principal cuenta el nivel nuevo, la lista de modelos es la del anterior.
  const components = status?.tier === tier ? status.components : []
  const pending = components.filter((c) => !c.installed).reduce((sum, c) => sum + c.size, 0)
  const installed = components.filter((c) => c.installed).reduce((sum, c) => sum + c.size, 0)
  const updating = components.some((c) => c.update)
  const downloading = Boolean(status?.downloading || progress)
  const cpuOk = status?.cpuOk !== false
  const lowMemory = tier === 'advanced' && status !== null && status.ramGb < 14

  const choose = (value: AiTier): void => {
    setConfirmRemove(false)
    useAi.setState({ error: null })
    void updateSettings({ aiTier: value }).then(() => refreshAi())
  }
  const download = (): void => {
    useAi.setState({ error: null })
    void window.folio.ai.install()
  }

  return (
    <>
      <Field label={T.title}>
        <span className="hint">{T.intro}</span>
      </Field>
      <Field label={T.level}>
        <Segmented<AiTier>
          value={tier}
          full
          options={[
            { value: 'off', label: T.off },
            { value: 'basic', label: T.basic },
            { value: 'advanced', label: T.advanced }
          ]}
          onChange={choose}
        />
        {tier === 'basic' && <span className="hint">{fmt(T.basicHint, { size: size(status?.tierSizes.basic ?? 0) })}</span>}
        {tier === 'advanced' && <span className="hint">{fmt(T.advancedHint, { size: size(status?.tierSizes.advanced ?? 0) })}</span>}
        {lowMemory && <span className="ai-warning">{fmt(T.lowMemory, { ram: Math.round(status?.ramGb ?? 0) })}</span>}
        {!cpuOk && <span className="error-text">{T.cpu}</span>}
      </Field>

      {tier !== 'off' && cpuOk && (
        <Field label={T.models}>
          <ul className="ai-models">
            {components.map((c) => (
              <li key={c.id}>
                <span className="grow">
                  {T.kinds[c.kind]} · {size(c.size)}
                </span>
                <span className={c.installed ? 'ok' : 'hint'}>{c.installed ? T.installed : c.update ? T.update : T.missing}</span>
                <button className="icon-btn sm" data-tip={`${T.license}: ${c.license}`} data-tip-side="left" aria-label={`${T.license}: ${c.license}`} onClick={() => void window.folio.shell.openExternal(c.licenseUrl)}>
                  <ExternalLink size={13} />
                </button>
              </li>
            ))}
          </ul>
          {downloading ? (
            <div className="ai-download">
              <div className="progress" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={progress ? Math.round((progress.done / Math.max(progress.total, 1)) * 100) : 0}>
                <div style={{ width: `${progress ? (progress.done / Math.max(progress.total, 1)) * 100 : 0}%` }} />
              </div>
              <div className="row">
                <span className="hint grow">{progress ? fmt(T.downloading, { done: size(progress.done), total: size(progress.total) }) : ''}</span>
                <button className="btn sm" onClick={() => void window.folio.ai.cancel()}>
                  <X size={13} />
                  {T.cancel}
                </button>
              </div>
            </div>
          ) : pending > 0 ? (
            <div className="row">
              <button className="btn sm primary" disabled={(status?.freeDisk ?? Infinity) < pending} onClick={download}>
                <Download size={14} />
                {fmt(updating ? T.updateButton : T.download, { size: size(pending) })}
              </button>
              {(status?.freeDisk ?? Infinity) < pending && <span className="error-text">{fmt(T.noDisk, { size: size(pending) })}</span>}
            </div>
          ) : components.length > 0 ? (
            <span className="ok">{T.ready}</span>
          ) : null}
          {error && <span className="error-text">{T.errors[error]}</span>}
        </Field>
      )}

      {tier !== 'off' && cpuOk && (
        <Field label="">
          <Checkbox checked={settings.aiGpu} onChange={(aiGpu) => void updateSettings({ aiGpu })}>
            {T.gpu}
          </Checkbox>
          <span className="hint">{T.gpuHint}</span>
          {status?.ready && <span className="hint">{status.gpu ? fmt(T.inUse, { device: status.gpu }) : T.inUseCpu}</span>}
        </Field>
      )}

      {installed > 0 && !downloading && (
        <Field label="">
          {confirmRemove ? (
            <div className="ai-confirm">
              <span>{fmt(T.removeConfirm, { size: size(installed) })}</span>
              <div className="row">
                <button
                  className="btn sm danger"
                  onClick={() => {
                    setConfirmRemove(false)
                    void window.folio.ai.remove().then(() => updateSettings({ aiTier: 'off' }))
                  }}
                >
                  {T.remove}
                </button>
                <button className="btn sm ghost" onClick={() => setConfirmRemove(false)}>
                  {L.common.cancel}
                </button>
              </div>
            </div>
          ) : (
            <button className="btn sm ghost" onClick={() => setConfirmRemove(true)}>
              <Trash2 size={14} />
              {T.remove}
            </button>
          )}
        </Field>
      )}

      {tier !== 'off' && <p className="hint ai-transparency">{T.transparency}</p>}
    </>
  )
}
