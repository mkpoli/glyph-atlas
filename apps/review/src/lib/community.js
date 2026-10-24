// The community's chat: one Matrix space, bridged to a Discord server and a Telegram group, so a message
// sent on any of them appears on all three. The Matrix door opens the space in Element on the web, where
// someone new can sign up. Each `mark` names a `LinkMark`.
export const SPACE = '#glyphatlas:glyphatlas.org'
export const CHATS = [
  { name: 'Matrix', mark: 'matrix', href: 'https://app.element.io/#/room/#glyphatlas:glyphatlas.org' },
  { name: 'Discord', mark: 'discord', href: 'https://discord.gg/DR25YeAYSg' },
  { name: 'Telegram', mark: 'telegram', href: 'https://t.me/+Q4wiIzMijRwzOWE1' },
]

// Other groups where people with the same interests gather, each with its own focus. Each is named as
// it names itself and marked with the language spoken there.
export const OTHER_GROUPS = [
  { name: '互助認字', lang: 'zh-Hant', language: '中文', href: 'https://t.me/+OH7sJST3huo1ODll', network: 'Telegram', mark: 'telegram' },
  { name: '漢字泉 #集字厨ノ天下', lang: 'ja', language: '日本語', href: 'https://discord.gg/PMMRZ7Dw8D', network: 'Discord', mark: 'discord' },
]
