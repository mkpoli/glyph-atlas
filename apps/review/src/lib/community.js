// The community’s chat: one Matrix space, bridged to a Discord server, a Telegram group and IRC
// channels on Libera.Chat, so a message sent in one place appears in all; QQ is bridged to the Chinese
// room alone. The Matrix door opens the space in the community's Element at chat.glyphatlas.org, where a
// Glyph Atlas account signs in. Each `mark` names a `LinkMark`.
export const SPACE = '#glyphatlas:glyphatlas.org'
export const CHATS = [
  { name: 'Matrix', mark: 'matrix', href: 'https://chat.glyphatlas.org/#/room/#glyphatlas:glyphatlas.org' },
  { name: 'Discord', mark: 'discord', href: 'https://discord.gg/DR25YeAYSg' },
  { name: 'Telegram', mark: 'telegram', href: 'https://t.me/+Q4wiIzMijRwzOWE1' },
  { name: 'QQ', mark: 'qq', href: 'https://qm.qq.com/q/bKrGelyYIE' },
  { name: 'IRC', mark: 'irc', href: 'https://web.libera.chat/#glyphatlas' },
]

// Each Matrix room's channel on Libera.Chat, named as the room is. `language` marks the room a page
// in that language starts with selected; Libera's web chat joins every channel listed in its hash.
export const IRC_WEB = 'https://web.libera.chat/'
export const IRC_CHANNELS = [
  { channel: '#glyphatlas', name: '歡迎 · Welcome' },
  { channel: '#glyphatlas-en', name: 'English', lang: 'en', language: 'en' },
  { channel: '#glyphatlas-zh', name: '中文', lang: 'zh', language: 'zh' },
  { channel: '#glyphatlas-ja', name: '日本語', lang: 'ja', language: 'ja' },
  { channel: '#glyphatlas-ko', name: '한국어', lang: 'ko', language: 'ko' },
  { channel: '#glyphatlas-vi', name: 'Tiếng Việt', lang: 'vi', language: 'vi' },
  { channel: '#glyphatlas-curiosities', name: '奇字 · Curiosities' },
  { channel: '#glyphatlas-questions', name: '詢問 · Questions' },
  { channel: '#glyphatlas-feedback', name: '聯絡 · Feedback' },
  { channel: '#glyphatlas-dev', name: '開發 · Dev' },
]

// Other groups where people with the same interests gather, each with its own focus. Each is named as
// it names itself and marked with the language spoken there.
export const OTHER_GROUPS = [
  { name: '互助認字', lang: 'zh-Hant', language: '中文', href: 'https://t.me/+OH7sJST3huo1ODll', network: 'Telegram', mark: 'telegram' },
  { name: '漢字泉 #集字厨ノ天下', lang: 'ja', language: '日本語', href: 'https://discord.gg/PMMRZ7Dw8D', network: 'Discord', mark: 'discord' },
]
