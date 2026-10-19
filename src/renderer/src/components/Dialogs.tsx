import { FileText, KeyRound, Lock, Palette, Settings2, Signature, Sparkles } from 'lucide-react'
import { type KeyboardEvent, type ReactNode, useState } from 'react'
import type { LangSetting } from '@shared/i18n'
import type { DateStyle, SignLevel, TimestampServer, UpdateStatus } from '@shared/types'
import { fmt, L, locale } from '@/i18n'
import { DATE_STYLES, formatDateStyle } from '@/lib/dates'
import { TIMESTAMP_SERVERS } from '@/lib/timestamp'
import { formatBytes, formatDate } from '@/lib/util'
import { mutate, submitPassword, unlockDoc, updateSettings } from '@/state/actions'
import { type ConfirmDialog, type DialogState, useApp } from '@/state/app'
import { chooseFolioAsDefault, useDefaultApp } from '@/state/defaultApp'
import { useUpdates } from '@/state/updates'
import { takeSettingsTab } from '@/modes/ai'
import { AiSettings } from '@/modes/ai/settings'
import { Checkbox, Dialog, Field, Logo, Segmented } from './ui'

function Confirm({ dialog }: { dialog: ConfirmDialog }) {
  const close = useApp((s) => s.closeDialog)
  const [working, setWorking] = useState(false)
  const [option, setOption] = useState(dialog.option?.value ?? false)
  const run = async (fn: () => void | Promise<void>): Promise<void> => {
    setWorking(true)
    // Se cierra antes de la acción, que puede abrir otro diálogo (y solo si sigue siendo este).
    if (useApp.getState().dialog === dialog) close()
    await fn()
  }
  const cancel = (): void => {
    close()
    dialog.onCancel?.()
  }
  return (
    <Dialog
      title={dialog.title}
      onClose={cancel}
      footer={
        <>
          {dialog.alt && (
            <button className="btn ghost" disabled={working} onClick={() => void run(() => dialog.alt!.run(option))}>
              {dialog.alt.label}
            </button>
          )}
          <div className="spacer" />
          <button className="btn" disabled={working} onClick={cancel}>
            {dialog.cancelLabel ?? L.common.cancel}
          </button>
          <button
            className={dialog.danger ? 'btn danger' : 'btn primary'}
            disabled={working}
            onClick={() => void run(() => dialog.onConfirm(option))}
          >
            {dialog.confirmLabel}
          </button>
        </>
      }
    >
      <p>{dialog.message}</p>
      {dialog.option && (
        <div className="confirm-option">
          <Checkbox checked={option} onChange={setOption}>
            {dialog.option.label}
          </Checkbox>
          {dialog.option.hint && <p className="hint">{dialog.option.hint}</p>}
        </div>
      )}
    </Dialog>
  )
}

function Password({ token, name, wrong }: { token: string; name: string; wrong: boolean }) {
  const close = useApp((s) => s.closeDialog)
  const [value, setValue] = useState('')
  const [working, setWorking] = useState(false)
  const submit = async (): Promise<void> => {
    if (!value || working) return
    setWorking(true)
    await submitPassword(token, value)
    setWorking(false)
    setValue('')
  }
  return (
    <Dialog
      title={L.dialogs.password.title}
      icon={<Lock size={18} color="var(--accent-fg)" />}
      onClose={close}
      footer={
        <>
          <button className="btn" onClick={close}>
            {L.common.cancel}
          </button>
          <button className="btn primary" disabled={!value || working} onClick={() => void submit()}>
            {L.common.open}
          </button>
        </>
      }
    >
      <p>{fmt(L.dialogs.password.message, { name })}</p>
      <Field label={L.dialogs.password.label}>
        <input
          className="input"
          type="password"
          autoComplete="off"
          value={value}
          onChange={(event) => setValue(event.target.value)}
          onKeyDown={(event) => event.key === 'Enter' && void submit()}
        />
      </Field>
      {wrong && <span className="error-text">{L.dialogs.password.wrong}</span>}
    </Dialog>
  )
}

function Unlock({ doc, retry }: { doc: string; retry?: () => void }) {
  const close = useApp((s) => s.closeDialog)
  const [value, setValue] = useState('')
  const [wrong, setWrong] = useState(false)
  const [working, setWorking] = useState(false)
  const submit = async (): Promise<void> => {
    if (!value || working) return
    setWorking(true)
    const ok = await unlockDoc(doc, value)
    setWorking(false)
    if (ok) {
      close()
      retry?.()
    } else {
      setWrong(true)
      setValue('')
    }
  }
  return (
    <Dialog
      title={L.dialogs.unlock.title}
      icon={<KeyRound size={18} color="var(--accent-fg)" />}
      onClose={close}
      footer={
        <>
          <button className="btn" onClick={close}>
            {L.common.cancel}
          </button>
          <button className="btn primary" disabled={!value || working} onClick={() => void submit()}>
            {L.dialogs.unlock.action}
          </button>
        </>
      }
    >
      <p>{L.dialogs.unlock.message}</p>
      <Field label={L.dialogs.unlock.label}>
        <input
          className="input"
          type="password"
          autoComplete="off"
          value={value}
          onChange={(event) => {
            setValue(event.target.value)
            setWrong(false)
          }}
          onKeyDown={(event) => event.key === 'Enter' && void submit()}
        />
      </Field>
      {wrong && <span className="error-text">{L.dialogs.unlock.wrong}</span>}
    </Dialog>
  )
}

const ACCENTS = ['#4f5bd5', '#2f7fe8', '#0f9d8a', '#2e9e55', '#d9822b', '#d9484f', '#b0479a', '#5b6472']

type SettingsTab = 'general' | 'appearance' | 'documents' | 'signature' | 'ai'

const SETTINGS_TABS: { id: SettingsTab; label: string; icon: ReactNode }[] = [
  { id: 'general', label: L.dialogs.settings.tabs.general, icon: <Settings2 size={16} /> },
  { id: 'appearance', label: L.dialogs.settings.tabs.appearance, icon: <Palette size={16} /> },
  { id: 'documents', label: L.dialogs.settings.tabs.documents, icon: <FileText size={16} /> },
  { id: 'signature', label: L.dialogs.settings.tabs.signature, icon: <Signature size={16} /> },
  { id: 'ai', label: L.ai.settings.tab, icon: <Sparkles size={16} /> }
]

/** La pestaña de la última vez: se vuelve a ella mientras Folio siga abierto. */
let lastSettingsTab: SettingsTab = 'general'

function SettingsDialog() {
  const close = useApp((s) => s.closeDialog)
  // El panel de la IA puede pedir que se abra directamente en su pestaña.
  const [tab, setTab] = useState<SettingsTab>(() => (takeSettingsTab() as SettingsTab | null) ?? lastSettingsTab)
  const choose = (next: SettingsTab): void => {
    lastSettingsTab = next
    setTab(next)
  }
  // Pestañas en vertical: flechas arriba y abajo, Inicio y Fin.
  const onTabKey = (event: KeyboardEvent<HTMLDivElement>): void => {
    const index = SETTINGS_TABS.findIndex((item) => item.id === tab)
    const last = SETTINGS_TABS.length - 1
    const target =
      event.key === 'ArrowDown' ? (index + 1) % SETTINGS_TABS.length
      : event.key === 'ArrowUp' ? (index + last) % SETTINGS_TABS.length
      : event.key === 'Home' ? 0
      : event.key === 'End' ? last
      : -1
    if (target < 0) return
    event.preventDefault()
    const next = SETTINGS_TABS[target].id
    choose(next)
    event.currentTarget.querySelector<HTMLElement>(`#settings-tab-${next}`)?.focus()
  }
  return (
    <Dialog
      title={L.dialogs.settings.title}
      className="settings-dialog"
      onClose={close}
      footer={
        <button className="btn primary" onClick={close}>
          {L.dialogs.settings.done}
        </button>
      }
    >
      <div className="settings-tabs" role="tablist" aria-orientation="vertical" aria-label={L.dialogs.settings.sections} onKeyDown={onTabKey}>
        {SETTINGS_TABS.map((item) => (
          <button
            key={item.id}
            id={`settings-tab-${item.id}`}
            className="settings-tab"
            role="tab"
            aria-selected={item.id === tab}
            aria-controls="settings-panel"
            tabIndex={item.id === tab ? 0 : -1}
            data-autofocus={item.id === tab ? '' : undefined}
            onClick={() => choose(item.id)}
          >
            {item.icon}
            {item.label}
          </button>
        ))}
      </div>
      <div className="settings-panel" id="settings-panel" role="tabpanel" aria-labelledby={`settings-tab-${tab}`}>
        {tab === 'general' && (
          <>
            <LanguageSettings />
            <DefaultAppSettings />
            <UpdatesSettings />
          </>
        )}
        {tab === 'appearance' && <AppearanceSettings />}
        {tab === 'documents' && <DocumentSettings />}
        {tab === 'signature' && <SignatureSettings />}
        {tab === 'ai' && <AiSettings />}
      </div>
    </Dialog>
  )
}

/** El idioma de Folio: se fija al abrirlo, así que el cambio vale para la próxima vez. */
function LanguageSettings() {
  const language = useApp((s) => s.settings.language)
  const T = L.dialogs.settings.language
  return (
    <Field label={T.label} hint={T.hint}>
      <Segmented<LangSetting>
        value={language}
        options={[
          { value: 'auto', label: T.auto },
          { value: 'es', label: T.es },
          { value: 'en', label: T.en }
        ]}
        onChange={(value) => void updateSettings({ language: value })}
      />
    </Field>
  )
}

function AppearanceSettings() {
  const settings = useApp((s) => s.settings)
  const T = L.dialogs.settings
  return (
    <>
      <Field label={T.theme.label}>
        <Segmented
          value={settings.theme}
          options={[
            { value: 'system', label: T.theme.system },
            { value: 'light', label: T.theme.light },
            { value: 'dark', label: T.theme.dark }
          ]}
          onChange={(theme) => void updateSettings({ theme })}
        />
      </Field>
      <Field label={T.accent.label}>
        <div className="row">
          {ACCENTS.map((color) => (
            <button
              key={color}
              aria-label={fmt(T.accent.color, { color })}
              onClick={() => void updateSettings({ accent: color })}
              style={{
                width: 26,
                height: 26,
                borderRadius: '50%',
                background: color,
                boxShadow: settings.accent === color ? `0 0 0 2px var(--bg-surface), 0 0 0 4px ${color}` : 'none'
              }}
            />
          ))}
        </div>
      </Field>
    </>
  )
}

function DocumentSettings() {
  const settings = useApp((s) => s.settings)
  const T = L.dialogs.settings
  return (
    <>
      <Field label={T.zoom.label}>
        <Segmented
          value={settings.defaultZoom}
          options={[
            { value: 'fit-width', label: T.zoom.fitWidth },
            { value: 'fit-page', label: T.zoom.fitPage },
            { value: 'actual', label: T.zoom.actual }
          ]}
          onChange={(defaultZoom) => void updateSettings({ defaultZoom })}
        />
      </Field>
      <Checkbox checked={settings.sidebarOpen} onChange={(sidebarOpen) => void updateSettings({ sidebarOpen })}>
        {T.sidebarOpen}
      </Checkbox>
      <Checkbox checked={settings.highlightFields} onChange={(highlightFields) => void updateSettings({ highlightFields })}>
        {T.highlightFields}
      </Checkbox>
      <Field label={T.author.label} hint={T.author.hint}>
        <input
          className="input"
          defaultValue={settings.author}
          maxLength={120}
          onBlur={(event) => void updateSettings({ author: event.target.value.trim() })}
        />
      </Field>
    </>
  )
}

/** Los tipos de firma con certificado, como se llaman al firmar (ver SignLevel). */
const SIGN_LEVEL_OPTIONS: { value: SignLevel; label: string }[] = [
  { value: 'basic', label: L.dialogs.settings.signLevel.basic },
  { value: 'timestamp', label: L.dialogs.settings.signLevel.timestamp },
  { value: 'longTerm', label: L.dialogs.settings.signLevel.longTerm }
]

function SignatureSettings() {
  const settings = useApp((s) => s.settings)
  const T = L.dialogs.settings
  return (
    <>
      <Field label={T.signLevel.label} hint={T.signLevel.hint}>
        <Segmented value={settings.signLevel} options={SIGN_LEVEL_OPTIONS} onChange={(signLevel) => void updateSettings({ signLevel })} />
      </Field>
      <Field label={T.timestamp.label} hint={T.timestamp.hint}>
        <div className="stack">
          <select
            className="select"
            aria-label={T.timestamp.label}
            value={settings.timestampServer}
            onChange={(event) => void updateSettings({ timestampServer: event.target.value as TimestampServer })}
          >
            {TIMESTAMP_SERVERS.map((server) => (
              <option key={server.value} value={server.value}>
                {server.label}
              </option>
            ))}
          </select>
          {settings.timestampServer === 'custom' && (
            <input
              className="input"
              defaultValue={settings.timestampUrl}
              placeholder={T.timestamp.urlPlaceholder}
              aria-label={T.timestamp.urlLabel}
              maxLength={300}
              spellCheck={false}
              onBlur={(event) => void updateSettings({ timestampUrl: event.target.value.trim() })}
            />
          )}
        </div>
      </Field>
      <Field label={T.dateStyle.label} hint={T.dateStyle.hint}>
        <select
          className="select"
          aria-label={T.dateStyle.label}
          value={settings.dateStyle}
          onChange={(event) => void updateSettings({ dateStyle: event.target.value as DateStyle })}
        >
          {DATE_STYLES.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label} · {formatDateStyle(option.value)}
            </option>
          ))}
        </select>
      </Field>
    </>
  )
}

function DefaultAppSettings() {
  const status = useDefaultApp((s) => s.status)
  const ask = useApp((s) => s.settings.askDefaultPdf)
  if (status === 'unknown') return null
  const T = L.dialogs.settings.defaultApp
  return (
    <Field label={T.label}>
      <div className="row">
        {status === 'default' ? (
          <span className="hint">{T.isDefault}</span>
        ) : (
          <>
            <button className="btn sm" onClick={() => void chooseFolioAsDefault()}>
              {T.use}
            </button>
            <span className="hint">{T.confirmHint}</span>
          </>
        )}
      </div>
      {status !== 'default' && (
        <Checkbox checked={ask} onChange={(askDefaultPdf) => void updateSettings({ askDefaultPdf })}>
          {T.ask}
        </Checkbox>
      )}
    </Field>
  )
}

const UPDATE_TEXT: Partial<Record<UpdateStatus['state'], string>> = {
  unavailable: L.dialogs.settings.updates.unavailable,
  checking: L.dialogs.settings.updates.checking,
  current: L.dialogs.settings.updates.current,
  downloading: L.dialogs.settings.updates.downloading,
  ready: L.dialogs.settings.updates.ready
}

function UpdatesSettings() {
  const settings = useApp((s) => s.settings)
  const status = useUpdates((s) => s.status)
  const T = L.dialogs.settings.updates
  const text =
    status.state === 'available'
      ? status.version && status.version !== '?'
        ? fmt(T.availableVersion, { version: status.version })
        : T.available
      : status.state === 'error'
        ? (status.message ?? '')
        : (UPDATE_TEXT[status.state] ?? '')
  const busy = ['unavailable', 'checking', 'downloading', 'ready'].includes(status.state)
  return (
    <Field label={T.label}>
      <Checkbox checked={settings.checkUpdates} onChange={(checkUpdates) => void updateSettings({ checkUpdates })}>
        {T.check}
      </Checkbox>
      <div className="row">
        <button className="btn sm" disabled={busy} onClick={() => window.folio.updates.check()}>
          {T.checkNow}
        </button>
        {text && <span className="hint">{text}</span>}
      </div>
    </Field>
  )
}

/**
 * Acerca de Folio, con los avisos legales que la AGPL pide mostrar en una
 * aplicación con interfaz: titular, software libre sin garantía, y cómo ver la
 * licencia y obtener el código.
 */
function AboutDialog() {
  const close = useApp((s) => s.closeDialog)
  const { version, built } = window.folio.env
  const date = built
    ? new Date(`${built}T12:00:00`).toLocaleDateString(locale, { day: 'numeric', month: 'long', year: 'numeric' })
    : ''
  const T = L.dialogs.about
  return (
    <Dialog
      title={T.title}
      onClose={close}
      footer={
        <button className="btn primary" onClick={close}>
          {L.common.close}
        </button>
      }
    >
      <div className="about">
        <div className="about-head">
          <div className="start-mark about-mark">
            <Logo size={24} />
          </div>
          <div>
            <b>Folio {version}</b>
            <span>{T.tagline}</span>
            {date && <span>{fmt(T.built, { date })}</span>}
          </div>
        </div>
        <p>Copyright © 2026 CuevasTech.</p>
        <p>{T.license}</p>
        <div className="row">
          <button className="btn sm" onClick={() => void window.folio.about.open('license')}>
            {T.licenseButton}
          </button>
          <button className="btn sm" onClick={() => void window.folio.about.open('source')}>
            {T.source}
          </button>
          <button className="btn sm" onClick={() => void window.folio.about.open('notices')}>
            {T.notices}
          </button>
        </div>
        <div className="about-support">
          <p>{T.support}</p>
          <div className="row">
            <button className="btn sm" onClick={() => void window.folio.about.open('sponsors')}>
              {T.coffee}
            </button>
          </div>
        </div>
      </div>
    </Dialog>
  )
}
function PropertiesDialog({ doc: docId }: { doc: string }) {
  const close = useApp((s) => s.closeDialog)
  const doc = useApp((s) => s.docs[docId])
  const [meta, setMeta] = useState(() => ({
    title: doc?.meta.title ?? '',
    author: doc?.meta.author ?? '',
    subject: doc?.meta.subject ?? '',
    keywords: doc?.meta.keywords ?? ''
  }))
  if (!doc) return null
  const changed =
    meta.title !== doc.meta.title ||
    meta.author !== doc.meta.author ||
    meta.subject !== doc.meta.subject ||
    meta.keywords !== doc.meta.keywords
  const save = async (): Promise<void> => {
    const result = await mutate<{ state: typeof doc.state; changed: []; meta: typeof doc.meta }>(docId, 'doc.setMetadata', meta)
    if (result) {
      useApp.getState().patchDoc(docId, { meta: result.meta })
      close()
    }
  }
  const T = L.dialogs.properties
  const rows: [string, string][] = [
    [T.rows.file, doc.state.path ?? T.notSaved],
    [L.common.pages, String(doc.pages.length)],
    [T.rows.size, formatBytes(doc.info.fileSize)],
    [T.rows.format, doc.info.format],
    [T.rows.created, formatDate(doc.meta.created)],
    [T.rows.modified, formatDate(doc.meta.modified)],
    [T.rows.creator, doc.meta.creator || '—'],
    [T.rows.producer, doc.meta.producer || '—'],
    [T.rows.protection, doc.info.encrypted ? T.encrypted : T.notEncrypted],
    [T.rows.signatures, doc.state.signed ? L.common.yes : L.common.no]
  ]
  return (
    <Dialog
      title={T.title}
      size="wide"
      onClose={close}
      footer={
        <>
          <button className="btn" onClick={close}>
            {L.common.close}
          </button>
          <button className="btn primary" disabled={!changed} onClick={() => void save()}>
            {T.saveChanges}
          </button>
        </>
      }
    >
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
        <Field label={T.fields.title}>
          <input className="input" value={meta.title} maxLength={500} onChange={(e) => setMeta({ ...meta, title: e.target.value })} />
        </Field>
        <Field label={T.fields.author}>
          <input className="input" value={meta.author} maxLength={500} onChange={(e) => setMeta({ ...meta, author: e.target.value })} />
        </Field>
        <Field label={T.fields.subject}>
          <input className="input" value={meta.subject} maxLength={500} onChange={(e) => setMeta({ ...meta, subject: e.target.value })} />
        </Field>
        <Field label={T.fields.keywords}>
          <input className="input" value={meta.keywords} maxLength={500} onChange={(e) => setMeta({ ...meta, keywords: e.target.value })} />
        </Field>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: '130px 1fr', gap: '7px 12px', fontSize: 12.5 }}>
        {rows.map(([label, value]) => (
          <div key={label} style={{ display: 'contents' }}>
            <span style={{ color: 'var(--text-3)' }}>{label}</span>
            <span style={{ color: 'var(--text)', overflowWrap: 'anywhere' }}>{value}</span>
          </div>
        ))}
      </div>
    </Dialog>
  )
}

const SC = L.dialogs.shortcuts
const SHORTCUTS: [string, string][] = [
  ['Ctrl + O', L.common.open],
  ['Ctrl + S', L.common.save],
  [SC.keys.saveAs, SC.saveAs],
  ['Ctrl + P', SC.print],
  ['Ctrl + W', SC.closeDoc],
  ['Ctrl + Tab', SC.nextDoc],
  ['Ctrl + Z / Ctrl + Y', SC.undoRedo],
  ['Ctrl + F', SC.find],
  [SC.keys.wheel, SC.zoom],
  ['Ctrl + 0 / Ctrl + 1', SC.fit],
  [SC.keys.delete, SC.deleteSelection],
  [SC.keys.arrows, SC.moveSelection],
  [SC.keys.escape, SC.escape]
]

function ShortcutsDialog() {
  const close = useApp((s) => s.closeDialog)
  return (
    <Dialog
      title={SC.title}
      onClose={close}
      footer={
        <button className="btn primary" onClick={close}>
          {L.common.close}
        </button>
      }
    >
      <div style={{ display: 'grid', gridTemplateColumns: 'auto 1fr', gap: '9px 18px', alignItems: 'center' }}>
        {SHORTCUTS.map(([keys, label]) => (
          <div key={keys} style={{ display: 'contents' }}>
            <span className="kbd" style={{ justifySelf: 'start', padding: '2px 7px' }}>
              {keys}
            </span>
            <span style={{ color: 'var(--text)' }}>{label}</span>
          </div>
        ))}
      </div>
    </Dialog>
  )
}

/** Diálogos propios de cada función; se registran desde sus módulos. */
export interface CustomDialogProps {
  doc?: string
  data?: Record<string, unknown>
}

const customDialogs = new Map<string, (props: CustomDialogProps) => ReactNode>()

export function registerDialog(id: string, component: (props: CustomDialogProps) => ReactNode): void {
  customDialogs.set(id, component)
}

function render(dialog: DialogState): ReactNode {
  switch (dialog.type) {
    case 'confirm':
      return <Confirm dialog={dialog} />
    case 'password':
      return <Password key={dialog.token} token={dialog.token} name={dialog.name} wrong={dialog.wrong} />
    case 'unlock':
      return <Unlock doc={dialog.doc} retry={dialog.retry} />
    case 'settings':
      return <SettingsDialog />
    case 'properties':
      return <PropertiesDialog doc={dialog.doc} />
    case 'shortcuts':
      return <ShortcutsDialog />
    case 'about':
      return <AboutDialog />
    case 'custom': {
      const Custom = customDialogs.get(dialog.id)
      return Custom ? <Custom doc={dialog.doc} data={dialog.data} /> : null
    }
  }
}

export function DialogHost() {
  const dialog = useApp((s) => s.dialog)
  return dialog ? <>{render(dialog)}</> : null
}
