import { useEffect, useState } from 'react'

import { api, formatBytes, formatDuration, type ExportRow } from '../api'
import { CategoryTag, ScoreBadge } from '../components/ui'

export function Exports() {
  const [rows, setRows] = useState<ExportRow[]>([])
  const [error, setError] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)

  useEffect(() => {
    api.listExports().then(setRows).catch((e) => setError(e.message))
  }, [])

  const reveal = async (id: string) => {
    setError(null)
    try {
      const r = await api.revealExport(id)
      setNote(`Opened ${r.opened}`)
    } catch (e) {
      setError((e as Error).message)
    }
  }

  return (
    <div className="pt-12">
      <p className="eyebrow">Exported clips</p>
      <h1 className="mt-3 font-display text-5xl text-ink-100">
        {rows.length} <span className="italic text-sodium-500">ready</span> to review
      </h1>
      <p className="mt-3 max-w-2xl text-xs text-ink-500">
        Nothing is uploaded or published for you. Each clip has a JPG thumbnail and a JSON file (titles, description,
        hashtags, score breakdown) beside it. Only reuse footage you own or have permission to edit.
      </p>
      {error && <p className="mt-4 text-sm text-signal-bad">{error}</p>}
      {note && <p className="mt-4 text-xs text-ink-400">{note}</p>}

      <ul className="mt-8">
        {rows.map((row) => (
          <li key={row.id} className="grid grid-cols-[7rem_1fr_auto] gap-x-6 border-t border-ink-800 py-5">
            <div className="aspect-[9/16] w-28 overflow-hidden bg-ink-850">
              {row.exists ? (
                <video src={row.download_url} poster={row.has_thumbnail ? `/api/exports/${row.id}/thumbnail` : undefined} controls preload="none" className="h-full w-full object-cover" />
              ) : (
                <p className="p-2 text-xs text-signal-bad">File moved or deleted</p>
              )}
            </div>
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-3"><CategoryTag category={row.category} /></div>
              <p className="mt-2 text-ink-100">{row.title}</p>
              <p className="numeric mt-1 text-xs text-ink-400">
                {row.filename} · {row.resolution} · {formatDuration(row.duration_s)} · {formatBytes(row.size_bytes)}
              </p>
              <p className="mt-1 break-all text-xs text-ink-500">{row.path}</p>
              <p className="mt-2 text-xs text-sodium-400">{row.hashtags.join(' ')}</p>
            </div>
            <div className="flex flex-col items-end gap-3">
              <ScoreBadge score={row.score} />
              <a className="btn btn-primary" href={row.download_url} download={row.filename}>Download</a>
              <button className="btn btn-ghost" onClick={() => reveal(row.id)}>Open containing folder</button>
            </div>
          </li>
        ))}
        {rows.length === 0 && <li className="border-t border-ink-800 py-10 text-sm text-ink-400">No clips generated yet. Approve highlights, then generate them.</li>}
      </ul>
    </div>
  )
}
