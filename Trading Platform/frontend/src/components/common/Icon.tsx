const PATHS: Record<string, string> = {
  eye: 'M1.5 9s2.8-5 7.5-5 7.5 5 7.5 5-2.8 5-7.5 5-7.5-5-7.5-5Zm7.5 2.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5Z',
  eyeOff: 'M2 2l14 14M7.2 4.2A7.6 7.6 0 0 1 9 4c4.7 0 7.5 5 7.5 5a12 12 0 0 1-2 2.6M11.6 11.8A3.9 3.9 0 0 1 9 12.5c-4.7 0-7.5-3.5-7.5-3.5a12.6 12.6 0 0 1 2.9-3.1',
  gear: 'M7.7 1h2.6l.4 2.1 1.4.6 1.8-1.2 1.6 1.6-1.2 1.8.6 1.4 2.1.4v2.6l-2.1.4-.6 1.4 1.2 1.8-1.6 1.6-1.8-1.2-1.4.6-.4 2.1H7.7l-.4-2.1-1.4-.6-1.8 1.2-1.6-1.6 1.2-1.8-.6-1.4-2.1-.4V7.7l2.1-.4.6-1.4-1.2-1.8 1.6-1.6 1.8 1.2 1.4-.6ZM9 6a3 3 0 1 0 0 6 3 3 0 0 0 0-6Z',
  close: 'M4 4l10 10M14 4 4 14',
  plus: 'M9 3v12M3 9h12',
  chevronDown: 'M5 7l4 4 4-4',
  chevronUp: 'M5 11l4-4 4 4',
  chevronRight: 'M7 5l4 4-4 4',
  play: 'M6 4v10l8-5Z',
  pause: 'M6 4v10M12 4v10',
  step: 'M5 4v10l7-5ZM14 4v10',
  replay: 'M3 9a6 6 0 1 0 2-4.5M3 3v3.5h3.5',
  database: 'M3 4.5C3 3.1 5.7 2 9 2s6 1.1 6 2.5S12.3 7 9 7 3 5.9 3 4.5Zm0 0v9C3 14.9 5.7 16 9 16s6-1.1 6-2.5v-9M3 9c0 1.4 2.7 2.5 6 2.5S15 10.4 15 9',
  search: 'M8 13A5 5 0 1 0 8 3a5 5 0 0 0 0 10Zm3.5-1.5L15 15',
  calendar: 'M3 5h12v10H3ZM3 8h12M6 3v3M12 3v3',
  check: 'M4 9.5l3.2 3L14 6',
  warning: 'M9 2.5 16.5 15h-15ZM9 7v4M9 12.5v.5',
  info: 'M9 16A7 7 0 1 0 9 2a7 7 0 0 0 0 14Zm0-8.5V12M9 5.5V6',
  upload: 'M9 12V3M5.5 6.5 9 3l3.5 3.5M3 12v3h12v-3',
  trash: 'M3.5 5h11M7 5V3h4v2M5 5l.8 10h6.4L13 5',
  target: 'M9 16A7 7 0 1 0 9 2a7 7 0 0 0 0 14Zm0-4a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z',
  realtime: 'M3 4v10M7 4l6 5-6 5Z',
  flask: 'M7 2h4M7.5 2v5L3.5 14.5c-.4.8.1 1.5 1 1.5h9c.9 0 1.4-.7 1-1.5L10.5 7V2',
}

export function Icon({ name, size = 18, className, title }: { name: keyof typeof PATHS | string; size?: number; className?: string; title?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 18 18" fill="none" stroke="currentColor" strokeWidth={1.4}
      strokeLinecap="round" strokeLinejoin="round" className={className} style={{ display: 'block', flexShrink: 0 }} aria-hidden={title ? undefined : true}>
      {title ? <title>{title}</title> : null}
      <path d={PATHS[name] ?? ''} />
    </svg>
  )
}

export function Spinner({ size = 14 }: { size?: number }) {
  return <span className="spinner" style={{ width: size, height: size }} aria-label="loading" />
}
