import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import { api, type Stats } from '../api'
import { CategoryTag, ScoreBadge, Stat } from '../components/ui'

export function Dashboard() {
  const [stats, setStats] = useState<Stats | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const load = () => api.stats().then(setStats).catch((e) => setError(String(e.message ?? e)))
    load()
    const timer = setInterval(load, 3000)
    return () => clearInterval(timer)
  }, [])

  if (error) return <p className="pt-24 text-sm text-signal-bad">{error}</p>
  if (!stats) return <p className="pt-24 text-sm text-ink-400">Loading…</p>

  const running = stats.recent_jobs.find((j) => j.status === 'running' || j.status === 'queued')

  return (
    <div className="pt-12">
      <p className="eyebrow">Dashboard</p>
      <h1 className="mt-3 font-display text-5xl text-ink-100">
        Your <span className="italic text-sodium-500">highlights</span>
      </h1>

      <div className="mt-10 grid gap-x-10 gap-y-8 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Videos processed" value={stats.videos_processed} />
        <Stat label="Highlights detected" value={stats.highlights_detected} />
        <Stat label="Approved" value={stats.approved} note="waiting to be generated" />
        <Stat label="Clips generated" value={stats.clips_generated} />
      </div>

      {running && (
        <section className="mt-12 border-t border-ink-800 pt-4">
          <p className="eyebrow">Processing {stats.queue.paused ? '· paused' : ''}</p>
          <Link to={`/jobs/${running.id}`} className="mt-3 block">
            <div className="flex items-baseline justify-between">
              <span className="text-ink-100">{running.title || 'Untitled video'}</span>
              <span className="numeric text-sm text-sodium-400">{Math.round(running.progress * 100)}%</span>
            </div>
            <div className="mt-2 h-1 bg-ink-800">
              <div className="h-1 bg-sodium-500 transition-all" style={{ width: `${running.progress * 100}%` }} />
            </div>
            <p className="mt-2 text-xs text-ink-500">
              {running.stage || 'queued'} · {stats.queue.queued} waiting
            </p>
          </Link>
        </section>
      )}

      <div className="mt-14 grid gap-x-16 gap-y-12 lg:grid-cols-[1.2fr_1fr]">
        <section>
          <p className="eyebrow border-b border-ink-800 pb-2">Highest-scoring clips</p>
          {stats.top_clips.length === 0 ? (
            <p className="mt-6 text-sm text-ink-400">
              Nothing yet. <Link className="text-sodium-400 underline" to="/import">Import a video</Link> to find highlights.
            </p>
          ) : (
            <ul>
              {stats.top_clips.map((clip) => (
                <li key={clip.id} className="border-b border-ink-850">
                  <Link to={`/highlights?job=${clip.job_id}`} className="grid grid-cols-[4.5rem_1fr] items-center gap-4 py-4">
                    <ScoreBadge score={clip.score} />
                    <div>
                      <p className="text-sm text-ink-100">{clip.title}</p>
                      <p className="mt-1 flex items-center gap-3 text-xs text-ink-500">
                        <CategoryTag category={clip.category} />
                        <span>{clip.source}</span>
                        <span>{clip.status === 'exported' ? 'generated' : clip.status}</span>
                      </p>
                    </div>
                  </Link>
                </li>
              ))}
            </ul>
          )}
          <p className="mt-3 text-xs text-ink-500">
            Scores are a heuristic estimate of entertainment value from audio, motion and speech. They do not predict views.
          </p>
        </section>

        <section>
          <p className="eyebrow border-b border-ink-800 pb-2">Recent projects</p>
          <ul>
            {stats.recent_jobs.map((job) => (
              <li key={job.id} className="border-b border-ink-850">
                <Link
                  to={job.status === 'done' ? `/highlights?job=${job.id}` : `/jobs/${job.id}`}
                  className="flex items-baseline justify-between gap-4 py-3"
                >
                  <span className="truncate text-sm text-ink-100">{job.title || 'Untitled video'}</span>
                  <span className="numeric shrink-0 text-xs text-ink-500">
                    {job.status === 'done' ? `${job.highlights} highlights` : job.status}
                  </span>
                </Link>
              </li>
            ))}
            {stats.recent_jobs.length === 0 && <li className="py-3 text-sm text-ink-500">No projects yet.</li>}
          </ul>
        </section>
      </div>
    </div>
  )
}
