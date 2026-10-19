import '@fontsource-variable/inter'
import './styles/tokens.css'
import './styles/base.css'
import './styles/shell.css'
import './styles/viewer.css'
import './styles/modes.css'
import { createRoot } from 'react-dom/client'
import { App } from './App'
import { lang } from './i18n'
import './modes'

// Idioma del documento: lectores de pantalla, guiones y corrector del sistema.
document.documentElement.lang = lang

const root = document.getElementById('root')
if (root) createRoot(root).render(<App />)
