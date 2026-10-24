{# numerator / denominator, NULL when the denominator is 0 or the window is incomplete. #}
{% macro safe_rate(numerator, denominator, window_complete='true') -%}
    case when {{ window_complete }} and {{ denominator }} > 0
         then round(cast({{ numerator }} as double) / {{ denominator }}, 6) end
{%- endmacro %}
