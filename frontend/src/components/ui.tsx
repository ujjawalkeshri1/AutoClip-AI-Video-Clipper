import { type ReactNode } from 'react'

export const CATEGORY_LABELS: Record<string, string> = {
  ACTION: 'Action',
  FUNNY: 'Funny',
  SURPRISE: 'Surprise',
  POLICE_CHASE: 'Police chase',
  CRASH: 'Crash',
  EXPLOSION: 'Explosion',
  SHOOTOUT: 'Shootout',
  NPC_INTERACTION: 'NPC interaction',
  STREAMER_REACTION: 'Streamer reaction',
  MISSION_HIGHLIGHT: 'Mission highlight',
}

export const FACTOR_LABELS: Record<string, string> = {
  entertainment: 'Entertainment value',
  unexpectedness: 'Unexpectedness & surprise',
  action: 'Action & intensity',
  reaction: 'Streamer reaction',
  story: 'Storytelling & context',
  engagement: 'Short-form engagement',
}

export function CategoryTag({ category }: { category: string }) {
  if (!category) return null
  return (
    <span className="border border-sodium-700 px-2 py-0.5 text-[0.6875rem] font-semibold uppercase tracking-wider text-sodium-400">
      {CATEGORY_LABELS[category] ?? category}
    </span>
  )
}

export function ScoreBadge({ score }: { score: number }) {
  const tone = score >= 65 ? 'text-signal-good' : score >= 45 ? 'text-sodium-400' : 'text-ink-300'
  return (
    <span className={`numeric font-display text-4xl leading-none ${tone}`} title="Heuristic estimate, not a prediction of views">
      {score}
      <span className="ml-0.5 font-sans text-[0.6875rem] text-ink-500">/100</span>
    </span>
  )
}

export function Stat({ label, value, note }: { label: string; value: ReactNode; note?: string }) {
  return (
    <div className="border-t border-ink-800 pt-3">
      <p className="eyebrow">{label}</p>
      <p className="numeric mt-2 font-display text-5xl leading-none text-ink-100">{value}</p>
      {note && <p className="mt-2 text-xs text-ink-500">{note}</p>}
    </div>
  )
}

export function Bar({ value, max = 1 }: { value: number; max?: number }) {
  return (
    <div className="h-1 w-full bg-ink-800">
      <div className="h-1 bg-sodium-500" style={{ width: `${Math.max(0, Math.min(1, value / max)) * 100}%` }} />
    </div>
  )
}

export const timeLabel = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`
