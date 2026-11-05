"""Штаты Бразилии."""
from __future__ import annotations

# UF -> (название по-русски, название по-португальски, регион)
STATES: dict[str, tuple[str, str, str]] = {
    "AC": ("Акри", "Acre", "Север"),
    "AL": ("Алагоас", "Alagoas", "Северо-Восток"),
    "AP": ("Амапа", "Amapá", "Север"),
    "AM": ("Амазонас", "Amazonas", "Север"),
    "BA": ("Баия", "Bahia", "Северо-Восток"),
    "CE": ("Сеара", "Ceará", "Северо-Восток"),
    "DF": ("Федеральный округ", "Distrito Federal", "Центр-Запад"),
    "ES": ("Эспириту-Санту", "Espírito Santo", "Юго-Восток"),
    "GO": ("Гояс", "Goiás", "Центр-Запад"),
    "MA": ("Мараньян", "Maranhão", "Северо-Восток"),
    "MT": ("Мату-Гросу", "Mato Grosso", "Центр-Запад"),
    "MS": ("Мату-Гросу-ду-Сул", "Mato Grosso do Sul", "Центр-Запад"),
    "MG": ("Минас-Жерайс", "Minas Gerais", "Юго-Восток"),
    "PA": ("Пара", "Pará", "Север"),
    "PB": ("Параиба", "Paraíba", "Северо-Восток"),
    "PR": ("Парана", "Paraná", "Юг"),
    "PE": ("Пернамбуку", "Pernambuco", "Северо-Восток"),
    "PI": ("Пиауи", "Piauí", "Северо-Восток"),
    "RJ": ("Рио-де-Жанейро", "Rio de Janeiro", "Юго-Восток"),
    "RN": ("Риу-Гранди-ду-Норти", "Rio Grande do Norte", "Северо-Восток"),
    "RS": ("Риу-Гранди-ду-Сул", "Rio Grande do Sul", "Юг"),
    "RO": ("Рондония", "Rondônia", "Север"),
    "RR": ("Рорайма", "Roraima", "Север"),
    "SC": ("Санта-Катарина", "Santa Catarina", "Юг"),
    "SP": ("Сан-Паулу", "São Paulo", "Юго-Восток"),
    "SE": ("Сержипи", "Sergipe", "Северо-Восток"),
    "TO": ("Токантинс", "Tocantins", "Север"),
}

ALL_UF = list(STATES.keys())
NATIONAL = "BR"


def state_name(uf: str) -> str:
    if uf == NATIONAL:
        return "Вся Бразилия"
    return STATES.get(uf, (uf, uf, ""))[0]


def scope_label(scope: list[str], short: bool = False) -> str:
    """Подпись «где»: «Вся Бразилия», «Сан-Паулу», «SP · RJ · MG»."""
    if not scope or NATIONAL in scope:
        return "Вся Бразилия"
    if len(scope) == 1:
        return f"{state_name(scope[0])} ({scope[0]})" if not short else state_name(scope[0])
    if len(scope) > 6:
        return " · ".join(scope[:6]) + f" +{len(scope) - 6}"
    return " · ".join(scope)


def normalize_scope(value) -> list[str]:
    if value is None:
        return [NATIONAL]
    if isinstance(value, str):
        value = [v for v in value.replace(";", ",").replace(" ", ",").split(",") if v]
    out = []
    for v in value:
        v = str(v).strip().upper()
        if v in ("BR", "BRASIL", "ALL", "ВСЕ"):
            return [NATIONAL]
        if v in STATES:
            out.append(v)
    return out or [NATIONAL]
