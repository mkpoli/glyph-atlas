// Helpers for the contact page's live chat preview: room labels in the reader's language,
// default room selection, consecutive-sender grouping, and safe URL splitting.

export const ROOM_META = {
  '#welcome:glyphatlas.org': { key: 'welcome' },
  '#en:glyphatlas.org': { label: 'English', lang: 'en', language: 'en' },
  '#zh:glyphatlas.org': { label: '中文', lang: 'zh', language: 'zh' },
  '#ja:glyphatlas.org': { label: '日本語', lang: 'ja', language: 'ja' },
  '#ko:glyphatlas.org': { label: '한국어', lang: 'ko', language: 'ko' },
  '#vi:glyphatlas.org': { label: 'Tiếng Việt', lang: 'vi', language: 'vi' },
  '#questions:glyphatlas.org': { key: 'questions' },
  '#curiosities:glyphatlas.org': { key: 'curiosities' },
  '#dev:glyphatlas.org': { key: 'dev' },
}

const NETWORK_MARKS = { Discord: 'discord', Telegram: 'telegram', QQ: 'qq', IRC: 'irc' }
const GROUP_WINDOW_MS = 5 * 60 * 1000
const URL_RE = /https?:\/\/[^\s<>]+/g
const TRAILING_PUNCT = /[.,;:!?)\]）】」』'"、。，；：！？]+$/

/** Localized label for a preview room: topic rooms from the catalogue, language rooms in their own script. */
export function roomLabel(room, t) {
  const meta = ROOM_META[room.alias]
  if (meta?.key) return t(`contact.preview.room.${meta.key}`)
  if (meta?.label) return meta.label
  return room.name
}

/** BCP 47 language tag for a language room's autonym, or undefined for localized topic rooms. */
export const roomLang = room => ROOM_META[room.alias]?.lang

/** The room matching the page's spoken language when available, otherwise the first active room. */
export function defaultRoom(rooms, spoken) {
  if (!rooms?.length) return null
  const match = rooms.find(room => ROOM_META[room.alias]?.language === spoken)
  return (match ?? rooms[0]).alias
}

/** LinkMark icon name for a message sender: its bridged network mark, or Matrix for native accounts. */
export const senderMark = name => NETWORK_MARKS[name] ?? 'matrix'

/** Merge consecutive messages from the same sender within five minutes on the same day. */
export function groupMessages(messages) {
  const groups = []
  for (const message of messages ?? []) {
    const prev = groups.at(-1)
    const last = prev?.items.at(-1)
    const sameSender = prev && prev.name === message.name
    const closeInTime = last && Math.abs(message.at - last.at) <= GROUP_WINDOW_MS
      && new Date(message.at).toDateString() === new Date(last.at).toDateString()
    if (sameSender && closeInTime) {
      prev.items.push(message)
    } else {
      groups.push({ name: message.name, mark: senderMark(message.name), at: message.at, items: [message] })
    }
  }
  return groups
}

/** Split plain message text into text and safe http(s) link segments, trimming trailing sentence punctuation. */
export function linkify(text) {
  const parts = []
  let cursor = 0
  for (const match of String(text ?? '').matchAll(URL_RE)) {
    const start = match.index
    if (start > cursor) parts.push({ type: 'text', text: text.slice(cursor, start) })
    let raw = match[0]
    // Keep balanced closing parentheses inside URLs like Wikipedia links, stripping unmatched trailing punctuation.
    while (raw.length && TRAILING_PUNCT.test(raw)) {
      if (raw.at(-1) === ')' && (raw.match(/\(/g)?.length ?? 0) >= (raw.match(/\)/g)?.length ?? 0)) break
      raw = raw.slice(0, -1)
    }
    if (raw) {
      parts.push({ type: 'link', href: raw, label: raw.replace(/^https?:\/\//, '').replace(/\/$/, '') })
      cursor = start + raw.length
    } else {
      cursor = start
    }
  }
  if (cursor < text.length) parts.push({ type: 'text', text: text.slice(cursor) })
  return parts.length ? parts : [{ type: 'text', text: String(text ?? '') }]
}
