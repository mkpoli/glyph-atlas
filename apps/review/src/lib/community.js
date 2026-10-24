// The community's chat: one Matrix space, bridged to a Discord server, a Telegram group and an IRC channel
// on Libera.Chat, so a message sent in one place appears in all. The Matrix door opens the space in the
// community's Element at chat.glyphatlas.org, where a Glyph Atlas account signs in. Each `mark` names a
// `LinkMark`.
export const SPACE = '#glyphatlas:glyphatlas.org'
export const CHATS = [
  { name: 'Matrix', mark: 'matrix', href: 'https://chat.glyphatlas.org/#/room/#glyphatlas:glyphatlas.org' },
  { name: 'Discord', mark: 'discord', href: 'https://discord.gg/DR25YeAYSg' },
  { name: 'Telegram', mark: 'telegram', href: 'https://t.me/+Q4wiIzMijRwzOWE1' },
  { name: 'IRC', mark: 'irc', href: 'https://web.libera.chat/#glyphatlas' },
]

// Other groups where people with the same interests gather, each with its own focus. Each is named as
// it names itself and marked with the language spoken there.
export const OTHER_GROUPS = [
  { name: '互助認字', lang: 'zh-Hant', language: '中文', href: 'https://t.me/+OH7sJST3huo1ODll', network: 'Telegram', mark: 'telegram' },
  { name: '漢字泉 #集字厨ノ天下', lang: 'ja', language: '日本語', href: 'https://discord.gg/PMMRZ7Dw8D', network: 'Discord', mark: 'discord' },
]
