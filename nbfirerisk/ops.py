from __future__ import division
from future import standard_library
standard_library.install_aliases()
from past.utils import old_div
from shapely.geometry import shape
from shapely import geometry
import requests
import os
from skimage import filters, morphology, measure, color, segmentation, exposure
from scipy import ndimage as ndi
from functools import partial
import pyproj
from shapely import ops
import json
from rasterio import features
import numpy as np
import pickle
import sys
import warnings
warnings.simplefilter("ignore", UserWarning)

# CONSTANTS
ecopia_buildings_shaver_lake = 'https://s3.amazonaws.com/gbdx-training/defensible_area/ecopia_footprints_shaver_lake.geojson'
tree_detection_model = 'https://s3.amazonaws.com/gbdx-training/defensible_area/tree_detection_rf_model.pkl'
ecopia_buildings_boulder = 'https://s3.amazonaws.com/gbdx-training/defensible_area/ecopia_footprints_boulder.geojson'
tree_detection_model_boulder = 'https://s3.amazonaws.com/gbdx-training/defensible_area/tree_detection_rf_model_boulder.pkl'
ecopia_buildings_lake_keswick = 'https://s3.amazonaws.com/gbdx-training/Lake_Keswick_Estates_footprints/Lake_Keswick_Estates_footprints.shp'

# FUNCTIONS
def calc_rsi(image):
    # roll axes to conventional row,col,depth
    img = np.rollaxis(image, 0, 3)

    # bands: Coastal(0), Blue(1), Green(2), Yellow(3), Red(4), Red-edge(5), NIR1(6), NIR2(7)) Multispectral
    B = img[:, :, 1]
    G = img[:, :, 2]
    Y = img[:, :, 3]
    R = img[:, :, 4]
    RE = img[:, :, 5]
    NIR1 = img[:, :, 6]
    NIR2 = img[:, :, 7]

    arvi = old_div((NIR1 - (R - (B - R))), (NIR1 + (R - (B - R))))
    dd = (2 * NIR1 - R) - (G - B)
    gi2 = (B * -0.2848 + G * -0.2434 + R * -0.5436 + NIR1 * 0.7243 + NIR2 * 0.0840) * 5
    gndvi = old_div((NIR1 - G), (NIR1 + G))
    ndre = old_div((NIR1 - RE), (NIR1 + RE))
    ndvi = old_div((NIR1 - R), (NIR1 + R))
    ndvi35 = old_div((G - R), (G + R))
    ndvi84 = old_div((NIR2 - Y), (NIR2 + Y))
    nirry = old_div((NIR1), (R + Y))
    normnir = old_div(NIR1, (NIR1 + R + G))
    psri = old_div((R - B), RE)
    rey = old_div((RE - Y), (RE + Y))
    rvi = old_div(NIR1, R)
    sa = old_div(((Y + R) * 0.35), 2) + old_div((0.7 * (NIR1 + NIR2)), 2) - 0.69
    vi1 = old_div((10000 * NIR1), (RE) ** 2)
    vire = old_div(NIR1, RE)
    br = (old_div(R, B)) * (old_div(G, B)) * (old_div(RE, B)) * (old_div(NIR1, B))
    gr = old_div(G, R)
    rr = (old_div(NIR1, R)) * (old_div(G, R)) * (old_div(NIR1, RE))

    rsi = np.stack([arvi, dd, gi2, gndvi, ndre, ndvi, ndvi35, ndvi84, nirry, normnir, psri, rey, rvi,
                    sa, vi1, vire, br, gr, rr], axis=2)

    return rsi


def power(image, kernel):
    # Normalize images for better comparison.
    image = old_div((image - image.mean()), image.std())
    return np.sqrt(ndi.convolve(image, np.real(kernel), mode='wrap') ** 2 +
                   ndi.convolve(image, np.imag(kernel), mode='wrap') ** 2)


def calc_gabors(image, frequency=1, theta_vals=[0, 1, 2, 3]):
    # convert to gray scale
    img = exposure.equalize_hist(color.rgb2gray(image.rgb(blm=True)))
    results_list = []
    for theta in theta_vals:
        theta = theta / 4. * np.pi
        kernel = filters.gabor_kernel(frequency, theta=theta)
        # Save kernel and the power image for each image
        results_list.append(power(img, kernel))

    gabors = np.rollaxis(np.dstack([results_list]), 0, 3)

    return gabors


def pixels_as_features(image, include_gabors=True):
    # roll axes to conventional row,col,depth
    img = np.rollaxis(image, 0, 3)
    rsi = calc_rsi(image)
    if include_gabors is True:
        gabors = calc_gabors(image)
        stack = np.dstack([img, rsi, gabors])
    else:
        stack = np.dstack([img, rsi])

    feats = stack.ravel().reshape(stack.shape[0] * stack.shape[1], stack.shape[2])

    return feats


def reproject(geom, from_proj=None, to_proj=None):
    tfm = partial(pyproj.transform, pyproj.Proj(init=from_proj), pyproj.Proj(init=to_proj))
    return ops.transform(tfm, geom)


def buffer_meters(geom, distance_m, from_proj='EPSG:4326', epsg_for_meters='EPSG:26944'):
    # convert the geometry from wgs84 to whatever projection is specified
    geom_tfm = reproject(geom, from_proj, epsg_for_meters)
    buffered_geom_meters = geom_tfm.buffer(distance_m)
    buffered_geom = reproject(buffered_geom_meters, epsg_for_meters, from_proj)

    return buffered_geom


def delineate_defensible_area(geom, area_radius_m=9):
    poly = buffer_meters(geom, area_radius_m)
    return poly


def delineate_fuel_reduction_zone(geom, inner_radius_m=9, outer_radius_m=30):
    poly = buffer_meters(geom, outer_radius_m).difference(buffer_meters(geom, inner_radius_m))
    return poly


def get_model(model_url):
    response = requests.get(model_url)
    if sys.version_info[0] == 2:
        pickle_opts = {}
    else:
        pickle_opts = {'encoding': 'latin1'}

    model = pickle.loads(response.content, **pickle_opts)

    return model


def labels_to_polygons(labels_array, image_affine, ignore_label=0):
    # create polygon generator object
    polygon_generator = features.shapes(labels_array.astype('uint8'),
                                        mask=labels_array != ignore_label,
                                        transform=image_affine)
    # Extract out the individual polygons, fixing any invald geometries using buffer(0)
    polygons = [shape(g).buffer(0) for g, v in polygon_generator]

    return polygons


def find_all_trees(chip, tree_model, segments_per_m2=0.04, return_segments=False):
    # find the dead trees and live trees around the building
    chip_feats = pixels_as_features(chip)

    # predict class of each pixel
    predictions = tree_model.predict(chip_feats)
    predictions_2d = predictions.reshape(chip.shape[1], chip.shape[2])

    # segment the image
    chip_area_m2 = chip.shape[1] * chip.shape[2] * chip.rda.metadata['image']['groundSampleDistanceMeters'] ** 2
    n_segments = int(round(chip_area_m2 * segments_per_m2))
    chip_segmented = segmentation.slic(chip.rgb(), n_segments=n_segments, max_iter=100)

    # determine which segments have >50% tree detection
    tree_regions = [r.label for r in measure.regionprops(chip_segmented, intensity_image=predictions_2d) if
                    r.mean_intensity > 0.5]

    tree_segments = np.isin(chip_segmented, tree_regions)
    if return_segments is True:
        trees = tree_segments * chip_segmented
    else:
        trees = tree_segments

    return trees


def extract_trees(chip, tree_model, segments_per_m2=0.04):

    # run the initial segmentation to find trees
    trees_array = find_all_trees(chip, tree_model, segments_per_m2, return_segments=False)
    tree_polygons = labels_to_polygons(trees_array, chip.affine)

    return tree_polygons


def segment_live_dead_trees(chip, tree_model, ndvi, segments_per_m2=0.04, return_geometries=False):
    # run the initial segmentation to find trees
    tree_segments = find_all_trees(chip, tree_model, segments_per_m2, return_segments=True)

    # determine ndvi threshold to use to split dead/live trees
    live_tree_threshold = filters.threshold_otsu(ndvi)

    # split tree segments based on whether their mean ndvi exceeds the threshold
    live_tree_regions = [r.label for r in measure.regionprops(tree_segments, intensity_image=ndvi)
                         if r.mean_intensity > live_tree_threshold]
    live_trees_binary = np.isin(tree_segments, live_tree_regions)

    dead_tree_regions = [r.label for r in measure.regionprops(tree_segments, intensity_image=ndvi)
                         if r.mean_intensity <= live_tree_threshold]
    dead_trees_binary = np.isin(tree_segments, dead_tree_regions)

    if return_geometries is True:
        live_trees = labels_to_polygons(live_trees_binary, chip.affine)
        dead_trees = labels_to_polygons(dead_trees_binary, chip.affine)
    else:
        live_trees = live_trees_binary
        dead_trees = dead_trees_binary

    return live_trees, dead_trees


def assess_tree_coverage(building_geom, live_tree_polys, dead_tree_polys):
    # delineate the defensible are and fuel zones
    defensible_area_geom = delineate_defensible_area(building_geom)
    fuel_area_geom = delineate_fuel_reduction_zone(building_geom)

    # limit to the polys that intersect these features
    live_tree_polys_defensible_area = [poly for poly in live_tree_polys if defensible_area_geom.intersects(poly)]
    live_tree_polys_fuel_area = [poly for poly in live_tree_polys if fuel_area_geom.intersects(poly)]
    dead_tree_polys_defensible_area = [poly for poly in dead_tree_polys if defensible_area_geom.intersects(poly)]
    dead_tree_polys_fuel_area = [poly for poly in dead_tree_polys if fuel_area_geom.intersects(poly)]

    defensible_area_pct_live_trees = old_div(np.sum([defensible_area_geom.intersection(poly).area for poly in
                                             live_tree_polys_defensible_area]), defensible_area_geom.area)
    defensible_area_pct_dead_trees = old_div(np.sum([defensible_area_geom.intersection(poly).area for poly in
                                             dead_tree_polys_defensible_area]), defensible_area_geom.area)

    fuel_area_pct_live_trees = old_div(np.sum([fuel_area_geom.intersection(poly).area for poly in
                                       live_tree_polys_fuel_area]), fuel_area_geom.area)
    fuel_area_pct_dead_trees = old_div(np.sum([fuel_area_geom.intersection(poly).area for poly in
                                       dead_tree_polys_fuel_area]), fuel_area_geom.area)

    results = {'defensible_area_pct_live_trees': round(defensible_area_pct_live_trees * 100, 1),
               'defensible_area_pct_dead_trees': round(defensible_area_pct_dead_trees * 100, 1),
               'fuel_area_pct_live_trees'      : round(fuel_area_pct_live_trees * 100, 1),
               'fuel_area_pct_dead_trees'      : round(fuel_area_pct_dead_trees * 100, 1)
               }
    results['defensible_area_pct_all_trees'] = round((results['defensible_area_pct_live_trees'] +
                                                      results['defensible_area_pct_dead_trees']), 1)
    results['fuel_area_pct_all_trees'] = round((results['fuel_area_pct_live_trees'] +
                                                results['fuel_area_pct_dead_trees']), 1)

    return results


def assess_tree_coverage_simple(building_geom, tree_polys):
    # delineate the defensible are and fuel zones
    defensible_area_geom = delineate_defensible_area(building_geom)
    fuel_area_geom = delineate_fuel_reduction_zone(building_geom)

    # limit to the polys that intersect these features
    tree_polys_defensible_area = [poly for poly in tree_polys if defensible_area_geom.intersects(poly)]
    tree_polys_fuel_area = [poly for poly in tree_polys if fuel_area_geom.intersects(poly)]

    defensible_area_pct_trees = old_div(np.sum([defensible_area_geom.intersection(poly).area for poly in
                                                tree_polys_defensible_area]), defensible_area_geom.area)

    fuel_area_pct_trees = old_div(np.sum([fuel_area_geom.intersection(poly).area for poly in
                                          tree_polys_fuel_area]), fuel_area_geom.area)

    results = {'defensible_area_pct_trees': round(defensible_area_pct_trees * 100, 1),
               'fuel_area_pct_trees'      : round(fuel_area_pct_trees * 100, 1)
               }

    return results



def from_geojson(source):
    if source.startswith('http'):
        response = requests.get(source)
        geojson = json.loads(response.content)
    else:
        if os.path.exists(source):
            with open(source, 'r') as f:
                geojson = json.loads(f.read())
        else:
            raise ValueError("File does not exist: {}".format(source))

    geometries = []
    feats = []
    for f in geojson['features']:
        geom = geometry.shape(f['geometry'])
        props = f['properties']
        feats.append({'geometry': geom, 'properties': props})
        geometries.append(geom)

    return geometries, feats


def to_geojson(l):
    g = {'crs': {u'properties': {u'name': u'urn:ogc:def:crs:OGC:1.3:CRS84'}, 'type': 'name'},
         'features': [{'geometry': d['geometry'].__geo_interface__, 'properties': d['properties'], 'type': 'Feature'}
                      for d in l],
         'type': u'FeatureCollection'}

    if sys.version_info[0] == 3:
        serializer = np_serializer
    else:
        serializer = None

    gj = json.dumps(g, default=serializer)

    return gj


def np_serializer(i):
    if type(i).__module__ == np.__name__:
        return np.asscalar(i)
    raise TypeError(repr(i) + " is not JSON serializable")


def geom_to_array(geom, img, geom_val=1, fill_value=0, all_touched=True, exterior_only=False):
    if exterior_only is True:
        geom_to_rasterize = geom.exterior
    else:
        geom_to_rasterize = geom

    geom_array = features.rasterize([(geom_to_rasterize, geom_val)],
                                    out_shape=(img.shape[1], img.shape[2]),
                                    transform=img.affine,
                                    fill=fill_value,
                                    all_touched=all_touched,
                                    dtype=np.uint8)

    return geom_array
