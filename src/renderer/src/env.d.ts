/// <reference types="vite/client" />
import type { FolioApi } from '@shared/api'

declare global {
  interface Window {
    folio: FolioApi
  }
}

export {}
