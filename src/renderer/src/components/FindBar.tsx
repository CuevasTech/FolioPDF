import { CaseSensitive, ChevronDown, ChevronUp, Search, WholeWord, X } from 'lucide-react'
import { useEffect, useRef } from 'react'
import { L, fmt } from '@/i18n'
import { cn } from '@/lib/util'
import type { DocView } from '@/state/app'
import { closeSearch, setSearch, stepSearch } from '@/state/search'

export function FindBar({ doc }: { doc: DocView }) {
  const input = useRef<HTMLInputElement>(null)
  const search = doc.search

  useEffect(() => {
    input.current?.focus()
    input.current?.select()
  }, [])

  let status = ''
  if (search.busy) status = L.shell.findBar.searching
  else if (search.query.trim() && search.hits.length === 0) status = L.shell.findBar.noResults
  else if (search.hits.length > 0) status = fmt(search.truncated ? L.shell.findBar.countMore : L.shell.findBar.count, { n: search.index + 1, total: search.hits.length })

  return (
    <div className="toolbar find" role="search">
      <Search size={15} color="var(--text-3)" />
      <input
        ref={input}
        value={search.query}
        placeholder={L.shell.findBar.placeholder}
        spellCheck={false}
        onChange={(event) => setSearch(doc.id, { query: event.target.value })}
        onKeyDown={(event) => {
          if (event.key === 'Enter') stepSearch(doc.id, event.shiftKey ? -1 : 1)
          else if (event.key === 'Escape') closeSearch(doc.id)
        }}
      />
      <span className="count">{status}</span>
      <button
        className={cn('icon-btn sm', search.matchCase && 'on')}
        data-tip={L.shell.findBar.matchCase}
        onClick={() => setSearch(doc.id, { matchCase: !search.matchCase })}
      >
        <CaseSensitive size={16} />
      </button>
      <button
        className={cn('icon-btn sm', search.wholeWord && 'on')}
        data-tip={L.shell.findBar.wholeWord}
        onClick={() => setSearch(doc.id, { wholeWord: !search.wholeWord })}
      >
        <WholeWord size={16} />
      </button>
      <div className="sep" />
      <button className="icon-btn sm" data-tip={L.shell.findBar.previous} disabled={search.hits.length === 0} onClick={() => stepSearch(doc.id, -1)}>
        <ChevronUp size={16} />
      </button>
      <button className="icon-btn sm" data-tip={L.shell.findBar.next} disabled={search.hits.length === 0} onClick={() => stepSearch(doc.id, 1)}>
        <ChevronDown size={16} />
      </button>
      <button className="icon-btn sm" data-tip={L.common.close} onClick={() => closeSearch(doc.id)}>
        <X size={15} />
      </button>
    </div>
  )
}
