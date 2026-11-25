import { useEffect, useState } from 'react'

import {
  api,
  type Accel,
  type MusicTrack,
  type GameplaySettings,
  type ProviderStatus,
  type Settings as SettingsData,
  type SystemStatus,
} from '../api'
import { ErrorNote } from '../components/ErrorNote'
import { FACTOR_LABELS } from '../components/ui'

const SECRET_LABELS: Record<string, string> = {
  anthropic: 'Anthropic API key',
  openai: 'OpenAI-compatible API key',
  gemini: 'Google Gemini API key',
  huggingface_token: 'HuggingFace token',
}

const QUALITY_LABELS: Record<string, string> = { '18': 'High (largest files)', '21': 'Balanced', '25': 'Small files' }

export function Settings() {
  const [settings, setSettings] = useState<SettingsData | null>(null)
  const [providers, setProviders] = useState<ProviderStatus[]>([])
  const [system, setSystem] = useState<SystemStatus | null>(null)
  const [accel, setAccel] = useState<Accel | null>(null)
  const [music, setMusic] = useState<MusicTrack[]>([])
  const [musicError, setMusicError] = useState<string | null>(null)
  // A hand-edited CRF outside the three presets is shown as "Custom" instead of being lost.
  const crfNow = settings ? String(settings.export.crf) : '18'
  const qualityOptions = Array.from(new Set([...Object.keys(QUALITY_LABELS), crfNow])).sort((a, b) => +a - +b)
  const qualityLabels = { ...QUALITY_LABELS, [crfNow]: QUALITY_LABELS[crfNow] ?? `Custom (CRF ${crfNow})` }
  const refreshMusic = () => api.listMusic().then(setMusic).catch(() => undefined)
  const [error, setError] = useState<Error | null>(null)
  const [saved, setSaved] = useState(false)

  const reload = () => {
    void api.getSettings().then(setSettings).catch((e) => setError(e as Error))
    void api.providerStatus().then(setProviders).catch(() => undefined)
    void api.system().then(setSystem).catch(() => undefined)
    void api.accel().then(setAccel).catch(() => undefined)
    void refreshMusic()
  }

  useEffect(reload, [])

  const patch = async (update: Partial<SettingsData>) => {
    setError(null)
    try {
      setSettings(await api.putSettings(update))
      setSaved(true)
      setTimeout(() => setSaved(false), 1600)
    } catch (err) {
      setError(err as Error)
    }
  }

  if (!settings) return <p className="pt-24 text-sm text-ink-500">Loading…</p>

  return (
    <div className="max-w-4xl pt-14">
      <div className="rise flex items-baseline justify-between border-b border-ink-800 pb-5">
        <h1 className="font-display text-[clamp(2rem,4vw,3rem)] leading-none text-ink-100">
          Settings
        </h1>
        <span
          className="text-xs text-signal-good transition-opacity duration-300"
          style={{ opacity: saved ? 1 : 0 }}
        >
          saved
        </span>
      </div>

      {error && (
        <div className="mt-6">
          <ErrorNote error={error} onDismiss={() => setError(null)} />
        </div>
      )}

      {settings.insecure_secret_storage && (
        <p className="mt-6 border-l-2 border-sodium-600 pl-4 text-sm leading-relaxed text-ink-300">
          No OS keyring is available on this machine, so API keys are stored in plain text
          in <code className="text-ink-200">config.json</code>. On headless Linux, installing
          a Secret Service provider or <code className="text-ink-200">keyrings.alt</code>{' '}
          fixes this.
        </p>
      )}

      <Section title="AI provider" note="Which model picks the clips.">
        <div className="space-y-1">
          {providers.map((provider) => (
            <button
              key={provider.name}
              onClick={() => patch({ active_provider: provider.name })}
              className={[
                'block w-full border-l-2 py-3 pl-3 text-left transition-colors duration-200',
                provider.name === settings.active_provider
                  ? 'border-sodium-500 bg-ink-850/60'
                  : 'border-transparent hover:border-ink-700 hover:bg-ink-850/30',
              ].join(' ')}
            >
              <div className="flex items-baseline justify-between gap-4">
                <span className="text-sm text-ink-100">{provider.name}</span>
                <span
                  className={`text-xs ${provider.available ? 'text-signal-good' : 'text-ink-500'}`}
                >
                  {provider.available ? 'reachable' : provider.detail || 'unavailable'}
                </span>
              </div>
              {provider.models.length > 0 && (
                <span className="numeric mt-1 block truncate text-xs text-ink-600">
                  {provider.models.slice(0, 6).join(' · ')}
                </span>
              )}
            </button>
          ))}
        </div>

        <div className="mt-6 grid gap-5 sm:grid-cols-2">
          <Field
            label="Model"
            value={settings.providers[settings.active_provider]?.model ?? ''}
            placeholder="model name"
            onCommit={(value) =>
              patch({
                providers: {
                  [settings.active_provider]: {
                    ...settings.providers[settings.active_provider],
                    model: value,
                  },
                },
              })
            }
          />
          <Field
            label="Base URL"
            hint="Point at OpenRouter, Groq, DeepSeek, or a local server."
            value={settings.providers[settings.active_provider]?.base_url ?? ''}
            placeholder="https://…"
            onCommit={(value) =>
              patch({
                providers: {
                  [settings.active_provider]: {
                    ...settings.providers[settings.active_provider],
                    base_url: value || null,
                  },
                },
              })
            }
          />
        </div>
      </Section>

      <Section title="Keys" note="Stored in your OS keyring. Never sent anywhere but the provider.">
        <div className="space-y-4">
          {Object.entries(SECRET_LABELS).map(([key, label]) => (
            <SecretField
              key={key}
              secretKey={key}
              label={label}
              present={settings.keys_present[key] ?? false}
              onChanged={reload}
              onError={setError}
            />
          ))}
        </div>
      </Section>

      <Section title="Transcription">
        <div className="grid gap-5 sm:grid-cols-2">
          <Select
            label="Whisper model"
            value={settings.whisper.model}
            onChange={(value) => patch({ whisper: { ...settings.whisper, model: value } })}
            options={['tiny', 'base', 'small', 'medium', 'large-v3']}
          />
          <Field
            label="Language"
            hint="Leave empty to detect automatically."
            value={settings.whisper.language}
            placeholder="auto"
            onCommit={(value) => patch({ whisper: { ...settings.whisper, language: value } })}
          />
        </div>

        <label className="mt-5 flex items-start gap-3 text-sm text-ink-200">
          <input
            type="checkbox"
            checked={settings.whisper.diarization}
            disabled={!system?.diarization_available}
            onChange={(e) =>
              patch({ whisper: { ...settings.whisper, diarization: e.target.checked } })
            }
            className="mt-0.5 size-4 accent-sodium-500"
          />
          <span>
            Identify speakers
            {!system?.diarization_available && (
              <span className="mt-1 block text-xs text-ink-500">
                Needs the diarization extra:{' '}
                <code className="text-ink-300">uv pip install &apos;autoclip[diarization]&apos;</code>
              </span>
            )}
          </span>
        </label>
      </Section>

      <Section title="Gameplay engine" note="Finds moments from audio, motion and speech together, so silent action counts.">
        <div className="grid gap-5 sm:grid-cols-2">
          <Select
            label="Performance preset"
            value={settings.gameplay.preset}
            options={['low_resource', 'medium', 'quality']}
            labels={{ low_resource: 'Low (default)', medium: 'Medium', quality: 'High' }}
            hint="Low: ~1 FPS, Whisper base. Medium: 2 FPS, Whisper small. High: 4 FPS, Whisper small, whole-video transcript. Only the speech around candidate moments is transcribed in Low and Medium."
            onChange={async (value) => {
              await api.setPreset(value)
              reload()
            }}
          />
          <Select
            label="Vertical framing"
            value={settings.gameplay.framing}
            options={['auto', 'blur_fit', 'motion_crop', 'center']}
            labels={{ auto: 'Auto (HUD-aware)', blur_fit: 'Whole frame over blur', motion_crop: 'Follow the action', center: 'Centre crop' }}
            onChange={(value) => patch({ gameplay: { ...settings.gameplay, framing: value as GameplaySettings['framing'] } })}
          />
          <NumberField
            label="Visual sampling (frames/s)"
            value={settings.gameplay.sample_fps}
            onCommit={(value) => patch({ gameplay: { ...settings.gameplay, sample_fps: Math.max(0.25, Math.min(8, value)) } })}
          />
          <NumberField
            label="Max highlights per video"
            value={settings.gameplay.max_candidates}
            onCommit={(value) => patch({ gameplay: { ...settings.gameplay, max_candidates: Math.max(1, Math.min(50, value)) } })}
          />
        </div>
        <div className="mt-6 flex flex-wrap gap-x-8 gap-y-3 text-sm text-ink-300">
          <label className="flex items-center gap-2">
            <input type="checkbox" className="size-4 accent-sodium-500" checked={settings.gameplay.hook_text}
              onChange={(e) => patch({ gameplay: { ...settings.gameplay, hook_text: e.target.checked } })} />
            Burn an opening hook line into clips
          </label>
          <label className="flex items-center gap-2">
            <input type="checkbox" className="size-4 accent-sodium-500" checked={settings.gameplay.vision.enabled}
              onChange={(e) => patch({ gameplay: { ...settings.gameplay, vision: { ...settings.gameplay.vision, enabled: e.target.checked } } })} />
            Vision model on keyframes (Ollama, {settings.gameplay.vision.model})
          </label>
          <label className="flex items-center gap-2">
            <input type="checkbox" className="size-4 accent-sodium-500" checked={settings.gameplay.llm_refine}
              onChange={(e) => patch({ gameplay: { ...settings.gameplay, llm_refine: e.target.checked } })} />
            Ask the AI provider to refine titles
          </label>
        </div>
        <p className="mt-6 eyebrow">Score weights (relative)</p>
        <div className="mt-2 grid gap-5 sm:grid-cols-3">
          {Object.entries(settings.gameplay.weights).map(([key, weight]) => (
            <NumberField key={key} label={FACTOR_LABELS[key] ?? key} value={weight}
              onCommit={(value) => patch({ gameplay: { ...settings.gameplay, weights: { ...settings.gameplay.weights, [key]: Math.max(0, value) } } })} />
          ))}
        </div>
        <p className="mt-3 text-xs leading-relaxed text-ink-500">
          Scores are heuristic estimates of entertainment and engagement potential. They use no audience data and do not predict views.
          No paid API is needed: audio, motion, speech and scoring all run locally.
        </p>
      </Section>

      <Section title="Clips">
        <div className="grid gap-5 sm:grid-cols-3">
          <NumberField
            label="Min length (s)"
            value={settings.clips.min_duration_s}
            onCommit={(value) => patch({ clips: { ...settings.clips, min_duration_s: value } })}
          />
          <NumberField
            label="Max length (s)"
            value={settings.clips.max_duration_s}
            onCommit={(value) => patch({ clips: { ...settings.clips, max_duration_s: value } })}
          />
          <NumberField
            label="Max clips"
            value={settings.clips.max_clips}
            onCommit={(value) => patch({ clips: { ...settings.clips, max_clips: value } })}
          />
        </div>
      </Section>

      <Section title="Ingest">
        <div className="grid gap-5 sm:grid-cols-2">
          <Select
            label="YouTube cookies from"
            hint="YouTube blocks most anonymous downloads. Sign in in that browser and close it before downloading."
            value={settings.ingest.cookies_from_browser}
            onChange={(value) =>
              patch({ ingest: { ...settings.ingest, cookies_from_browser: value } })
            }
            options={['', 'chrome', 'firefox', 'edge', 'brave', 'chromium', 'safari']}
            labels={{ '': 'None' }}
          />
        </div>
      </Section>

      <Section title="Audio" note="Mix your own background music under the original gameplay audio.">
        <div className="grid gap-5 sm:grid-cols-2">
          <Select
            label="Default background music"
            value={settings.audio.music_id || 'none'}
            options={['none', ...music.map((t) => t.id)]}
            labels={{ none: 'None: original audio only', ...Object.fromEntries(music.map((t) => [t.id, t.name])) }}
            onChange={(value) => patch({ audio: { ...settings.audio, music_id: value === 'none' ? '' : value } })}
          />
          <label className="block">
            <span className="eyebrow">Upload a track</span>
            <input
              type="file"
              accept=".mp3,.wav,.m4a,.aac,.flac,.ogg,.opus,audio/*"
              className="field mt-1 cursor-pointer text-sm"
              onChange={async (e) => {
                const file = e.target.files?.[0]
                e.target.value = ''
                if (!file) return
                setMusicError(null)
                try {
                  await api.uploadMusic(file)
                  await refreshMusic()
                } catch (err) {
                  setMusicError((err as Error).message)
                }
              }}
            />
          </label>
          <NumberField
            label="Music level (0 to 100)"
            value={Math.round(settings.audio.music_volume * 100)}
            onCommit={(value) => patch({ audio: { ...settings.audio, music_volume: Math.max(0, Math.min(100, value)) / 100 } })}
          />
          <NumberField
            label="Original audio level (100 = unchanged)"
            value={Math.round(settings.audio.game_volume * 100)}
            onCommit={(value) => patch({ audio: { ...settings.audio, game_volume: Math.max(0, Math.min(200, value)) / 100 } })}
          />
        </div>
        <label className="mt-5 flex items-center gap-2 text-sm text-ink-300">
          <input type="checkbox" className="size-4 accent-sodium-500" checked={settings.audio.duck}
            onChange={(e) => patch({ audio: { ...settings.audio, duck: e.target.checked } })} />
          Lower the music while the original audio is loud (commentary, explosions)
        </label>
        {musicError && <p className="mt-3 text-sm text-signal-bad">{musicError}</p>}
        {music.length > 0 && (
          <ul className="mt-5">
            {music.map((t) => (
              <li key={t.id} className="flex items-center gap-4 border-t border-ink-850 py-2 text-sm">
                <span className="min-w-0 flex-1 truncate text-ink-200">{t.name}</span>
                <span className="numeric text-xs text-ink-500">{Math.round(t.duration_s)}s</span>
                <audio src={api.musicUrl(t.id)} controls preload="none" className="h-8 w-56" />
                <button className="btn btn-quiet text-xs" onClick={async () => { await api.deleteMusic(t.id); await refreshMusic(); reload() }}>Remove</button>
              </li>
            ))}
          </ul>
        )}
        <p className="mt-4 text-xs leading-relaxed text-ink-500">
          Music is looped to the clip length, faded in and out, mixed under the original audio, and the result is loudness-normalised
          like every export. If the recording has no audio, the music becomes the clip's only audio. Only upload music you have the
          right to use: this app supplies none and does not check licences.
        </p>
      </Section>

      <Section title="Export">
        <div className="grid gap-5 sm:grid-cols-2">
          <Select
            label="Output quality"
            value={String(settings.export.crf)}
            options={qualityOptions}
            labels={qualityLabels}
            hint="H.264 quality. Lower CRF = better picture and bigger files. All exports are MP4 (H.264 + AAC)."
            onChange={(value) => patch({ export: { ...settings.export, crf: Number(value) } })}
          />
          <Select
            label="Default ratio"
            value={settings.export.ratio}
            onChange={(value) => patch({ export: { ...settings.export, ratio: value } })}
            options={['9:16', '1:1', '16:9']}
          />
          <NumberField
            label="Loudness target (LUFS)"
            value={settings.export.loudness_lufs}
            onCommit={(value) => patch({ export: { ...settings.export, loudness_lufs: value } })}
          />
        </div>

        <label className="mt-5 flex items-start gap-3 text-sm text-ink-200">
          <input
            type="checkbox"
            checked={settings.export.prefer_hardware_encoder}
            onChange={(e) =>
              patch({
                export: { ...settings.export, prefer_hardware_encoder: e.target.checked },
              })
            }
            className="mt-0.5 size-4 accent-sodium-500"
          />
          <span>
            Use GPU encoding when available
            {system && !system.nvenc_works && (
              <span className="mt-1 block text-xs text-ink-500">
                Not usable on this machine — exports will use the CPU encoder. Same quality,
                slower.
              </span>
            )}
          </span>
        </label>

        <label className="mt-4 flex items-center gap-3 text-sm text-ink-200">
          <input
            type="checkbox"
            checked={settings.export.write_srt}
            onChange={(e) => patch({ export: { ...settings.export, write_srt: e.target.checked } })}
            className="size-4 accent-sodium-500"
          />
          Also write an .srt sidecar
        </label>
      </Section>

      {accel && (
        <Section title="Intel acceleration" note="Checked by trying, not assumed.">
          <dl className="grid gap-x-8 gap-y-3 text-sm sm:grid-cols-2">
            <Row label="Graphics" value={accel.gpus.length ? accel.gpus.map((g) => g.Name).join(', ') : 'not detected (Windows only)'} />
            <Row label="Quick Sync encode (H.264)" value={accel.qsv_works ? 'works: used for exports' : accel.qsv_listed ? `not usable: ${accel.qsv_error}` : 'no h264_qsv in this FFmpeg'} />
            <Row label="OpenVINO" value={accel.openvino_installed ? `devices: ${accel.openvino_devices.join(', ') || 'none'}` : 'not installed (optional)'} />
            <Row label="NPU" value={accel.npu_listed ? 'visible to OpenVINO (not used by Whisper)' : 'not detected'} />
          </dl>
          {accel.notes.map((n) => <p key={n} className="mt-3 text-xs leading-relaxed text-ink-500">{n}</p>)}
          <p className="mt-3 text-xs leading-relaxed text-ink-500">
            Windows may report shared graphics memory as 2 GB or more; that is not dedicated VRAM and is never treated as such.
            Everything works on CPU if acceleration is unavailable.
          </p>
        </Section>
      )}
      {system && (
        <Section title="This machine">
          <dl className="grid gap-x-8 gap-y-3 text-sm sm:grid-cols-2">
            <Row label="Platform" value={system.platform} />
            <Row label="Python" value={system.python_version} />
            <Row label="ffmpeg" value={system.ffmpeg_version ?? 'not found'} />
            <Row label="Acceleration" value={system.accel.toUpperCase()} />
            <Row label="Device" value={system.gpu_name ?? '—'} />
            <Row label="Whisper compute" value={system.compute_type} />
            <Row label="GPU encode" value={system.nvenc_works ? 'available' : 'unavailable'} />
            <Row label="Captions" value={system.has_libass ? 'libass present' : 'libass missing'} />
          </dl>
        </Section>
      )}
    </div>
  )
}

function Section({
  title,
  note,
  children,
}: {
  title: string
  note?: string
  children: React.ReactNode
}) {
  return (
    <section className="rise mt-14">
      <div className="border-b border-ink-800 pb-2">
        <h2 className="eyebrow">{title}</h2>
        {note && <p className="mt-1 text-xs text-ink-500">{note}</p>}
      </div>
      <div className="mt-5">{children}</div>
    </section>
  )
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-4 border-b border-ink-850 pb-2">
      <dt className="text-ink-500">{label}</dt>
      <dd className="numeric truncate text-right text-ink-200">{value}</dd>
    </div>
  )
}

/** Commits on blur rather than per keystroke, so a PUT isn't fired per letter. */
function Field({
  label,
  hint,
  value,
  placeholder,
  onCommit,
}: {
  label: string
  hint?: string
  value: string
  placeholder?: string
  onCommit: (value: string) => void
}) {
  const [draft, setDraft] = useState(value)
  useEffect(() => setDraft(value), [value])

  return (
    <label className="block">
      <span className="eyebrow">{label}</span>
      <input
        className="field mt-1 text-sm"
        value={draft}
        placeholder={placeholder}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={() => draft !== value && onCommit(draft)}
        onKeyDown={(e) => e.key === 'Enter' && e.currentTarget.blur()}
        spellCheck={false}
      />
      {hint && <span className="mt-1.5 block text-xs leading-snug text-ink-500">{hint}</span>}
    </label>
  )
}

function NumberField({
  label,
  value,
  onCommit,
}: {
  label: string
  value: number
  onCommit: (value: number) => void
}) {
  const [draft, setDraft] = useState(String(value))
  useEffect(() => setDraft(String(value)), [value])

  return (
    <label className="block">
      <span className="eyebrow">{label}</span>
      <input
        type="number"
        className="field numeric mt-1 text-sm"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={() => {
          const parsed = Number(draft)
          if (!Number.isNaN(parsed) && parsed !== value) onCommit(parsed)
        }}
        onKeyDown={(e) => e.key === 'Enter' && e.currentTarget.blur()}
      />
    </label>
  )
}

function Select({
  label,
  hint,
  value,
  onChange,
  options,
  labels = {},
}: {
  label: string
  hint?: string
  value: string
  onChange: (value: string) => void
  options: string[]
  labels?: Record<string, string>
}) {
  return (
    <label className="block">
      <span className="eyebrow">{label}</span>
      <select
        className="field mt-1 cursor-pointer text-sm"
        value={value}
        onChange={(e) => onChange(e.target.value)}
      >
        {options.map((option) => (
          <option key={option} value={option} className="bg-ink-850">
            {labels[option] ?? option}
          </option>
        ))}
      </select>
      {hint && <span className="mt-1.5 block text-xs leading-snug text-ink-500">{hint}</span>}
    </label>
  )
}

function SecretField({
  secretKey,
  label,
  present,
  onChanged,
  onError,
}: {
  secretKey: string
  label: string
  present: boolean
  onChanged: () => void
  onError: (error: Error) => void
}) {
  const [value, setValue] = useState('')
  const [busy, setBusy] = useState(false)

  const save = async () => {
    if (!value.trim()) return
    setBusy(true)
    try {
      await api.putSecret(secretKey, value.trim())
      setValue('')
      onChanged()
    } catch (err) {
      onError(err as Error)
    } finally {
      setBusy(false)
    }
  }

  const remove = async () => {
    setBusy(true)
    try {
      await api.deleteSecret(secretKey)
      onChanged()
    } catch (err) {
      onError(err as Error)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex flex-wrap items-end gap-3">
      <label className="min-w-56 flex-1">
        <span className="eyebrow">
          {label}
          {present && <span className="ml-2 text-signal-good">set</span>}
        </span>
        <input
          type="password"
          className="field mt-1 text-sm"
          value={value}
          placeholder={present ? '••••••••••••' : 'paste to add'}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && save()}
          autoComplete="off"
        />
      </label>
      <button onClick={save} disabled={!value.trim() || busy} className="btn btn-ghost">
        Save
      </button>
      {present && (
        <button onClick={remove} disabled={busy} className="btn btn-quiet">
          Remove
        </button>
      )}
    </div>
  )
}
