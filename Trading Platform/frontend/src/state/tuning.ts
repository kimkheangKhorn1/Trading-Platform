import { create } from 'zustand'
import { api } from '../api/client'
import type { InputValue, TuningJob, TuningRequest } from '../api/types'
import { useIndicators } from './indicators'

const POLL_MS = 700

/** The winner written into an indicator's settings, kept so it can be undone. */
export interface AppliedTune {
  uid: string
  n: number
  before: Record<string, InputValue>
  after: Record<string, InputValue>
}

interface TuningState {
  /** indicator instance being tuned */
  uid: string | null
  job: TuningJob | null
  error: string | null
  starting: boolean
  applied: AppliedTune | null
  /** the run finished and the current settings stayed the best */
  keptCurrent: boolean
  start: (uid: string, req: TuningRequest) => Promise<void>
  stop: () => Promise<void>
  undo: () => void
  /** write any tune of the current run into the settings (e.g. a runner-up from the leaderboard) */
  apply: (n: number) => void
  reset: () => void
}

let timer: ReturnType<typeof setTimeout> | null = null

export const useTuning = create<TuningState>((set, get) => {
  const poll = async (id: string) => {
    timer = null
    try {
      const job = await api.tuning(id)
      if (get().job?.id !== id && get().job !== null) return // a newer run replaced this one
      set({ job, error: null })
      if (job.status === 'running') timer = setTimeout(() => poll(id), POLL_MS)
      else finish(job)
    } catch (e) {
      set({ error: (e as Error).message })
      timer = setTimeout(() => poll(id), POLL_MS * 3)
    }
  }

  const write = (n: number, params: Record<string, InputValue>) => {
    const uid = get().uid
    const ind = useIndicators.getState()
    const inst = ind.instances.find((i) => i.uid === uid)
    if (!uid || !inst) return
    // Undo goes back to the settings from before the run, even after applying several tunes
    const before = get().applied?.before ?? Object.fromEntries(Object.keys(params).map((k) => [k, inst.inputs[k]]))
    ind.update(uid, { inputs: { ...inst.inputs, ...params } })
    set({ applied: { uid, n, before, after: params }, keptCurrent: false })
  }

  /** At the end of a run the winner becomes the indicator's settings (Undo restores the previous ones). */
  const finish = (job: TuningJob) => {
    const best = job.best
    if (job.status === 'error' || !best?.qualified) return
    if (best.n === 1) set({ keptCurrent: true })
    else write(best.n, best.params)
  }

  return {
    uid: null,
    job: null,
    error: null,
    starting: false,
    applied: null,
    keptCurrent: false,
    start: async (uid, req) => {
      if (timer) clearTimeout(timer)
      set({ uid, job: null, error: null, starting: true, applied: null, keptCurrent: false })
      try {
        const { id } = await api.startTuning(req)
        set({ starting: false })
        await poll(id)
      } catch (e) {
        set({ starting: false, error: (e as Error).message })
      }
    },
    stop: async () => {
      const job = get().job
      if (job && job.status === 'running') await api.stopTuning(job.id).catch(() => undefined)
    },
    undo: () => {
      const a = get().applied
      if (!a) return
      const ind = useIndicators.getState()
      const inst = ind.instances.find((i) => i.uid === a.uid)
      if (inst) ind.update(a.uid, { inputs: { ...inst.inputs, ...a.before } })
      set({ applied: null })
    },
    apply: (n) => {
      const r = get().job?.top.find((t) => t.n === n) ?? get().job?.recent.find((t) => t.n === n)
      if (r) write(r.n, r.params)
    },
    reset: () => {
      if (get().job?.status === 'running') return
      set({ job: null, error: null, applied: null, keptCurrent: false })
    },
  }
})
