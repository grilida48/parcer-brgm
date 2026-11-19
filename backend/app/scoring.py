DEFAULTS = {
    'weights': {'not_listed': 30, 'unavailable': 25, 'mobile': 15, 'rating': 10, 'reviews': 10, 'priority': 20, 'quality': -40},
    'priority_categories': ['Автосервисы', 'Стоматологии', 'Строительные компании', 'Мебель на заказ'],
    'daily_goal': 1, 'default_order_value': 15000, 'api_request_budget': 100,
    'api_request_cost_rub': 1.0, 'api_spend_limit_rub': 100.0,
}
STATUSES = ['Новый', 'Не связывались', 'Не дозвонились', 'Связались', 'Отказался', 'Заинтересован', 'Отправлена демонстрация', 'Отправлено коммерческое предложение', 'Ведутся переговоры', 'Ожидается оплата', 'Оплатил', 'В разработке', 'Заказ завершён', 'Не связываться повторно']
PAID = ['Оплатил', 'В разработке', 'Заказ завершён']


def score_lead(lead, config):
    w = config['weights']
    reasons = []
    def add(key, label):
        reasons.append({'rule': label, 'points': w[key]})
    if not lead.website:
        add('not_listed', 'Сайт не указан; его отсутствие не подтверждено')
    if lead.website_status == 'unavailable':
        add('unavailable', 'Недоступность подтверждена повторной проверкой')
    audit = lead.audit or {}
    if audit.get('mobile_friendly') is False:
        add('mobile', 'Не найден признак мобильной адаптации')
    if lead.rating is not None and lead.rating > 4.5:
        add('rating', 'Рейтинг выше 4,5')
    if lead.review_count is not None and lead.review_count > 30:
        add('reviews', 'Более 30 отзывов')
    if (lead.category or '').casefold() in [x.casefold() for x in config['priority_categories']]:
        add('priority', 'Приоритетная категория')
    if lead.website_status == 'working' and all(audit.get(k) is True for k in ('mobile_friendly', 'has_contacts', 'has_form', 'has_seo', 'https')) and (audit.get('response_ms') or 999999) < 2000:
        add('quality', 'Сайт проходит базовые проверки качества')
    lead.score_reasons = reasons
    lead.score = max(0, min(100, sum(x['points'] for x in reasons)))
    return lead.score
