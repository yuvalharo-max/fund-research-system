# כלי מחקר שוק ואיתור רעיונות השקעה

כלי מחקר אישי (Streamlit) עם שני טאבים:

1. **ניתוח קרנות** — סורק את רשימת ה-Superinvestors ב-[Dataroma](https://www.dataroma.com) ומאתר מניות שקרן הגדילה בהן אחזקה ברבעון האחרון, בזמן שהמחיר צנח ב-10% ומעלה לעומת הרבעון הקודם.
2. **ניתוח מאגר Magic Formula** — מריץ את הסקרינר של [Magic Formula Investing](https://www.magicformulainvesting.com) (שווי שוק מעל מיליארד דולר) ומעשיר את התוצאות בסקטור, חברות דומות, וחיתוך עם רשימת הקרנות מטאב 1.

הכלי מיועד למשתמש בודד, ריצה מקומית. אין ייעוץ השקעות ואין ביצוע מסחר — כל השערה/סיכום הוא טקסט מבוסס-כללים על סמך נתונים ציבוריים, לא המלצה.

## הרצה מקומית

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# ערוך את .env והכנס MAGICFORMULA_EMAIL / MAGICFORMULA_PASSWORD שלך (חשבון רשום בהם ב-magicformulainvesting.com)
streamlit run app.py
```

`.env` לא נכנס ל-git — הפרטים שלך נשארים מקומיים בלבד.

## ריצה ממחשב אחר

```bash
git clone https://github.com/yuvalharo-max/fund-research-system.git
cd fund-research-system
# אותם שלבים כמו לעיל: venv, pip install, .env, streamlit run
```

## מגבלות ידועות

- **לינק לדף קשרי משקיעים (IR)** הוא היוריסטי — לינק לחיפוש Google ולעמוד Yahoo Finance של המניה, לא URL מדויק לדף ה-IR (אין מקור ציבורי אמין ל-IR URL מדויק בלי API בתשלום).
- **שבירות scraping** — ל-Dataroma ול-Magic Formula Investing אין API רשמי. שינוי במבנה ה-HTML של האתרים יכול לשבור את השליפה. אם ריצה נכשלת עם שגיאה על "מבנה האתר השתנה" — יש לבדוק ידנית את העמוד הרלוונטי בדפדפן ולעדכן את הפרסינג ב-`src/dataroma_client.py` / `src/magicformula_client.py`.
- **סקרינר Magic Formula** — מכיוון שהטופס באתר דורש התחברות כדי להיות פעיל, `src/magicformula_client.py` מגלה את שמות השדות של הטופס בזמן ריצה (אחרי login) במקום שהם קבועים בקוד. אם ההרצה הראשונה שלך נכשלת, ייתכן שצריך להתאים את הפרסינג לפי המבנה האמיתי שרק חשבון מחובר חושף.
- **אין LLM** — הסיכומים וההשערות הם טקסט מבוסס-כללים (תבניות עם המספרים שנאספו), לא ניתוח שפה חופשי. אפשר לשדרג בעתיד אם יתווסף מפתח API.
- **אין ריבוי משתמשים, ריבוי הרשאות, או ניתוח בזמן אמת** — בכוונה, מעבר לסקופ ה-MVP (ראה מסמך האפיון).
