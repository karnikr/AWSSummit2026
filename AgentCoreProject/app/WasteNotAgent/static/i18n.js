// UI translations for English and Arabic. Arabic switches the page to RTL.
const I18N = {
  en: {
    brand: "WasteNot",
    tagline: "Food rescue & redistribution · real-time matching + routing",
    report: "Report surplus",
    pickup: "Pickup location",
    foodType: "Food type",
    dietary: "Dietary info",
    portions: "Portions",
    hours: "Hours to expiry",
    run: "Run rescue →",
    runningBtn: "Running agent…",
    impact: "Impact",
    meals: "meals rescued",
    co2: "kg CO₂ avoided",
    cost: "cost avoided",
    route: "km route",
    reasoning: "Agent reasoning",
    thinking: "thinking…",
    prompt: "Submit a donation to see the agent explain each decision — safe-window classification, matching, splitting, routing, dispatch, and impact.",
    running: "The agent is reasoning through the rescue…",
    stop: "Stop",
    noDriver: "No driver available — dispatch pending.",
    splitAcross: (n) => `Split across ${n} recipients`,
    unallocated: (n) => `${n} portions unallocated`,
    excluded: (n) => `${n} excluded on dietary rule`,
  },
  ar: {
    brand: "بلا هدر",
    tagline: "إنقاذ الطعام وإعادة توزيعه · مطابقة وتوجيه فوري",
    report: "الإبلاغ عن فائض",
    pickup: "موقع الاستلام",
    foodType: "نوع الطعام",
    dietary: "المعلومات الغذائية",
    portions: "الحصص",
    hours: "الساعات حتى الانتهاء",
    run: "تنفيذ الإنقاذ ←",
    runningBtn: "...جارٍ التشغيل",
    impact: "الأثر",
    meals: "وجبة تم إنقاذها",
    co2: "كجم ثاني أكسيد الكربون",
    cost: "التكلفة الموفرة",
    route: "كم للمسار",
    reasoning: "استدلال الوكيل",
    thinking: "...يفكر",
    prompt: "أرسل تبرعًا لترى الوكيل يشرح كل قرار — تصنيف نافذة الأمان، المطابقة، التقسيم، التوجيه، الإرسال، والأثر.",
    running: "...الوكيل يحلل عملية الإنقاذ",
    stop: "محطة",
    noDriver: "لا يوجد سائق متاح — الإرسال معلّق.",
    splitAcross: (n) => `تم التقسيم على ${n} مستفيدين`,
    unallocated: (n) => `${n} حصة غير مخصصة`,
    excluded: (n) => `${n} مستبعد بسبب القاعدة الغذائية`,
  },
};

let CURRENT_LANG = "en";

function t(key, arg) {
  const dict = I18N[CURRENT_LANG] || I18N.en;
  const v = dict[key];
  if (v === undefined) return key;
  return typeof v === "function" ? v(arg) : v;
}

function applyLang(lang) {
  if (!I18N[lang]) return;
  CURRENT_LANG = lang;
  const dict = I18N[lang];
  const html = document.documentElement;
  html.lang = lang;
  html.dir = lang === "ar" ? "rtl" : "ltr";

  // Translate all elements tagged with data-i18n (skip nested markup safely).
  document.querySelectorAll("[data-i18n]").forEach((el) => {
    const key = el.getAttribute("data-i18n");
    if (!dict[key] || typeof dict[key] !== "string") return;
    // Safety: never overwrite an element that wraps child controls (inputs/selects),
    // which would remove them. Only translate leaf/text elements.
    if (el.children.length === 0) el.textContent = dict[key];
  });

  // Keep the submit button label in sync (it is not idle when disabled).
  const btn = document.getElementById("submit-btn");
  if (btn && !btn.disabled) btn.textContent = dict.run;

  const toggle = document.getElementById("lang-toggle");
  if (toggle) {
    toggle.classList.toggle("ar", lang === "ar");
    toggle.setAttribute("aria-checked", lang === "ar" ? "true" : "false");
  }
}

// Wire the single sliding toggle once the DOM is ready.
function initLangToggle() {
  const langToggle = document.getElementById("lang-toggle");
  if (!langToggle) return;
  const flip = () => applyLang(CURRENT_LANG === "ar" ? "en" : "ar");
  langToggle.addEventListener("click", flip);
  langToggle.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      flip();
    }
  });
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initLangToggle);
} else {
  initLangToggle();
}
