import { useMemo, useState } from 'react'
import { useApp } from '../state/app'
import { useIndicators } from '../state/indicators'
import { Icon } from './common/Icon'
import { Modal } from './common/Modal'

export function IndicatorsDialog() {
  const scripts = useApp((s) => s.scripts)
  const close = () => useApp.getState().openDialog(null)
  const add = useIndicators((s) => s.add)
  const instances = useIndicators((s) => s.instances)
  const [q, setQ] = useState('')
  const list = useMemo(
    () => scripts.filter((s) => `${s.name} ${s.description} ${s.category}`.toLowerCase().includes(q.toLowerCase())),
    [scripts, q],
  )
  return (
    <Modal title="Indicators, metrics & strategies" onClose={close} width={620}>
      <div className="search-box">
        <Icon name="search" size={16} />
        <input autoFocus className="input bare" placeholder="Search" value={q} onChange={(e) => setQ(e.target.value)} />
      </div>
      <div className="script-list">
        {list.map((s) => {
          const count = instances.filter((i) => i.scriptId === s.id).length
          return (
            <button key={s.id} className="script-item" onClick={() => { add(s); close() }}>
              <div className="script-main">
                <span className="script-name">{s.name}</span>
                {s.strategies.length ? <span className="badge strat">{s.strategies.length} strateg{s.strategies.length > 1 ? 'ies' : 'y'}</span> : null}
                {count ? <span className="badge">on chart ×{count}</span> : null}
              </div>
              <div className="script-desc muted">{s.description}</div>
            </button>
          )
        })}
        {list.length === 0 ? <div className="muted pad">No scripts match.</div> : null}
      </div>
      <p className="muted small note">
        Converted Pine indicators will appear here once their Python modules are added to the engine's registry.
      </p>
    </Modal>
  )
}
