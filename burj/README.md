# Horoscope JSON API — GitHub Pages

Ushbu repository Mail.ru'dan goroskoplarni yig'adi, DeepSeek orqali o'zbekchaga tarjima qiladi va tayyor JSON'larni GitHub Pages orqali ochiq URL sifatida beradi.

## Ishlash tartibi

Birinchi ishga tushishda:

`Mail.ru -> yesterday + today + tomorrow -> DeepSeek -> JSON`

Keyingi kunlarda:

`kechagi/today/tomorrow fayllari rotatsiya qilinadi -> faqat yangi tomorrow scrape qilinadi -> faqat yangi tomorrow tarjima qilinadi`

Natijada sayt va Telegram bot doim quyidagi uchta URL'dan o'qishi mumkin:

- `horo_data/yesterday.json`
- `horo_data/today.json`
- `horo_data/tomorrow.json`

Tarjima qilingan `translated` maydoni Telegram uchun qulay `\n\n` abzatslar bilan saqlanadi. Qo'shimcha ravishda `translated_paragraphs` va xavfsiz `translated_html` maydonlari ham beriladi.

## GitHub'da sozlash

1. Ushbu fayllarni yangi repository'ga yuklang.
2. Repository Settings -> Secrets and variables -> Actions -> New repository secret.
3. Secret nomi: `DEEPSEEK_API_KEY`.
4. Qiymatga DeepSeek API kalitingizni qo'ying.
5. Settings -> Pages -> Build and deployment -> Source: **Deploy from a branch**.
6. Branch sifatida `main`, folder sifatida `/ (root)` tanlang.
7. Actions bo'limidan **Update horoscopes** workflow'ini bir marta `Run workflow` bilan ishga tushiring.

Birinchi manual run muvaffaqiyatli bo'lsa, `horo_data` ichida uchta tayyor JSON paydo bo'ladi.

## Vaqt

Workflow har kuni 04:00 Asia/Tashkent uchun 23:00 UTC da rejalashtirilgan.

## Muhim

`DEEPSEEK_API_KEY` hech qachon repository kodiga yozilmaydi. U faqat GitHub Actions Secret orqali beriladi.
