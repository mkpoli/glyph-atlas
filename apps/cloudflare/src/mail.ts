// The one mail the site sends: a sign-in code. It is written in the reader's site language, and its
// last line is the origin-bound form (`@host #code`) that lets a browser offer the code only on this site.
type Words = { subject: string; intro: string; expiry: string; ignore: string };
export const MAIL: Record<string, Words> = {
  en: { subject: '{code} is your Glyph Atlas sign-in code', intro: 'Enter this code to sign in to Glyph Atlas:',
    expiry: 'It works for ten minutes.', ignore: 'If you did not ask for it, you can ignore this mail.' },
  ja: { subject: '{code}は字形大図譜のサインインコードです', intro: '字形大図譜にサインインするには、このコードを入力してください：',
    expiry: '有効期限は10分です。', ignore: 'お心当たりがない場合は、このメールを破棄してください。' },
  'ja-x-classical': { subject: '{code}ハ字形大圖譜ノ登入符號ナリ', intro: '字形大圖譜ニ登入スルニハ此ノ符號ヲ書入レヨ：',
    expiry: '十分間有効ナリ。', ignore: '心當リナケレバ、此ノ書簡ハ捨テ置キテ苦シカラズ。' },
  ko: { subject: '자형대도보 로그인 코드: {code}', intro: '자형대도보에 로그인하려면 아래 코드를 입력하세요.',
    expiry: '10분 동안 사용할 수 있습니다.', ignore: '요청하지 않았다면 이 이메일은 무시하셔도 됩니다.' },
  'ko-Kore': { subject: '字形大圖譜 로그인 코드: {code}', intro: '字形大圖譜에 로그인하려면 아래 코드를 入力하세요.',
    expiry: '10分 동안 使用할 수 있습니다.', ignore: '要請하지 않았다면 이 이메일은 無視하셔도 됩니다.' },
  lzh: { subject: '字形大圖譜登入碼：{code}', intro: '欲入字形大圖譜，請輸入此碼：',
    expiry: '十分之內有效。', ignore: '非君所索，置此郵可也。' },
  vi: { subject: '{code} là mã đăng nhập Tự Hình Đại Đồ Phổ của bạn', intro: 'Nhập mã này để đăng nhập Tự Hình Đại Đồ Phổ:',
    expiry: 'Mã có hiệu lực trong mười phút.', ignore: 'Nếu không yêu cầu mã, bạn có thể bỏ qua email này.' },
  'vi-Hani': { subject: '{code}羅碼登入字形大圖譜𧵑伴', intro: '入碼呢底登入字形大圖譜：',
    expiry: '碼𣎏效力𥪝𨒒丿。', ignore: '𠮩空要求碼，伴固體𠬃過書電子呢。' },
  'zh-Hans': { subject: '字形大图谱登录验证码：{code}', intro: '请输入以下验证码，登录字形大图谱：',
    expiry: '验证码在十分钟内有效。', ignore: '如果你没有请求验证码，请忽略此邮件。' },
  'zh-Hant': { subject: '字形大圖譜登入驗證碼：{code}', intro: '請輸入以下驗證碼，登入字形大圖譜：',
    expiry: '驗證碼在十分鐘內有效。', ignore: '如果你沒有要求驗證碼，可以忽略這封電子郵件。' },
};

export function signInMail(locale: string, code: string, host: string) {
  const words = MAIL[locale] ?? MAIL.en;
  const subject = words.subject.replace('{code}', code);
  const text = `${words.intro}\n\n${code}\n\n${words.expiry}\n${words.ignore}\n\n@${host} #${code}\n`;
  const html = `<!doctype html><html lang="${Object.hasOwn(MAIL, locale) ? locale : 'en'}"><body style="margin:0;padding:32px 16px;background:#fafafa;font-family:Inter,'Helvetica Neue',Arial,sans-serif;color:#19191c">
<div style="max-width:440px;margin:auto;background:#fff;border:1px solid #e3e3e7;border-radius:12px;padding:32px">
<p style="margin:0 0 4px;font-size:11px;letter-spacing:1.5px;color:#77777f">GLYPH ATLAS</p>
<p style="margin:16px 0;font-size:15px;line-height:1.5">${words.intro}</p>
<p style="margin:24px 0;font:600 34px/1 ui-monospace,SFMono-Regular,Consolas,monospace;letter-spacing:10px;color:#6356e5">${code}</p>
<p style="margin:0;font-size:13px;line-height:1.6;color:#77777f">${words.expiry}<br>${words.ignore}</p>
</div><p style="margin:16px auto 0;max-width:440px;font-size:11px;color:#a0a0a7">@${host} #${code}</p></body></html>`;
  return { subject, text, html };
}
