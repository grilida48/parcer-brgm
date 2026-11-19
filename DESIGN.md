---
name: DUS Leads
description: Русскоязычная рабочая база организаций и контактов.
colors:
  accent-ink: "#fff"
  accent-hover: "#7651ca"
  scrim: "#0008"
  elevation-shadow: "#0003"
  clear: "transparent"
  bg: "#17171c"
  surface: "#1f1f26"
  surface-alt: "#24242d"
  sidebar: "#131318"
  text: "#f0eff5"
  muted: "#adabbc"
  border: "#35343f"
  accent: "#b19aff"
  accent-bg: "#33294f"
  accent-solid: "#8060d8"
  danger: "#ffaaa9"
  green: "#99d9b2"
  yellow: "#ecd387"
  orange: "#efb28b"
  light-bg: "#f5f5f8"
  light-surface: "#fff"
  light-surface-alt: "#eeeef4"
  light-sidebar: "#eeeef3"
  light-text: "#24222d"
  light-muted: "#625f70"
  light-border: "#d8d6e0"
  light-accent: "#6441b5"
  light-accent-bg: "#e8dff9"
  light-accent-solid: "#6c48bd"
  light-danger: "#a52d2d"
  light-green: "#226740"
  light-yellow: "#755b13"
  light-orange: "#894b1b"
typography:
  headline:
    fontFamily: "Segoe UI, Arial, sans-serif"
    fontSize: "28px"
    fontWeight: 650
    lineHeight: 1.25
    letterSpacing: "-0.025em"
  title:
    fontFamily: "Segoe UI, Arial, sans-serif"
    fontSize: "17px"
    fontWeight: 600
    lineHeight: 1.35
  body:
    fontFamily: "Segoe UI, Arial, sans-serif"
    fontSize: "14px"
  label:
    fontFamily: "Segoe UI, Arial, sans-serif"
    fontSize: "11px"
    fontWeight: 500
rounded:
  badge: "5px"
  control: "7px"
  brand: "8px"
  card: "9px"
  panel: "12px"
spacing:
  button-gap: "8px"
  panel: "23px"
  content: "34px"
components:
  button-primary:
    backgroundColor: "{colors.accent-solid}"
    textColor: "{colors.accent-ink}"
    rounded: "{rounded.control}"
    padding: "9px 14px"
  button-primary-hover:
    backgroundColor: "{colors.accent-hover}"
  button-secondary:
    backgroundColor: "{colors.surface-alt}"
    textColor: "{colors.text}"
    rounded: "{rounded.control}"
    padding: "9px 14px"
  panel:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    rounded: "{rounded.panel}"
    padding: "{spacing.panel}"
---

# Design System: DUS Leads

## Overview

Рабочий интерфейс с плотными таблицами, формами и боковой навигацией. Графитовые поверхности и фиолетовый акцент заданы пользователем. Светлая тема сохраняет те же смысловые роли. Текст интерфейса — русский.

## Colors

Фиолетовый обозначает действия, ссылки и выбранный раздел. `accent-solid` применяется под белым текстом кнопок; `accent` — к тексту на поверхности. `bg` задаёт фон страницы, `sidebar` — навигацию, `surface` — панели, `surface-alt` — вторичные поверхности, `border` — разделители, `text` и `muted` — основной и дополнительный текст. `light-*` фиксируют переопределения светлой темы.

`danger` обозначает ошибки; `green`, `yellow`, `orange` используются в оценках вместе с числом. Статусы CRM имеют текстовые названия.

## Typography

Segoe UI с Arial и sans-serif в резерве. Заголовок страницы: 28px, вес 650, высота строки 1.25, интервал −0.025em; на телефоне — 24px. Заголовки секций: 17px/1.35, вес 600. Размер корня — 14px; таблицы — 12px, их заголовки — 11px. Числовые метрики и оценки используют tabular-nums.

## Layout

Фиксированная боковая панель 234px, верхняя строка 71px, отступ рабочей области 34px. От 1500px контент ограничен 1550px. Панели имеют внутренний отступ 23px. Метрики образуют четыре колонки, при 1000px — три, при 760px — две.

На 1250px боковая панель сужается до 215px, на 1000px — до 195px. На 760px навигация открывается поверх страницы, верхняя строка имеет высоту 61px, горизонтальные отступы контента — 17px. Таблицы прокручиваются внутри своей области. Колонки CRM прокручиваются горизонтально: ширина 310px, на телефоне 270px. Панель клиента справа имеет ширину 650px с ограничением 100%.

## Elevation & Depth

Постоянные панели разделяются фоном и рамкой 1px. Их CSS не использует тени. Панель клиента и мобильная навигация располагаются над scrim. Токен `elevation-shadow` объявлен, но не применяется к постоянным панелям.

## Shapes

Скругления: поля, кнопки и навигация — 7px; индикаторы и статусы — 5px; CRM-карточки и уведомления — 9px; панели — 12px. Аватары круглые.

## Components

`Button` поддерживает primary, secondary, ghost и destructive; размеры default, small и icon. Обычная кнопка имеет минимальную высоту 38px; small — 32px; icon — 36px. Hover меняет цвет поверхности, active снижает непрозрачность до .86, disabled — до .5.

Поля имеют фон `bg`, рамку `border`, отступ 10px 12px и минимальную высоту 39px. Видимый фокус интерактивных элементов — обводка 2px цвета `accent` с отступом 3px.

Таблица использует заголовок на `surface-alt`, разделители строк и подсветку строки при наведении. Ячейки имеют отступ 19px 18px. Панель таблицы не имеет внутреннего отступа. Активный раздел навигации получает `accent-bg`, текст `accent` и вес 600.

Переходы цвета кнопок длятся .15s ease-out. Панель клиента появляется за .18s ease-out, мобильная навигация перемещается за .2s ease-out. При prefers-reduced-motion анимации и переходы отключаются.

## Do's and Don'ts

- Do использовать смысловые роли цветов и обе темы из `frontend/src/tokens.css`.
- Do сохранять видимый фокус, текстовые статусы и числовые оценки.
- Do оставлять таблицам внутреннюю горизонтальную прокрутку.
- Don't заменять рабочие таблицы декоративными блоками.
- Don't добавлять декоративную анимацию или новые акценты без изменения согласованного направления.
