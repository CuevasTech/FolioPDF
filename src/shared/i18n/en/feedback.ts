import type { feedback as source } from '../es/feedback'

export const feedback: typeof source = {
  title: 'Send feedback',
  kind: {
    bug: 'A bug',
    idea: 'An idea',
    other: 'Something else'
  },
  placeholder: {
    bug: 'What happened, and what were you doing?',
    idea: 'What would you like Folio to do?',
    other: 'Tell us whatever you like.'
  },
  message: 'Comment',
  hint: 'It goes to CuevasTech, along with your Folio and Windows versions. Don’t include personal information or anything from your documents.',
  send: 'Send',
  sending: 'Sending…',
  thanks: 'Thank you! We’ve received your feedback',
  viewOnGitHub: 'View on GitHub',
  openGitHub: 'Open GitHub',
  githubText: 'Tell us about bugs and ideas for improvements on Folio’s GitHub page (you’ll need a GitHub account).'
}
