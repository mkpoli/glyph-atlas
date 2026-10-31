// The one mail the site sends: a sign-in code. It is written in the reader's site language and carries
// that language's own name for the site, the way the site header does. Its last line is the origin-bound
// form (`@host #code`) that lets a browser offer the code only on this site.
//
// The card is drawn with the site's design tokens (apps/review/src/app.css) and cannot use custom
// properties, since mail clients strip them, so each colour is written out at its light-mode value. The
// tokens below are the ones the sign-in page itself uses: --paper, --surface, --line, --accent, --muted,
// --faint, --accent-light. Keep them in step with that file, and the mark and type scale with the header
// and the six-cell code field (SignIn.svelte).
type Words = { subject: string; intro: string; expiry: string; ignore: string };
// `name` is the site's name in this language, as `app.name` holds it, and `latin` marks the one language
// whose name is already Latin: those keep the wordmark's letterspaced caps instead of the local name.
type Mail = Words & { name: string; latin?: true };
export const MAIL: Record<string, Mail> = {
  en: { name: 'GLYPH ATLAS', latin: true, subject: '{code} is your Glyph Atlas sign-in code',
    intro: 'Enter this code to sign in to Glyph Atlas.',
    expiry: 'The code works for ten minutes.', ignore: 'If you did not ask for it, ignore this mail.' },
  ja: { name: '字形大図譜', subject: '{code}は字形大図譜のサインインコードです',
    intro: '字形大図譜にサインインするには、このコードを入力してください。',
    expiry: '有効期限は10分です。', ignore: 'お心当たりがない場合は、このメールを破棄してください。' },
  'ja-x-classical': { name: '字形大圖譜', subject: '{code}ハ字形大圖譜ノ登入符號ナリ',
    intro: '字形大圖譜ニ登入スルニハ此ノ符號ヲ書入レヨ。',
    expiry: '十分間有効ナリ。', ignore: '心當リナケレバ、此ノ書簡ハ捨テ置キテ苦シカラズ。' },
  ko: { name: '자형대도보', subject: '자형대도보 로그인 코드: {code}',
    intro: '자형대도보에 로그인하려면 아래 코드를 입력하세요.',
    expiry: '10분 동안 사용할 수 있습니다.', ignore: '요청하지 않았다면 이 이메일은 무시하셔도 됩니다.' },
  'ko-Kore': { name: '字形大圖譜', subject: '字形大圖譜 로그인 코드: {code}',
    intro: '字形大圖譜에 로그인하려면 아래 코드를 入力하세요.',
    expiry: '10分 동안 使用할 수 있습니다.', ignore: '要請하지 않았다면 이 이메일은 無視하셔도 됩니다.' },
  lzh: { name: '字形大圖譜', subject: '字形大圖譜登入碼：{code}',
    intro: '欲入字形大圖譜，請輸入此碼。',
    expiry: '十分之內有效。', ignore: '非君所索，置此郵可也。' },
  vi: { name: 'Tự Hình Đại Đồ Phổ', subject: '{code} là mã đăng nhập Tự Hình Đại Đồ Phổ của bạn',
    intro: 'Nhập mã này để đăng nhập Tự Hình Đại Đồ Phổ.',
    expiry: 'Mã có hiệu lực trong mười phút.', ignore: 'Nếu không yêu cầu mã, bạn có thể bỏ qua email này.' },
  'vi-Hani': { name: '字形大圖譜', subject: '{code}羅碼登入字形大圖譜𧵑伴',
    intro: '入碼呢底登入字形大圖譜。',
    expiry: '碼𣎏效力𥪝𨒒丿。', ignore: '𠮩空要求碼，伴固體𠬃過書電子呢。' },
  'zh-Hans': { name: '字形大图谱', subject: '字形大图谱登录验证码：{code}',
    intro: '请输入以下验证码，登录字形大图谱。',
    expiry: '验证码在十分钟内有效。', ignore: '如果你没有请求验证码，请忽略此邮件。' },
  'zh-Hant': { name: '字形大圖譜', subject: '字形大圖譜登入驗證碼：{code}',
    intro: '請輸入以下驗證碼，登入字形大圖譜。',
    expiry: '驗證碼在十分鐘內有效。', ignore: '如果你沒有要求驗證碼，可以忽略這封電子郵件。' },
};

// The site's mark: three filled cells and one drawn open, as the header and the sign-in page draw it.
const MARK = `<svg width="26" height="26" viewBox="0 0 32 32" fill="none" aria-hidden="true">`
  + `<path d="M4 4h9v9H4z" fill="#6356e5"/><path d="M19 4h9v9h-9z" fill="#6356e5"/>`
  + `<path d="M4 19h9v9H4z" fill="#6356e5"/><path d="M19 19h9v9h-9z" stroke="#6356e5" stroke-width="2"/></svg>`;
// The code sits in six cells, the keyboard-friendly shape the sign-in form shows once a code is sent.
// The cell is a shade wider than its digit and the glyph is held left and right, so a wide fallback
// face in a client without the mono stack still has room.
const cells = (code: string) => code.split('').map((digit) =>
  `<td width="46" height="54" align="center" valign="middle" style="width:46px;height:54px;border:1px solid #e3e3e7;`
  + `border-radius:8px;background:#fff;font-family:ui-monospace,SFMono-Regular,Consolas,monospace;`
  + `font-size:24px;font-weight:500;line-height:54px;color:#6356e5;text-align:center">${digit}</td>`)
  .join('<td width="8" style="width:8px"></td>');

export function signInMail(locale: string, code: string, host: string) {
  const words = MAIL[locale] ?? MAIL.en;
  const subject = words.subject.replace('{code}', code);
  const text = `${words.intro}\n\n${code}\n\n${words.expiry}\n${words.ignore}\n\n@${host} #${code}\n`;
  const name = words.latin
    ? `<span style="font-size:12px;font-weight:750;letter-spacing:1.2px">${words.name}</span>`
    : `<span style="font-size:17px;font-weight:700;letter-spacing:.12em">${words.name}</span>`;
  const html = `<!doctype html><html lang="${Object.hasOwn(MAIL, locale) ? locale : 'en'}"><head>`
    + `<meta charset="utf-8"><meta name="viewport" content="width=device-width">`
    + `<meta name="color-scheme" content="light"><meta name="supported-color-schemes" content="light">`
    + `<title>${subject}</title></head>`
    + `<body style="margin:0;padding:40px 16px;background:#fafafa;font-family:Inter,'Helvetica Neue',Arial,'Noto Sans',sans-serif;color:#19191c">`
    + `<div style="max-width:440px;margin:0 auto;background:#fff;border:1px solid #e3e3e7;border-radius:16px;padding:36px 34px 28px;box-shadow:0 24px 70px #0001">`
    + `<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin:0 0 22px"><tr>`
    + `<td style="padding-right:12px;vertical-align:middle">${MARK}</td>`
    + `<td style="vertical-align:middle;color:#19191c">${name}</td></tr></table>`
    + `<p style="margin:0 0 24px;font-size:13px;line-height:1.6;color:#77777f">${words.intro}</p>`
    + `<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin:0 0 22px"><tr>${cells(code)}</tr></table>`
    + `<p style="margin:0;font-size:12px;line-height:1.6;color:#77777f">${words.expiry}<br>${words.ignore}</p>`
    + `</div><p style="margin:16px auto 0;max-width:440px;font-size:11px;line-height:1.5;color:#a0a0a7;text-align:center">@${host} #${code}</p>`
    + `</body></html>`;
  return { subject, text, html };
}
