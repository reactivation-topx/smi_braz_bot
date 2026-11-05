"""Бесплатный режим: важность новостей без ИИ.

Идея: громкую новость пишут сразу многие издания и её ищут в Google.
1. Похожие заголовки из разных лент склеиваются в одну «историю».
2. Важность = сколько разных изданий её подхватили
   + бонус, если тема в трендах Google по Бразилии
   + бонус за сильные слова («morre», «tragédia», «campeão»…) и громкие имена
   − штраф за рецепты, гороскопы, промо.
"""
from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter

from .states import STATES

STOPWORDS = set("""
a o as os um uma uns umas de do da dos das no na nos nas em por para pelo pela pelos pelas com sem sob sobre
e ou mas que se ao aos à às é são foi ser ter tem têm há como mais menos muito já não sim seu sua seus suas
ele ela eles elas isso este esta esse essa após até entre contra diz dizem veja saiba entenda vídeo video
quem qual quando onde porque hoje ontem amanhã anos ano dia dias novo nova vai vão pode podem após
""".split())

STRONG = re.compile(
    r"\b(morre|morreu|morte|falece|faleceu|velorio|tragedia|desaba|desabamento|explosao|massacre|atentado|"
    r"tiroteio|terremoto|enchente|enchentes|deslizamento|incendio|queda de aviao|acidente aereo|renuncia|"
    r"impeachment|preso|prisao|campeao|campea|conquista o titulo|vence a final|titulo|rebaixado|demitido|"
    r"anuncia aposentadoria|aposentadoria)\b"
)
FAMOUS = re.compile(
    r"\b(neymar|vini jr|vinicius junior|ancelotti|pele|anitta|ludmilla|ivete|gusttavo lima|virginia|"
    r"lula|bolsonaro|alexandre de moraes|stf|selecao|flamengo|palmeiras|corinthians|vasco|fluminense|"
    r"botafogo|gremio|internacional|cruzeiro|atletico|santos|libertadores|brasileirao|copa do brasil|"
    r"bbb|carnaval|petrobras|dolar|pix|inss|bolsa familia|enem)\b"
)
LOW = re.compile(
    r"\b(receita|receitas|horoscopo|signo|signos|como fazer|passo a passo|promocao|cupom|desconto|"
    r"loteria|mega sena|quina|lotofacil|resultado da|palpite|onde assistir|horario do jogo|escalacao)\b"
)
SPORT_WORDS = re.compile(
    r"\b(gol|jogo|partida|campeonato|brasileirao|libertadores|copa|tecnico|treinador|atacante|zagueiro|"
    r"goleiro|selecao|fifa|cbf|formula 1|f1|ufc|tenis|volei|basquete|nba|flamengo|palmeiras|corinthians|"
    r"vasco|fluminense|botafogo|gremio|cruzeiro)\b"
)
POLITICS_WORDS = re.compile(r"\b(lula|bolsonaro|governo|congresso|senado|camara|stf|ministro|eleicao|deputado|senador|presidente)\b")
ECONOMY_WORDS = re.compile(r"\b(dolar|inflacao|selic|juros|pib|bolsa|ibovespa|petrobras|imposto|salario minimo|banco central)\b")
INCIDENT_WORDS = re.compile(r"\b(morre|morte|acidente|tiroteio|preso|policia|crime|assassinato|incendio|explosao|tragedia)\b")
WEATHER_WORDS = re.compile(r"\b(chuva|chuvas|enchente|temporal|calor|onda de calor|frio|seca|ciclone|alerta da defesa civil)\b")
SHOWBIZ_WORDS = re.compile(r"\b(cantor|cantora|ator|atriz|show|novela|bbb|influenciadora|famosa|famoso|filme|serie)\b")

def fold(text: str) -> str:
    """нижний регистр, без диакритики и пунктуации"""
    t = unicodedata.normalize("NFKD", text.lower())
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    t = re.sub(r"[^\w\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


# Штаты определяем только по полным названиям, без SP и RJ: новости оттуда почти всегда общенациональные,
# а «São Paulo», «Bahia» и т. п. — ещё и названия клубов, поэтому спортивные ленты не размечаем.
STATE_PATTERNS = {
    uf: re.compile(r"\b(?:no|na|em|do|da|de|para o|para a)\s+" + re.escape(fold(name)) + r"\b")
    for uf, (_, name, _) in sorted(STATES.items(), key=lambda kv: -len(kv[1][1]))  # длинные названия первыми
    if uf not in ("SP", "RJ")
}


def tokens(title: str) -> set[str]:
    return {w for w in fold(title).split() if len(w) >= 3 and w not in STOPWORDS and not w.isdigit()}


def detect_state(text: str, topic: str) -> str:
    if topic == "sport":
        return "BR"
    t = fold(text)
    for uf, pat in STATE_PATTERNS.items():
        if pat.search(t):
            return uf
    return "BR"


def detect_category(text: str, topic: str) -> str:
    t = fold(text)
    if topic == "sport" or SPORT_WORDS.search(t) and not POLITICS_WORDS.search(t):
        return "sport"
    if WEATHER_WORDS.search(t):
        return "weather"
    if INCIDENT_WORDS.search(t):
        return "incident"
    if POLITICS_WORDS.search(t):
        return "politics"
    if ECONOMY_WORDS.search(t):
        return "economy"
    if topic == "entertainment" or SHOWBIZ_WORDS.search(t):
        return "showbiz"
    return "society"


def cluster(items: list[dict]) -> list[list[dict]]:
    """Склеивает новости об одном и том же (по редким общим словам в заголовках)."""
    toks = [tokens(it["title"]) for it in items]
    df = Counter(w for ts in toks for w in ts)
    n = max(1, len(items))
    idf = {w: math.log(1 + n / c) for w, c in df.items()}
    parent = list(range(len(items)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(items)):
        if not toks[i]:
            continue
        for j in range(i + 1, len(items)):
            if not toks[j]:
                continue
            shared = toks[i] & toks[j]
            if len(shared) < 2:
                continue
            small = toks[i] if len(toks[i]) <= len(toks[j]) else toks[j]
            w_shared = sum(idf[w] for w in shared)
            w_small = sum(idf[w] for w in small) or 1
            if w_shared / w_small >= 0.45:
                parent[find(i)] = find(j)
    groups: dict[int, list[dict]] = {}
    for i, it in enumerate(items):
        groups.setdefault(find(i), []).append(it)
    return list(groups.values())


def trend_hit(text: str, trends: list[dict]) -> int:
    """Трафик самого популярного тренда Google, совпавшего с новостью (0 — нет совпадений)."""
    t = " " + fold(text) + " "
    best = 0
    for tr in trends:
        term = fold(tr["term"])
        if not term:
            continue
        words = term.split()
        if len(words) == 1 and len(term) < 4:
            continue
        if all(f" {w} " in t for w in words):
            best = max(best, int(tr.get("traffic") or 1))
    return best


def score_cluster(group: list[dict], trends: list[dict]) -> dict:
    outlets = {(it.get("source") or "").strip().lower() for it in group}
    text = " ".join(f"{it['title']} {it.get('summary') or ''}" for it in group)
    t = fold(text)
    score = 2 + min(len(outlets), 6)  # 1 издание → 3 … 6+ изданий → 8
    traffic = trend_hit(" ".join(it["title"] for it in group), trends)
    if traffic >= 100_000:
        score += 3
    elif traffic >= 20_000:
        score += 2
    elif traffic > 0:
        score += 1
    if STRONG.search(t):
        score += 2
    if FAMOUS.search(t):
        score += 1
    if LOW.search(t):
        score -= 4
    topic = Counter(it.get("topic") or "general" for it in group).most_common(1)[0][0]
    return {
        "importance": max(1, min(10, score)),
        "category": detect_category(text, topic),
        "state": detect_state(" ".join(it["title"] for it in group), topic),
        "outlets": len(outlets),
        "trend_traffic": traffic,
    }


def pick_representative(group: list[dict]) -> dict:
    """Какую из новостей истории показывать: уже отправленную, иначе с описанием/фото, иначе самую раннюю."""
    sent = [it for it in group if it.get("breaking_sent")]
    if sent:
        return sent[0]
    return sorted(
        group,
        key=lambda it: (
            "news.google." in (it.get("url") or ""),  # прямые ссылки на издание лучше
            not it.get("summary"),
            not it.get("image_url"),
            it.get("published_at") or "",
        ),
    )[0]
