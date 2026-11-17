"""Extract a concise product class, never feed previous article copy to Planner."""
import re

# Existing published headings with editorial copy not reducible to a short product class.
# Used only when historical metadata has no explicit product_theme.
LEGACY_THEMES = {
    "facial-beauty-devices-6-picks": "美顔器",
    "washing-machine-drum-cleaners-6-picks": "洗濯槽クリーナー",
    "disinfectant-sprays-6-picks": "除菌・消臭スプレー",
    "beauty-collagen-supplements-6-picks": "コラーゲンサプリメント",
    "cat-litter-6-picks": "猫砂",
    "microwave-steamer-containers-for-weeknight-meals": "電子レンジ用蒸し調理容器",
    "usb-heated-insoles-6-picks": "電熱インソールとヒーターソックス",
    "sweet-potato-makers-6-picks": "焼き芋メーカー・焼き芋鍋",
    "christmas-cakes-6-picks": "クリスマスケーキ",
    "personal-blenders-6-picks": "パーソナルブレンダー",
    "shoe-dryers-6-picks": "靴乾燥機",
    "rice-storage-bins-6-picks": "密閉米びつ",
    "home-water-servers-6-picks": "ウォーターサーバー",
    "lip-oils-glosses-6-picks": "リップオイル・グロス",
}


def product_theme(record):
    for key in ("product_theme", "theme"):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    slug = str(record.get("slug") or "")
    if slug in LEGACY_THEMES:
        return LEGACY_THEMES[slug]
    text = str(record.get("title") or record.get("query") or "").strip()
    if not text:
        raise ValueError("product theme source missing")
    text = re.split(r"[｜|]", text, maxsplit=1)[0]
    text = re.split(r"[。！？!]", text)[-1].strip()
    text = re.sub(r"^.+で選ぶ(?=[^ ]{2,})", "", text)
    text = re.sub(r"^(?:おすすめの)", "", text)
    text = re.sub(r"(?:[0-9０-９]+(?:選|商品|製品|機種|件))?(?:を)?比較(?:は.*)?$", "", text)
    text = re.sub(r"(?:[0-9０-９]+(?:選|商品|製品|機種|件))(?:を)?$", "", text)
    text = re.sub(r"(?:の比較は.*|の選び方|を比較|を選ぶ|比較|おすすめ|ランキング)\s*$", "", text)
    text = re.split(r"、", text)[-1]
    text = text.strip(" \t　。、！!？?:：・-")
    if not text or len(text) > 55:
        raise ValueError("product theme is ambiguous or empty")
    return text
