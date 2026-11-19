import planning_runtime as planning
import pytest


def test_popularity_is_fixed_and_planner_generates_only_two_axes():
    topic = {'comparison_axes': [{'id':'design','label':'デザイン','keywords':['デザイン']}, {'id':'ease','label':'扱いやすさ','keywords':['操作']}]}
    fixed = planning._with_popular_axis(topic)
    assert [a['id'] for a in fixed['comparison_axes']] == ['popular','design','ease']
    assert fixed['comparison_axes'][0] == {'id':'popular','label':'人気・定番の基準','keywords':['人気']}
    assert len(topic['comparison_axes']) == 2

def test_forbid_planner_adding_third_axis():
    with pytest.raises(planning.PlanningError):
        planning._with_popular_axis({'comparison_axes':[{'id':'one'},{'id':'two'},{'id':'three'}]})

def test_forbid_planner_overriding_popular_axis():
    with pytest.raises(planning.PlanningError):
        planning._with_popular_axis({'comparison_axes':[{'id':'popular'},{'id':'two'}]})

def test_benchmark_search_and_existing_three_by_two_contract():
    from growth_role_business import _product_queries
    topic={'query':'鍋に入れる蒸し器', 'product_theme':'鍋に入れる蒸し器', 'comparison_axes':planning._with_popular_axis({'comparison_axes':[{'id':'a','keywords':['平型']},{'id':'b','keywords':['伸縮']}]})['comparison_axes']}
    assert _product_queries(topic)[0].endswith('人気')
    assert len(_product_queries(topic)) == 3
