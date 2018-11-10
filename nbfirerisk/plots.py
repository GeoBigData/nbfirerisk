import jinja2
import json
import folium
from matplotlib import pyplot as plt, colors, cm
from matplotlib.colors import ListedColormap
import pandas as pd

# CONSTANTS
TMS_103001006E28F900 = 'https://s3.amazonaws.com/notebooks-small-tms/103001006E28F900/{z}/{x}/{y}.png'
TMS_1030010082740C00 = 'https://s3.amazonaws.com/notebooks-small-tms/1030010082740C00/{z}/{x}/{y}.png'


COLORS = {'gray'       : '#8F8E8E',
          'white'      : '#FFFFFF',
          'brightgreen': '#00FF17',
          'red'        : '#FF0000',
          'cyan'       : '#1FFCFF',
          'yellow'     : '#FCFE1A',
          'pink'       : '#F30EFE'}


trees_cmap = ListedColormap([(0.0, 0.0, 1.0, 0), (0.0, 1.0, 1.0, 0.4)])

# FUNCTIONS
def footprints_outline_styler(x):
    return {'fillOpacity': .25,
            'color'      : COLORS['pink'],
            'fillColor'  : COLORS['gray'],
            'weight'     : 1}


def footprints_risk_styler(x):
    return {'fillColor'  : COLORS['gray'],
            'color'      : COLORS['red'] if x['properties']['high_risk'].lower() == 'true' else COLORS['brightgreen'],
            'fillOpacity': 0,
            'weight'     : 2}


def tree_coverage_styler(x):
    return {'fillColor'  : COLORS['gray'],
            'color'      : colors.rgb2hex(cm.RdYlBu_r(x['properties']['defensible_area_pct_trees'] / 100.)[:3]),
            'fillOpacity': 0,
            'weight'     : 2}


def plot_array(array, subplot_ijk, title="", font_size=18, cmap=None):
    sp = plt.subplot(*subplot_ijk)
    sp.set_title(title, fontsize=font_size)
    plt.axis('off')
    plt.imshow(array, cmap=cmap)


def folium_map(geojson_to_overlay, layer_name, location, style_function=None, tiles='Stamen Terrain', zoom_start=16,
               show_layer_control=True, width='100%', height='75%', attr=None, map_zoom=18, max_zoom=20, tms=False,
               zoom_beyond_max=None, base_tiles='OpenStreetMap', opacity=1,
               tooltip_props=None, tooltip_aliases=None):
    m = folium.Map(location=location, zoom_start=zoom_start, width=width, height=height, max_zoom=map_zoom,
                   tiles=base_tiles)
    tiles = folium.TileLayer(tiles=tiles, attr=attr, name=attr, max_zoom=max_zoom,
                             overlay=True, show=True)
    if tms is True:
        options = json.loads(tiles.options)
        options.update({'tms': True})
        tiles.options = json.dumps(options, sort_keys=True, indent=2)
        tiles._template = jinja2.Template(u"""
        {% macro script(this, kwargs) %}
            var {{this.get_name()}} = L.tileLayer(
                '{{this.tiles}}',
                {{ this.options }}
                ).addTo({{this._parent.get_name()}});
        {% endmacro %}
        """)
    if zoom_beyond_max is not None:
        options = json.loads(tiles.options)
        options.update({'maxNativeZoom': zoom_beyond_max, 'maxZoom': max_zoom})
        tiles.options = json.dumps(options, sort_keys=True, indent=2)
        tiles._template = jinja2.Template(u"""
        {% macro script(this, kwargs) %}
            var {{this.get_name()}} = L.tileLayer(
                '{{this.tiles}}',
                {{ this.options }}
                ).addTo({{this._parent.get_name()}});
        {% endmacro %}
        """)
    if opacity < 1:
        options = json.loads(tiles.options)
        options.update({'opacity': opacity})
        tiles.options = json.dumps(options, sort_keys=True, indent=2)
        tiles._template = jinja2.Template(u"""
        {% macro script(this, kwargs) %}
            var {{this.get_name()}} = L.tileLayer(
                '{{this.tiles}}',
                {{ this.options }}
                ).addTo({{this._parent.get_name()}});
        {% endmacro %}
        """)

    tiles.add_to(m)
    if style_function is not None:
        gj = folium.GeoJson(geojson_to_overlay, overlay=True, name=layer_name, style_function=style_function)
    else:
        gj = folium.GeoJson(geojson_to_overlay, overlay=True, name=layer_name)
    if tooltip_props is not None:
        folium.features.GeoJsonTooltip(tooltip_props, aliases=tooltip_aliases).add_to(gj)
    gj.add_to(m)

    if show_layer_control is True:
        folium.LayerControl().add_to(m)

    return m