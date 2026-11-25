import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import { api, formatDuration, type Job, type Stats } from '../api'
import { Bar } from '../components/ui'

const STAGE_NOTES: Record<string, string> = {
  prepare: 'Extracting audio and thumbnails',
  analyze: 'Audio energy, impacts, sirens, motion, flashes, colour cues',
  transcribe: 'Transcribing commentary around candidate moments',
  highlights: 'Scoring, categorising, snapping clip edges',
}

export function Analysis() {
  const [jobs, setJobs] = useState<Job[]>([])
  const [stats, setStats] = useState<Stats | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = () => {
    api.listJobs(30).then(setJobs).catch((e) => setError(e.message))
    api.stats().then(setStats).catch(() => undefined)
  }
  useEffect(() => {
    load()
    const timer = setInterval(load, 2000)
    return () => clearInterval(timer)
  }, [])

  const act = async (fn: () => Promise<unknown>) => {
    setError(null)
    try {
      await fn()
      load()
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const paused = stats?.queue.paused

  return (
    <div className="pt-12">
      <p className="eyebrow">AI Analysis</p>
      <h1 className="mt-3 font-display text-5xl text-ink-100">What the engine is doing</h1>
      {error && <p className="mt-4 text-sm text-signal-bad">{error}</p>}

      <ul className="mt-10">
        {jobs.map((job) => {
          const live = job.status === 'running'
          return (
            <li key={job.id} className="border-t border-ink-800 py-5">
              <div className="flex flex-wrap items-baseline gap-x-6 gap-y-1">
                <Link to={job.status === 'done' ? `/highlights?job=${job.id}` : `/jobs/${job.id}`} className="text-ink-100 hover:text-sodium-400">
                  {job.source?.title || 'Untitled video'}
                </Link>
                <span className="numeric text-xs text-ink-500">
                  {job.source ? formatDuration(job.source.duration_s) : ''} · {job.mode === 'gameplay' ? 'gameplay engine' : 'transcript mode'}
                </span>
                <span className="ml-auto flex items-center gap-3 text-xs">
                  <span className={job.status === 'failed' ? 'text-signal-bad' : job.status === 'done' ? 'text-signal-good' : 'text-ink-400'}>
                    {live && paused ? 'paused' : job.status}
                  </span>
                  {live && !paused && <button className="btn btn-quiet" onClick={() => act(() => api.pauseJob(job.id))}>Pause</button>}
                  {live && paused && <button className="btn btn-quiet" onClick={() => act(() => api.resumeJob(job.id))}>Resume</button>}
                  {(live || job.status === 'queued') && <button className="btn btn-quiet" onClick={() => act(() => api.cancelJob(job.id))}>Cancel</button>}
                  {(job.status === 'failed' || job.status === 'cancelled') && <button className="btn btn-quiet" onClick={() => act(() => api.retryJob(job.id))}>Retry</button>}
                </span>
              </div>
              {(live || job.status === 'queued') && (
                <div className="mt-3">
                  <Bar value={job.progress} />
                  <p className="mt-2 text-xs text-ink-500">
                    <span className="numeric">{Math.round(job.progress * 100)}%</span> · {job.current_stage || 'waiting in queue'}
                    {STAGE_NOTES[job.current_stage] ? ` — ${STAGE_NOTES[job.current_stage]}` : ''}
                  </p>
                </div>
              )}
              {job.error && <p className="mt-2 whitespace-pre-wrap text-xs text-signal-bad">{job.error}</p>}
            </li>
          )
        })}
        {jobs.length === 0 && <li className="border-t border-ink-800 py-6 text-sm text-ink-400">Nothing analysed yet.</li>}
      </ul>
    </div>
  )
}
