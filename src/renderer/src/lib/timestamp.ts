import type { Settings, TimestampServer } from '@shared/types'
import { L, fmt } from '@/i18n'

/**
 * Servicios públicos de sello de tiempo (RFC 3161), gratuitos y reconocidos por
 * Windows. Que sean http no resta seguridad: la respuesta va firmada por la
 * autoridad de sellado y el motor comprueba esa firma y su cadena antes de
 * aceptarla. Solo se les envía la huella de la firma, nunca el documento.
 */
export const TIMESTAMP_SERVERS: { value: TimestampServer; label: string; name: string; url: string }[] = [
  { value: 'digicert', label: fmt(L.state.timestamp.recommended, { name: 'DigiCert' }), name: 'DigiCert', url: 'http://timestamp.digicert.com' },
  { value: 'globalsign', label: 'GlobalSign', name: 'GlobalSign', url: 'http://timestamp.globalsign.com/tsa/r6advanced1' },
  { value: 'sectigo', label: 'Sectigo', name: 'Sectigo', url: 'http://timestamp.sectigo.com' },
  { value: 'custom', label: L.state.timestamp.other, name: '', url: '' }
]

/** Dirección del servicio de sello que está elegido ('' si es «otro» y no se ha escrito). */
export function timestampUrlOf(settings: Pick<Settings, 'timestampServer' | 'timestampUrl'>): string {
  if (settings.timestampServer === 'custom') return settings.timestampUrl.trim()
  return TIMESTAMP_SERVERS.find((server) => server.value === settings.timestampServer)?.url ?? ''
}

/** Nombre corto del servicio elegido, para explicarlo en el diálogo de firma. */
export function timestampNameOf(settings: Pick<Settings, 'timestampServer' | 'timestampUrl'>): string {
  if (settings.timestampServer === 'custom') {
    try {
      return new URL(settings.timestampUrl).host
    } catch {
      return L.state.timestamp.configured
    }
  }
  return TIMESTAMP_SERVERS.find((server) => server.value === settings.timestampServer)?.name ?? ''
}
