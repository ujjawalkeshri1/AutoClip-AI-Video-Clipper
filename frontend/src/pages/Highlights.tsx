import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'

import { api, formatDuration, type BatchState, type Clip, type FramingMode, type MusicTrack } from '../api'
import { Bar, CATEGORY_LABELS, CategoryTag, FACTOR_LABELS, ScoreBadge, timeLabel } from '../components/ui'

const FRAMING: { key: FramingMode; label: string; note: string }[] = [
  { key: 'auto', label: 'Auto', note: 'Crops only when the HUD and action fit; otherwise whole frame over blur' },
  { key: 'blur_fit', label: 'Whole frame', note: 'Nothing cut off, shown over a blurred copy' },
  { key: 'motion_crop', label: 'Follow action', note: 'A vertical window that follows the motion' },
  { key: 'center', label: 'Centre crop', note: 'Plain centred crop' },
]

export function Highlights() {
  const [params] = useSearchParams()
  const jobId = params.get('job') ?? undefined
  const [clips, setClips] = useState<Clip[]>([])
  const [category, setCategory] = useState('')
  const [status, setStatus] = useState('')
  const [minScore, setMinScore] = useState(0)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [framing, setFraming] = useState<FramingMode>('auto')
  const [hook, setHook] = useState(false)
  const [tracks, setTracks] = useState<MusicTrack[]>([])
  const [musicId, setMusicId] = useState<string>('') // '' = saved default, 'none' = no music
  useEffect(() => { api.listMusic().then(setTracks).catch(() => undefined) }, [])
  const [batch, setBatch] = useState<BatchState | null>(null)
  const [previewId, setPreviewId] = useState<string | null>(null)
  const [openId, setOpenId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const poll = useRef<number | undefined>(undefined)

  const load = useCallback(() => {
    api
      .highlights({ job_id: jobId, category, status, min_score: minScore })
      .then(setClips)
      .catch((e) => setError(e.message))
  }, [jobId, category, status, minScore])
  useEffect(load, [load])
  useEffect(() => () => window.clearInterval(poll.current), [])

  const toggle = (id: string) =>
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })

  const setStatusFor = async (ids: string[], action: 'approve' | 'reject' | 'reset') => {
    await api.batchAction(ids, action)
    load()
  }

  const generate = async (ids: string[]) => {
    setError(null)
    try {
      const state = await api.batchExport({ clip_ids: ids, framing, hook_text: hook, ...(musicId ? { music_id: musicId } : {}) })
      setBatch(state)
      window.clearInterval(poll.current)
      poll.current = window.setInterval(async () => {
        const s = await api.batchStatus(state.id)
        setBatch(s)
        if (s.finished) {
          window.clearInterval(poll.current)
          load()
        }
      }, 1200)
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const categories = useMemo(() => Object.keys(CATEGORY_LABELS), [])
  const approved = clips.filter((c) => c.status === 'kept').map((c) => c.id)
  const chosen = [...selected]

  return (
    <div className="pt-12">
      <p className="eyebrow">Detected highlights</p>
      <h1 className="mt-3 font-display text-5xl text-ink-100">
        {clips.length} <span className="italic text-sodium-500">moments</span> found
      </h1>
      <p className="mt-3 max-w-2xl text-xs text-ink-500">
        Scores estimate entertainment and engagement potential from audio, motion, speech and optional vision evidence.
        They are not predictions of views. Review every clip; reject what you do not want.
      </p>

      {/* Filters and batch controls */}
      <div className="mt-8 flex flex-wrap items-end gap-x-8 gap-y-4 border-y border-ink-800 py-4 text-sm">
        <label className="block">
          <span className="eyebrow">Category</span>
          <select className="field mt-1 w-44 cursor-pointer" value={category} onChange={(e) => setCategory(e.target.value)}>
            <option value="" className="bg-ink-850">All</option>
            {categories.map((c) => <option key={c} value={c} className="bg-ink-850">{CATEGORY_LABELS[c]}</option>)}
          </select>
        </label>
        <label className="block">
          <span className="eyebrow">Status</span>
          <select className="field mt-1 w-36 cursor-pointer" value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="" className="bg-ink-850">All</option>
            <option value="candidate" className="bg-ink-850">Needs review</option>
            <option value="kept" className="bg-ink-850">Approved</option>
            <option value="discarded" className="bg-ink-850">Rejected</option>
            <option value="exported" className="bg-ink-850">Generated</option>
          </select>
        </label>
        <label className="block">
          <span className="eyebrow">Min score · <span className="numeric">{minScore}</span></span>
          <input type="range" min={0} max={90} step={5} value={minScore} onChange={(e) => setMinScore(+e.target.value)} className="mt-3 block w-36 accent-[var(--color-sodium-500)]" />
        </label>
        <label className="block">
          <span className="eyebrow">Vertical framing</span>
          <select className="field mt-1 w-40 cursor-pointer" value={framing} onChange={(e) => setFraming(e.target.value as FramingMode)} title={FRAMING.find((f) => f.key === framing)?.note}>
            {FRAMING.map((f) => <option key={f.key} value={f.key} className="bg-ink-850">{f.label}</option>)}
          </select>
        </label>
        <label className="block">
          <span className="eyebrow">Background music</span>
          <select className="field mt-1 w-44 cursor-pointer" value={musicId} onChange={(e) => setMusicId(e.target.value)}>
            <option value="" className="bg-ink-850">Saved default</option>
            <option value="none" className="bg-ink-850">None</option>
            {tracks.map((t) => <option key={t.id} value={t.id} className="bg-ink-850">{t.name}</option>)}
          </select>
        </label>
        <label className="flex items-center gap-2 pb-2 text-xs text-ink-300">
          <input type="checkbox" checked={hook} onChange={(e) => setHook(e.target.checked)} /> Opening hook text
        </label>
        <div className="ml-auto flex flex-wrap gap-2">
          <button className="btn btn-ghost" disabled={!chosen.length} onClick={() => setStatusFor(chosen, 'approve')}>Approve selected</button>
          <button className="btn btn-ghost" disabled={!chosen.length} onClick={() => setStatusFor(chosen, 'reject')}>Reject selected</button>
          <button className="btn btn-primary" disabled={!chosen.length || (!!batch && !batch.finished)} onClick={() => generate(chosen)}>
            Generate {chosen.length || ''} selected
          </button>
          <button className="btn btn-primary" disabled={!approved.length || (!!batch && !batch.finished)} onClick={() => generate(approved)}>
            Generate all approved ({approved.length})
          </button>
        </div>
      </div>

      {batch && (
        <div className="mt-4">
          <Bar value={batch.done} max={batch.total} />
          <p className="mt-2 text-xs text-ink-400">
            {batch.finished ? 'Finished' : `Rendering “${batch.current}”`} · <span className="numeric">{batch.done}/{batch.total}</span>
            {batch.finished && <> · <Link className="text-sodium-400 underline" to="/exports">View exported clips</Link></>}
          </p>
          {batch.errors.map((e) => <p key={e.clip_id} className="mt-1 text-xs text-signal-bad">{e.error}</p>)}
        </div>
      )}
      {error && <p className="mt-4 text-sm text-signal-bad">{error}</p>}

      <ul className="mt-2">
        {clips.map((clip) => (
          <li key={clip.id} className="grid grid-cols-[1.5rem_11rem_1fr_auto] gap-x-5 border-b border-ink-850 py-5">
            <input type="checkbox" className="mt-1" checked={selected.has(clip.id)} onChange={() => toggle(clip.id)} aria-label={`Select ${clip.title}`} />

            <button className="group relative block aspect-video w-44 overflow-hidden bg-ink-850" onClick={() => setPreviewId(previewId === clip.id ? null : clip.id)} title="Preview this moment">
              {previewId === clip.id ? (
                <video
                  src={`${api.mediaUrl(clip.job_id)}#t=${clip.start_s},${clip.end_s}`}
                  controls autoPlay className="h-full w-full object-cover"
                  onTimeUpdate={(e) => {
                    const v = e.currentTarget
                    if (v.currentTime >= clip.end_s) v.currentTime = clip.start_s
                  }}
                />
              ) : (
                <>
                  <img src={api.thumbnailUrl(clip.id)} alt="" loading="lazy" className="h-full w-full object-cover opacity-90 transition group-hover:opacity-100" />
                  <span className="absolute inset-0 grid place-items-center text-xs font-semibold uppercase tracking-wider text-ink-100 opacity-0 transition group-hover:opacity-100">▶ Preview</span>
                </>
              )}
            </button>

            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-3">
                <CategoryTag category={clip.category} />
                {clip.meta.confidence !== undefined && clip.meta.confidence < 0.55 && <span className="text-[0.6875rem] text-ink-500">low confidence label</span>}
                <span className="numeric text-xs text-ink-400">{timeLabel(clip.start_s)}–{timeLabel(clip.end_s)} · {formatDuration(clip.duration_s)}</span>
                <span className={`text-xs ${clip.status === 'kept' ? 'text-signal-good' : clip.status === 'discarded' ? 'text-signal-bad' : clip.status === 'exported' ? 'text-sodium-400' : 'text-ink-500'}`}>
                  {{ candidate: 'needs review', kept: 'approved', discarded: 'rejected', exported: 'generated' }[clip.status]}
                </span>
              </div>
              <p className="mt-2 text-ink-100">{clip.title}</p>
              <p className="mt-1 max-w-3xl text-xs leading-relaxed text-ink-400">{clip.meta.explanation || clip.reason}</p>
              <button className="btn btn-quiet mt-2 text-xs" onClick={() => setOpenId(openId === clip.id ? null : clip.id)}>
                {openId === clip.id ? 'Hide score breakdown' : 'Score breakdown & metadata'}
              </button>
              {openId === clip.id && <Details clip={clip} />}
            </div>

            <div className="flex flex-col items-end gap-3">
              <ScoreBadge score={clip.score} />
              <div className="flex gap-2">
                <button className="btn btn-ghost" onClick={() => setStatusFor([clip.id], clip.status === 'kept' ? 'reset' : 'approve')}>{clip.status === 'kept' ? 'Approved ✓' : 'Approve'}</button>
                <button className="btn btn-ghost" onClick={() => setStatusFor([clip.id], clip.status === 'discarded' ? 'reset' : 'reject')}>{clip.status === 'discarded' ? 'Rejected' : 'Reject'}</button>
              </div>
              <div className="flex gap-2">
                <button className="btn btn-primary" disabled={!!batch && !batch.finished} onClick={() => generate([clip.id])}>Generate clip</button>
                <Link className="btn btn-ghost" to={`/jobs/${clip.job_id}/clips?clip=${clip.id}`}>Edit</Link>
              </div>
            </div>
          </li>
        ))}
        {clips.length === 0 && <li className="py-10 text-sm text-ink-400">No highlights match these filters.</li>}
      </ul>
    </div>
  )
}

function Details({ clip }: { clip: Clip }) {
  const pts = clip.meta.points ?? {}
  const max = clip.meta.max_points ?? {}
  return (
    <div className="mt-3 grid gap-x-10 gap-y-4 border-l border-ink-800 pl-4 md:grid-cols-2">
      <div>
        {Object.keys(FACTOR_LABELS).map((k) => (
          <div key={k} className="mb-2">
            <div className="flex justify-between text-xs text-ink-300">
              <span>{FACTOR_LABELS[k]}</span>
              <span className="numeric">{pts[k]?.toFixed(1) ?? '–'} / {max[k]?.toFixed(0) ?? '–'}</span>
            </div>
            <Bar value={clip.factors[k] ?? 0} />
          </div>
        ))}
      </div>
      <div className="text-xs text-ink-300">
        <p className="eyebrow">Title options</p>
        <ul className="mt-1 list-disc pl-4">{(clip.meta.titles ?? []).map((t) => <li key={t}>{t}</li>)}</ul>
        <p className="eyebrow mt-3">Hook</p>
        <p className="mt-1">{clip.meta.hook}</p>
        <p className="eyebrow mt-3">Hashtags</p>
        <p className="mt-1 text-sodium-400">{(clip.meta.hashtags ?? []).join(' ')}</p>
        <p className="eyebrow mt-3">Evidence</p>
        <p className="mt-1">{(clip.meta.evidence ?? []).join('; ')}</p>
        {clip.meta.vision?.description && <p className="mt-2">Vision model: {clip.meta.vision.description}</p>}
      </div>
    </div>
  )
}
