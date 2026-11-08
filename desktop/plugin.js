// Rehearse — Desktop page: Hermes asks, you answer on camera, you get a scored replay.
import { Button, ROUTES_AREA, SIDEBAR_NAV_AREA } from '@hermes/plugin-sdk'
import { useCallback, useEffect, useRef, useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

// One element factory: pulls `key` out of props (jsx-runtime takes it as its own argument)
// and picks jsxs for static child arrays.
function h(type, props) {
  const { key, ...rest } = props || {}
  return (Array.isArray(rest.children) ? jsxs : jsx)(type, rest, key)
}

const PATH = '/rehearse'
let plugin = null // the scoped PluginContext, set in register()

// Backend calls go to the plugin's own REST namespace (/api/plugins/rehearse/app), which hands
// them to the agent half. See bridge.py for why not plugin RPC or command.dispatch.
async function rpc(name, params = {}) {
  let out
  try {
    out = await plugin.rest('/app', { method: 'POST', body: { method: name, params }, timeoutMs: 30000 })
  } catch {
    throw new Error('Rehearse could not reach Hermes. Check that the plugin is enabled, then restart the gateway.')
  }
  if (out?.error) throw new Error(out.error)
  return out?.ok
}
const sleep = ms => new Promise(r => setTimeout(r, ms))

const STAGES = {
  starting: 'Getting ready…',
  speech_model: 'Preparing the speech engine (one-time download, about a minute)…',
  face_model: 'Preparing the eye-contact check…',
  planning: 'Writing your questions…',
  transcribing: 'Listening back to your answer…',
  eye_contact: 'Checking eye contact…',
  feedback: 'Writing your feedback…'
}

const MARKS = {
  filler: { color: 'var(--ui-yellow)', name: 'Filler word' },
  pause: { color: 'var(--ui-blue)', name: 'Long pause' },
  away: { color: 'var(--ui-purple)', name: 'Looked away' },
  fast: { color: 'var(--ui-orange)', name: 'Rushing' },
  slow: { color: 'var(--ui-orange)', name: 'Dragging' },
  repeat: { color: 'var(--ui-red)', name: 'Stumble' }
}

// Keys match coach.SCENARIOS in the Python half.
const SCENES = [
  { group: 'Interviews', items: [
    { key: 'job', emoji: '💼', label: 'Job interview', ask: 'What role are you interviewing for?', eg: 'Junior data analyst at a bank', more: 'Paste the job description for sharper questions' },
    { key: 'technical', emoji: '🧠', label: 'Technical', ask: 'What role and stack?', eg: 'Backend engineer, Python and Postgres', more: 'Add the job description or topics to cover' },
    { key: 'case', emoji: '📊', label: 'Case interview', ask: 'What firm or kind of case?', eg: 'Consulting, market-entry case', more: 'Add the firm, industry or case type' },
    { key: 'admission', emoji: '🎓', label: 'University or scholarship', ask: 'Which programme or scholarship?', eg: 'MSc Computer Science, scholarship interview', more: 'Add your background and why this programme' },
    { key: 'visa', emoji: '🛂', label: 'Visa or immigration', ask: 'Which visa, and what for?', eg: "Student visa for a master's in Canada", more: 'Add your situation (practise telling the truth clearly)' },
    { key: 'residency', emoji: '🩺', label: 'Medical or residency', ask: 'Which programme or specialty?', eg: 'Internal medicine residency', more: 'Add your experience and the programme' },
    { key: 'media', emoji: '📰', label: 'Media or press', ask: 'What is the interview about?', eg: 'Local news on our new community clinic', more: 'Add your key message and tricky topics' },
    { key: 'podcast', emoji: '🎙️', label: 'Podcast guest', ask: 'Which show, and your topic?', eg: 'A startup podcast, building in Lagos', more: 'Add the host, audience and your story' }
  ] },
  { group: 'Talks', items: [
    { key: 'presentation', emoji: '🎤', label: 'Presentation', ask: 'What is your talk about?', eg: 'Our Q3 results, 5 minutes, to leadership', more: 'Add audience, length or key points' },
    { key: 'pitch', emoji: '🚀', label: 'Investor pitch', ask: 'What are you pitching?', eg: 'Seed round for a logistics app, 3 minutes', more: 'Add traction, market and your ask' },
    { key: 'demo', emoji: '🖥️', label: 'Product demo', ask: 'What are you demoing?', eg: 'My Hermes plugin, 90-second demo video', more: 'Add who watches it and what should wow them' },
    { key: 'sales', emoji: '🤝', label: 'Sales pitch', ask: 'What are you selling, to whom?', eg: 'Payroll software to a 50-person company', more: "Add the buyer's situation and objections" },
    { key: 'academic', emoji: '📚', label: 'Class talk or thesis', ask: 'What is your topic?', eg: 'Master’s thesis defence on solar microgrids', more: 'Add your method, findings and committee' },
    { key: 'speech', emoji: '🥂', label: 'Speech or toast', ask: 'What is the occasion?', eg: "Best man's speech at my brother's wedding", more: 'Add names, stories and the length you want' }
  ] }
]
const SCENE = Object.fromEntries(SCENES.flatMap(g => g.items).map(s => [s.key, s]))

const SCORE_NAMES ={ fillers: 'Filler words', pace: 'Pace', eye_contact: 'Eye contact', pauses: 'Pauses', length: 'Length' }

async function waitJob(job, onStage) {
  for (;;) {
    await sleep(700)
    const s = await rpc('job.status', { job })
    if (s.state === 'done') return s.result
    if (s.state === 'error' || s.state === 'missing') throw new Error(s.error || 'Something went wrong.')
    onStage?.(s.stage)
  }
}

const errorText = e => e?.data?.detail || e?.message || String(e)
const scoreColor = n => (n >= 80 ? 'var(--ui-green)' : n >= 60 ? 'var(--ui-yellow)' : 'var(--ui-red)')
const fmtTime = s => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`

// ---------- styles (theme variables, so light and dark both look native) ----------
const S = {
  page: { height: '100%', overflowY: 'auto', color: 'var(--ui-text-primary)' },
  wrap: { maxWidth: 980, margin: '0 auto', padding: '28px 24px 48px', display: 'flex', flexDirection: 'column', gap: 20 },
  h1: { fontSize: 26, fontWeight: 650, margin: 0, letterSpacing: '-0.01em' },
  sub: { color: 'var(--ui-text-secondary)', fontSize: 14, margin: 0, lineHeight: 1.5 },
  muted: { color: 'var(--ui-text-tertiary)', fontSize: 12.5, lineHeight: 1.5 },
  card: { border: '1px solid var(--ui-stroke-secondary)', borderRadius: 12, padding: 18, background: 'var(--ui-bg-elevated)' },
  label: { fontSize: 13, fontWeight: 600, marginBottom: 6, display: 'block' },
  input: {
    width: '100%', boxSizing: 'border-box', padding: '10px 12px', borderRadius: 8, fontSize: 14,
    border: '1px solid var(--ui-stroke-secondary)', background: 'var(--ui-chat-surface-background)', color: 'inherit', outline: 'none'
  },
  row: { display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' },
  grid2: { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 16 }
}

const el = (tag, style, children, extra = {}) => h(tag, { style, ...extra, children })

function SceneChip({ scene, active, onClick }) {
  return h('button', {
    type: 'button', onClick, 'aria-pressed': active,
    style: {
      display: 'flex', alignItems: 'center', gap: 8, padding: '9px 14px', borderRadius: 999, cursor: 'pointer',
      fontSize: 13.5, color: 'inherit', background: active ? 'color-mix(in srgb, var(--ui-accent) 14%, transparent)' : 'var(--ui-bg-elevated)',
      border: `1px solid ${active ? 'var(--ui-accent)' : 'var(--ui-stroke-secondary)'}`,
      boxShadow: active ? '0 0 0 2px color-mix(in srgb, var(--ui-accent) 25%, transparent)' : 'none'
    },
    children: [el('span', { fontSize: 16, lineHeight: 1 }, scene.emoji), el('span', { fontWeight: active ? 600 : 500 }, scene.label)]
  })
}

function ScoreRing({ value, size = 96, label }) {
  const r = size / 2 - 8
  const c = 2 * Math.PI * r
  const v = Math.max(0, Math.min(100, value ?? 0))
  return h('div', {
    style: { position: 'relative', width: size, height: size, flex: 'none' },
    children: [
      h('svg', {
        width: size, height: size, style: { transform: 'rotate(-90deg)' },
        children: [
          h('circle', { cx: size / 2, cy: size / 2, r, fill: 'none', stroke: 'var(--ui-stroke-secondary)', strokeWidth: 8 }),
          h('circle', {
            cx: size / 2, cy: size / 2, r, fill: 'none', stroke: scoreColor(v), strokeWidth: 8, strokeLinecap: 'round',
            strokeDasharray: c, strokeDashoffset: c * (1 - v / 100), style: { transition: 'stroke-dashoffset 900ms ease' }
          })
        ]
      }),
      h('div', {
        style: { position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center' },
        children: [el('div', { fontSize: size / 3.4, fontWeight: 700 }, v), label && el('div', { ...S.muted, fontSize: 11 }, label)]
      })
    ]
  })
}

function Bar({ name, value }) {
  return h('div', {
    children: [
      h('div', { style: { display: 'flex', justifyContent: 'space-between', fontSize: 12.5, marginBottom: 4 }, children: [el('span', null, name), el('span', { fontWeight: 600 }, value)] }),
      el('div', { height: 6, borderRadius: 3, background: 'var(--ui-stroke-secondary)', overflow: 'hidden' },
        el('div', { width: `${value}%`, height: '100%', background: scoreColor(value), transition: 'width 700ms ease' }))
    ]
  })
}

function Busy({ text }) {
  return h('div', {
    style: { ...S.row, ...S.muted, fontSize: 13.5 },
    children: [
      el('span', { width: 14, height: 14, borderRadius: '50%', border: '2px solid var(--ui-stroke-secondary)', borderTopColor: 'var(--ui-accent)', animation: 'rehearse-spin 0.9s linear infinite', display: 'inline-block' }),
      el('span', null, text),
      h('style', { children: '@keyframes rehearse-spin{to{transform:rotate(360deg)}}' })
    ]
  })
}

function Problem({ text, onRetry }) {
  return h('div', {
    style: { ...S.card, borderColor: 'var(--ui-red)', display: 'flex', gap: 12, alignItems: 'center', justifyContent: 'space-between' },
    children: [el('span', { fontSize: 13.5 }, text), onRetry && h(Button, { variant: 'outline', size: 'sm', onClick: onRetry, children: 'Try again' })]
  })
}

// ---------- home: the whole onboarding is three choices ----------
function Home({ engine, onStart }) {
  const [scene, setScene] = useState('job')
  const [subject, setSubject] = useState('')
  const [details, setDetails] = useState('')
  const [showDetails, setShowDetails] = useState(false)
  const [count, setCount] = useState(5)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [history, setHistory] = useState(null)

  useEffect(() => {
    rpc('sessions.list').then(setHistory).catch(() => setHistory({ sessions: [], progress: {} }))
  }, [])

  const start = async () => {
    setError('')
    setBusy(STAGES.planning)
    try {
      const { job } = await rpc('plan.create', { scenario: scene, subject, details, count })
      const session = await waitJob(job, s => setBusy(STAGES[s] || STAGES.planning))
      onStart(session)
    } catch (e) {
      setError(errorText(e))
      setBusy('')
    }
  }

  const sc = SCENE[scene]
  const recent = history?.sessions?.slice(0, 5) || []
  const prog = history?.progress || {}

  return el('div', S.wrap, [
    h('div', {
      key: 'hero',
      children: [
        el('h1', S.h1, 'Rehearse'),
        el('p', { ...S.sub, marginTop: 6 }, 'Practise out loud. Hermes asks the questions, watches you answer, and shows you exactly what to fix.')
      ]
    }),
    h('div', {
      key: 'scenes', style: { display: 'flex', flexDirection: 'column', gap: 12 },
      children: [
        el('span', S.label, 'What are you preparing for?', { key: 'q' }),
        ...SCENES.map(g => h('div', {
          key: g.group, style: { display: 'flex', flexDirection: 'column', gap: 8 },
          children: [
            el('span', { ...S.muted, textTransform: 'uppercase', letterSpacing: '0.06em', fontSize: 11 }, g.group),
            el('div', { display: 'flex', flexWrap: 'wrap', gap: 8 },
              g.items.map(it => h(SceneChip, { key: it.key, scene: it, active: it.key === scene, onClick: () => setScene(it.key) })))
          ]
        }))
      ]
    }),
    h('div', {
      key: 'form', style: { ...S.card, display: 'flex', flexDirection: 'column', gap: 14 },
      children: [
        h('label', {
          children: [
            el('span', S.label, sc.ask),
            h('input', {
              style: S.input, value: subject, autoFocus: true, maxLength: 300,
              placeholder: `e.g. ${sc.eg}`,
              onChange: e => setSubject(e.target.value),
              onKeyDown: e => e.key === 'Enter' && subject.trim() && !busy && start()
            }),
            !subject && h('button', {
              type: 'button', onClick: () => setSubject(sc.eg),
              style: { background: 'none', border: 'none', padding: '6px 0 0', cursor: 'pointer', color: 'var(--ui-accent)', fontSize: 13 },
              children: `Try the example: “${sc.eg}”`
            })
          ]
        }),
        showDetails
          ? h('label', {
              children: [
                el('span', S.label, `${sc.more} (optional)`),
                h('textarea', { style: { ...S.input, minHeight: 90, resize: 'vertical' }, value: details, maxLength: 6000, onChange: e => setDetails(e.target.value) })
              ]
            })
          : h('button', { type: 'button', onClick: () => setShowDetails(true), style: { background: 'none', border: 'none', padding: 0, cursor: 'pointer', color: 'var(--ui-accent)', fontSize: 13, textAlign: 'left' }, children: `+ ${sc.more}` }),
        h('div', {
          style: { ...S.row, justifyContent: 'space-between' },
          children: [
            h('div', {
              style: S.row,
              children: [
                el('span', S.muted, 'Questions'),
                ...[3, 5, 8].map(n =>
                  h('button', {
                    key: n, type: 'button', onClick: () => setCount(n),
                    style: { padding: '4px 12px', borderRadius: 999, fontSize: 13, cursor: 'pointer', color: 'inherit', border: `1px solid ${count === n ? 'var(--ui-accent)' : 'var(--ui-stroke-secondary)'}`, background: count === n ? 'color-mix(in srgb, var(--ui-accent) 14%, transparent)' : 'transparent' },
                    children: n
                  }))
              ]
            }),
            h(Button, { onClick: start, disabled: !subject.trim() || !!busy, children: busy ? 'Preparing…' : 'Start rehearsal' })
          ]
        }),
        busy && h(Busy, { text: busy }),
        error && h(Problem, { text: error }),
        el('div', S.muted, '🔒 Your camera and microphone are used only while you practise. Speech and eye contact are analysed on this computer; only the transcript of your answer goes to your AI model for feedback. Recordings stay here and can be deleted.')
      ]
    }),
    engine !== 'ready' && el('div', { key: 'engine' }, h(Busy, { text: engine === 'error' ? 'The speech engine could not start. Answers can still be typed in chat with /rehearse.' : STAGES[engine] || STAGES.speech_model })),
    recent.length > 0 && h('div', {
      key: 'history', style: S.card,
      children: [
        h('div', { style: { ...S.row, justifyContent: 'space-between', marginBottom: 10 }, children: [el('span', { fontWeight: 600 }, 'Your progress'), prog.first_score != null && el('span', S.muted, `First ${prog.first_score} → latest ${prog.latest_score}`)] }),
        ...recent.map(r => h('div', {
          key: r.id, style: { display: 'flex', justifyContent: 'space-between', padding: '8px 0', borderTop: '1px solid var(--ui-stroke-secondary)', fontSize: 13.5 },
          children: [el('span', null, `${(SCENE[r.scenario] || SCENE.job).emoji}  ${r.title}`), el('span', { fontWeight: 600, color: r.score != null ? scoreColor(r.score) : 'var(--ui-text-tertiary)' }, r.score ?? 'not answered')]
        }))
      ]
    })
  ])
}

// ---------- the practice room ----------
function useCamera(active) {
  const [stream, setStream] = useState(null)
  const [error, setError] = useState('')
  useEffect(() => {
    if (!active) return
    let live = null
    let cancelled = false
    navigator.mediaDevices
      .getUserMedia({ video: { width: 640, height: 480, facingMode: 'user' }, audio: { echoCancellation: true, noiseSuppression: true } })
      .then(s => (cancelled ? s.getTracks().forEach(t => t.stop()) : ((live = s), setStream(s))))
      .catch(e => setError(e?.name === 'NotAllowedError'
        ? 'Hermes needs your camera and microphone to rehearse. Allow them in your system privacy settings, then try again.'
        : e?.name === 'NotFoundError' ? 'No camera or microphone was found. Plug one in and try again.' : `The camera could not start: ${errorText(e)}`))
    return () => {
      cancelled = true
      live?.getTracks().forEach(t => t.stop())
      setStream(null)
    }
  }, [active])
  return { stream, error }
}

// The interviewer speaks with a voice installed on this computer: free, offline, many languages.
function useVoices() {
  const [voices, setVoices] = useState(() => window.speechSynthesis?.getVoices() || [])
  useEffect(() => {
    const synth = window.speechSynthesis
    if (!synth) return
    const update = () => setVoices(synth.getVoices())
    update()
    synth.addEventListener('voiceschanged', update)
    return () => synth.removeEventListener('voiceschanged', update)
  }, [])
  return voices
}

function pickVoice(voices, uri) {
  return voices.find(v => v.voiceURI === uri)
    || voices.find(v => /^en(-|_)/i.test(v.lang) && /natural|neural|online/i.test(v.name))
    || voices.find(v => /^en/i.test(v.lang))
}

// The default interviewer voice is Hermes's own text-to-speech (natural neural voices, or the user's
// provider). A system voice can be picked instead; it is also the fallback when Hermes cannot speak.
let speaking = null

function stopSpeech() {
  window.speechSynthesis?.cancel()
  if (speaking) speaking.pause()
  speaking = null
}

function speakSystem(text, uri) {
  const synth = window.speechSynthesis
  if (!synth) return
  const u = new SpeechSynthesisUtterance(text)
  const voice = pickVoice(synth.getVoices(), uri)
  if (voice) u.voice = voice
  synth.speak(u)
}

let speechTurn = 0

async function speak(text, enabled, uri = '') {
  if (!enabled || !text) return
  stopSpeech()
  if (uri) return speakSystem(text, uri)
  const turn = ++speechTurn
  try {
    const { audio, mime } = await rpc('voice.speak', { text })
    if (turn !== speechTurn) return
    speaking = new Audio(`data:${mime};base64,${audio}`)
    await speaking.play()
  } catch {
    if (turn === speechTurn) speakSystem(text, '')
  }
}

const voiceLabel = v => `${v.name.replace(/^(Microsoft|Google|Apple)\s+/, '').replace(/\s*-\s*.*$/, '')} · ${v.lang}`

function VoicePicker({ voices, value, onChange }) {
  const sorted = [...voices].sort((a, b) => a.lang.localeCompare(b.lang) || a.name.localeCompare(b.name))
  return h('select', {
    'aria-label': 'Interviewer voice', title: 'Interviewer voice', value, onChange: e => onChange(e.target.value),
    style: { ...S.input, width: 'auto', maxWidth: 260, padding: '5px 8px', fontSize: 12.5 },
    children: [
      h('option', { key: 'hermes', value: '', children: 'Interviewer voice: natural (Hermes)' }),
      ...sorted.map(v => h('option', { key: v.voiceURI, value: v.voiceURI, children: `System · ${voiceLabel(v)}` }))
    ]
  })
}

function pickMime() {
  for (const m of ['video/webm;codecs=vp9,opus', 'video/webm;codecs=vp8,opus', 'video/webm']) if (window.MediaRecorder?.isTypeSupported(m)) return m
  return ''
}

function Room({ session, index, onResult, onExit }) {
  const q = session.questions[index]
  const { stream, error } = useCamera(true)
  const [voice, setVoice] = useState(true)
  const voices = useVoices()
  const [voiceUri, setVoiceUri] = useState(() => plugin.storage.get('voice', ''))
  const chooseVoice = uri => {
    setVoiceUri(uri)
    plugin.storage.set('voice', uri)
    speak("Hi, I'll be asking the questions today.", true, uri)
  }
  const [phase, setPhase] = useState('ready') // ready | recording | working | failed
  const [elapsed, setElapsed] = useState(0)
  const [stage, setStage] = useState('')
  const [problem, setProblem] = useState('')
  const video = useRef(null)
  const recorder = useRef(null)
  const chunks = useRef([])
  const timer = useRef(null)
  const lastBlob = useRef(null)

  useEffect(() => { if (video.current && stream) video.current.srcObject = stream }, [stream])
  useEffect(() => {
    const opening = index === 0 && session.opening ? `${session.opening} ` : ''
    speak(opening + q.question, voice, voiceUri)
    return () => stopSpeech()
  }, [index])
  useEffect(() => () => clearInterval(timer.current), [])

  const analyse = async blob => {
    setPhase('working')
    setProblem('')
    try {
      setStage('Saving your take…')
      const bytes = await blob.arrayBuffer()
      const contentType = (blob.type || 'video/webm').split(';')[0]
      const { upload_id } = await plugin.rest('/upload', { method: 'POST', upload: { filename: blob.name || 'take.webm', contentType, bytes }, timeoutMs: 120000 })
      const { job } = await rpc('take.analyze', { session_id: session.id, question_index: index, upload_id })
      const attempt = await waitJob(job, s => setStage(STAGES[s] || ''))
      onResult(attempt, URL.createObjectURL(blob))
    } catch (e) {
      setProblem(errorText(e))
      setPhase('failed')
    }
  }

  const begin = () => {
    stopSpeech()
    chunks.current = []
    const mime = pickMime()
    const rec = new MediaRecorder(stream, mime ? { mimeType: mime, videoBitsPerSecond: 900000 } : undefined)
    rec.ondataavailable = e => e.data.size && chunks.current.push(e.data)
    rec.onstop = () => {
      const blob = new Blob(chunks.current, { type: 'video/webm' })
      lastBlob.current = blob
      analyse(blob)
    }
    recorder.current = rec
    rec.start(1000)
    const t0 = Date.now()
    setElapsed(0)
    timer.current = setInterval(() => setElapsed((Date.now() - t0) / 1000), 250)
    setPhase('recording')
  }

  const finish = () => {
    clearInterval(timer.current)
    recorder.current?.state === 'recording' && recorder.current.stop()
  }

  // Space starts and stops an answer, so nobody has to reach for the mouse mid-sentence.
  useEffect(() => {
    const onKey = e => {
      if (e.code !== 'Space' || e.repeat || /INPUT|TEXTAREA|BUTTON/.test(e.target?.tagName)) return
      if (phase === 'ready' && stream) { e.preventDefault(); begin() }
      else if (phase === 'recording') { e.preventDefault(); finish() }
    }
    return plugin.addEventListener(window, 'keydown', onKey)
  }, [phase, stream])

  const target = q.target_seconds
  const over = elapsed > target * 1.3

  return el('div', S.wrap, [
    h('div', {
      key: 'top', style: { ...S.row, justifyContent: 'space-between' },
      children: [
        el('span', S.muted, `${session.title}  ·  Question ${index + 1} of ${session.questions.length}`),
        h('div', { style: S.row, children: [
          voice && h(VoicePicker, { voices, value: voiceUri, onChange: chooseVoice }),
          h(Button, { variant: 'ghost', size: 'sm', onClick: () => { setVoice(!voice); stopSpeech() }, children: voice ? '🔊 Voice on' : '🔈 Voice off' }),
          h(Button, { variant: 'ghost', size: 'sm', onClick: onExit, children: 'Finish' })
        ] })
      ]
    }),
    h('div', {
      key: 'q', style: { ...S.card, display: 'flex', flexDirection: 'column', gap: 8 },
      children: [
        el('div', { fontSize: 20, fontWeight: 600, lineHeight: 1.35 }, q.question),
        el('div', S.muted, `💡 ${q.tip}   ·   aim for about ${fmtTime(target)}`),
        h('button', { type: 'button', onClick: () => speak(q.question, true, voiceUri), style: { alignSelf: 'flex-start', background: 'none', border: 'none', padding: 0, color: 'var(--ui-accent)', cursor: 'pointer', fontSize: 13 }, children: 'Hear the question again' })
      ]
    }),
    error
      ? h(Problem, { key: 'cam', text: `${error} You can also upload a recording below.` })
      : h('div', {
          key: 'stage', style: { position: 'relative', borderRadius: 14, overflow: 'hidden', background: '#000', aspectRatio: '4 / 3', maxHeight: 460, alignSelf: 'center', width: '100%', maxWidth: 620 },
          children: [
            h('video', { ref: video, autoPlay: true, muted: true, playsInline: true, style: { width: '100%', height: '100%', objectFit: 'cover', transform: 'scaleX(-1)' } }),
            phase === 'recording' && h('div', {
              style: { position: 'absolute', top: 12, left: 12, display: 'flex', gap: 8, alignItems: 'center', background: 'rgba(0,0,0,.55)', color: '#fff', padding: '5px 10px', borderRadius: 999, fontSize: 13 },
              children: [el('span', { width: 9, height: 9, borderRadius: '50%', background: '#ef4444', display: 'inline-block' }), `${fmtTime(elapsed)} / ${fmtTime(target)}`]
            }),
            phase === 'recording' && el('div', { position: 'absolute', left: 0, right: 0, bottom: 0, height: 5, background: 'rgba(255,255,255,.2)' },
              el('div', { height: '100%', width: `${Math.min(100, (elapsed / target) * 100)}%`, background: over ? '#ef4444' : elapsed > target ? '#f59e0b' : '#22c55e', transition: 'width 250ms linear' })),
            !stream && el('div', { position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#bbb', fontSize: 14 }, 'Starting camera…')
          ]
        }),
    el('div', { ...S.row, justifyContent: 'center' }, [
      phase === 'ready' && h(Button, { key: 'b', size: 'lg', disabled: !stream, onClick: begin, children: '● Start answering' }),
      phase === 'recording' && h(Button, { key: 'f', size: 'lg', onClick: finish, children: over ? '■ Done (running long)' : '■ Done' }),
      phase === 'recording' && el('span', { ...S.muted, width: '100%', textAlign: 'center' }, 'or press Space', { key: 'k' }),
      phase === 'ready' && h('div', {
        key: 'alt', style: { ...S.muted, width: '100%', textAlign: 'center' },
        children: [
          stream && 'or press Space  ·  ',
          h('label', {
            key: 'up', style: { color: 'var(--ui-accent)', cursor: 'pointer' },
            children: [
              'Upload a recording instead',
              h('input', {
                type: 'file', accept: 'audio/*,video/*', 'data-rehearse-upload': '', style: { display: 'none' },
                onChange: e => { const f = e.target.files?.[0]; e.target.value = ''; if (f) { lastBlob.current = f; analyse(f) } }
              })
            ]
          })
        ]
      }),
      phase === 'working' && h(Busy, { key: 'w', text: stage || 'Working…' }),
      phase === 'failed' && h(Problem, { key: 'p', text: problem, onRetry: () => lastBlob.current && analyse(lastBlob.current) })
    ], { key: 'actions' })
  ])
}

// ---------- the replay ----------
function Timeline({ moments, duration, onSeek }) {
  if (!duration) return null
  return h('div', {
    children: [
      el('div', { position: 'relative', height: 26, borderRadius: 6, background: 'var(--ui-stroke-secondary)', cursor: 'pointer' },
        moments.map((m, i) => h('button', {
          key: i, type: 'button', title: `${fmtTime(m.t)}  ${m.label}`, onClick: () => onSeek(m.t),
          style: { position: 'absolute', top: 3, bottom: 3, left: `${(m.t / duration) * 100}%`, width: m.length ? `max(4px, ${(m.length / duration) * 100}%)` : 4, background: (MARKS[m.kind] || {}).color, borderRadius: 2, border: 'none', padding: 0, cursor: 'pointer', opacity: 0.9 }
        }))),
      el('div', { ...S.row, gap: 14, marginTop: 8 }, Object.entries(MARKS).filter(([k]) => k !== 'slow' && moments.some(m => m.kind === k || (k === 'fast' && m.kind === 'slow'))).map(([k, v]) =>
        h('span', { key: k, style: { ...S.muted, display: 'flex', alignItems: 'center', gap: 6 }, children: [el('span', { width: 10, height: 10, borderRadius: 2, background: v.color, display: 'inline-block' }), `${v.name} (${moments.filter(m => m.kind === k || (k === 'fast' && m.kind === 'slow')).length})`] })))
    ]
  })
}

const FILLER_RE = /\b(um+|uh+|uhm|erm|er|ah|hmm|you know,|i mean,|like,)/gi

function Transcript({ text }) {
  const parts = text.split(FILLER_RE)
  return el('p', { fontSize: 14, lineHeight: 1.7, margin: 0, color: 'var(--ui-text-secondary)' },
    parts.map((p, i) => (i % 2 ? h('mark', { key: i, style: { background: 'color-mix(in srgb, var(--ui-yellow) 35%, transparent)', color: 'inherit', borderRadius: 3, padding: '0 2px' }, children: p }) : p)))
}

function List({ title, items, icon }) {
  if (!items?.length) return null
  return h('div', { children: [el('div', { fontWeight: 600, fontSize: 13.5, marginBottom: 6 }, title), ...items.map((t, i) => el('div', { fontSize: 13.5, lineHeight: 1.55, marginBottom: 4 }, `${icon} ${t}`, { key: i }))] })
}

function Replay({ session, index, attempt, videoUrl, onRetry, onNext, isLast }) {
  const player = useRef(null)
  const d = attempt.delivery
  const fb = attempt.feedback
  const seek = t => { if (player.current) { player.current.currentTime = Math.max(0, t - 0.4); player.current.play() } }
  const headline = [
    d.filler_count != null && `${d.filler_count} filler${d.filler_count === 1 ? '' : 's'}`,
    d.wpm && `${Math.round(d.wpm)} words/min`,
    d.facing_ratio != null && `${Math.round(d.facing_ratio * 100)}% eye contact`,
    d.duration && `${fmtTime(d.duration)} long`
  ].filter(Boolean).join('  ·  ')

  return el('div', S.wrap, [
    el('span', S.muted, `${session.title}  ·  Question ${index + 1} of ${session.questions.length}`, { key: 'crumb' }),
    el('div', { fontSize: 18, fontWeight: 600 }, attempt.question, { key: 'q' }),
    h('div', {
      key: 'score', style: { ...S.card, display: 'flex', gap: 22, alignItems: 'center', flexWrap: 'wrap' },
      children: [
        h(ScoreRing, { value: attempt.overall, label: 'overall' }),
        h('div', { style: { flex: 1, minWidth: 220 }, children: [
          el('div', { fontSize: 16, fontWeight: 600, marginBottom: 4 }, fb ? fb.summary : 'Delivery scored. Content feedback could not be written this time.'),
          el('div', S.muted, headline),
          attempt.improvement != null && el('div', { marginTop: 8, fontWeight: 600, color: attempt.improvement >= 0 ? 'var(--ui-green)' : 'var(--ui-red)' },
            attempt.improvement > 0 ? `▲ ${attempt.improvement} points better than your best try` : attempt.improvement === 0 ? 'Same as your best try' : `▼ ${-attempt.improvement} below your best try`)
        ] }),
        fb && h(ScoreRing, { key: 'c', value: fb.content_score, size: 74, label: 'content' }),
        h(ScoreRing, { value: d.score, size: 74, label: 'delivery' })
      ]
    }),
    h('div', {
      key: 'replay', style: S.grid2,
      children: [
        h('div', { style: { display: 'flex', flexDirection: 'column', gap: 12 }, children: [
          videoUrl && h('video', { ref: player, src: videoUrl, controls: true, playsInline: true, style: { width: '100%', borderRadius: 12, background: '#000' } }),
          h(Timeline, { moments: d.moments || [], duration: d.duration, onSeek: seek }),
          el('div', S.muted, 'Click a mark to jump to that moment.')
        ] }),
        h('div', { style: { ...S.card, display: 'flex', flexDirection: 'column', gap: 12 }, children: Object.entries(d.scores || {}).map(([k, v]) => h(Bar, { key: k, name: SCORE_NAMES[k] || k, value: v })) })
      ]
    }),
    h('div', {
      key: 'coach', style: { ...S.card, display: 'flex', flexDirection: 'column', gap: 14 },
      children: [
        ...(fb ? [
          h(List, { key: 'w', title: 'What worked', items: fb.strengths, icon: '✓' }),
          h(List, { key: 'f', title: 'Fix next', items: fb.improvements, icon: '→' }),
          h('div', { key: 's', children: [el('div', { fontWeight: 600, fontSize: 13.5, marginBottom: 6 }, 'A stronger answer, from what you said'), el('div', { fontSize: 13.5, lineHeight: 1.6, whiteSpace: 'pre-wrap' }, fb.stronger_answer)] }),
          el('div', S.muted, `Likely follow-up: “${fb.follow_up}”`, { key: 'u' })
        ] : []),
        ...(attempt.notes || []).map((n, i) => el('div', S.muted, `ⓘ ${n}`, { key: `n${i}` }))
      ]
    }),
    h('div', { key: 'tx', style: S.card, children: [el('div', { fontWeight: 600, fontSize: 13.5, marginBottom: 8 }, 'What you said'), h(Transcript, { text: attempt.transcript || '(nothing was heard)' })] }),
    el('div', { ...S.row, justifyContent: 'center' }, [
      h(Button, { key: 'r', variant: 'outline', size: 'lg', onClick: onRetry, children: '↺ Try this one again' }),
      h(Button, { key: 'n', size: 'lg', onClick: onNext, children: isLast ? 'See my results' : 'Next question →' })
    ], { key: 'nav' })
  ])
}

// ---------- the wrap-up ----------
function Summary({ sessionId, onDone }) {
  const [data, setData] = useState(null)
  useEffect(() => { rpc('session.get', { session_id: sessionId }).then(setData).catch(() => onDone()) }, [sessionId])
  if (!data) return el('div', S.wrap, h(Busy, { text: 'Adding up your scores…' }))
  const { session, summary } = data
  const best = {}
  for (const a of session.attempts) if (!best[a.question_index] || a.overall > best[a.question_index].overall) best[a.question_index] = a
  const tries = session.attempts.length
  return el('div', S.wrap, [
    el('h1', S.h1, 'Your results', { key: 'h' }),
    h('div', { key: 's', style: { ...S.card, display: 'flex', gap: 22, alignItems: 'center' }, children: [
      h(ScoreRing, { value: summary.score ?? 0, size: 110, label: 'average best' }),
      h('div', { children: [
        el('div', { fontSize: 16, fontWeight: 600 }, `${summary.answered} of ${summary.questions} questions answered in ${tries} ${tries === 1 ? 'take' : 'takes'}`),
        summary.fillers_per_minute != null && el('div', { ...S.muted, marginTop: 4 }, `${summary.fillers_per_minute} filler words per minute on average`)
      ] })
    ] }),
    h('div', { key: 'q', style: S.card, children: session.questions.map((q, i) => h('div', {
      key: i, style: { display: 'flex', justifyContent: 'space-between', gap: 12, padding: '9px 0', borderTop: i ? '1px solid var(--ui-stroke-secondary)' : 'none', fontSize: 13.5 },
      children: [el('span', null, `${i + 1}. ${q.question}`), el('span', { fontWeight: 600, color: best[i] ? scoreColor(best[i].overall) : 'var(--ui-text-tertiary)' }, best[i] ? best[i].overall : 'skipped')]
    })) }),
    el('div', { ...S.row, justifyContent: 'center' }, h(Button, { size: 'lg', onClick: onDone, children: 'Practise again' }), { key: 'b' })
  ])
}

// ---------- page ----------
function RehearsePage() {
  const [view, setView] = useState({ name: 'home' })
  const [engine, setEngine] = useState('starting')

  useEffect(() => {
    let stop = false
    rpc('engine.prepare')
      .then(async r => {
        if (r.ready) return setEngine('ready')
        await waitJob(r.job, s => !stop && setEngine(s))
        !stop && setEngine('ready')
      })
      .catch(() => !stop && setEngine('error'))
    return () => { stop = true }
  }, [])

  const go = useCallback(v => setView(v), [])
  const page = {
    home: () => h(Home, { engine, onStart: session => go({ name: 'room', session, index: 0 }) }),
    room: () => h(Room, {
      key: `${view.session?.id}-${view.index}-${view.take || 0}`, session: view.session, index: view.index,
      onExit: () => go({ name: 'summary', session: view.session }),
      onResult: (attempt, videoUrl) => go({ ...view, name: 'replay', attempt, videoUrl })
    }),
    replay: () => h(Replay, {
      session: view.session, index: view.index, attempt: view.attempt, videoUrl: view.videoUrl,
      isLast: view.index >= view.session.questions.length - 1,
      onRetry: () => go({ name: 'room', session: view.session, index: view.index, take: (view.take || 0) + 1 }),
      onNext: () => view.index >= view.session.questions.length - 1
        ? go({ name: 'summary', session: view.session })
        : go({ name: 'room', session: view.session, index: view.index + 1 })
    }),
    summary: () => h(Summary, { sessionId: view.session.id, onDone: () => go({ name: 'home' }) })
  }
  return el('div', S.page, page[view.name]())
}

export default {
  id: 'rehearse',
  name: 'Rehearse',
  register(ctx) {
    plugin = ctx
    ctx.registerMany([
      { id: 'page', area: ROUTES_AREA, data: { path: PATH }, render: () => h(RehearsePage, {}) },
      { id: 'nav', area: SIDEBAR_NAV_AREA, data: { path: PATH, label: 'Rehearse', codicon: 'mic' } }
    ])
  }
}
