from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Category:
    slug: str
    label: str
    article_path: str
    eyecatch: str


CATEGORIES = {
    'beauty': Category('beauty', '美容', '/article/beauty/', 'img/beauty-category-eyecatch.png'),
    'dailygoods': Category('dailygoods', '日用品', '/article/dailygoods/', 'img/dailygoods-category-eyecatch.png'),
    'gadget': Category('gadget', '家電', '/article/gadget/', 'img/gadget-category-eyecatch.png'),
}


def get_category(slug: str) -> Category:
    try:
        return CATEGORIES[slug]
    except KeyError as exc:
        raise ValueError(f'unsupported Sellemy category: {slug}') from exc
